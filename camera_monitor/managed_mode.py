"""傻瓜模式：现场只输一次设备码，之后一切听云端的。

界面锁死、全屏与否按云端的设置、执行后台下发的动作、把运行状态和截图报上去。
维护人员唯一的出口是组合键加维护密码。
"""
from __future__ import annotations

import logging
import platform
import sys
import time

from PySide6.QtCore import QObject, QProcess, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout)

from . import __version__
from .cloud_state import DEFAULT_CREDENTIAL_ACCOUNT
from .credentials import CredentialError
from .discovery import validate_target_ip
from .player_state import PLAYING, player_state

logger = logging.getLogger(__name__)

STATUS_TICK_MS = 1_000
STATUS_HEARTBEAT_SECONDS = 60
OFFLINE_NOTICE_MS = 10_000
STATUS_ERROR_LOG_SECONDS = 60


def escape_keys(platform=sys.platform):
    """维护入口的组合键。

    Qt 在 macOS 上把 Ctrl 映射成 ⌘，而 ⌘⇧⌥Q 是系统的「立即注销」，按下去现场直接退出登录；
    macOS 上的 Meta 才是物理 Control 键，所以那边用 Meta，按的还是同一排键。
    """
    return 'Meta+Shift+Alt+Q' if platform == 'darwin' else 'Ctrl+Shift+Alt+Q'


