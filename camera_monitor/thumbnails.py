"""One-shot camera images. Network workers never own or stop live players."""
from collections import OrderedDict
import threading
import av
from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
from .credentials import CredentialStore, CredentialError
from .streams import OnvifClient, AuthError, authenticated_url, same_device_url, dahua_streams

av.logging.set_level(av.logging.PANIC)


def first_frame(url, cancel, transport='tcp'):
    """Decode exactly one image and close the source even on cancellation/error."""
    if cancel.is_set(): return None
    with av.open(url, options={'rtsp_transport': transport, 'rw_timeout': '3000000'},
                 timeout=(4.0, 3.0)) as source:
        if cancel.is_set() or not source.streams.video: return None
        video = source.streams.video[0]
        video.thread_type = 'SLICE'
        video.codec_context.thread_count = 1
        for frame in source.decode(video):
            if cancel.is_set(): return None
            width = min(frame.width, 1280)
            height = max(1, round(frame.height * width / frame.width))
            rgb = frame.reformat(width=width, height=height, format='rgb24')
            plane = rgb.planes[0]
            return QImage(bytes(plane), width, height, plane.line_size, QImage.Format.Format_RGB888).copy()
    return None


class ThumbnailWorker(QThread):
    result = Signal(str, int, object, str)

    def __init__(self, device, token, store, options, credentials=None, parent=None):
        super().__init__(parent)
        self.device, self.token, self.store, self.options = device, token, store, options
        self.credentials = credentials
        self.cancel = threading.Event()

    def run(self):
        client = None
        image, status = None, '暂未取得画面'
        # Total resolving budget, with each in-progress network read bounded separately.
        deadline = threading.Timer(20, self.cancel.set)
        deadline.daemon = True
        deadline.start()
        try:
            credentials = self.credentials if self.credentials is not None else self.store.load(self.device.ip)
            if self.cancel.is_set(): return
            # Match playback: missing saved credentials may mean an anonymous camera.
            username, password = credentials[:2] if credentials is not None else ('', '')
            mode = self.options.get('mode', 'onvif')
            if mode == 'manual':
                url = same_device_url(self.options.get('url', ''), self.device.ip)
            elif mode == 'dahua':
                url = dahua_streams(self.device.ip, self.options.get('channel', 1))[0].url
            else:
                client = OnvifClient(self.device, username, password, self.cancel)
                url = client.streams()[0].url
            if not self.cancel.is_set():
                image = first_frame(authenticated_url(url, username, password), self.cancel,
                                    self.options.get('transport', 'tcp'))
                if image is not None: status = '抓拍画面 · 点击放大'
        except CredentialError:
            status = '无法读取密码，请打开连接设置'
        except (AuthError, av.error.HTTPUnauthorizedError, av.error.HTTPForbiddenError):
            status = '认证失败，请检查账号密码'
        except Exception:
            # Never propagate exception strings: FFmpeg/URLs can contain credentials.
            status = '预览失败，可打开连接设置重试'
        finally:
            deadline.cancel()
            if client: client.close()
            self.credentials = None
            # Emit even on timeout; controller rejects explicitly cancelled generations.
            self.result.emit(self.device.ip, self.token, image, status)


class ThumbnailController(QObject):
    updated = Signal(str, object, str)

    def __init__(self, store=None, connection_options=None, live_image=None, connection=None,
                 parent=None, concurrency=2, worker_factory=ThumbnailWorker):
        super().__init__(parent)
        self.store = store if store is not None else CredentialStore()
        self.connection_options = connection_options
        self.live_image = live_image or (lambda ip: None)
        self.connection = connection
        self.limit = max(1, min(2, concurrency))
        self.worker_factory = worker_factory
        self.pending = OrderedDict()
        self.active = set()
        self.tokens = {}
        self.sequence = 0

    def request(self, device, credentials=None):
        image = self.live_image(device.ip)
        if image is not None and not image.isNull():
            self.sequence += 1
            self.tokens[device.ip] = self.sequence
            self.pending.pop(device.ip, None)
            for worker in self.active:
                if worker.device.ip == device.ip: worker.cancel.set()
            self.updated.emit(device.ip, image.copy(), '播放画面 · 点击放大')
            return
        if device.ip in self.pending or any(w.device.ip == device.ip and not w.cancel.is_set() for w in self.active):
            return
        if len(self.pending) >= 128:
            self.updated.emit(device.ip, None, '等待手动预览')
            return
        self.sequence += 1
        self.tokens[device.ip] = self.sequence
        options = {'mode': 'dahua' if '大华 DHIP' in device.protocols else 'onvif', 'transport': 'tcp'}
        if self.connection_options: options['transport'] = self.connection_options.transport(device.ip)
        if self.connection: options.update(self.connection(device) or {})
        self.pending[device.ip] = (device, self.sequence, options, credentials)
        self.updated.emit(device.ip, None, '等待获取画面…')
        self._pump()

    def _pump(self):
        while self.pending and len(self.active) < self.limit:
            _, (device, token, options, credentials) = self.pending.popitem(last=False)
            worker = self.worker_factory(device, token, self.store, options, credentials, self)
            self.active.add(worker)
            worker.result.connect(self._accept)
            worker.finished.connect(self._finished)
            self.updated.emit(device.ip, None, '正在获取画面…')
            worker.start()

    @Slot(str, int, object, str)
    def _accept(self, ip, token, image, status):
        if self.tokens.get(ip) == token:
            live = self.live_image(ip)
            if live is not None and not live.isNull():
                image, status = live.copy(), '播放画面 · 点击放大'
            self.updated.emit(ip, image, status)

    @Slot()
    def _finished(self):
        worker = self.sender()
        self.active.discard(worker)
        worker.deleteLater()
        self._pump()

    def cancel_all(self):
        self.pending.clear()
        self.tokens.clear()
        for worker in self.active: worker.cancel.set()

    def busy(self):
        return bool(self.active)


class ThumbnailPreview(QDialog):
    """Resizable image preview, deliberately separate from live/fullscreen controls."""
    def __init__(self, image, title='', parent=None):
        super().__init__(parent)
        self.image = image.copy()
        self.setWindowTitle(title or '设备画面')
        self.resize(960, 620)
        layout = QVBoxLayout(self)
        note = QLabel('最近获取的画面（非实时）')
        layout.addWidget(note)
        self.surface = QLabel()
        self.surface.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.surface.setMinimumSize(160, 90)
        from PySide6.QtWidgets import QSizePolicy
        self.surface.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.surface.setStyleSheet('background:#101827;')
        layout.addWidget(self.surface, 1)
        close = QPushButton('关闭')
        close.clicked.connect(self.close)
        layout.addWidget(close)
        self._render()

    def _render(self):
        self.surface.setPixmap(QPixmap.fromImage(self.image).scaled(self.surface.size(),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()
