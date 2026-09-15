"""Video-only RTSP preview with bounded network waits and latest-frame rendering."""
from __future__ import annotations
from .choices import ChoiceButton as QComboBox
import threading
import time
from datetime import datetime
import av
from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QSpinBox, QSizePolicy, QPlainTextEdit, QApplication)
from .credentials import CredentialStore, CredentialError
from .device_names import DeviceNames
from .connection_options import ConnectionOptions
from .streams import OnvifClient, Stream, StreamError, authenticated_url, same_device_url, dahua_streams

# FFmpeg errors can include credential-bearing URLs. UI uses authored messages only.
av.logging.set_level(av.logging.PANIC)


class Decoder(QThread):
    ready = Signal(str)
    recovering = Signal(str)
    error = Signal(str)
    diagnostic = Signal(str)

    def __init__(self, url, parent=None, max_width=1920, transport='tcp'):
        super().__init__(parent)
        if transport not in ('tcp', 'udp'):raise ValueError('Unsupported transport')
        self.transport = transport
        self._url = url
        self.max_width=max_width
        self.cancel = threading.Event()
        self._lock = threading.Lock()
        self._frame = None
        self.received = False

    def take_frame(self):
        with self._lock:
            frame, self._frame = self._frame, None
        return frame

    def run(self):
        live = self._url.startswith(('rtsp://', 'rtsps://'))
        retries = 0
        try:
            while not self.cancel.is_set():
                retryable = True
                session_received = False
                started = time.monotonic()
                frames = 0
                cause = '流结束（EOF）'
                stage = '建立连接'
                error_code = None
                try:
                    options = {'rtsp_transport':self.transport, 'rw_timeout':'4000000'} if live else {}
                    with av.open(self._url, options=options, timeout=(5.0, 4.0)) as source:
                        if not source.streams.video:
                            raise StreamError('此地址没有视频轨道。')
                        video = source.streams.video[0]
                        # Preserve the single-thread teardown fix on macOS.
                        video.thread_type = 'SLICE'
                        video.codec_context.thread_count = 1
                        stage = '读取视频'
                        for frame in source.decode(video):
                            if self.cancel.is_set(): break
                            width = min(frame.width, self.max_width)
                            height = max(1, round(frame.height * width / frame.width))
                            rgb = frame.reformat(width=width, height=height, format='rgb24')
                            plane = rgb.planes[0]
                            image = QImage(bytes(plane), width, height, plane.line_size, QImage.Format.Format_RGB888).copy()
                            with self._lock: self._frame = image
                            frames += 1
                            if not session_received:
                                session_received = self.received = True
                                self.ready.emit(f'{frame.width} × {frame.height} · {video.codec_context.name.upper()}')
                        message = '视频流已结束，请重新连接。' if self.received else '已连接，但没有收到可解码的视频帧。'
                except Exception as exc:
                    # Only fixed labels and numeric fields enter diagnostics, never raw errors or URLs.
                    error_code = getattr(exc, 'errno', None)
                    if not isinstance(error_code, int):error_code = None
                    cause = ('连接或读取超时' if isinstance(exc, (av.error.TimeoutError, TimeoutError)) else
                             '视频数据损坏或无法解码' if isinstance(exc, av.error.InvalidDataError) else
                             '认证失败' if isinstance(exc, (av.error.HTTPUnauthorizedError, av.error.HTTPForbiddenError)) else
                             '视频地址不存在' if isinstance(exc, av.error.HTTPNotFoundError) else
                             '流结束（EOF）' if isinstance(exc, av.error.EOFError) else '连接或解码异常')
                    if isinstance(exc, StreamError):
                        message, retryable = str(exc), False
                    else:
                        # PyAV messages can contain credentials/paths: classify by type only.
                        if isinstance(exc, (av.error.HTTPUnauthorizedError, av.error.HTTPForbiddenError)):
                            message, retryable = '视频认证失败，请检查摄像头用户名和密码。', False
                        elif isinstance(exc, av.error.HTTPNotFoundError):
                            message, retryable = '没有找到此视频流，请尝试其他通道或检查 RTSP 地址。', False
                        else:
                            message = '视频连接失败或超时，请检查账号密码、RTSP 服务、网络和所选通道。'
                if self.cancel.is_set(): break
                self.diagnostic.emit(f'{datetime.now():%H:%M:%S} · {self.transport.upper()} · {stage} · {cause} · 持续={time.monotonic()-started:.1f}秒 · 帧={frames} · 错误码={error_code if error_code is not None else "无"}')
                # Only recover a previously verified live stream, never retry bad credentials.
                if not (live and self.received and retryable):
                    self.error.emit(message)
                    break
                delay = 3
                retries += 1
                with self._lock: self._frame = None
                self.recovering.emit(f'视频中断，{delay} 秒后自动重连 · 第 {retries} 次（可点击停止）')
                if self.cancel.wait(delay): break
        finally:
            self._url = ''


