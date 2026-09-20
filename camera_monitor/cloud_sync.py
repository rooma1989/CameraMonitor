"""Keeps this computer's monitor wall in step with its cloud profile.

Every network call runs on its own thread: a monitoring screen must never freeze
because the internet hiccupped. Losing the connection is not an error state —
the wall keeps playing from what is already on this computer.
"""
from __future__ import annotations

import json
import uuid

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Signal

from . import cloud_state
from .cloud import CloudAuthError, CloudClient, CloudConflict, CloudError
from .credentials import CloudSessionStore, CredentialError

POLL_INTERVAL_MS = 60_000
PUSH_DEBOUNCE_MS = 2_000


class CloudCall(QThread):
    """One cloud request, off the UI thread."""

    done = Signal(str, object)
    failed = Signal(str, str, str)
    conflicted = Signal(str, object)

    def __init__(self, kind, run, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._run = run

    def run(self):
        try:
            self.done.emit(self.kind, self._run())
        except CloudConflict as exc:
            self.conflicted.emit(self.kind, exc.snapshot)
        except CloudError as exc:
            self.failed.emit(self.kind, str(exc), exc.failure_code)
        except Exception:
            # 绝不透传底层异常文本：配置里带着摄像头密码
            self.failed.emit(self.kind, '云端同步遇到未知问题，请稍后重试。', '')


class CloudSync(QObject):
    status = Signal(str)
    applied = Signal(object)
    session_changed = Signal(bool)
    login_result = Signal(bool, str)

    def __init__(self, names, options, store, collector, parent=None,
                 client=None, settings=None, session_store=None):
        super().__init__(parent)
        self.names = names
        self.options = options
        self.store = store
        self.collector = collector
        self.settings = settings if settings is not None else QSettings('CameraMonitor', 'Cloud')
        self.session = session_store if session_store is not None else CloudSessionStore()
        self.client = client if client is not None else CloudClient(self.base_url())
        self.token = ''
        self.calls = []
        self.closing = False
        self._relogin_pending = ''

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(POLL_INTERVAL_MS)
        self.poll_timer.timeout.connect(self.check_for_updates)

        self.push_timer = QTimer(self)
        self.push_timer.setSingleShot(True)
        self.push_timer.setInterval(PUSH_DEBOUNCE_MS)
        self.push_timer.timeout.connect(self.push_now)

    # ---------- 本机状态 ----------

    def base_url(self):
        value = self.settings.value('cloud/base_url', '')
        return str(value) if value else None

    def enabled(self):
        return str(self.settings.value('cloud/enabled', False)).lower() in ('true', '1')

    def profile_name(self):
        return str(self.settings.value('cloud/profile_name', '') or '')

    def version(self):
        try:
            return int(self.settings.value('cloud/version', 0))
        except (TypeError, ValueError):
            return 0

    def client_uid(self):
        value = str(self.settings.value('cloud/client_uid', '') or '')
        if not value:
            value = uuid.uuid4().hex
            self.settings.setValue('cloud/client_uid', value)
            self.settings.sync()
        return value

    def cached_snapshot(self):
        raw = self.settings.value('cloud/snapshot', '')
        if not raw:
            return None
        try:
            snapshot = json.loads(str(raw))
            return snapshot if isinstance(snapshot, dict) else None
        except ValueError:
            return None

    def _remember(self, snapshot):
        self.settings.setValue('cloud/version', int(snapshot.get('version', 0)))
        self.settings.setValue('cloud/profile_name',
                               str((snapshot.get('profile') or {}).get('name', '')))
        self.settings.setValue('cloud/snapshot',
                               json.dumps(cloud_state.cacheable(snapshot), ensure_ascii=False))
        self.settings.sync()

    # ---------- 生命周期 ----------

    def start(self):
        """离线优先：先把本机缓存铺上去，再去云端看有没有更新。"""
        if not self.enabled():
            return

        try:
            session = self.session.load_session()
        except CredentialError as exc:
            self.status.emit(str(exc))
            return
        if not session or not session.get('token'):
            self.status.emit('云端同步未登录，请重新输入授权码。')
            return

        self.token = session['token']

        cached = self.cached_snapshot()
        if cached:
            self._apply(cached, announce=False)
            self.status.emit(f'{self.profile_name()} · 使用本机缓存，正在联系云端…')

        self.poll_timer.start()
        self.refresh()

    def stop(self):
        self.closing = True
        self.poll_timer.stop()
        self.push_timer.stop()
        for call in list(self.calls):
            call.wait(1500)

    def busy(self):
        return any(call.isRunning() for call in self.calls)

    # ---------- 动作 ----------

    def login(self, auth_code, device_name='', app_version=''):
        code = (auth_code or '').strip()
        if not code:
            self.login_result.emit(False, '请输入授权码。')
            return
        uid = self.client_uid()
        self.status.emit('正在登录云端…')
        self._dispatch('login', lambda: self.client.login(code, uid, device_name, app_version),
                       context=code)

    def logout(self):
        self.poll_timer.stop()
        self.push_timer.stop()
        self.token = ''
        try:
            self.session.clear_session()
        except CredentialError:
            pass
        self.settings.setValue('cloud/enabled', False)
        self.settings.remove('cloud/snapshot')
        self.settings.remove('cloud/version')
        self.settings.remove('cloud/profile_name')
        self.settings.sync()
        self.session_changed.emit(False)
        self.status.emit('已退出云端同步，本机配置保持不变。')

    def refresh(self):
        if not self.token or self.closing:
            return
        self._dispatch('fetch', lambda token=self.token: self.client.fetch(token))

    def check_for_updates(self):
        if not self.token or self.closing or self.busy():
            return
        self._dispatch('ping', lambda token=self.token: self.client.ping(token))

    def schedule_push(self):
        """配置改动后调用。防抖，避免拖拽过程中连发。"""
        if self.token and self.enabled() and not self.closing:
            self.push_timer.start()

    def push_now(self):
        if not self.token or self.closing:
            return
        payload = self.collector()
        if payload is None:
            return
        version, layout, cameras = payload['version'], payload['layout'], payload['cameras']
        self.status.emit('正在上传配置…')
        self._dispatch('push', lambda token=self.token: self.client.push(token, version, layout, cameras))

    # ---------- 调度 ----------

    def _dispatch(self, kind, run, context=''):
        call = CloudCall(kind, run, self)
        call.context = context
        call.done.connect(self._on_done)
        call.failed.connect(self._on_failed)
        call.conflicted.connect(self._on_conflict)
        call.finished.connect(lambda c=call: self._retire(c))
        self.calls.append(call)
        call.start()

    def _retire(self, call):
        if call in self.calls:
            self.calls.remove(call)
        call.deleteLater()

    def _sender_context(self):
        sender = self.sender()
        return getattr(sender, 'context', '')

    def _on_done(self, kind, payload):
        if self.closing:
            return

        if kind == 'login':
            self._finish_login(payload, self._sender_context())
        elif kind == 'fetch':
            self._apply(payload)
            self._remember(payload)
            self.status.emit(f'{self.profile_name()} · 已是最新（版本 {payload.get("version")}）')
        elif kind == 'ping':
            if int(payload.get('version', 0)) > self.version():
                self.status.emit('云端配置有更新，正在拉取…')
                self.refresh()
            else:
                self.status.emit(f'{self.profile_name()} · 已连接')
        elif kind == 'push':
            self._apply(payload, announce=False)
            self._remember(payload)
            self.status.emit(f'{self.profile_name()} · 配置已上传（版本 {payload.get("version")}）')

    def _finish_login(self, payload, auth_code):
        self.token = str(payload.get('token', ''))
        try:
            self.session.save_session(auth_code, self.token)
        except CredentialError as exc:
            self.login_result.emit(False, str(exc))
            return

        self.settings.setValue('cloud/enabled', True)
        self.settings.sync()

        local_count = len(self.names.slot_order()) or 0
        direction = cloud_state.first_sync_direction(payload, local_count)

        self._remember(payload)
        if direction == 'download':
            self._apply(payload)
            message = f'已连接「{self.profile_name()}」，配置来自云端。'
        else:
            self._apply(payload, announce=False)
            message = f'已连接「{self.profile_name()}」，本机配置将上传为该点位的初始配置。'
            self.schedule_push()

        self.poll_timer.start()
        self.session_changed.emit(True)
        self.login_result.emit(True, message)
        self.status.emit(message)

    def _on_conflict(self, kind, snapshot):
        if self.closing:
            return
        # 服务端已经把最新配置一并返回，直接采用，不必再发一次请求
        self._apply(snapshot)
        self._remember(snapshot)
        self.status.emit('配置已在别处更新，已载入最新版本。')

    def _on_failed(self, kind, message, failure_code):
        if self.closing:
            return

        if kind == 'login':
            self.login_result.emit(False, message)
            self.status.emit(message)
            return

        if failure_code == 'INVALID_TOKEN' and not self._relogin_pending:
            self._silent_relogin()
            return

        if isinstance(failure_code, str) and failure_code in ('PROFILE_DISABLED', 'INVALID_AUTH_CODE'):
            self.poll_timer.stop()
            self.token = ''
            self.session_changed.emit(False)
            self.status.emit(message)
            return

        # 网络类失败不改变任何本机状态，墙继续放
        self.status.emit(f'{self.profile_name() or "云端同步"} · {message}')

    def _silent_relogin(self):
        try:
            session = self.session.load_session()
        except CredentialError:
            session = None
        code = (session or {}).get('auth_code', '')
        if not code:
            self.token = ''
            self.session_changed.emit(False)
            self.status.emit('云端登录已失效，请重新输入授权码。')
            return

        self._relogin_pending = code
        self.status.emit('云端登录已过期，正在自动重新登录…')
        uid = self.client_uid()
        self._dispatch('login', lambda: self.client.login(code, uid), context=code)
        self._relogin_pending = ''

    def _apply(self, snapshot, announce=True):
        result = cloud_state.apply_snapshot(snapshot, self.names, self.options, self.store)
        self.applied.emit(result)
        if announce and result.credential_failures:
            self.status.emit('部分摄像头密码未能写入系统安全存储，需要在连接设置中手动输入。')
