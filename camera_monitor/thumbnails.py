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
        # 每个线程开工时用的账号，用来判断后来要的账号是不是新的
        self.started_with = {}
        # 线程已经在跑时又有人带着另一组账号来要：这次没抓到就用它再试一次
        self.retry_credentials = {}

    def request(self, device, credentials=None):
        image = self.live_image(device.ip)
        if image is not None and not image.isNull():
            self.sequence += 1
            self.tokens[device.ip] = self.sequence
            self.pending.pop(device.ip, None)
            self.retry_credentials.pop(device.ip, None)
            for worker in self.active:
                if worker.device.ip == device.ip: worker.cancel.set()
            self.updated.emit(device.ip, image.copy(), '播放画面 · 点击放大')
            return
        # 同一台已经在排队或在抓时不重复排，但后来的请求可能带着先前没有的账号
        # （比如搜索结束时先不带账号排上，云端托管再带着默认账号要一次），不能直接吞掉
        if device.ip in self.pending:
            if credentials is not None:
                queued, token, options, _ = self.pending[device.ip]
                self.pending[device.ip] = (queued, token, options, credentials)
            return
        running = next((w for w in self.active if w.device.ip == device.ip and not w.cancel.is_set()), None)
        if running is not None:
            # 正在抓的不打断：多半是免密就能抓到；抓不到再拿新账号补一次
            if credentials is not None and credentials != self.started_with.get(running):
                self.retry_credentials[device.ip] = (device, credentials)
            return
        self._enqueue(device, credentials)

    def _enqueue(self, device, credentials):
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
            self.started_with[worker] = credentials
            worker.result.connect(self._accept)
            worker.finished.connect(self._finished)
            self.updated.emit(device.ip, None, '正在获取画面…')
            worker.start()

    @Slot(str, int, object, str)
    def _accept(self, ip, token, image, status):
        if self.tokens.get(ip) != token:
            return
        live = self.live_image(ip)
        if live is not None and not live.isNull():
            image, status = live.copy(), '播放画面 · 点击放大'
        retry = self.retry_credentials.pop(ip, None)
        if image is None and retry is not None:
            # 只补这一次：重试的线程开工时用的就是这组账号，同样的账号不会再登记重试
            device, credentials = retry
            for worker in self.active:
                if worker.device.ip == ip and worker.token == token: worker.cancel.set()
            self._enqueue(device, credentials)
            return
        self.updated.emit(ip, image, status)

    @Slot()
    def _finished(self):
        worker = self.sender()
        self.active.discard(worker)
        self.started_with.pop(worker, None)
        worker.deleteLater()
        self._pump()

    def cancel_all(self):
        self.pending.clear()
        self.tokens.clear()
        self.retry_credentials.clear()
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