class ResolveWorker(QThread):
    resolved = Signal(object)
    error = Signal(str)

    def __init__(self, device, username, password, parent=None):
        super().__init__(parent)
        self.device = device
        self.username, self.password = username, password
        self.cancel = threading.Event()

    def run(self):
        client = None
        try:
            client = OnvifClient(self.device, self.username, self.password, self.cancel)
            streams = client.streams()
            if not self.cancel.is_set():
                self.resolved.emit(streams)
        except StreamError as exc:
            if not self.cancel.is_set(): self.error.emit(str(exc))
        except Exception:
            if not self.cancel.is_set(): self.error.emit('获取通道失败，请检查摄像头 ONVIF 设置。')
        finally:
            if client: client.close()
            self.password = ''


class VideoSurface(QLabel):
    aspect_changed = Signal(float)
    activated = Signal()
    def __init__(self):
        super().__init__('连接后将在这里显示实时画面')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(480, 270)
        self.setStyleSheet('background: #edf2f8; color: #64748b; border-radius: 8px;')
        self._image = None
        self.aspect_ratio = 16/9
        self.fill = False
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def set_fill(self, fill):
        self.fill = fill
        if self._image is not None:self.show_frame(self._image)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
            event.accept()
        else:super().mouseDoubleClickEvent(event)

    def show_frame(self, image):
        if image.isNull():return
        ratio=image.width()/image.height()
        if abs(ratio-self.aspect_ratio) > .001:
            self.aspect_ratio=ratio
            self.aspect_changed.emit(ratio)
        self._image = image
        self.setStyleSheet('background: transparent; color: #64748b;')
        mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding if self.fill else Qt.AspectRatioMode.KeepAspectRatio
        pixmap = QPixmap.fromImage(image).scaled(self.size(), mode, Qt.TransformationMode.SmoothTransformation)
        if self.fill:
            pixmap = pixmap.copy((pixmap.width()-self.width())//2, (pixmap.height()-self.height())//2, self.width(), self.height())
        self.setPixmap(pixmap)

    def reset(self, text):
        self.setStyleSheet('background: #edf2f8; color: #64748b; border-radius: 8px;')
        self._image = None
        self.clear()
        self.setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._image is not None: self.show_frame(self._image)


class PlayerWindow(QDialog):
    playing = Signal(str)
    status_changed = Signal(str)
    settings_hidden = Signal()
    credentials_required = Signal()

    def __init__(self, device, parent=None, credential_store=None, device_names=None, connection_options=None):
        super().__init__(parent)
        self.device = device
        self.device_names = device_names if device_names is not None else DeviceNames()
        self.connection_options = connection_options if connection_options is not None else ConnectionOptions()
        self.credential_store = credential_store or CredentialStore()
        self._remembered = None
        self._verified_credentials = None
        self._hide_when_ready = False
        self.hosted = False
        self.preview_width=1920
        self.decoder = None
        self.resolver = None
        self.stopping = False
        self.rendering = False
        self.setWindowTitle(f'实时预览 · {device.ip}')
        self.resize(980, 740)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20,20,20,20)
        layout.setSpacing(12)
        title = self.device_title = QLabel(f'{self.device_names.display(device)} · {device.ip}')
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        title.setStyleSheet('font-size: 19px; font-weight: 600;')
        layout.addWidget(title)
        name_form = QHBoxLayout()
        self.custom_name = QLineEdit(self.device_names.get(device.ip))
        self.custom_name.setMaxLength(80)
        self.custom_name.setPlaceholderText('自定义名称，例如：大门、仓库、前台')
        self.save_name_button = QPushButton('保存名称')
        self.save_name_button.clicked.connect(self.save_name)
        self.custom_name.returnPressed.connect(self.save_name)
        name_form.addWidget(self.custom_name, 1)
        name_form.addWidget(self.save_name_button)
        layout.addLayout(name_form)
        self.name_note = QLabel('名称保存在这台电脑，清空后恢复设备名称。')
        self.name_note.setWordWrap(True)
        layout.addWidget(self.name_note)
        self.device_names.changed.connect(self.refresh_name)
        form = QHBoxLayout()
        self.username = QLineEdit()
        self.username.setPlaceholderText('摄像头用户名')
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('摄像头密码')
        self.mode = QComboBox()
        self.mode.addItems(['ONVIF 自动获取通道', '大华 RTSP', '手动 RTSP'])
        if '大华 DHIP' in device.protocols: self.mode.setCurrentIndex(1)
        form.addWidget(self.username)
        form.addWidget(self.password)
        form.addWidget(self.mode)
        self.connect_button = QPushButton('连接并播放')
        self.connect_button.clicked.connect(self.connect_camera)
        form.addWidget(self.connect_button)
        layout.addLayout(form)
        self.remember=QCheckBox('记住此摄像头密码（系统安全存储）')
        self.remember.setChecked(True)
        layout.addWidget(self.remember)
        self.credential_note=QLabel('成功播放后自动保存账号密码，可取消勾选。')
        self.credential_note.setWordWrap(True)
        layout.addWidget(self.credential_note)
        options = QHBoxLayout()
        self.channel_label = QLabel('大华通道')
        self.channel = QSpinBox()
        self.channel.setRange(1, 256)
        options.addWidget(self.channel_label)
        options.addWidget(self.channel)
        self.manual = QLineEdit()
        self.manual.setPlaceholderText(f'rtsp://{device.ip}:554/视频路径（账号密码填在上方）')
        options.addWidget(self.manual, 1)
        layout.addLayout(options)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.update_mode()
        transport_form = QHBoxLayout()
        transport_form.addWidget(QLabel('传输方式'))
        self.transport = QComboBox()
        self.transport.addItem('TCP（默认）', 'tcp')
        self.transport.addItem('UDP（兼容模式）', 'udp')
        self.transport.setCurrentIndex(1 if self.connection_options.transport(device.ip) == 'udp' else 0)
        self.transport.currentIndexChanged.connect(self.save_transport)
        transport_form.addWidget(self.transport, 1)
        layout.addLayout(transport_form)
        self.transport_note = QLabel('频繁断流时可停止播放后尝试 UDP；选择会自动保存。')
        self.transport_note.setWordWrap(True)
        layout.addWidget(self.transport_note)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('视频通道'))
        self.profiles = QComboBox()
        controls.addWidget(self.profiles,1)
        self.play_button = QPushButton('播放所选通道')
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.play_selected)
        controls.addWidget(self.play_button)
        self.stop_button = QPushButton('停止播放')
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_playback)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)
        self.surface = VideoSurface()
        layout.addWidget(self.surface,1)
        self.status = QLabel('输入账号密码后连接；免认证设备可以留空。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.connection_diagnostics = QPlainTextEdit()
        self.connection_diagnostics.setReadOnly(True)
        self.connection_diagnostics.setMaximumBlockCount(100)
        self.connection_diagnostics.setMaximumHeight(90)
        self.connection_diagnostics.setPlaceholderText('断线诊断会记录在这里（不包含账号密码）')
        layout.addWidget(self.connection_diagnostics)
        self.copy_diagnostics_button = QPushButton('复制连接诊断')
        self.copy_diagnostics_button.clicked.connect(self.copy_diagnostics)
        layout.addWidget(self.copy_diagnostics_button)
        self.note = QLabel('实时视频预览，不录制；当前版本不播放音频。')
        note=self.note
        note.setWordWrap(True)
        layout.addWidget(note)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.render_frame)
        self.timer.start()
        self.password.returnPressed.connect(self.connect_camera)
        try:
            saved=self.credential_store.load(self.device.ip)
            if saved:
                self.username.setText(saved[0]);self.password.setText(saved[1])
                self.remember.setChecked(True)
                self._remembered=saved
                self.credential_note.setText('已从系统安全存储读取此设备的账号密码。')
        except CredentialError as exc:
            self.credential_note.setText(str(exc))
        self.remember.toggled.connect(self.remember_changed)

    def copy_diagnostics(self):
        QApplication.clipboard().setText(f'设备 IP：{self.device.ip}\n传输：{self.transport.currentData().upper()}；连接超时5秒；读取超时4秒；重试间隔3秒\n' + self.connection_diagnostics.toPlainText())

    def save_transport(self):
        try:
            self.connection_options.save_transport(self.device.ip, self.transport.currentData())
            self.transport_note.setText('传输方式已保存，下次连接自动使用。')
        except OSError:
            self.transport_note.setText('传输方式未保存，本次连接仍可使用所选方式。')

    def refresh_name(self, ip, name):
        if ip == self.device.ip:
            self.custom_name.setText(name)
            self.device_title.setText(f'{self.device_names.display(self.device)} · {ip}')

    def save_name(self):
        try:
            self.device_names.save(self.device.ip, self.custom_name.text())
            self.name_note.setText('名称已保存。' if self.custom_name.text().strip() else '已恢复设备名称。')
        except (OSError, ValueError):
            self.name_note.setText('名称未保存，请检查本机设置存储权限后重试。')

    def set_status(self,text):
        self.status.setText(text)
        self.status_changed.emit(text)

    def remember_changed(self,checked):
        if checked:
            if self._verified_credentials == (self.username.text(), self.password.text()):
                self.save_credentials()
            else:
                self.credential_note.setText('成功播放后保存；按当前设备 IP 区分。')
        else:
            try:
                self.credential_store.forget(self.device.ip)
                self._remembered=None
                self.credential_note.setText('已忘记保存的密码；当前窗口仍可继续连接。')
            except CredentialError as exc:
                self.credential_note.setText(str(exc))
                self.remember.blockSignals(True)
                self.remember.setChecked(True)
                self.remember.blockSignals(False)

    def update_mode(self):
        self.channel_label.setVisible(self.mode.currentIndex() == 1)
        self.channel.setVisible(self.mode.currentIndex() == 1)
        self.manual.setVisible(self.mode.currentIndex() == 2)

    def busy(self):
        return any(w and w.isRunning() for w in (self.decoder, self.resolver))

    def set_busy(self, value):
        for widget in (self.username,self.password,self.mode,self.channel,self.manual,self.connect_button,self.transport):
            widget.setEnabled(not value)
        self.stop_button.setEnabled(value)
        self.play_button.setEnabled(not value and self.profiles.count() > 0)
        self.profiles.setEnabled(not value)

    def connect_camera(self):
        if self.busy(): return
        self.stopping = False
        self._verified_credentials = None
        self._hide_when_ready = self.hosted and self.isVisible()
        self.profiles.clear()
        self.surface.reset('正在连接摄像头…')
        self.set_status('正在获取可播放通道…')
        self.set_busy(True)
        if self.mode.currentIndex() == 1:
            self.load_streams(dahua_streams(self.device.ip, self.channel.value()))
        elif self.mode.currentIndex() == 2:
            try:
                url = same_device_url(self.manual.text(), self.device.ip)
                self.load_streams([Stream('手动视频流', url)])
            except StreamError as exc:
                self.report_error(str(exc))
                self.set_busy(False)
        else:
            if self.resolver: self.resolver.deleteLater()
            self.resolver = ResolveWorker(self.device, self.username.text(), self.password.text(), self)
            self.resolver.resolved.connect(self.load_streams)
            self.resolver.error.connect(self.report_error)
            self.resolver.finished.connect(self.resolve_finished)
            self.resolver.start()

    def load_streams(self, streams):
        if self.stopping: return
        self.profiles.clear()
        for stream in streams: self.profiles.addItem(stream.name, stream)
        if streams: self.start_decoder(streams[0])

    def resolve_finished(self):
        if self.stopping:
            self.set_status('已停止')
        if not self.decoder or not self.decoder.isRunning(): self.set_busy(False)

    def play_selected(self):
        if self.busy() or self.profiles.currentData() is None: return
        self.stopping = False
        self._hide_when_ready = self.hosted and self.isVisible()
        self.start_decoder(self.profiles.currentData())

    def start_decoder(self, stream):
        self._verified_credentials = None
        try:
            url = authenticated_url(stream.url, self.username.text(), self.password.text())
        except (StreamError,ValueError):
            self.report_error('视频地址无效，请检查地址和端口。')
            self.set_busy(False)
            return
        if self.decoder: self.decoder.deleteLater()
        self.surface.reset('正在连接视频流，等待首帧…')
        self.set_status('正在连接视频流，等待首帧…')
        self.rendering = True
        self.set_busy(True)
        self.decoder = Decoder(url, self, max_width=self.preview_width, transport=self.transport.currentData())
        self.decoder.diagnostic.connect(self.connection_diagnostics.appendPlainText)
        self.decoder.recovering.connect(self.on_recovering)
        self.decoder.ready.connect(self.on_ready)
        self.decoder.error.connect(self.report_error)
        self.decoder.finished.connect(self.decode_finished)
        self.decoder.start()

    def on_recovering(self, message):
        if not self.stopping:
            self.rendering = False
            self.surface.reset(message)
            self.set_status(message)

    def on_ready(self, info):
        if not self.stopping:
            self.rendering = True
            self.set_status(f'正在播放 · {info}')
            self._verified_credentials = (self.username.text(), self.password.text())
            saved = self.save_credentials()
            self.playing.emit(self.device.ip)
            if self._hide_when_ready:
                self._hide_when_ready = False
                if saved:
                    self.hide()
                    self.settings_hidden.emit()

    def save_credentials(self):
        current = (self.username.text(), self.password.text())
        if not self.remember.isChecked() or not any(current): return True
        if current != self._verified_credentials: return False
        if self._remembered == current: return True
        try:
            self.credential_store.save(self.device.ip, *current)
            self._remembered = current
            self.credential_note.setText('账号密码已保存到系统安全存储。')
            return True
        except CredentialError as exc:
            self.credential_note.setText(str(exc))
            self.set_status('画面正在播放 · 密码未保存，请查看连接设置')
            return False

    def render_frame(self):
        if self.decoder and not self.stopping and self.rendering:
            image = self.decoder.take_frame()
            if image is not None: self.surface.show_frame(image)

    def report_error(self, message):
        if not self.stopping:
            self.rendering = False
            self.set_status(message)
            self.surface.reset(message)
            if '认证失败' in message:self.credentials_required.emit()

    def stop_playback(self):
        self.stopping = True
        self._verified_credentials = None
        self._hide_when_ready = False
        self.rendering = False
        for worker in (self.decoder, self.resolver):
            if worker: worker.cancel.set()
        self.stop_button.setEnabled(False)
        self.set_status('正在停止，等待网络连接结束…' if self.busy() else '已停止')
        self.surface.reset('播放已停止')
        if not self.busy(): self.set_busy(False)

    def decode_finished(self):
        if self.stopping: self.set_status('已停止')
        self.set_busy(self.busy())

    def reject(self):
        # Escape must cancel workers and erase credentials just like the close button.
        self.close()

    def closeEvent(self, event):
        if self.hosted:
            event.ignore()
            self.hide()
            self.settings_hidden.emit()
            return
        self.stop_playback()
        if self.busy():
            event.ignore()
            QTimer.singleShot(150,self.close)
            return
        self.password.clear()
        self._remembered=None
        self.manual.clear()
        self.profiles.clear()
        self.timer.stop()
        event.accept()
        self.done(QDialog.DialogCode.Rejected)
