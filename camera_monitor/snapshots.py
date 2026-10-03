"""把画面压成小 JPEG 交给云端：后台靠它认出哪个 IP 是哪个摄像头、现场屏幕上正放着什么。"""
from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, QThread, Signal

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

    def run(self):
        try:
            self.upload(self.kind, self.jpeg, self.ip)
            self.ok = True
        except Exception:
            # 截图传不上去只是后台少一张图，不值得打扰现场
            self.ok = False


class SnapshotUploader(QObject):
    """一次只传一张；排队中的同一路只留最新的那张。"""

    uploaded = Signal(str, str)

    def __init__(self, upload, parent=None):
        super().__init__(parent)
        self.upload = upload
        self.queue = []
        self.worker = None

    def submit(self, kind, image, ip=''):
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
        self.queue.clear()
        if self.worker is not None:
            self.worker.wait(15_000)

    def _pump(self):
        if self.worker is not None or not self.queue:
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
        worker.deleteLater()
        self._pump()
