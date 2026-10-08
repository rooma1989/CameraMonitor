"""Keeps this computer's monitor wall in step with its cloud profile.

Every network call runs on its own thread: a monitoring screen must never freeze
because the internet hiccupped. Losing the connection is not an error state —
the wall keeps playing from what is already on this computer.
"""
from __future__ import annotations

import json
import uuid

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Signal

from . import __version__, cloud_state
from .cloud import (READ_TIMEOUT, CloudAuthError, CloudClient, CloudConflict,
                    CloudError)
from .credentials import CloudSessionStore, CredentialError

POLL_INTERVAL_MS = 60_000
# 后台明确不让这个设备码再用了：重试没有意义，只能等人换码或后台恢复
PERMANENT_FAILURES = ('PROFILE_DISABLED', 'INVALID_AUTH_CODE', 'PROFILE_IN_USE')
PUSH_DEBOUNCE_MS = 2_000
# 启动时钥匙串读不出会话：先等半分钟再试，之后每次翻倍，最多五分钟一次。
# 托管现场没人值守，缓过来要自己接上云端，不能等人去重启
STORAGE_RETRY_MS = 30_000
STORAGE_RETRY_MAX_MS = 300_000


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
    mode_changed = Signal(str)
    # 带着 failure_code。托管电脑没有侧边栏可以重新登录，界面据此彻底解绑回欢迎页
    revoked = Signal(str)
    # 安全存储一时读不出会话（刚开机钥匙串服务还没起来之类）。和 session_changed(False)
    # 分开：后者意味着登录真的没了，托管电脑会据此解绑回欢迎页；这里只是暂时连不上
    storage_unavailable = Signal(str)
    # 首次登录走上传方向时，本机的开机启动 / 自动全屏被云端打开了：界面据此刷新勾选框、
    # 对齐系统启动项。下发方向不用这个，applied 已经带着
    startup_switches_changed = Signal()

    def __init__(self, names, options, store, collector, parent=None,
                 client=None, settings=None, session_store=None,
                 storage_retry_ms=None, storage_retry_max_ms=None):
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
        # 会话代数。每次退出（含解绑）加一；发出去的请求记下当时的代数，结果回来时
        # 代数已经变了就整个丢掉。光看 token 不够：静默重登的结果本身就带着新令牌，
        # 解绑前发出、解绑后才到，会把这台电脑又绑回去。退出后人亲手发起的登录
        # 带的是新代数，照常生效。
        self._generation = 0
        # 断网时的改动不能悄悄丢掉：记下来，等连上了补传
        self.pending_changes = False
        # 正在把云端配置往本机写。这期间本机存储发出的「变了」全是我们自己写的，
        # 不能当成用户的改动再传回去——那就成了死循环。
        self.applying = False
        # 云端现在那份在本机上传时长什么样。一模一样就不再传——服务端每收一次
        # 都会把版本号加一，多发的每一次都会被人看成「它又在上传了」。
        # 每次传上去、每次落下云端配置都要更新（见 _apply）
        self.last_pushed = ''
        # 正在落云端配置时调了 push_later：本机和云端对不上了，_apply 收尾不能再把
        # last_pushed 记成本机这份
        self._diverged = False

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(POLL_INTERVAL_MS)
        self.poll_timer.timeout.connect(self.check_for_updates)

        self.push_timer = QTimer(self)
        self.push_timer.setSingleShot(True)
        self.push_timer.setInterval(PUSH_DEBOUNCE_MS)
        self.push_timer.timeout.connect(self.push_now)

        self.storage_retry_ms = storage_retry_ms or STORAGE_RETRY_MS
        self.storage_retry_max_ms = storage_retry_max_ms or STORAGE_RETRY_MAX_MS
        self.storage_retry_timer = QTimer(self)
        self.storage_retry_timer.setSingleShot(True)
        self.storage_retry_timer.setInterval(self.storage_retry_ms)
        self.storage_retry_timer.timeout.connect(self._retry_storage)

    # ---------- 本机状态 ----------

    def base_url(self):
        value = self.settings.value('cloud/base_url', '')
        return str(value) if value else None

    def enabled(self):
        return str(self.settings.value('cloud/enabled', False)).lower() in ('true', '1')

    def profile_name(self):
        return str(self.settings.value('cloud/profile_name', '') or '')

    def mode(self):
        value = str(self.settings.value('cloud/mode', 'full') or 'full')
        return value if value in cloud_state.MODES else 'full'

    def _set_mode(self, mode):
        if mode == self.mode():
            return
        self.settings.setValue('cloud/mode', mode)
        self.settings.sync()
        if mode == 'managed':
            # 托管点位不上传：切过来时留着的待补传没有意义，还会让每次心跳都误报「补传」
            self.pending_changes = False
            self.push_timer.stop()
        self.mode_changed.emit(mode)

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
        self.storage_retry_timer.setInterval(self.storage_retry_ms)
        self._resume_session(initial=True)

    def _retry_storage(self):
        """钥匙串缓过来没有。已经退出、关闭，或者人手登录拿到了令牌，就不再试。"""
        if self.closing or self.token or not self.enabled():
            return
        self._resume_session(initial=False)

    def _resume_session(self, initial):
        try:
            session = self.session.load_session()
        except CredentialError as exc:
            if initial:
                # 安全存储可能只是暂时不可用：设置、模式、缓存都不动，照样先把缓存铺上，
                # 画面不能空着。没有令牌就不联网，界面据 storage_unavailable 显示未连接
                cached = self.cached_snapshot()
                if cached:
                    self._apply(cached, announce=False)
                self.storage_unavailable.emit(str(exc))
                self.status.emit(str(exc))
                self.storage_retry_timer.start()
            else:
                # 还是读不出来：缓存早已铺好，不再重复提示，拉长间隔接着等
                self.storage_retry_timer.setInterval(
                    min(self.storage_retry_timer.interval() * 2, self.storage_retry_max_ms))
                self.storage_retry_timer.start()
            return
        self.storage_retry_timer.stop()

        if not session or not session.get('token'):
            # 设置说已启用、钥匙串里却没有会话（条目被删，或配置迁移到了新机器）。
            # 这时必须回到未连接，否则按钮会写着「退出云端」而其实根本没登录。
            self.settings.setValue('cloud/enabled', False)
            self.settings.sync()
            self.session_changed.emit(False)
            self.status.emit('云端同步需要重新登录，请输入授权码。')
            return

        self.token = session['token']

        if initial:
            cached = self.cached_snapshot()
            if cached:
                self._apply(cached, announce=False)
                self.status.emit(f'{self.profile_name()} · 使用本机缓存，正在联系云端…')
        else:
            # 缓存在第一次读失败时已经铺好了。这里要明说「连上了」：界面据此把按钮
            # 改回已连接、接通下行通道——正常启动时这一步由 start_cloud 自己做
            self.status.emit(f'{self.profile_name()} · 已重新读到登录信息，正在联系云端…')
            self.session_changed.emit(True)

        self.poll_timer.start()
        self.refresh()

    def stop(self):
        """等线程真正结束再放手，否则退出时 Qt 会报 thread still running。"""
        self.closing = True
        self.poll_timer.stop()
        self.push_timer.stop()
        self.storage_retry_timer.stop()
        for call in list(self.calls):
            if call.isRunning():
                # 网络调用本身已有超时上界，这里留足它跑完的时间
                call.wait(int(READ_TIMEOUT * 1000) + 2000)
        self.calls.clear()

    def busy(self):
        return any(call.isRunning() for call in self.calls)

    # ---------- 动作 ----------

    def login(self, auth_code, device_name='', app_version=''):
        code = (auth_code or '').strip()
        if not code:
            self.login_result.emit(False, '请输入授权码。')
            return
        uid = self.client_uid()
        # 人亲手登录了，就以这次为准，不再去等钥匙串里那份旧会话
        self.storage_retry_timer.stop()
        self.status.emit('正在登录云端…')
        self._dispatch('login', lambda: self.client.login(code, uid, device_name, app_version),
                       context=code)

    def logout(self):
        self._generation += 1
        # 在路上的静默重登已经作废；不清掉的话，之后的令牌失效再也触发不了重登
        self._relogin_pending = ''
        self.poll_timer.stop()
        self.push_timer.stop()
        self.storage_retry_timer.stop()
        self.token = ''
        self.last_pushed = ''
        try:
            self.session.clear_session()
        except CredentialError:
            pass
        self.settings.setValue('cloud/enabled', False)
        self.settings.remove('cloud/snapshot')
        self.settings.remove('cloud/version')
        self.settings.remove('cloud/profile_name')
        was_managed = self.mode() == 'managed'
        self.settings.remove('cloud/mode')
        self.settings.sync()
        self.session_changed.emit(False)
        self.status.emit('已退出云端同步，本机配置保持不变。')
        if was_managed:
            self.mode_changed.emit('full')

    def refresh(self):
        if not self.token or self.closing:
            return
        self._dispatch('fetch', lambda token=self.token: self.client.fetch(token))

    def check_for_updates(self):
        if not self.token or self.closing or self.busy():
            return
        self._dispatch('ping', lambda token=self.token: self.client.ping(token))

    def note_remote_version(self, version):
        """下行通道说云端有新版本了。比本机新才去拉，旧的、相同的都不理。"""
        try:
            version = int(version)
        except (TypeError, ValueError):
            return
        if version > self.version():
            self.refresh()

    def relogin(self):
        """下行通道说令牌失效了：和 HTTP 那边一样，用钥匙串里的授权码静默重登。"""
        if self.closing or not self.token or self._relogin_pending:
            return
        self._silent_relogin()

    def schedule_push(self):
        """配置改动后调用。防抖，避免拖拽过程中连发。"""
        if not self.enabled() or self.closing or self.applying or self.mode() == 'managed':
            return
        self.pending_changes = True
        if self.token:
            self.push_timer.start()

    def push_later(self):
        """云端刚下发的配置在本机落不下去（比如开机启动写不进系统），下一次心跳把实际状态传上去。

        正在落配置时 schedule_push 会被挡掉，所以只记下来。上次传的那份可能正好和本机
        现在一样，不清掉的话 push_now 会把它当成重复跳过。
        """
        if not self.enabled() or self.mode() == 'managed':
            return
        self.pending_changes = True
        self.last_pushed = ''
        self._diverged = True

    def push_now(self):
        if not self.token or self.closing or self.mode() == 'managed':
            return
        payload = self.collector()
        if payload is None:
            return
        version, layout, cameras = payload['version'], payload['layout'], payload['cameras']

        # 和云端上一份一模一样就别传了。服务端每收一次就把版本号加一，
        # 一旦哪里多发了一次，版本号会自己滚下去，看着就像「一直在上传」。
        fingerprint = cloud_state.config_fingerprint(layout, cameras)
        if fingerprint and fingerprint == self.last_pushed:
            self.pending_changes = False
            return

        self.status.emit('正在上传配置…')
        self._dispatch('push', lambda token=self.token: self.client.push(token, version, layout, cameras),
                       context=fingerprint)

    # ---------- 调度 ----------

    def _dispatch(self, kind, run, context=''):
        call = CloudCall(kind, run, self)
        call.context = context
        call.generation = self._generation
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

    def _stale(self):
        """这个结果是不是上一段会话（退出、解绑之前）发出的请求带回来的。"""
        sender = self.sender()
        return getattr(sender, 'generation', self._generation) < self._generation

    def _on_done(self, kind, payload):
        if self.closing or self._stale():
            return

        if kind == 'login':
            silent = bool(self._relogin_pending)
            self._relogin_pending = ''
            self._finish_login(payload, self._sender_context(), silent=silent)
        elif not self.token:
            # 请求发出去之后已经退出（或被解绑）了：结果不能再落到本机，
            # 否则刚清掉的缓存和模式又被写回来，下次启动又进了云端
            return
        elif kind == 'fetch':
            self._apply(payload)
            self._remember(payload)
            self.status.emit(f'{self.profile_name()} · 已是最新（版本 {payload.get("version")}）')
        elif kind == 'ping':
            if int(payload.get('version', 0)) > self.version():
                self.status.emit('云端配置有更新，正在拉取…')
                self.refresh()
            elif self.pending_changes:
                # 之前断网时改过东西，现在通了，补传
                self.status.emit(f'{self.profile_name()} · 正在补传离线期间的改动…')
                self.push_now()
            else:
                self.status.emit(f'{self.profile_name()} · 已连接')
        elif kind == 'push':
            self.pending_changes = False
            self.last_pushed = self._sender_context()
            self._apply(payload, announce=False)
            self._remember(payload)
            self.status.emit(f'{self.profile_name()} · 配置已上传（版本 {payload.get("version")}）')

    def _finish_login(self, payload, auth_code, silent=False):
        self.token = str(payload.get('token', ''))

        # 登录在服务端已经成功、令牌也拿到了。本机存不住授权码只影响「下次启动
        # 免输入」，不该把整件事判成失败——那样现场会以为没连上，而服务端其实
        # 已经把这台机器绑定了。
        persist_warning = ''
        try:
            self.session.save_session(auth_code, self.token)
        except CredentialError as exc:
            persist_warning = f'{exc} 本次仍可正常同步，重启软件后需要重新输入授权码。'

        self.settings.setValue('cloud/enabled', True)
        self.settings.sync()

        # 用「这次真要上传的内容」来判方向，而不是 slot_order：
        # 添加摄像头并不会立刻写入槽位顺序，用后者会把有画面的机器误判成空的。
        pending = self.collector() or {}
        local_count = len(pending.get('cameras') or [])
        # 托管点位一律以云端为准：本机就算有摄像头，也不能把后台配的那份顶掉
        if cloud_state.profile_mode(payload) == 'managed':
            direction = 'download'
        else:
            direction = cloud_state.first_sync_direction(payload, local_count)

        # 两个方向都要按这次的点位定模式：上次托管没退干净留下的 managed 会挡住
        # 上传方向的首次上传。放在 applying 里，mode_changed 的处理函数不会排上传
        self.applying = True
        try:
            self._set_mode(cloud_state.profile_mode(payload))
        finally:
            self.applying = False

        self._remember(payload)
        # 两个开关各自「开着的一边说了算」，只在人手登录时这样做。静默重登（令牌过期）时
        # 本机一直跟着云端，两边不一样只能是后台刚改过、本机还没拉到，要以云端为准。
        # 托管点位一律以云端为准，也不合并
        merge = not silent and cloud_state.profile_mode(payload) != 'managed'
        keep, adopt = cloud_state.first_login_switches(payload, self.names.settings) if merge else ([], [])
        if direction == 'download':
            if keep:
                # 本机开着的照样落下去（设置和启动项都留着），落完再把这一边传上去
                payload = dict(payload, layout=dict(payload.get('layout') or {}, **{key: True for key in keep}))
            self._apply(payload)
            if keep:
                # 去重基准此时是本机这份，和云端对不上，得先清掉
                self.push_later()
                self.schedule_push()
            message = f'已连接「{self.profile_name()}」，配置来自云端。'
        else:
            # 上传方向：本机这份正是要保留的，绝不能先用云端的空配置把它抹掉。
            # 只记下版本号，等这次上传成功后缓存自然会被结果覆盖。
            if adopt:
                for key in adopt:
                    self.names.settings.setValue(f'monitor/{key}', True)
                self.names.settings.sync()
                self.startup_switches_changed.emit()
            message = f'已连接「{self.profile_name()}」，本机配置将上传为该点位的初始配置。'
            self.schedule_push()

        self.poll_timer.start()
        self.session_changed.emit(True)
        self.login_result.emit(True, message)
        self.status.emit(persist_warning or message)

    def _on_conflict(self, kind, snapshot):
        # 和 _on_done 一样：已经退出、被解绑或被占用清掉了令牌，结果就不能再落到本机
        if self.closing or self._stale() or not self.token:
            return
        # 服务端已经把最新配置一并返回，直接采用，不必再发一次请求。
        # 去重的基准由 _apply 改成这份
        self.pending_changes = False
        self._apply(snapshot)
        self._remember(snapshot)
        self.status.emit('配置已在别处更新，已载入最新版本。')

    def _on_failed(self, kind, message, failure_code):
        # 上一段会话的失败：再去静默重登、再报未连接都只会打扰已经解绑的界面
        if self.closing or self._stale():
            return

        if kind == 'login':
            silent = bool(self._relogin_pending)
            self._relogin_pending = ''
            self.login_result.emit(False, message)
            self.status.emit(message)
            # 只有静默重登才算「被收回」；人手输错码只是这次登录没成功。
            # 放在 login_result 之后：解绑会显示欢迎页，不能再被服务端原话覆盖
            if silent and failure_code in PERMANENT_FAILURES:
                self.revoked.emit(failure_code)
            return

        if failure_code == 'MANAGED_PROFILE':
            # 后台把这里改成了托管：本机的改动作废，去拉最新的那份（里面带着新模式）
            self.pending_changes = False
            self.status.emit(message)
            self.refresh()
            return

        if failure_code == 'INVALID_TOKEN' and not self._relogin_pending:
            self._silent_relogin()
            return

        if failure_code in PERMANENT_FAILURES:
            # 先于 session_changed(False) 发出：界面要先按具体原因解绑，
            # 否则会先按「登录失效」处理掉托管状态，具体原因就丢了
            self.revoked.emit(failure_code)

        if failure_code == 'PROFILE_IN_USE':
            self.poll_timer.stop()
            self.push_timer.stop()
            self.token = ''
            self.session_changed.emit(False)
            self.status.emit(message)
            return

        if isinstance(failure_code, str) and failure_code in ('PROFILE_DISABLED', 'INVALID_AUTH_CODE'):
            self.poll_timer.stop()
            self.token = ''
            self.session_changed.emit(False)
            self.status.emit(message)
            return

        # 网络类失败不改变任何本机状态，墙继续放
        if kind == 'push':
            self.pending_changes = True
            self.status.emit(f'{self.profile_name() or "云端同步"} · {message}改动已保存在本机，联网后自动补传。')
        else:
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
        # 一直占着，直到这次登录结束（成功或失败都在 _on_done/_on_failed 里清掉）。
        # 带上版本号：后台靠它判断这台电脑是不是太旧、用不了傻瓜模式
        self._dispatch('login', lambda: self.client.login(code, uid, app_version=__version__),
                       context=code)

    def _apply(self, snapshot, announce=True):
        # 往本机存储写的时候，DeviceNames 这些会发「变了」的信号，界面那边接着
        # 就去排上传。云端配置是我们自己刚写进去的，不该再传回去。
        self.applying = True
        self._diverged = False
        try:
            # 先切模式再落配置：托管模式要先锁好界面，再把全屏之类的设置铺上去
            # （放在 applying 里面，mode_changed 的处理函数动本机设置也不会排上传）
            self._set_mode(cloud_state.profile_mode(snapshot))
            result = cloud_state.apply_snapshot(snapshot, self.names, self.options, self.store)
            self.applied.emit(result)
        finally:
            self.applying = False
        # 本机现在就是云端那份，把去重的基准换成它：不换的话，现场改回上次传过的样子
        # 会被当成重复跳过，后台永远看不到；换成云端下发的原文也不行，它比本机上传时
        # 多了 fullscreen 之类的键，每收一次下发都会多传一次。所以用 collector 再取一遍
        self.last_pushed = '' if self._diverged else self._local_fingerprint()
        if announce and result.credential_failures:
            self.status.emit('部分摄像头密码未能写入系统安全存储，需要在连接设置中手动输入。')

    def _local_fingerprint(self):
        # 托管点位不上传，用不着基准，也省得去读一遍钥匙串
        if self.mode() == 'managed':
            return ''
        payload = self.collector()
        if payload is None:
            return ''
        return cloud_state.config_fingerprint(payload['layout'], payload['cameras'])
