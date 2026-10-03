"""把画面压成小 JPEG 交给云端：后台靠它认出哪个 IP 是哪个摄像头、现场屏幕上正放着什么。"""
from __future__ import annotations

import logging

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, QThread, Signal

from .cloud import CloudError

logger = logging.getLogger(__name__)

# 退出时还没传完的上传线程会被摘出来放在这里，直到它自己跑完。
# 正在运行的 QThread 一旦被销毁，Qt 会直接 abort 整个进程，所以宁可让它多活一会儿。
_ORPHANS = set()

# (最大宽度, 最大字节数)。服务端的上限更宽松，这里留了余量。
CAMERA_LIMIT = (480, 200_000)
SCREEN_LIMIT = (1280, 400_000)


def encode_jpeg(image, max_width, max_bytes):
    if image is None or image.isNull():
        return b''
    if image.width() > max_width:
        image = image.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
    for quality in (80, 65, 50, 35):
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'JPG', quality)
        buffer.close()
        raw = bytes(data)
        if raw and len(raw) <= max_bytes:
            return raw
    return b''


class UploadCall(QThread):
    def __init__(self, upload, kind, ip, jpeg, parent=None):
        super().__init__(parent)
        self.upload, self.kind, self.ip, self.jpeg = upload, kind, ip, jpeg
        self.ok = False
        self.error = ''

    def run(self):
        try:
            self.upload(self.kind, self.jpeg, self.ip)
            self.ok = True
        except Exception as exc:
            # 截图传不上去只是后台少一张图，不值得打扰现场；但要留个记录，否则永远不知道为什么没图。
            # CloudError 的文字是我们自己写的、不含凭据，可以原样记；
            # 其他异常（比如 requests 的）文字里可能带着 URL、令牌，只记类型名。
            self.ok = False
            self.error = str(exc) if isinstance(exc, CloudError) else type(exc).__name__


def _release_orphan(worker):
    _ORPHANS.discard(worker)
    worker.deleteLater()


class SnapshotUploader(QObject):
    """一次只传一张；排队中的同一路只留最新的那张。"""

    uploaded = Signal(str, str)
    failed = Signal(str, str)

    def __init__(self, upload, parent=None):
        super().__init__(parent)
        self.upload = upload
        self.queue = []
        self.worker = None
        self.stop_timeout_ms = 15_000
        self._closed = False

    def submit(self, kind, image, ip=''):
        if self._closed:
            return False
        limit = SCREEN_LIMIT if kind == 'screen' else CAMERA_LIMIT
        jpeg = encode_jpeg(image, *limit)
        if not jpeg:
            return False
        self.queue = [item for item in self.queue if item[:2] != (kind, ip)]
        self.queue.append((kind, ip, jpeg))
        self._pump()
        return True

    def busy(self):
        return self.worker is not None

    def stop(self):
        # 停下之后不再接新活，否则退出途中又冒出一个上传线程
        self._closed = True
        self.queue.clear()
        worker = self.worker
        if worker is None or worker.wait(self.stop_timeout_ms):
            return
        # 等了这么久还没传完（比如 DNS 卡住）：不能让它跟着上传器一起被销毁。
        # 摘掉父对象，并由模块级集合持有，等它自己结束后再回收。
        self.worker = None
        try:
            worker.finished.disconnect(self._finished)
        except (RuntimeError, TypeError):
            pass
        worker.setParent(None)
        _ORPHANS.add(worker)
        worker.finished.connect(lambda w=worker: _release_orphan(w))
        # 连接之前线程恰好结束的话信号已经错过了，这里补一次
        if worker.isFinished():
            _release_orphan(worker)

    def _pump(self):
        if self._closed or self.worker is not None or not self.queue:
            return
        kind, ip, jpeg = self.queue.pop(0)
        self.worker = UploadCall(self.upload, kind, ip, jpeg, self)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker is None:
            return
        if worker.ok:
            self.uploaded.emit(worker.kind, worker.ip)
        else:
            logger.warning('截图上传失败：%s %s（%s）', worker.kind, worker.ip, worker.error)
            self.failed.emit(worker.kind, worker.ip)
        worker.deleteLater()
        self._pump()