class EscapeDialog(QDialog):
    QUIT, UNBIND = 'quit', 'unbind'

    def __init__(self, lock, parent=None):
        super().__init__(parent)
        self.lock = lock
        self.choice = None
        self.setWindowTitle('维护人员入口')
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        intro = QLabel('仅供安装和维护人员使用。请输入维护密码，密码由管理员在后台设置。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.password)
        self.error = QLabel('')
        self.error.setStyleSheet('color:#c0392b;')
        layout.addWidget(self.error)
        row = QHBoxLayout()
        self.quit_button = QPushButton('退出软件')
        self.unbind_button = QPushButton('解除绑定（换设备码）')
        self.quit_button.clicked.connect(lambda: self.choose(self.QUIT))
        self.unbind_button.clicked.connect(lambda: self.choose(self.UNBIND))
        row.addWidget(self.quit_button)
        row.addWidget(self.unbind_button)
        layout.addLayout(row)
        cancel = QPushButton('取消，回到监控画面')
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

    def choose(self, choice):
        if not self.lock.verify(self.password.text()):
            self.error.setText('密码不正确。')
            self.password.clear()
            return
        self.choice = choice
        self.accept()


def ask_escape(window):
    dialog = EscapeDialog(window.screen_lock, window)
    try:
        dialog.exec()
        return dialog.choice
    finally:
        dialog.deleteLater()


def restart_application():
    args = sys.argv[1:] if getattr(sys, 'frozen', False) else ['-m', 'camera_monitor.app']
    QProcess.startDetached(sys.executable, args)
    QApplication.quit()


OFFLINE_TEXT = '云端连接中断，画面正常播放中 · 正在自动重连'
# 被顶号后通道不再重连，不能再写「正在自动重连」让现场干等
REPLACED_TEXT = '该设备码已在另一台电脑上登录 · 画面正常播放，本机不再接收云端指令'


class ManagedController(QObject):
    def __init__(self, window, channel, uploader, autostart, parent=None,
                 escape=ask_escape, restart=restart_application, clock=time.monotonic):
        super().__init__(parent if parent is not None else window)
        self.window = window
        self.channel = channel
        self.uploader = uploader
        self.autostart = autostart
        self.escape = escape
        self.restart = restart
        self.clock = clock
        self.active = False
        self.scan_command = None
        # 搜索一开始会丢掉所有排队的抓图，这里记下被丢掉的，搜完再补
        self.scan_dropped = set()
        self.snapshot_wanted = set()
        self.seen_playing = set()
        self.last_status = None
        self.last_status_at = 0.0
        self.cached_code_tail = None
        # 回报状态每秒一拍，出错时同一种错一分钟只记一次，别把日志刷爆
        self.status_errors = {}
        # 当前这条命令的 ack 发过没有；处理函数半路出错时据此决定要不要补一个失败的 ack
        self.acked_command = None

        root = window.centralWidget()
        self.waiting = QLabel(root)
        self.waiting.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.waiting.setStyleSheet('background:#0f1829;color:#c9d3e6;font-size:20px;')
        self.waiting.hide()
        self.offline = QLabel(OFFLINE_TEXT, root)
        self.offline.setStyleSheet('background:rgba(245,158,11,235);color:#1f1300;'
                                   'padding:6px 12px;border-radius:12px;')
        self.offline.hide()

        self.offline_timer = QTimer(self)
        self.offline_timer.setSingleShot(True)
        self.offline_timer.setInterval(OFFLINE_NOTICE_MS)
        self.offline_timer.timeout.connect(self.show_offline)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(STATUS_TICK_MS)
        self.status_timer.timeout.connect(self.report_status)

        self.shortcut = QShortcut(QKeySequence(escape_keys()), window)
        self.shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut.activated.connect(self.open_escape)
        self.shortcut.setEnabled(False)

        channel.online_changed.connect(self.on_online_changed)
        window.thumbnails.updated.connect(self.on_thumbnail)
        window.scan_completed.connect(self.on_scan_completed)

    # ---------- 进出 ----------

    def enter(self):
        if self.active:
            return
        self.active = True
        self.window.close_settings()
        self.window.wall.set_locked(True)
        self.window.set_presentation(True)
        # 每次进入都补一遍：现场有人手动删了启动项，下次开机前就能补回来
        self.autostart.enable()
        self.shortcut.setEnabled(True)
        self.seen_playing.clear()
        self.last_status = None
        self.cached_code_tail = None
        self.offline.setText(OFFLINE_TEXT)
        self.status_timer.start()
        self.on_online_changed(self.channel.online)
        self.refresh_overlays()

    def leave(self):
        if not self.active:
            return
        self.active = False
        self.status_timer.stop()
        self.offline_timer.stop()
        # 离开托管后还在路上的截图、搜索结果都不该再往云端送
        self.snapshot_wanted.clear()
        self.scan_dropped.clear()
        self.seen_playing.clear()
        self.scan_command = None
        self.shortcut.setEnabled(False)
        self.waiting.hide()
        self.offline.hide()
        self.offline.setText(OFFLINE_TEXT)
        self.autostart.disable()
        self.window.wall.set_locked(False)
        self.window.leave_managed_window()

    def apply(self, result):
        if not self.active:
            return
        self.window.set_managed_fullscreen(result.fullscreen)
        if result.escape_password_hash:
            self.window.screen_lock.set_record(result.escape_password_hash)
        self.refresh_overlays()
        # 配置变了，下一拍必报，后台据此显示「已是最新配置」
        self.last_status = None

    # ---------- 界面 ----------

    def refresh_overlays(self):
        root = self.window.centralWidget()
        self.waiting.setGeometry(root.rect())
        if self.active and not self.window.wall.tiles:
            self.waiting.setText('已连接云端\n正在等待管理员配置摄像头\n\n'
                                 f'本机名称 {platform.node() or "监控屏"} · 设备码尾号 {self.code_tail()}')
            self.waiting.show()
            self.waiting.raise_()
        else:
            self.waiting.hide()
        self.offline.adjustSize()
        self.offline.move(max(0, root.width() - self.offline.width() - 16), 12)
        if not self.offline.isHidden():
            self.offline.raise_()

    def code_tail(self):
        # 等待页每秒刷新一次，别每秒都去读钥匙串；托管期间设备码不会变，换码要先解绑退出
        if self.cached_code_tail is None:
            try:
                session = self.window.cloud.session.load_session() or {}
            except CredentialError:
                session = {}
            code = ''.join(ch for ch in str(session.get('auth_code', '')) if ch.isalnum()).upper()
            self.cached_code_tail = code[-4:] or '----'
        return self.cached_code_tail

    def on_online_changed(self, online):
        if online:
            self.offline_timer.stop()
            self.offline.hide()
            self.offline.setText(OFFLINE_TEXT)
            self.last_status = None
        elif self.active and not self.offline_timer.isActive() and self.offline.isHidden():
            # 抖一下就重连上是常事，10 秒内恢复的不必让现场看到
            self.offline_timer.start()

    def show_offline(self):
        if not self.active or self.channel.online:
            return
        self.offline.show()
        self.refresh_overlays()

    def show_replaced(self):
        """同一个设备码在别处登录，本机通道让位了：常驻提示，直到重新连上或离开托管。"""
        self.offline_timer.stop()
        self.offline.setText(REPLACED_TEXT)
        self.offline.show()
        self.refresh_overlays()

    # ---------- 逃生口 ----------

    def open_escape(self):
        if not self.active:
            return
        choice = self.escape(self.window)
        if choice == EscapeDialog.QUIT:
            self.window.authorized_quit = True
            self.window.close()
        elif choice == EscapeDialog.UNBIND:
            self.window.unbind_cloud()

    # ---------- 动作 ----------

    def run_command(self, payload):
        command_id = str(payload.get('id') or '')
        name = str(payload.get('name') or '')
        args = payload.get('args') if isinstance(payload.get('args'), dict) else {}
        if not self.active:
            self.ack(command_id, False, 'NOT_MANAGED')
            return
        handler = {
            'scan': self.cmd_scan,
            'refresh_snapshots': self.cmd_refresh_snapshots,
            'capture_screen': self.cmd_capture_screen,
            'reconnect_all': self.cmd_reconnect_all,
            'restart_app': self.cmd_restart_app,
        }.get(name)
        if handler is None:
            self.ack(command_id, False, 'UNKNOWN_COMMAND')
            return
        self.acked_command = None
        try:
            handler(command_id, args)
        except Exception:
            # 槽函数里的异常 PySide 只会悄悄吞掉，后台就永远看到「执行中」；
            # 至少留个日志，并把这条命令收尾
            logger.exception('执行云端动作 %s 失败', name)
            if self.scan_command == command_id:
                self.scan_command = None
            if self.acked_command != command_id:
                # 还没来得及确认就出错了，后台那边连「已收到」都没看到，补一个失败的确认
                self.ack(command_id, False, 'FAILED')
            self.done(command_id, False, 'FAILED')

    def ack(self, command_id, ok, error=''):
        self.acked_command = command_id
        message = {'type': 'ack', 'id': command_id, 'ok': ok}
        if error:
            message['error'] = error
        self.channel.send(message)

    def done(self, command_id, ok=True, error=''):
        message = {'type': 'command_done', 'id': command_id, 'ok': ok}
        if error:
            message['error'] = error
        self.channel.send(message)

    def cmd_scan(self, command_id, args):
        target = args.get('target_ip')
        if target:
            try:
                target = validate_target_ip(str(target))
            except ValueError:
                self.ack(command_id, False, 'BAD_TARGET')
                return
        if self.scan_command is not None:
            self.ack(command_id, False, 'BUSY')
            return
        self.scan_command = command_id
        self.scan_dropped = set(self.snapshot_wanted)
        if not self.window.run_scan(target_ip=target or None):
            self.scan_command = None
            self.scan_dropped = set()
            self.ack(command_id, False, 'BUSY')
            return
        self.ack(command_id, True)

    def on_scan_completed(self, cancelled):
        command_id, self.scan_command = self.scan_command, None
        dropped, self.scan_dropped = self.scan_dropped, set()
        if command_id is None or not self.active:
            return
        # 先把搜索结果报上去：后面补截图哪里出了错，也不能让后台一直显示「搜索中」
        try:
            devices = list(self.window.devices.values())
            result = {'type': 'scan_result', 'id': command_id, 'devices': [
                {'ip': d.ip, 'model': d.model or '', 'manufacturer': d.manufacturer or '',
                 'protocols': list(d.protocols or []), 'onvif_urls': list(d.urls or [])}
                for d in devices]}
        except Exception:
            logger.exception('整理搜索结果失败')
            self.done(command_id, False, 'FAILED')
            return
        self.channel.send(result)
        try:
            self.request_scan_snapshots(devices, dropped)
        except Exception:
            logger.exception('搜索后补截图失败')

    def request_scan_snapshots(self, devices, dropped):
        on_wall = {tile.player.device.ip for tile in self.window.wall.tiles}
        requested = set()
        for device in devices:
            if device.ip not in on_wall:
                self.want_snapshot(device)
                requested.add(device.ip)
        # 搜索开始时被 thumbnails.cancel_all() 丢掉的抓图：还认得的重新排上，
        # 这次没搜到的就别让它一直挂在等待名单里
        known = self.known_devices()
        for ip in dropped - requested:
            if ip not in self.snapshot_wanted:
                continue
            if ip in known:
                self.want_snapshot(known[ip])
            else:
                self.snapshot_wanted.discard(ip)

    def credentials_for(self, ip):
        """这台自己存过账号就让抓图线程自己读；没存过就试点位默认账号。"""
        store = self.window.wall.credential_store
        try:
            if store.load(ip) is not None:
                return None
            return store.load(DEFAULT_CREDENTIAL_ACCOUNT)
        except CredentialError:
            return None

    def want_snapshot(self, device):
        self.snapshot_wanted.add(device.ip)
        self.window.thumbnails.request(device, self.credentials_for(device.ip))

    def known_devices(self):
        devices = dict(self.window.devices)
        devices.update({tile.player.device.ip: tile.player.device for tile in self.window.wall.tiles})
        return devices

    def cmd_refresh_snapshots(self, command_id, args):
        devices = self.known_devices()
        wanted = args.get('ips')
        ips = [ip for ip in wanted if ip in devices] if isinstance(wanted, list) else list(devices)
        self.ack(command_id, True)
        for ip in ips:
            self.want_snapshot(devices[ip])
        self.done(command_id, True)

    def cmd_capture_screen(self, command_id, args):
        self.ack(command_id, True)
        ok = self.uploader.submit('screen', self.window.grab().toImage())
        self.done(command_id, ok, '' if ok else 'CAPTURE_FAILED')

    def cmd_reconnect_all(self, command_id, args):
        self.ack(command_id, True)
        self.window.wall.reconnect_all()
        self.done(command_id, True)

    def cmd_restart_app(self, command_id, args):
        self.ack(command_id, True)
        # 重启后进程就没了，不会再有人回报这条命令；不在这里报完成，后台会一直显示「执行中」
        self.done(command_id, True)
        self.window.authorized_quit = True
        # 留一点时间让消息发出去；挂在自己身上，控制器先没了就不再重启
        QTimer.singleShot(300, self, self.restart)

    def on_thumbnail(self, ip, image, status):
        if not self.active:
            return
        try:
            if ip not in self.snapshot_wanted or image is None or image.isNull():
                return
            self.snapshot_wanted.discard(ip)
            self.uploader.submit('camera', image, ip)
        except Exception:
            logger.exception('上传摄像头截图失败')

    # ---------- 状态 ----------

    def status_payload(self):
        cameras = []
        for tile in self.window.wall.tiles:
            player = tile.player
            entry = {'ip': player.device.ip, 'state': player_state(player.status.text())}
            image = player.surface._image
            if entry['state'] == PLAYING and image is not None and not image.isNull():
                entry['width'], entry['height'] = image.width(), image.height()
            cameras.append(entry)
        return {'type': 'status', 'config_version': self.window.cloud.version(),
                'fullscreen': self.window.isFullScreen(), 'app_version': __version__,
                'cameras': cameras}

    def report_status(self):
        if not self.active:
            return
        try:
            self.send_status()
        except Exception as error:
            # 定时器槽里的异常 PySide 只会悄悄吞掉；每秒都会再来，同一种错一分钟只记一次
            now = self.clock()
            last = self.status_errors.get(type(error))
            if last is None or now - last >= STATUS_ERROR_LOG_SECONDS:
                self.status_errors[type(error)] = now
                logger.exception('回报运行状态失败')

    def send_status(self):
        payload = self.status_payload()
        for camera in payload['cameras']:
            # 真画出第一帧才算播起来：只是状态写着「正在播放」时就抓图，会对摄像头再开一路流
            if 'width' in camera and camera['ip'] not in self.seen_playing:
                self.seen_playing.add(camera['ip'])
                tile = next(t for t in self.window.wall.tiles if t.player.device.ip == camera['ip'])
                self.want_snapshot(tile.player.device)
        self.refresh_overlays()
        now = self.clock()
        if payload == self.last_status and now - self.last_status_at < STATUS_HEARTBEAT_SECONDS:
            return
        if self.channel.send(payload):
            self.last_status, self.last_status_at = payload, now
