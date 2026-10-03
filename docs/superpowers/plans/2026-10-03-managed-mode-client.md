# 傻瓜模式 · 客户端 0.9.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 客户端支持云端托管（傻瓜）模式。现场只输一次设备码，之后界面锁死、全屏与否听云端的、执行后台下发的动作，并把运行状态和截图回报给云端。

**Architecture:** 同一个安装包，模式由云端快照里的 `mode` 字段决定。配置仍走现有的 HTTP 同步（`CloudSync`）；新增一条 `QWebSocket` 下行通道（`CloudChannel`），用来接收"配置变了"和一次性动作。`ManagedController` 负责锁界面、执行动作、回报状态；`Window` 只做接线，外加几个"不看界面状态"的程序化入口。

**Tech Stack:** Python 3.12、PySide6 6.8.3（其中自带 QtWebSockets，不新增依赖）、requests、unittest。

**Spec:** `docs/superpowers/specs/2026-10-03-managed-mode-design.md`（第 4 节"客户端"、第 5 节"云端通道协议"）。

**后端依赖：** 本计划的全部测试都用假服务器和假客户端，**不依赖** yunqi 已经上线。要和真实服务器联调，需要 yunqi 计划 A（`2026-10-03-managed-mode-backend.md`）已经部署。

---

## 约定（每个任务都适用）

- Python 解释器：`PY=/Users/rooma/Projects/云栖/CameraMonitor/.venv/bin/python`（主仓库的 venv，worktree 共用这一个）。
- 跑单个测试文件：`QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_xxx.py' -v`
- 跑全量测试：`QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests`。基线：225 个测试全部通过。
- **测试绝不能碰真实配置和真实钥匙串。**
  - QSettings 一律注入临时 ini 文件。原因：在 macOS 上没法用 HOME 隔离 QSettings。
  - 凡是会构造 `Window` 的测试，都要 patch `camera_monitor.credentials.CredentialStore.vault`，换成 `MemoryVault`（写法见 `tests/test_cloud_window.py`）。
- 代码风格跟随所在文件：`app.py` 和 `multiview.py` 是紧凑写法（分号连写、少空格），新模块用常规 PEP 8。注释用中文，写"为什么"。
- 提交信息用中文，格式同仓库现有提交（`feat:` / `fix:` / `test:` / `docs:`），结尾加：
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `camera_monitor/cloud_state.py` | 改 | 解析 `mode` / `fullscreen` / `escape_password_hash` / `default_credentials`；离线缓存保留模式，但剥掉默认密码 |
| `camera_monitor/screen_lock.py` | 改 | `set_record()`：原样采用云端算好的维护密码记录 |
| `camera_monitor/cloud.py` | 改 | `upload_snapshot()`，以 multipart 方式上传 |
| `camera_monitor/cloud_sync.py` | 改 | 记录当前模式、`mode_changed` 信号；managed 下不上传；处理 `MANAGED_PROFILE`；`note_remote_version()`、`relogin()` |
| `camera_monitor/startup.py` | 新 | 启动时决定进哪个界面（欢迎页 / 单机 / 云端），并记录"选过单机" |
| `camera_monitor/welcome.py` | 新 | 欢迎页组件 |
| `camera_monitor/autostart.py` | 新 | 开机启动：Windows 注册表 Run 项、macOS LaunchAgent |
| `camera_monitor/multiview.py` | 改 | `locked`：吞掉鼠标和拖放 |
| `camera_monitor/player_state.py` | 新 | 把播放器的状态文字映射成枚举 |
| `camera_monitor/cloud_channel.py` | 新 | `CloudChannel`：负责握手、心跳、退避重连 |
| `camera_monitor/snapshots.py` | 新 | JPEG 压缩、后台上传队列 |
| `camera_monitor/managed_mode.py` | 新 | `ManagedController`、`EscapeDialog` |
| `camera_monitor/app.py` | 改 | 欢迎页遮罩、`run_scan`、`scan_completed`、托管全屏、接线 |
| `tests/support.py` | 改 | `make_window(fresh_install=False)`；清理时停掉通道 |
| `packaging/*.spec`、`packaging/launcher.py` | 改 | 打包时带上 QtWebSockets，冒烟测试检查 TLS 能用 |
| `camera_monitor/__init__.py`、`README.md`、spec | 改 | 版本号改为 0.9.0、更新说明 |

---

### Task 1: 云端快照里的托管字段

**Files:**
- Modify: `camera_monitor/cloud_state.py`
- Test: `tests/test_managed_state.py`（新建）

- [ ] **Step 1: 写失败的测试**

`tests/test_managed_state.py`：

```python
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest

from PySide6.QtCore import QSettings

from camera_monitor import cloud_state
from camera_monitor.connection_options import ConnectionOptions
from camera_monitor.credentials import CredentialStore
from camera_monitor.device_names import DeviceNames
from test_credentials import MemoryVault


def managed_snapshot(**overrides):
    snap = {
        'version': 3,
        'mode': 'managed',
        'escape_password_hash': 'pbkdf2_sha256$240000$00$11',
        'default_credentials': {'username': 'admin', 'password': 'dflt'},
        'profile': {'id': 1, 'name': '一楼大厅'},
        'layout': {'capacity': 4, 'columns': {}, 'fill_width': False,
                   'organization': '', 'fullscreen': True},
        'cameras': [
            {'ip': '10.0.0.1', 'slot_index': 0, 'username': '', 'password': ''},
            {'ip': '10.0.0.2', 'slot_index': 1, 'username': 'own', 'password': 'pw'},
        ],
    }
    snap.update(overrides)
    return snap


class ManagedSnapshotTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        ini = lambda name: QSettings(os.path.join(folder.name, name), QSettings.Format.IniFormat)
        self.names = DeviceNames(ini('names.ini'))
        self.options = ConnectionOptions(ini('conn.ini'))
        self.store = CredentialStore(MemoryVault())

    def apply(self, snap):
        return cloud_state.apply_snapshot(snap, self.names, self.options, self.store)

    def test_mode_defaults_to_full_and_rejects_unknown_values(self):
        self.assertEqual('full', cloud_state.profile_mode({}))
        self.assertEqual('full', cloud_state.profile_mode({'mode': 'kiosk'}))
        self.assertEqual('managed', cloud_state.profile_mode({'mode': 'managed'}))

    def test_managed_fields_reach_the_applied_config(self):
        applied = self.apply(managed_snapshot())

        self.assertEqual('managed', applied.mode)
        self.assertTrue(applied.fullscreen)
        self.assertEqual('pbkdf2_sha256$240000$00$11', applied.escape_password_hash)

    def test_a_plain_snapshot_is_full_mode_and_windowed(self):
        applied = self.apply({'version': 1, 'layout': {'capacity': 4}, 'cameras': []})

        self.assertEqual('full', applied.mode)
        self.assertFalse(applied.fullscreen)
        self.assertEqual('', applied.escape_password_hash)

    def test_cameras_without_their_own_login_get_the_default_one(self):
        self.apply(managed_snapshot())

        self.assertEqual(('admin', 'dflt'), self.store.load('10.0.0.1'))
        self.assertEqual(('own', 'pw'), self.store.load('10.0.0.2'), '单路另填的优先')
        self.assertEqual(('admin', 'dflt'),
                         self.store.load(cloud_state.DEFAULT_CREDENTIAL_ACCOUNT),
                         '默认账号单独存一份，给还没上墙的摄像头抓图用')

    def test_without_defaults_an_empty_login_still_means_anonymous(self):
        self.store.save('10.0.0.1', 'old', 'old')

        self.apply(managed_snapshot(default_credentials=None))

        self.assertIsNone(self.store.load('10.0.0.1'))

    def test_the_offline_cache_keeps_mode_and_hash_but_no_default_password(self):
        cached = cloud_state.cacheable(managed_snapshot())

        self.assertEqual('managed', cached['mode'])
        self.assertEqual('pbkdf2_sha256$240000$00$11', cached['escape_password_hash'])
        self.assertTrue(cached['layout']['fullscreen'])
        self.assertEqual({'username': 'admin'}, cached['default_credentials'],
                         '缓存是明文 ini，密码只能留在钥匙串')

    def test_replaying_the_cache_leaves_the_vault_alone(self):
        self.apply(managed_snapshot())

        self.apply(cloud_state.cacheable(managed_snapshot()))

        self.assertEqual(('admin', 'dflt'), self.store.load('10.0.0.1'))
        self.assertEqual(('admin', 'dflt'),
                         self.store.load(cloud_state.DEFAULT_CREDENTIAL_ACCOUNT))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_state.py' -v`
Expected: FAIL / ERROR，报 `AttributeError: module 'camera_monitor.cloud_state' has no attribute 'profile_mode'`

- [ ] **Step 3: 实现**

在 `camera_monitor/cloud_state.py` 中：

1. 在 `CAPACITIES = (...)` 这一行下面加：

```python
MODES = ('full', 'managed')
# 钥匙串里专门存「这个点位的默认摄像头账号」的那一条，和摄像头 IP 不会撞名
DEFAULT_CREDENTIAL_ACCOUNT = '__default__'


def profile_mode(snapshot) -> str:
    mode = str((snapshot or {}).get('mode') or 'full')
    return mode if mode in MODES else 'full'


def _default_credentials(snapshot):
    """只有带着 password 键的那份（云端直接下发的）才算数；离线缓存里剥掉了密码，返回 None。"""
    value = (snapshot or {}).get('default_credentials')
    if not isinstance(value, dict) or 'password' not in value:
        return None
    return str(value.get('username') or ''), str(value.get('password') or '')
```

2. 把 `cacheable` 整个替换成：

```python
def cacheable(snapshot) -> dict:
    """离线缓存用的副本：剥掉摄像头密码和默认密码。

    缓存落在 QSettings 的明文 ini / plist 里，密码只能留在系统钥匙串。
    重放这份缓存时 apply_snapshot 不会碰钥匙串，凭据依然可用。
    """
    cameras = []
    for camera in snapshot.get('cameras') or []:
        cameras.append({key: value for key, value in camera.items() if key != 'password'})
    defaults = snapshot.get('default_credentials')
    return {
        'version': snapshot.get('version', 0),
        'mode': profile_mode(snapshot),
        'escape_password_hash': snapshot.get('escape_password_hash') or '',
        'default_credentials': ({'username': str(defaults.get('username') or '')}
                                if isinstance(defaults, dict) else None),
        'profile': dict(snapshot.get('profile') or {}),
        'layout': dict(snapshot.get('layout') or {}),
        'cameras': cameras,
    }
```

3. `AppliedConfig` 末尾增加三个字段：

```python
    mode: str = 'full'
    fullscreen: bool = False
    escape_password_hash: str = ''
```

4. 在 `apply_snapshot` 中，`applied = AppliedConfig(...)` 之后、`for camera in cameras:` 之前插入：

```python
    applied.mode = profile_mode(snapshot)
    applied.fullscreen = bool((snapshot.get('layout') or {}).get('fullscreen', False))
    applied.escape_password_hash = str(snapshot.get('escape_password_hash') or '')

    defaults = _default_credentials(snapshot)
    if defaults is not None:
        try:
            if any(defaults):
                store.save(DEFAULT_CREDENTIAL_ACCOUNT, *defaults)
            else:
                store.forget(DEFAULT_CREDENTIAL_ACCOUNT)
        except CredentialError:
            applied.credential_failures.append(DEFAULT_CREDENTIAL_ACCOUNT)
```

5. 循环里处理 `if 'password' in camera:` 的那一块，替换成：

```python
        # 与服务端相同的三态：没有 password 键就完全不碰钥匙串。
        # 离线缓存正是靠这一点——它剥掉了密码，重放时不会把已存的凭据抹掉。
        if 'password' in camera:
            username = str(camera.get('username') or '')
            password = str(camera.get('password') or '')
            if not (username or password) and defaults and any(defaults):
                # 后台没单独填的摄像头用点位默认账号；写进钥匙串，断网重启也能播
                username, password = defaults
            try:
                if username or password:
                    store.save(ip, username, password)
                else:
                    # 匿名摄像头不在钥匙串里留空记录
                    store.forget(ip)
            except CredentialError:
                applied.credential_failures.append(ip)
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_state.py' -v`
Expected: 7 tests OK

再跑：`QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_cloud*.py'`
Expected: OK（缓存格式变了，但现有测试只检查摄像头密码有没有剥掉）

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/cloud_state.py tests/test_managed_state.py
git commit -m "feat: 云端快照带上托管模式、全屏、维护密码和默认账号

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 维护密码直接采用云端的记录

**Files:**
- Modify: `camera_monitor/screen_lock.py`（在 `ScreenLock` 类里，`verify` 方法之后）
- Test: `tests/test_managed_lock.py`（新建）

下面的测试向量是用固定盐算出来的，yunqi 的 PHP 测试也用同一条，以此保证两边算法一致。

- [ ] **Step 1: 写失败的测试**

```python
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest

from PySide6.QtCore import QSettings

from camera_monitor.screen_lock import ScreenLock

# 盐 = 00..0f，密码 = 246810，迭代 240000。yunqi 的 PHP 测试里有同一条。
VECTOR = ('pbkdf2_sha256$240000$000102030405060708090a0b0c0d0e0f$'
          '6779a48d726cc20519aa5f663a475da180d9ddcf213ec794eef28f7ed9f9bc4e')


class CloudEscapePasswordTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = QSettings(os.path.join(folder.name, 'lock.ini'), QSettings.Format.IniFormat)
        self.lock = ScreenLock(self.settings)

    def test_a_cloud_record_is_adopted_verbatim(self):
        self.assertTrue(self.lock.set_record(VECTOR))

        self.assertTrue(self.lock.verify('246810'))
        self.assertFalse(self.lock.verify('000000'))
        self.assertEqual(VECTOR, self.settings.value(ScreenLock.KEY))

    def test_malformed_records_are_refused_and_the_local_password_survives(self):
        for bad in ('', 'md5$1$aa$bb', 'pbkdf2_sha256$1000$00$11', VECTOR[:-2], None):
            self.assertFalse(self.lock.set_record(bad), bad)
        self.assertTrue(self.lock.verify('000000'))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_lock.py' -v`
Expected: ERROR `AttributeError: 'ScreenLock' object has no attribute 'set_record'`

- [ ] **Step 3: 实现**

在 `ScreenLock.verify` 之后加入：

```python
    @classmethod
    def valid_record(cls, record):
        try:
            algorithm, rounds, salt, digest = str(record).split('$')
            return (algorithm == 'pbkdf2_sha256' and int(rounds) == cls.ITERATIONS
                    and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32)
        except (TypeError, ValueError):
            return False

    def set_record(self, record):
        """采用云端算好的维护密码记录（与 _save 写出的格式完全相同）。

        格式不对就不动本机，宁可沿用旧密码也不能把现场锁死。
        """
        if record is None or not self.valid_record(record):
            return False
        if self.settings.value(self.KEY) != record:
            self.settings.setValue(self.KEY, record)
            self.settings.sync()
        return True
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_*lock*.py' -v`
Expected: 全部 OK（含现有的 `test_screen_lock.py`）

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/screen_lock.py tests/test_managed_lock.py
git commit -m "feat: 维护密码可直接采用云端算好的记录

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 截图上传接口

**Files:**
- Modify: `camera_monitor/cloud.py`
- Test: `tests/test_snapshot_upload.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
import unittest

from camera_monitor.cloud import CloudClient, CloudError
from test_cloud import FakeResponse, FakeSession, fail, ok


class SnapshotUploadTests(unittest.TestCase):
    def client(self, *responses):
        session = FakeSession(*responses)
        return CloudClient('https://example.com/api/camera-monitor', session=session), session

    def test_a_camera_snapshot_is_posted_as_multipart(self):
        client, session = self.client(ok({'captured_at': '2026-10-03 10:00:00'}))

        result = client.upload_snapshot('cm1.t', 'camera', b'\xff\xd8jpeg', ip='10.0.0.1')

        call = session.calls[0]
        self.assertEqual('POST', call['method'])
        self.assertEqual('https://example.com/api/camera-monitor/snapshots', call['url'])
        self.assertEqual('cm1.t', call['headers']['token'])
        self.assertEqual({'kind': 'camera', 'ip': '10.0.0.1'}, call['data'])
        self.assertEqual(('snapshot.jpg', b'\xff\xd8jpeg', 'image/jpeg'), call['files']['image'])
        self.assertNotIn('json', call, 'multipart 请求不能再带 JSON 体')
        self.assertEqual('2026-10-03 10:00:00', result['captured_at'])

    def test_a_screen_capture_has_no_ip(self):
        client, session = self.client(ok({'captured_at': 'x'}))

        client.upload_snapshot('cm1.t', 'screen', b'\xff\xd8')

        self.assertEqual({'kind': 'screen'}, session.calls[0]['data'])

    def test_failures_are_reported_without_the_payload(self):
        client, _ = self.client(fail(422, 'INVALID_SNAPSHOT', '截图格式不正确'))

        with self.assertRaises(CloudError) as error:
            client.upload_snapshot('cm1.t', 'camera', b'secret-bytes', ip='10.0.0.1')

        self.assertNotIn('secret-bytes', str(error.exception))

    def test_json_calls_still_send_json(self):
        client, session = self.client(ok({'version': 1}))

        client.ping('cm1.t')

        self.assertIn('json', session.calls[0])
        self.assertNotIn('files', session.calls[0])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_snapshot_upload.py' -v`
Expected: ERROR `AttributeError: 'CloudClient' object has no attribute 'upload_snapshot'`

- [ ] **Step 3: 实现**

在 `cloud.py` 的 `push` 方法之后加入：

```python
    def upload_snapshot(self, token, kind, jpeg, ip=''):
        """kind 是 camera（某一路）或 screen（整个大屏）。"""
        form = {'kind': kind}
        if ip:
            form['ip'] = ip
        return self._call('POST', '/snapshots', token=token, form=form,
                          files={'image': ('snapshot.jpg', jpeg, 'image/jpeg')})
```

再把 `_call` 的签名和发请求的部分改成：

```python
    def _call(self, method, path, token=None, body=None, form=None, files=None):
        headers = {'Accept': 'application/json'}
        if token:
            headers['token'] = token

        payload = {'data': form, 'files': files} if files else {'json': body}
        try:
            response = self.session.request(
                method, self.base_url + path,
                headers=headers, timeout=self.timeout,
                allow_redirects=False, **payload,
            )
```

`except` 分支和 `return self._unpack(response)` 保持不变。

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_*cloud*.py' -v && QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_snapshot_upload.py' -v`
Expected: 全部 OK

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/cloud.py tests/test_snapshot_upload.py
git commit -m "feat: 云端接口加截图上传

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: CloudSync 记录模式，托管时不上传

**Files:**
- Modify: `camera_monitor/cloud_sync.py`
- Test: `tests/test_managed_sync.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from camera_monitor.cloud import CloudError
from camera_monitor.cloud_sync import CloudSync
from camera_monitor.connection_options import ConnectionOptions
from camera_monitor.credentials import CloudSessionStore, CredentialStore
from camera_monitor.device_names import DeviceNames
from test_cloud_sync import FakeClient, snapshot
from test_credentials import MemoryVault


def managed(version=1, **extra):
    return dict(snapshot(version=version), mode='managed', **extra)


class ManagedSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        ini = lambda name: QSettings(os.path.join(folder.name, name), QSettings.Format.IniFormat)
        self.settings = ini('cloud.ini')
        self.session_store = CloudSessionStore(MemoryVault())
        self.client = FakeClient()
        self.collected = {'version': 1, 'layout': {'capacity': 4},
                          'cameras': [{'ip': '10.0.0.9', 'slot_index': 0}]}
        self.sync = CloudSync(DeviceNames(ini('names.ini')), ConnectionOptions(ini('conn.ini')),
                              CredentialStore(MemoryVault()), collector=lambda: self.collected,
                              client=self.client, settings=self.settings,
                              session_store=self.session_store)
        self.addCleanup(self.sync.stop)
        self.modes = []
        self.sync.mode_changed.connect(self.modes.append)

    def settled(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if not self.sync.busy() and not self.sync.calls:
                return True
            time.sleep(0.01)
        return False

    def kinds(self):
        return [call[0] for call in self.client.calls]

    def test_mode_follows_the_snapshot_and_is_announced_once(self):
        self.sync._apply(managed())
        self.sync._apply(managed())

        self.assertEqual(['managed'], self.modes)
        self.assertEqual('managed', self.sync.mode())

    def test_a_snapshot_without_mode_is_full(self):
        self.sync._apply(managed())
        self.sync._apply(snapshot())

        self.assertEqual(['managed', 'full'], self.modes)

    def test_managed_profiles_never_upload(self):
        self.sync._apply(managed())
        self.settings.setValue('cloud/enabled', True)
        self.sync.token = 'cm1.t'

        self.sync.schedule_push()
        self.sync.push_now()
        self.assertTrue(self.settled())

        self.assertFalse(self.sync.pending_changes)
        self.assertNotIn('push', self.kinds())

    def test_login_to_a_managed_profile_always_downloads(self):
        self.client.login_result = dict(managed(cameras=[]), token='cm1.token')

        self.sync.login('code12345')
        self.assertTrue(self.settled())

        self.assertNotIn('push', self.kinds(), '托管点位以云端为准，本机有摄像头也不上传')
        self.assertEqual('managed', self.sync.mode())

    def test_a_managed_rejection_pulls_the_new_mode_instead_of_retrying(self):
        self.sync.token = 'cm1.t'
        self.client.raises['push'] = CloudError('该点位由后台托管。', 'MANAGED_PROFILE')
        self.client.fetch_result = managed(version=7)

        self.sync.push_now()
        self.assertTrue(self.settled())
        self.assertTrue(self.settled())

        self.assertIn('fetch', self.kinds())
        self.assertEqual('managed', self.sync.mode())
        self.assertFalse(self.sync.pending_changes, '不能留着待补传，否则联网后又去撞墙')

    def test_logout_returns_to_full(self):
        self.sync._apply(managed())

        self.sync.logout()

        self.assertEqual(['managed', 'full'], self.modes)
        self.assertEqual('full', self.sync.mode())

    def test_a_remote_version_triggers_a_fetch_only_when_newer(self):
        self.sync.token = 'cm1.t'
        self.settings.setValue('cloud/version', 3)

        self.sync.note_remote_version(3)
        self.assertTrue(self.settled())
        self.assertNotIn('fetch', self.kinds())

        self.sync.note_remote_version('4')
        self.assertTrue(self.settled())
        self.assertIn('fetch', self.kinds())

    def test_relogin_uses_the_stored_code(self):
        self.session_store.save_session('code-in-vault', 'old-token')

        self.sync.relogin()
        self.assertTrue(self.settled())

        self.assertEqual('code-in-vault', self.client.calls[0][1])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_sync.py' -v`
Expected: ERROR `AttributeError: 'CloudSync' object has no attribute 'mode_changed'`

- [ ] **Step 3: 实现**

修改 `camera_monitor/cloud_sync.py`：

1. 在类的信号声明里加 `mode_changed = Signal(str)`。

2. 在 `profile_name` 方法之后加：

```python
    def mode(self):
        value = str(self.settings.value('cloud/mode', 'full') or 'full')
        return value if value in cloud_state.MODES else 'full'

    def _set_mode(self, mode):
        if mode == self.mode():
            return
        self.settings.setValue('cloud/mode', mode)
        self.settings.sync()
        self.mode_changed.emit(mode)
```

3. `logout` 中，在 `self.settings.remove('cloud/profile_name')` 之后加 `was_managed = self.mode() == 'managed'` 和 `self.settings.remove('cloud/mode')`；在方法末尾加：

```python
        if was_managed:
            self.mode_changed.emit('full')
```

4. `schedule_push` 的第一行条件改成：

```python
        if not self.enabled() or self.closing or self.applying or self.mode() == 'managed':
            return
```

5. `push_now` 的第一行条件改成：

```python
        if not self.token or self.closing or self.mode() == 'managed':
            return
```

6. 在 `check_for_updates` 之后加：

```python
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
        if not self._relogin_pending:
            self._silent_relogin()
```

7. `_finish_login` 中，把 `direction = cloud_state.first_sync_direction(payload, local_count)` 改成：

```python
        # 托管点位一律以云端为准：本机就算有摄像头，也不能把后台配的那份顶掉
        if cloud_state.profile_mode(payload) == 'managed':
            direction = 'download'
        else:
            direction = cloud_state.first_sync_direction(payload, local_count)
```

8. `_on_failed` 中，在 `if failure_code == 'INVALID_TOKEN' ...` 之前加：

```python
        if failure_code == 'MANAGED_PROFILE':
            # 后台把这里改成了托管：本机的改动作废，去拉最新的那份（里面带着新模式）
            self.pending_changes = False
            self.status.emit(message)
            self.refresh()
            return
```

9. `_apply` 中，在 `self.applying = True` 之前加：

```python
        # 先切模式再落配置：托管模式要先锁好界面，再把全屏之类的设置铺上去
        self._set_mode(cloud_state.profile_mode(snapshot))
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_*sync*.py' -v`
Expected: 全部 OK

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/cloud_sync.py tests/test_managed_sync.py
git commit -m "feat: 云端同步记住托管模式，托管时不再上传本机配置

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 启动分流

**Files:**
- Create: `camera_monitor/startup.py`
- Test: `tests/test_startup.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
import os
import tempfile
import unittest

from PySide6.QtCore import QSettings

from camera_monitor import startup


class StartupRouteTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cloud = QSettings(os.path.join(folder.name, 'cloud.ini'), QSettings.Format.IniFormat)
        self.names = QSettings(os.path.join(folder.name, 'names.ini'), QSettings.Format.IniFormat)

    def route(self):
        return startup.startup_route(self.cloud, self.names)

    def test_a_fresh_computer_sees_the_welcome_page(self):
        self.assertEqual(startup.WELCOME, self.route())

    def test_the_default_screen_password_alone_is_not_a_previous_install(self):
        # ScreenLock 一构造就会写入初始密码，不能因此把新电脑当成老用户
        self.names.setValue('fullscreen/password', 'pbkdf2_sha256$...')

        self.assertEqual(startup.WELCOME, self.route())

    def test_a_logged_in_computer_goes_straight_to_the_cloud(self):
        self.cloud.setValue('cloud/enabled', True)

        self.assertEqual(startup.CLOUD, self.route())

    def test_choosing_standalone_is_remembered(self):
        startup.choose_standalone(self.cloud)

        self.assertEqual(startup.STANDALONE, self.route())

        startup.clear_standalone(self.cloud)
        self.assertEqual(startup.WELCOME, self.route())

    def test_an_upgraded_computer_skips_the_welcome_page_for_good(self):
        self.names.setValue('monitor/capacity', 9)

        self.assertEqual(startup.STANDALONE, self.route())
        self.assertTrue(self.cloud.value(startup.STANDALONE_KEY), '要记下来，下次不再判断')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_startup.py' -v`
Expected: ERROR `ModuleNotFoundError: No module named 'camera_monitor.startup'`

- [ ] **Step 3: 实现**

`camera_monitor/startup.py`：

```python
"""打开软件后先去哪：欢迎页、完整界面，还是直接按云端登录态走。"""

WELCOME, STANDALONE, CLOUD = 'welcome', 'standalone', 'cloud'
STANDALONE_KEY = 'cloud/standalone'
# ScreenLock 构造时就会写入初始密码，它不能算作「以前用过」
_IGNORED_KEYS = ('fullscreen/password',)


def _truthy(value):
    return str(value).lower() in ('true', '1')


def startup_route(cloud_settings, names_settings):
    if _truthy(cloud_settings.value('cloud/enabled', False)):
        return CLOUD
    if _truthy(cloud_settings.value(STANDALONE_KEY, False)):
        return STANDALONE
    # 老用户升级上来：以前存过名称、顺序、分屏，就当他是单机用户，不出欢迎页
    if any(key not in _IGNORED_KEYS for key in names_settings.allKeys()):
        choose_standalone(cloud_settings)
        return STANDALONE
    return WELCOME


def choose_standalone(cloud_settings):
    cloud_settings.setValue(STANDALONE_KEY, True)
    cloud_settings.sync()


def clear_standalone(cloud_settings):
    cloud_settings.remove(STANDALONE_KEY)
    cloud_settings.sync()
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_startup.py' -v`
Expected: 5 tests OK

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/startup.py tests/test_startup.py
git commit -m "feat: 启动分流——新电脑出欢迎页，老用户和已登录的直接进

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 欢迎页组件

**Files:**
- Create: `camera_monitor/welcome.py`
- Test: `tests/test_welcome.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from PySide6.QtWidgets import QApplication

from camera_monitor.welcome import WelcomePage


class WelcomePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.page = WelcomePage()
        self.addCleanup(self.page.deleteLater)
        self.codes, self.alone = [], []
        self.page.login_requested.connect(self.codes.append)
        self.page.standalone_chosen.connect(lambda: self.alone.append(1))

    def test_the_code_is_trimmed_and_sent(self):
        self.page.code.setText('  7k2m-9qxt-4b8n ')
        self.page.start.click()

        self.assertEqual(['7k2m-9qxt-4b8n'], self.codes)

    def test_an_empty_code_is_explained_not_sent(self):
        self.page.start.click()

        self.assertEqual([], self.codes)
        self.assertFalse(self.page.error.isHidden())
        self.assertIn('设备码', self.page.error.text())

    def test_busy_blocks_double_submission(self):
        self.page.code.setText('abc')
        self.page.set_busy(True)
        self.page.submit()

        self.assertEqual([], self.codes)
        self.assertFalse(self.page.start.isEnabled())

        self.page.set_busy(False)
        self.assertTrue(self.page.start.isEnabled())

    def test_standalone_is_one_click(self):
        self.page.alone.click()

        self.assertEqual([1], self.alone)

    def test_an_error_can_be_shown_and_cleared(self):
        self.page.show_error('授权码不正确')
        self.assertEqual('授权码不正确', self.page.error.text())

        self.page.show_error('')
        self.assertTrue(self.page.error.isHidden())


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_welcome.py' -v`
Expected: ERROR `ModuleNotFoundError: No module named 'camera_monitor.welcome'`

- [ ] **Step 3: 实现**

`camera_monitor/welcome.py`：

```python
"""第一次打开时的欢迎页：输一个设备码就行，剩下的交给后台。"""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

CENTER = Qt.AlignmentFlag.AlignCenter


class WelcomePage(QWidget):
    login_requested = Signal(str)
    standalone_chosen = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('welcomePage')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet('''
            QWidget#welcomePage { background:#f2f5fb; }
            QFrame#welcomeCard { background:white; border-radius:16px; }
            QFrame#welcomeCard QLabel { background:transparent; }
            QLabel#welcomeTitle { font-size:26px; font-weight:700; color:#111827; }
            QLabel#welcomeHint { color:#6b7280; font-size:14px; }
            QLabel#welcomeError { color:#c0392b; font-size:13px; }
            QLineEdit#welcomeCode { font-size:24px; padding:12px; border:2px solid #2463eb;
                                    border-radius:10px; background:white; }
            QPushButton#welcomeStart { background:#2463eb; color:white; font-size:18px;
                                       font-weight:600; padding:12px; border-radius:10px; border:none; }
            QPushButton#welcomeStart:disabled { background:#9db7f5; }
            QPushButton#welcomeAlone { background:transparent; border:none; color:#2463eb; font-size:13px; }
        ''')

        card = QFrame()
        card.setObjectName('welcomeCard')
        card.setFixedWidth(520)
        box = QVBoxLayout(card)
        box.setContentsMargins(48, 40, 48, 32)
        box.setSpacing(14)

        icon = QLabel()
        icon.setAlignment(CENTER)
        pixmap = QPixmap(str(Path(__file__).parent / 'assets' / 'app-icon.png'))
        if not pixmap.isNull():
            icon.setPixmap(pixmap.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        box.addWidget(icon)

        title = QLabel('内网监控中心')
        title.setObjectName('welcomeTitle')
        title.setAlignment(CENTER)
        box.addWidget(title)

        hint = QLabel('请输入管理员给您的设备码\n输入后无需任何设置，画面会自动出现')
        hint.setObjectName('welcomeHint')
        hint.setAlignment(CENTER)
        box.addWidget(hint)

        self.code = QLineEdit()
        self.code.setObjectName('welcomeCode')
        self.code.setAlignment(CENTER)
        self.code.setMaxLength(64)
        self.code.setPlaceholderText('例如 7K2M-9QXT-4B8N')
        self.code.returnPressed.connect(self.submit)
        box.addWidget(self.code)

        self.start = QPushButton('开始使用')
        self.start.setObjectName('welcomeStart')
        self.start.clicked.connect(self.submit)
        box.addWidget(self.start)

        self.error = QLabel('')
        self.error.setObjectName('welcomeError')
        self.error.setAlignment(CENTER)
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.hide()
        box.addWidget(self.error)

        tip = QLabel('不区分大小写，中间的横线可以不输')
        tip.setObjectName('welcomeHint')
        tip.setAlignment(CENTER)
        box.addWidget(tip)

        self.alone = QPushButton('没有设备码？不用云端，单机使用 →')
        self.alone.setObjectName('welcomeAlone')
        self.alone.clicked.connect(self.standalone_chosen)
        box.addWidget(self.alone)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

    def submit(self):
        if not self.start.isEnabled():
            return
        code = self.code.text().strip()
        if not code:
            self.show_error('请输入设备码。')
            return
        self.show_error('')
        self.login_requested.emit(code)

    def set_busy(self, busy):
        for widget in (self.start, self.code, self.alone):
            widget.setEnabled(not busy)
        self.start.setText('正在连接…' if busy else '开始使用')

    def show_error(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_welcome.py' -v`
Expected: 5 tests OK

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/welcome.py tests/test_welcome.py
git commit -m "feat: 欢迎页——一个设备码输入框，外加单机使用的出口

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 主窗口挂上欢迎页

**Files:**
- Modify: `camera_monitor/app.py`、`tests/support.py`
- Test: `tests/test_welcome_window.py`（新建）

- [ ] **Step 1: 改测试工具，让现有测试保持"老用户"身份**

在 `tests/support.py` 中，把 `make_window` 的签名和前几行改成：

```python
def make_window(case, fresh_install=False, **kwargs):
    """Build a Window whose settings are isolated and whose timers stop on cleanup.

    fresh_install=False 时模拟老用户（选过单机），否则所有窗口都会盖着一层欢迎页。
    """
    from camera_monitor.app import Window
    from camera_monitor.connection_options import ConnectionOptions
    from camera_monitor.device_names import DeviceNames

    ini = isolated_settings(case)
    kwargs.setdefault('device_names', DeviceNames(ini('names.ini')))
    cloud = kwargs.setdefault('cloud_settings', ini('cloud.ini'))
    if not fresh_install:
        cloud.setValue('cloud/standalone', True)
    kwargs.setdefault('connection_options', ConnectionOptions(ini('conn.ini')))
```

后面的代码不变。

- [ ] **Step 2: 写失败的测试**

`tests/test_welcome_window.py`：

```python
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from support import make_window
from test_credentials import MemoryVault


class WelcomeWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_fresh_install_opens_on_the_welcome_page(self):
        window = make_window(self, fresh_install=True)

        self.assertFalse(window.welcome.isHidden())

    def test_a_previous_user_never_sees_it(self):
        window = make_window(self)

        self.assertTrue(window.welcome.isHidden())

    def test_the_code_goes_to_cloud_login(self):
        window = make_window(self, fresh_install=True)
        calls = []
        window.cloud.login = lambda code, **kwargs: calls.append(code)

        window.welcome.code.setText('7K2M9QXT4B8N')
        window.welcome.start.click()

        self.assertEqual(['7K2M9QXT4B8N'], calls)
        self.assertFalse(window.welcome.start.isEnabled(), '等结果期间不能重复提交')

    def test_a_failed_login_stays_on_the_page_with_the_reason(self):
        window = make_window(self, fresh_install=True)
        window.welcome.set_busy(True)

        window.cloud_login_result(False, '授权码不正确，请核对后重试。')

        self.assertFalse(window.welcome.isHidden())
        self.assertTrue(window.welcome.start.isEnabled())
        self.assertEqual('授权码不正确，请核对后重试。', window.welcome.error.text())

    def test_a_successful_login_reveals_the_app(self):
        window = make_window(self, fresh_install=True)

        window.cloud_login_result(True, '已连接')

        self.assertTrue(window.welcome.isHidden())

    def test_standalone_is_remembered(self):
        window = make_window(self, fresh_install=True)

        window.welcome.alone.click()

        self.assertTrue(window.welcome.isHidden())
        self.assertEqual('true', str(window.cloud.settings.value('cloud/standalone')).lower())


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 3: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_welcome_window.py' -v`
Expected: ERROR `AttributeError: 'Window' object has no attribute 'welcome'`

- [ ] **Step 4: 实现**

修改 `camera_monitor/app.py`：

1. 导入部分：`from PySide6.QtCore import QThread, Signal, Qt, QTimer, QEvent` 改成 `from PySide6.QtCore import QThread, Signal, Qt, QTimer, QEvent, QSettings`；再在 `from .cloud_sync import CloudSync` 下面加：

```python
from . import startup
from .welcome import WelcomePage
```

2. `Window.__init__` 中，紧接 `self.device_names.changed.connect(self.refresh_device_name)` 之后插入：

```python
        cloud_settings=cloud_settings if cloud_settings is not None else QSettings('CameraMonitor','Cloud')
        # 必须赶在 ScreenLock 之前判断：它一构造就往 DeviceNames 的设置里写初始密码
        self.startup_route=startup.startup_route(cloud_settings,self.device_names.settings)
```

3. `Window.__init__` 中，在 `self.cloud_start_timer=QTimer(self)` 这一行之前插入：

```python
        self.welcome=WelcomePage(root)
        self.welcome.login_requested.connect(self.welcome_login)
        self.welcome.standalone_chosen.connect(self.use_standalone)
        self.welcome.setVisible(self.startup_route==startup.WELCOME)
        self.position_overlay()
```

4. `position_overlay` 末尾加：

```python
        if hasattr(self,'welcome'):
            self.welcome.setGeometry(root.rect())
            if not self.welcome.isHidden():self.welcome.raise_()
```

5. `cloud_login_result` 替换成：

```python
    def cloud_login_result(self,ok,message):
        self.cloud_panel.set_status(message,error=not ok)
        self.cloud_panel.set_connected(self.cloud.enabled(),self.cloud.profile_name())
        if not self.welcome.isHidden():
            self.welcome.set_busy(False)
            if ok:self.welcome.hide()
            else:self.welcome.show_error(message)
```

6. 在 `cloud_logout` 之后加：

```python
    def welcome_login(self,code):
        self.welcome.set_busy(True)
        self.cloud_login(code)

    def use_standalone(self):
        startup.choose_standalone(self.cloud.settings)
        self.welcome.hide()
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_welcome_window.py' -v`
Expected: 6 tests OK

再跑全量：`QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests`
Expected: OK（225 个原有测试加上 Task 1–7 新增的，全部通过）

- [ ] **Step 6: 提交**

```bash
git add camera_monitor/app.py tests/support.py tests/test_welcome_window.py
git commit -m "feat: 新电脑打开先看到欢迎页，输入设备码或选单机后进入

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 开机启动

**Files:**
- Create: `camera_monitor/autostart.py`
- Test: `tests/test_autostart.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
import plistlib
import tempfile
import unittest
from pathlib import Path

from camera_monitor.autostart import APP_NAME, FLAG, MAC_LABEL, AutoStart


class FakeRegistry:
    """只实现 autostart 用到的那几个 winreg 接口。"""
    HKEY_CURRENT_USER = 'HKCU'
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, path, reserved, access):
        return self._Key()

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ


class WindowsAutoStartTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.auto = AutoStart(platform='win32', executable=r'C:\CM\CameraMonitor.exe',
                              frozen=True, registry=self.registry)

    def test_enable_writes_the_run_key_with_the_flag(self):
        self.assertTrue(self.auto.enable())

        self.assertEqual(rf'"C:\CM\CameraMonitor.exe" {FLAG}', self.registry.values[APP_NAME])
        self.assertTrue(self.auto.is_enabled())

    def test_disable_is_idempotent(self):
        self.auto.enable()

        self.assertTrue(self.auto.disable())
        self.assertTrue(self.auto.disable())
        self.assertFalse(self.auto.is_enabled())

    def test_a_moved_program_counts_as_not_enabled(self):
        self.registry.values[APP_NAME] = r'"D:\old\CameraMonitor.exe" --autostart'

        self.assertFalse(self.auto.is_enabled(), '程序挪了位置要重新写')


class MacAutoStartTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.agents = Path(folder.name) / 'LaunchAgents'
        self.auto = AutoStart(platform='darwin',
                              executable='/Applications/Camera Monitor.app/Contents/MacOS/Camera Monitor',
                              frozen=True, launch_agents=self.agents)

    def test_enable_writes_a_run_at_load_agent(self):
        self.assertTrue(self.auto.enable())

        with open(self.agents / f'{MAC_LABEL}.plist', 'rb') as fh:
            plist = plistlib.load(fh)
        self.assertEqual(MAC_LABEL, plist['Label'])
        self.assertTrue(plist['RunAtLoad'])
        self.assertEqual(['/Applications/Camera Monitor.app/Contents/MacOS/Camera Monitor', FLAG],
                         plist['ProgramArguments'])
        self.assertTrue(self.auto.is_enabled())

    def test_disable_removes_it_and_tolerates_absence(self):
        self.auto.enable()

        self.assertTrue(self.auto.disable())
        self.assertTrue(self.auto.disable())
        self.assertFalse(self.auto.is_enabled())


class SourceRunTests(unittest.TestCase):
    def test_running_from_source_never_touches_the_system(self):
        registry = FakeRegistry()
        auto = AutoStart(platform='win32', executable='python.exe', frozen=False, registry=registry)

        self.assertFalse(auto.enable())
        self.assertFalse(auto.disable())
        self.assertEqual({}, registry.values)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_autostart.py' -v`
Expected: ERROR `ModuleNotFoundError: No module named 'camera_monitor.autostart'`

- [ ] **Step 3: 实现**

`camera_monitor/autostart.py`：

```python
"""开机自动运行。

只在打包后的程序里真正改系统：源码运行（开发、测试）时什么都不做，免得把开发
机的 python 写进启动项，或者删掉已安装那份程序的启动项。
"""
import plistlib
import sys
from pathlib import Path

APP_NAME = 'CameraMonitor'
MAC_LABEL = 'com.cameramonitor.desktop'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
FLAG = '--autostart'


class AutoStart:
    def __init__(self, platform=None, executable=None, frozen=None,
                 launch_agents=None, registry=None):
        self.platform = platform or sys.platform
        self.executable = executable or sys.executable
        self.frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
        self.launch_agents = Path(launch_agents) if launch_agents else Path.home() / 'Library' / 'LaunchAgents'
        self.registry = registry

    @property
    def plist_path(self):
        return self.launch_agents / f'{MAC_LABEL}.plist'

    def command(self):
        return f'"{self.executable}" {FLAG}'

    def _winreg(self):
        if self.registry is None:
            import winreg
            self.registry = winreg
        return self.registry

    def enable(self):
        if not self.frozen:
            return False
        try:
            if self.platform == 'win32':
                reg = self._winreg()
                with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                    reg.SetValueEx(key, APP_NAME, 0, reg.REG_SZ, self.command())
                return True
            if self.platform == 'darwin':
                self.launch_agents.mkdir(parents=True, exist_ok=True)
                with open(self.plist_path, 'wb') as fh:
                    plistlib.dump({'Label': MAC_LABEL,
                                   'ProgramArguments': [self.executable, FLAG],
                                   'RunAtLoad': True}, fh)
                return True
        except OSError:
            pass
        return False

    def disable(self):
        if not self.frozen:
            return False
        try:
            if self.platform == 'win32':
                reg = self._winreg()
                with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                    reg.DeleteValue(key, APP_NAME)
            elif self.platform == 'darwin':
                self.plist_path.unlink(missing_ok=True)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def is_enabled(self):
        try:
            if self.platform == 'win32':
                reg = self._winreg()
                with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
                    value, _ = reg.QueryValueEx(key, APP_NAME)
                return value == self.command()
            if self.platform == 'darwin':
                return self.plist_path.exists()
        except OSError:
            pass
        return False
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_autostart.py' -v`
Expected: 8 tests OK

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/autostart.py tests/test_autostart.py
git commit -m "feat: 开机自动运行（Windows 注册表 / macOS LaunchAgent）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: 监控墙可锁定

**Files:**
- Modify: `camera_monitor/multiview.py`
- Test: `tests/test_managed_wall.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from camera_monitor.discovery import Device
from support import make_window
from test_credentials import MemoryVault


def mouse(kind, button=Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, QPointF(5, 5), QPointF(5, 5), button, button,
                       Qt.KeyboardModifier.NoModifier)


class LockedWallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = make_window(self)
        self.window.thumbnails.request = lambda *args, **kwargs: None
        self.wall = self.window.wall
        self.wall.add_device(Device('10.0.0.1'))
        self.tile = self.wall.tiles[0]

    def test_a_click_on_an_unlocked_wall_enlarges_the_tile(self):
        surface = self.tile.player.surface
        QApplication.sendEvent(surface, mouse(QEvent.Type.MouseButtonPress))
        QApplication.sendEvent(surface, mouse(QEvent.Type.MouseButtonRelease))

        self.assertIs(self.tile, self.wall.focused_tile)

    def test_a_locked_wall_swallows_clicks_and_double_clicks(self):
        self.wall.set_locked(True)
        surface = self.tile.player.surface

        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                     QEvent.Type.MouseButtonDblClick):
            QApplication.sendEvent(surface, mouse(kind))

        self.assertIsNone(self.wall.focused_tile)

    def test_locking_drops_an_enlarged_tile(self):
        self.wall.toggle_focus(self.tile)

        self.wall.set_locked(True)

        self.assertIsNone(self.wall.focused_tile)



if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_wall.py' -v`
Expected: ERROR `AttributeError: 'MultiView' object has no attribute 'set_locked'`

- [ ] **Step 3: 实现**

修改 `camera_monitor/multiview.py`：

1. 文件顶部 import 之后加一个常量：

```python
# 傻瓜模式下画面上的这些鼠标动作一律吞掉：现场不能放大、拖动、点开菜单
LOCKED_EVENTS=(QEvent.Type.MouseButtonPress,QEvent.Type.MouseButtonRelease,QEvent.Type.MouseButtonDblClick,
    QEvent.Type.MouseMove,QEvent.Type.ContextMenu,QEvent.Type.Wheel)
```

2. `CameraTile.eventFilter` 方法体的第一行插入：

```python
        if self.monitor.locked and watched is self.player.surface and event.type() in LOCKED_EVENTS:
            return True
```

3. `CameraTile.dragEnterEvent`、`EmptySlot.dragEnterEvent` 的第一行都插入：

```python
        if self.monitor.locked:event.ignore();return
```

4. `CameraTile.enterEvent` 中，把 `if not self.monitor.presentation:` 改成 `if not self.monitor.presentation and not self.monitor.locked:`。

5. `MultiView.__init__` 中，紧接 `self.presentation=False` 加 `self.locked=False`。

6. 在 `MultiView.set_presentation` 之后加：

```python
    def set_locked(self,locked):
        """傻瓜模式：现场只能看，任何点击、拖动都不起作用。"""
        self.locked=bool(locked)
        if self.locked:
            self.focused_tile=None
            for tile in self.tiles:tile.controls.hide()
        self.relayout()
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_wall.py' -v`
Expected: 3 tests OK

再跑：`QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_*.py'`
Expected: 全部 OK

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/multiview.py tests/test_managed_wall.py
git commit -m "feat: 傻瓜模式下监控墙锁定，现场点击、拖动都不起作用

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: 把播放器的状态文字归成几类

**Files:**
- Create: `camera_monitor/player_state.py`
- Test: `tests/test_player_state.py`（新建）

下面用例里的文字都摘自 `playback.py` 现有的 `set_status` 调用和 `Decoder` 的报错，不要改动。

- [ ] **Step 1: 写失败的测试**

```python
import unittest

from camera_monitor.player_state import (AUTH_FAILED, CONNECTING, PLAYING, STOPPED,
                                         UNREACHABLE, player_state)


class PlayerStateTests(unittest.TestCase):
    def test_the_texts_playback_really_shows(self):
        cases = {
            '正在播放 · 1280 × 720 · H264': PLAYING,
            '画面正在播放 · 密码未保存，请查看连接设置': PLAYING,
            '视频认证失败，请检查摄像头用户名和密码。': AUTH_FAILED,
            '正在获取可播放通道…': CONNECTING,
            '正在连接视频流，等待首帧…': CONNECTING,
            '视频中断，3 秒后自动重连 · 第 1 次（可点击停止）': CONNECTING,
            '已停止': STOPPED,
            '正在停止，等待网络连接结束…': STOPPED,
            '待连接': STOPPED,
            '': STOPPED,
            '视频连接失败或超时，请检查账号密码、RTSP 服务、网络和所选通道。': UNREACHABLE,
            '获取通道失败，请检查摄像头 ONVIF 设置。': UNREACHABLE,
            '视频地址无效，请检查地址和端口。': UNREACHABLE,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, player_state(text))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_player_state.py' -v`
Expected: ERROR `ModuleNotFoundError`

- [ ] **Step 3: 实现**

`camera_monitor/player_state.py`：

```python
"""把播放器给人看的状态文字，归成云端能统计的几类。"""

PLAYING = 'playing'
CONNECTING = 'connecting'
AUTH_FAILED = 'auth_failed'
UNREACHABLE = 'unreachable'
STOPPED = 'stopped'


def player_state(text):
    text = text or ''
    if text.startswith('正在播放') or text.startswith('画面正在播放'):
        return PLAYING
    if '认证失败' in text:
        return AUTH_FAILED
    if text in ('', '待连接') or text.startswith('已停止') or text.startswith('正在停止'):
        return STOPPED
    if text.startswith('正在') or '重连' in text:
        return CONNECTING
    return UNREACHABLE
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_player_state.py' -v`
Expected: OK

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/player_state.py tests/test_player_state.py
git commit -m "feat: 播放状态归类，供回报云端

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: 云端下行通道

**Files:**
- Create: `camera_monitor/cloud_channel.py`
- Test: `tests/test_cloud_channel.py`（新建）

测试里用 Qt 自带的 `QWebSocketServer` 在本机起一个假服务器，不需要新增依赖。

- [ ] **Step 1: 写失败的测试**

```python
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import unittest

from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer
from PySide6.QtWidgets import QApplication

from camera_monitor.cloud_channel import CloudChannel, channel_url


def pump(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    QApplication.processEvents()
    return predicate()


def idle(seconds):
    pump(lambda: False, timeout=seconds)


class FakeServer:
    def __init__(self):
        self.server = QWebSocketServer('test', QWebSocketServer.SslMode.NonSecureMode)
        assert self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0)
        self.server.newConnection.connect(self._accept)
        self.clients = []
        self.received = []
        self.reply_to_hello = {'type': 'welcome', 'version': 5}
        self.answer_pings = True

    def url(self):
        return f'ws://127.0.0.1:{self.server.serverPort()}/api/camera-monitor/ws'

    def _accept(self):
        socket = self.server.nextPendingConnection()
        self.clients.append(socket)
        socket.textMessageReceived.connect(lambda text, s=socket: self._on_text(s, text))

    def _on_text(self, socket, text):
        message = json.loads(text)
        self.received.append(message)
        if message['type'] == 'hello' and self.reply_to_hello:
            socket.sendTextMessage(json.dumps(self.reply_to_hello))
        elif message['type'] == 'ping' and self.answer_pings:
            socket.sendTextMessage('{"type":"pong"}')

    def send(self, payload):
        self.clients[-1].sendTextMessage(json.dumps(payload))

    def close(self):
        for socket in self.clients:
            socket.abort()
        self.server.close()


class ChannelUrlTests(unittest.TestCase):
    def test_https_becomes_wss_under_the_same_path(self):
        self.assertEqual('wss://tj.example.cn/api/camera-monitor/ws',
                         channel_url('https://tj.example.cn/api/camera-monitor'))
        self.assertEqual('ws://127.0.0.1:8000/api/camera-monitor/ws',
                         channel_url('http://127.0.0.1:8000/api/camera-monitor/'))


class CloudChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.server = FakeServer()
        self.addCleanup(self.server.close)
        self.hello = {'token': 'cm1.t', 'client_uid': 'uid12345', 'app_version': '0.9.0'}
        self.make()

    def make(self, **kwargs):
        kwargs.setdefault('backoff', (0.05, 0.05))
        kwargs.setdefault('jitter', lambda: 1.0)
        self.channel = CloudChannel(self.server.url(), lambda: self.hello, **kwargs)
        self.addCleanup(self.channel.stop)
        self.messages, self.denials, self.replaced = [], [], []
        self.channel.message.connect(self.messages.append)
        self.channel.denied.connect(self.denials.append)
        self.channel.replaced.connect(lambda: self.replaced.append(1))

    def test_hello_then_welcome_brings_it_online(self):
        self.channel.start()

        self.assertTrue(pump(lambda: self.channel.online))
        self.assertEqual(dict(self.hello, type='hello'), self.server.received[0])
        self.assertEqual('welcome', self.messages[0]['type'])

    def test_server_messages_are_passed_up(self):
        self.channel.start()
        pump(lambda: self.channel.online)

        self.server.send({'type': 'command', 'id': 'c1', 'name': 'scan', 'args': {}})

        self.assertTrue(pump(lambda: len(self.messages) == 2))
        self.assertEqual('c1', self.messages[1]['id'])

    def test_send_only_works_while_online(self):
        self.assertFalse(self.channel.send({'type': 'status'}))

        self.channel.start()
        pump(lambda: self.channel.online)
        self.assertTrue(self.channel.send({'type': 'status'}))
        self.assertTrue(pump(lambda: self.server.received[-1]['type'] == 'status'))

    def test_a_dropped_connection_is_retried(self):
        self.channel.start()
        pump(lambda: self.channel.online)

        self.server.clients[-1].close()

        self.assertTrue(pump(lambda: len(self.server.clients) == 2 and self.channel.online))

    def test_a_permanent_denial_stops_retrying(self):
        self.server.reply_to_hello = {'type': 'denied', 'failure_code': 'PROFILE_DISABLED'}
        self.channel.start()

        self.assertTrue(pump(lambda: self.denials == ['PROFILE_DISABLED']))
        idle(0.3)
        self.assertEqual(1, len(self.server.clients))
        self.assertFalse(self.channel.online)

    def test_an_expired_token_is_reported_and_retried(self):
        self.server.reply_to_hello = {'type': 'denied', 'failure_code': 'INVALID_TOKEN'}
        self.channel.start()

        self.assertTrue(pump(lambda: len(self.server.clients) >= 2))
        self.assertIn('INVALID_TOKEN', self.denials)

    def test_being_replaced_stops_for_good(self):
        self.channel.start()
        pump(lambda: self.channel.online)

        self.server.send({'type': 'replaced'})

        self.assertTrue(pump(lambda: self.replaced == [1]))
        idle(0.3)
        self.assertEqual(1, len(self.server.clients))

    def test_no_session_means_no_hello(self):
        self.hello = None
        self.channel.start()

        idle(0.3)
        self.assertEqual([], self.server.received)
        self.assertFalse(self.channel.online)

    def test_heartbeats_are_sent(self):
        self.channel.stop()
        self.make(heartbeat_ms=50)
        self.channel.start()

        self.assertTrue(pump(lambda: any(m['type'] == 'ping' for m in self.server.received)))

    def test_a_silent_server_is_dropped_and_redialled(self):
        self.channel.stop()
        self.server.answer_pings = False
        self.make(heartbeat_ms=50, silence_limit=0.15)
        self.channel.start()
        pump(lambda: self.channel.online)

        self.assertTrue(pump(lambda: len(self.server.clients) >= 2))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_cloud_channel.py' -v`
Expected: ERROR `ModuleNotFoundError: No module named 'camera_monitor.cloud_channel'`

- [ ] **Step 3: 实现**

`camera_monitor/cloud_channel.py`：

```python
"""云端下行通道：后台的操作 1 秒内送到这台电脑。

连不上不是故障：配置照样靠 60 秒轮询同步，画面照样放。这里只负责尽快连上、
断了就退避重连，收到的消息原样交给上层。
"""
from __future__ import annotations

import json
import random
import time
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

BACKOFF_SECONDS = (1, 2, 4, 8, 16, 32, 60)
HEARTBEAT_MS = 30_000
SILENCE_LIMIT_SECONDS = 90
MAX_MESSAGE_BYTES = 256 * 1024
# 这些拒绝重试也没用，只能等人重新输入设备码
PERMANENT_DENIALS = ('PROFILE_DISABLED', 'INVALID_AUTH_CODE', 'PROFILE_IN_USE')


def channel_url(base_url):
    parts = urlsplit(base_url)
    scheme = {'https': 'wss', 'http': 'ws'}.get(parts.scheme, parts.scheme)
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip('/') + '/ws', '', ''))


class CloudChannel(QObject):
    online_changed = Signal(bool)
    message = Signal(dict)
    denied = Signal(str)
    replaced = Signal()

    def __init__(self, url, hello, parent=None, backoff=BACKOFF_SECONDS,
                 heartbeat_ms=HEARTBEAT_MS, silence_limit=SILENCE_LIMIT_SECONDS,
                 jitter=lambda: random.uniform(0.8, 1.2), clock=time.monotonic):
        super().__init__(parent)
        self.url = url
        # 每次连上时现取：令牌会被静默重登换掉
        self.hello = hello
        self.backoff = tuple(backoff)
        self.silence_limit = silence_limit
        self.jitter = jitter
        self.clock = clock
        self.running = False
        self.online = False
        self.attempt = 0
        self.last_heard = 0.0

        self.socket = QWebSocket()
        self.socket.setParent(self)
        self.socket.connected.connect(self._on_connected)
        self.socket.disconnected.connect(self._on_disconnected)
        self.socket.errorOccurred.connect(self._on_error)
        self.socket.textMessageReceived.connect(self._on_text)

        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self._open)
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(heartbeat_ms)
        self.heartbeat.timeout.connect(self._beat)

    # ---------- 对外 ----------

    def start(self):
        if self.running:
            return
        self.running = True
        self.attempt = 0
        self._open()

    def stop(self):
        self.running = False
        self.retry_timer.stop()
        self.heartbeat.stop()
        self.socket.abort()
        self._set_online(False)

    def send(self, payload):
        if not self.online:
            return False
        self.socket.sendTextMessage(json.dumps(payload, ensure_ascii=False))
        return True

    # ---------- 内部 ----------

    def _open(self):
        if not self.running:
            return
        if self.socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            self.socket.abort()
        self.socket.open(QUrl(self.url))

    def _on_connected(self):
        hello = self.hello()
        if not hello:
            # 还没登录：断开，等下一轮退避再试
            self.socket.close()
            return
        self.last_heard = self.clock()
        self.socket.sendTextMessage(json.dumps(dict(hello, type='hello'), ensure_ascii=False))

    def _on_text(self, text):
        self.last_heard = self.clock()
        if len(text.encode('utf-8')) > MAX_MESSAGE_BYTES:
            return
        try:
            payload = json.loads(text)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        kind = payload.get('type')
        if kind == 'welcome':
            self.attempt = 0
            self.heartbeat.start()
            self._set_online(True)
            self.message.emit(payload)
        elif kind == 'denied':
            code = str(payload.get('failure_code') or '')
            if code in PERMANENT_DENIALS:
                self.running = False
            self.denied.emit(code)
            self.socket.close()
        elif kind == 'replaced':
            # 同一个设备码在别处连上了：这一条让位，不再重连，免得两边来回抢
            self.running = False
            self.replaced.emit()
            self.socket.close()
        elif kind == 'pong':
            pass
        else:
            self.message.emit(payload)

    def _beat(self):
        if self.clock() - self.last_heard > self.silence_limit:
            # 半开连接：TCP 看着还在，其实早断了。主动掐掉，走重连
            self.socket.abort()
            return
        self.send({'type': 'ping'})

    def _on_error(self, *args):
        if self.socket.state() == QAbstractSocket.SocketState.UnconnectedState:
            self._on_disconnected()

    def _on_disconnected(self):
        self.heartbeat.stop()
        self._set_online(False)
        if not self.running or self.retry_timer.isActive():
            return
        delay = self.backoff[min(self.attempt, len(self.backoff) - 1)] * self.jitter()
        self.attempt += 1
        self.retry_timer.start(max(1, int(delay * 1000)))

    def _set_online(self, online):
        if online != self.online:
            self.online = online
            self.online_changed.emit(online)
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_cloud_channel.py' -v`
Expected: 11 tests OK。连续跑 3 次都要通过，用来确认没有时序抖动：

```bash
for i in 1 2 3; do QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_cloud_channel.py' || break; done
```

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/cloud_channel.py tests/test_cloud_channel.py
git commit -m "feat: 云端下行通道——握手、心跳、断线退避重连

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: 截图压缩与上传队列

**Files:**
- Create: `camera_monitor/snapshots.py`
- Test: `tests/test_snapshots.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from camera_monitor.snapshots import SnapshotUploader, encode_jpeg


def picture(width=2000, height=1000):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.darkGreen)
    return image


def pump(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class EncodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_wide_picture_is_shrunk_into_a_small_jpeg(self):
        data = encode_jpeg(picture(), 480, 200_000)

        self.assertTrue(data.startswith(b'\xff\xd8'))
        self.assertLessEqual(len(data), 200_000)
        self.assertLessEqual(QImage.fromData(data).width(), 480)

    def test_a_small_picture_keeps_its_size(self):
        data = encode_jpeg(picture(320, 180), 480, 200_000)

        self.assertEqual(320, QImage.fromData(data).width())

    def test_nothing_to_encode(self):
        self.assertEqual(b'', encode_jpeg(None, 480, 200_000))
        self.assertEqual(b'', encode_jpeg(QImage(), 480, 200_000))


class UploaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()

        def upload(kind, jpeg, ip):
            self.gate.wait(3)
            self.calls.append((kind, ip, jpeg[:2]))

        self.uploader = SnapshotUploader(upload)
        self.addCleanup(self.uploader.stop)

    def test_uploads_run_off_the_ui_thread_in_order(self):
        self.uploader.submit('camera', picture(), '10.0.0.1')
        self.uploader.submit('screen', picture())

        self.assertTrue(pump(lambda: len(self.calls) == 2 and not self.uploader.busy()))
        self.assertEqual([('camera', '10.0.0.1', b'\xff\xd8'), ('screen', '', b'\xff\xd8')], self.calls)

    def test_the_latest_picture_of_the_same_camera_wins(self):
        self.gate.clear()
        self.uploader.submit('camera', picture(), '10.0.0.9')
        self.uploader.submit('camera', picture(), '10.0.0.1')
        self.uploader.submit('camera', picture(), '10.0.0.1')

        self.assertEqual(1, len(self.uploader.queue), '排队中的同一路只留最新一张')
        self.gate.set()
        self.assertTrue(pump(lambda: len(self.calls) == 2))

    def test_a_failed_upload_does_not_stop_the_queue(self):
        outcomes = iter([RuntimeError('boom'), None])

        def flaky(kind, jpeg, ip):
            error = next(outcomes)
            if error:
                raise error
            self.calls.append(ip)

        uploader = SnapshotUploader(flaky)
        self.addCleanup(uploader.stop)
        uploader.submit('camera', picture(), '10.0.0.1')
        uploader.submit('camera', picture(), '10.0.0.2')

        self.assertTrue(pump(lambda: self.calls == ['10.0.0.2']))

    def test_an_empty_image_is_refused(self):
        self.assertFalse(self.uploader.submit('camera', QImage(), '10.0.0.1'))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_snapshots.py' -v`
Expected: ERROR `ModuleNotFoundError`

- [ ] **Step 3: 实现**

`camera_monitor/snapshots.py`：

```python
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
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_snapshots.py' -v`
Expected: 7 tests OK

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/snapshots.py tests/test_snapshots.py
git commit -m "feat: 截图压缩成小 JPEG，后台线程逐张上传

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: 主窗口的程序化入口

**Files:**
- Modify: `camera_monitor/app.py`
- Test: `tests/test_managed_window_state.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from support import make_window
from test_credentials import MemoryVault


class ProgrammaticEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = make_window(self)
        self.window.thumbnails.request = lambda *args, **kwargs: None

    def test_run_scan_works_while_presenting(self):
        self.window.set_presentation(True)

        with patch('camera_monitor.app.SearchWorker') as worker:
            worker.return_value.isRunning.return_value = False
            self.assertTrue(self.window.run_scan(target_ip='192.168.2.216'))

        self.assertEqual('192.168.2.216', worker.call_args.kwargs['target_ip'])

    def test_the_sidebar_button_still_refuses_while_presenting(self):
        self.window.set_presentation(True)

        with patch('camera_monitor.app.SearchWorker') as worker:
            self.window.start_scan()

        worker.assert_not_called()

    def test_run_scan_refuses_while_a_scan_is_running(self):
        self.window.worker = MagicMock()
        self.window.worker.isRunning.return_value = True

        self.assertFalse(self.window.run_scan())

    def test_finishing_a_scan_is_announced(self):
        results = []
        self.window.scan_completed.connect(results.append)

        self.window.finish_scan()

        self.assertEqual([False], results)

    def test_managed_fullscreen_needs_no_password(self):
        with patch('camera_monitor.app.request_unlock', side_effect=AssertionError('不该弹密码框')):
            self.window.set_managed_fullscreen(True)
            self.window.toggle_fullscreen()
            self.assertFalse(self.window.exit_fullscreen())

        self.assertTrue(self.window.presentation)
        self.assertTrue(self.window.managed_fullscreen)

    def test_managed_windowed_mode_still_hides_the_sidebar(self):
        self.window.set_managed_fullscreen(False)

        self.assertTrue(self.window.presentation)
        self.assertFalse(self.window.managed_fullscreen)

    def test_a_managed_window_refuses_to_close(self):
        self.window.set_managed_fullscreen(True)
        event = QCloseEvent()

        self.window.closeEvent(event)

        self.assertFalse(event.isAccepted())

    def test_leaving_managed_mode_restores_the_normal_window(self):
        self.window.set_managed_fullscreen(True)

        self.window.leave_managed_window()

        self.assertIsNone(self.window.managed_fullscreen)
        self.assertFalse(self.window.presentation)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_window_state.py' -v`
Expected: ERROR `AttributeError: 'Window' object has no attribute 'run_scan'`（其他用例也会因为缺属性报错）

- [ ] **Step 3: 实现**

修改 `camera_monitor/app.py`：

1. `class Window(QMainWindow):` 下面紧接着加一行类属性：

```python
    scan_completed=Signal(bool)
```

2. `__init__` 中，紧接 `self.presentation = False` 加：

```python
        # None：不是傻瓜模式；True / False：云端要求全屏 / 窗口
        self.managed_fullscreen=None
        self.authorized_quit=False
```

3. 把 `start_scan` 拆成两个方法：

```python
    def start_scan(self, checked=False, *, target_ip=None):
        if self.presentation:return
        self.run_scan(target_ip)

    def run_scan(self, target_ip=None):
        """真正去搜。不看界面状态，云端远程搜索也走这里。返回是否真的开始了。"""
        if self.worker and self.worker.isRunning():
            return False
```

原来 `start_scan` 中从 `net = self.network.currentData()` 开始，一直到 `self.worker.start()` 的全部代码，原样放到 `run_scan` 里，接在上面这几行之后；最后再加一行 `return True`。

4. `finish_scan` 方法的最后加一行：

```python
        self.scan_completed.emit(bool(cancelled))
```

5. `toggle_fullscreen` 的第一行插入 `if self.managed_fullscreen is not None:return`。

6. `exit_fullscreen` 的第一行插入 `if self.managed_fullscreen is not None:return False`。

7. `native_exit_requested` 的第一行插入 `if self.managed_fullscreen is not None:return`。

8. `changeEvent` 中，在 `if event.type()==QEvent.Type.WindowStateChange and hasattr(self,'wall') and self.wall:` 下面先插入：

```python
            if self.managed_fullscreen is not None:
                # 傻瓜模式：云端说全屏就一直全屏，被系统退出了就拉回来，不弹密码
                if self.managed_fullscreen and not self.isFullScreen() and not self.authorized_exit:
                    QTimer.singleShot(0,self.showFullScreen)
                return
```

9. `closeEvent` 的前两行改成：

```python
        if self.managed_fullscreen is not None and not self.authorized_quit:
            event.ignore();return
        if self.presentation and self.managed_fullscreen is None and not self.exit_fullscreen():
            event.ignore();return
```

10. 在 `exit_fullscreen` 之后加：

```python
    def set_managed_fullscreen(self,enabled):
        """傻瓜模式下全屏与否由云端决定：不弹密码，侧边栏始终收起。"""
        self.managed_fullscreen=bool(enabled)
        if not self.presentation:self.set_presentation(True)
        self.authorized_exit=True
        try:
            self.showFullScreen() if enabled else self.showMaximized()
        finally:self.authorized_exit=False

    def leave_managed_window(self):
        self.managed_fullscreen=None
        self.authorized_exit=True
        try:
            self.set_presentation(False)
            self.showMaximized()
        finally:self.authorized_exit=False
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_window_state.py' -v`
Expected: 8 tests OK

再跑：`QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests`
Expected: 全部 OK（重点看 `test_target_app.py`、`test_fullscreen_guard.py`）

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/app.py tests/test_managed_window_state.py
git commit -m "refactor: 搜索与全屏拆出不看界面状态的入口，供云端远程调用

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: ManagedController

**Files:**
- Create: `camera_monitor/managed_mode.py`
- Test: `tests/test_managed_mode.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from camera_monitor.cloud_state import DEFAULT_CREDENTIAL_ACCOUNT
from camera_monitor.discovery import Device
from camera_monitor.managed_mode import EscapeDialog, ManagedController
from support import make_window
from test_credentials import MemoryVault


class FakeChannel(QObject):
    online_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.online = True
        self.sent = []

    def send(self, payload):
        if not self.online:
            return False
        self.sent.append(payload)
        return True

    def of(self, kind):
        return [message for message in self.sent if message['type'] == kind]


class FakeUploader:
    def __init__(self):
        self.submitted = []

    def submit(self, kind, image, ip=''):
        self.submitted.append((kind, ip))
        return True


class FakeAutoStart:
    def __init__(self):
        self.enabled = False

    def enable(self):
        self.enabled = True
        return True

    def disable(self):
        self.enabled = False
        return True


def frame():
    image = QImage(64, 36, QImage.Format.Format_RGB32)
    image.fill(0)
    return image


class ManagedControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: self.vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = make_window(self)
        self.requests = []
        self.window.thumbnails.request = lambda device, credentials=None: self.requests.append(
            (device.ip, credentials))
        self.channel = FakeChannel()
        self.uploader = FakeUploader()
        self.autostart = FakeAutoStart()
        self.choice = None
        self.restarts = []
        self.managed = ManagedController(self.window, self.channel, self.uploader, self.autostart,
                                         escape=lambda window: self.choice,
                                         restart=lambda: self.restarts.append(1))

    def command(self, name, args=None, command_id='c1'):
        self.managed.run_command({'type': 'command', 'id': command_id, 'name': name, 'args': args or {}})

    def last(self, kind):
        return self.channel.of(kind)[-1]

    # ---------- 进出 ----------

    def test_entering_locks_everything_and_enables_autostart(self):
        self.managed.enter()

        self.assertTrue(self.window.wall.locked)
        self.assertTrue(self.window.presentation)
        self.assertTrue(self.autostart.enabled)
        self.assertTrue(self.managed.shortcut.isEnabled())

    def test_leaving_undoes_it(self):
        self.managed.enter()
        self.window.set_managed_fullscreen(True)

        self.managed.leave()

        self.assertFalse(self.window.wall.locked)
        self.assertFalse(self.window.presentation)
        self.assertIsNone(self.window.managed_fullscreen)
        self.assertFalse(self.autostart.enabled)

    def test_an_empty_wall_shows_the_waiting_notice(self):
        self.managed.enter()

        self.assertFalse(self.managed.waiting.isHidden())
        self.assertIn('等待管理员配置', self.managed.waiting.text())

        self.window.wall.add_device(Device('10.0.0.1'))
        self.managed.refresh_overlays()
        self.assertTrue(self.managed.waiting.isHidden())

    def test_apply_sets_fullscreen_and_the_escape_password(self):
        self.managed.enter()
        record = ('pbkdf2_sha256$240000$000102030405060708090a0b0c0d0e0f$'
                  '6779a48d726cc20519aa5f663a475da180d9ddcf213ec794eef28f7ed9f9bc4e')

        self.managed.apply(SimpleNamespace(fullscreen=True, escape_password_hash=record))

        self.assertTrue(self.window.managed_fullscreen)
        self.assertTrue(self.window.screen_lock.verify('246810'))

    # ---------- 动作 ----------

    def test_commands_are_refused_outside_managed_mode(self):
        self.command('scan')

        self.assertEqual({'type': 'ack', 'id': 'c1', 'ok': False, 'error': 'NOT_MANAGED'},
                         self.last('ack'))

    def test_unknown_commands_are_refused(self):
        self.managed.enter()
        self.command('format_disk')

        self.assertEqual('UNKNOWN_COMMAND', self.last('ack')['error'])

    def test_scan_reports_what_it_found_and_fetches_snapshots(self):
        self.managed.enter()
        scans = []
        self.window.run_scan = lambda target_ip=None: scans.append(target_ip) or True

        self.command('scan', {'target_ip': '192.168.2.216'})
        self.assertEqual(['192.168.2.216'], scans)
        self.assertTrue(self.last('ack')['ok'])

        self.window.devices = {'192.168.2.216': Device('192.168.2.216', model='IPC', manufacturer='Dahua',
                                                       protocols=['ONVIF'], urls=['http://x'])}
        self.window.scan_completed.emit(False)

        result = self.last('scan_result')
        self.assertEqual('c1', result['id'])
        self.assertEqual([{'ip': '192.168.2.216', 'model': 'IPC', 'manufacturer': 'Dahua',
                           'protocols': ['ONVIF'], 'onvif_urls': ['http://x']}], result['devices'])
        self.assertIn('192.168.2.216', self.managed.snapshot_wanted)
        self.assertEqual('192.168.2.216', self.requests[-1][0])

    def test_a_second_scan_while_busy_is_refused(self):
        self.managed.enter()
        self.window.run_scan = lambda target_ip=None: True

        self.command('scan', command_id='a')
        self.command('scan', command_id='b')

        self.assertEqual('BUSY', self.last('ack')['error'])

    def test_a_bad_target_is_refused(self):
        self.managed.enter()

        self.command('scan', {'target_ip': '8.8.8.8; rm -rf'})

        self.assertEqual('BAD_TARGET', self.last('ack')['error'])

    def test_discovered_cameras_without_their_own_login_use_the_default(self):
        self.managed.enter()
        self.window.wall.credential_store.save(DEFAULT_CREDENTIAL_ACCOUNT, 'admin', 'dflt')
        self.window.devices = {'10.0.0.7': Device('10.0.0.7')}

        self.command('refresh_snapshots')

        self.assertEqual(('10.0.0.7', ('admin', 'dflt')), self.requests[-1])
        self.assertTrue(self.last('command_done')['ok'])

    def test_a_wanted_thumbnail_is_uploaded_once(self):
        self.managed.enter()
        self.window.devices = {'10.0.0.7': Device('10.0.0.7')}
        self.command('refresh_snapshots', {'ips': ['10.0.0.7']})

        self.managed.on_thumbnail('10.0.0.7', frame(), '抓拍画面')
        self.managed.on_thumbnail('10.0.0.7', frame(), '抓拍画面')
        self.managed.on_thumbnail('10.0.0.8', frame(), '抓拍画面')

        self.assertEqual([('camera', '10.0.0.7')], self.uploader.submitted)

    def test_capture_screen_uploads_the_window(self):
        self.managed.enter()

        self.command('capture_screen')

        self.assertEqual([('screen', '')], self.uploader.submitted)
        self.assertTrue(self.last('command_done')['ok'])

    def test_reconnect_all(self):
        self.managed.enter()
        calls = []
        self.window.wall.reconnect_all = lambda: calls.append(1)

        self.command('reconnect_all')

        self.assertEqual([1], calls)

    def test_restart_is_acknowledged_before_it_happens(self):
        self.managed.enter()

        self.command('restart_app')

        self.assertTrue(self.last('ack')['ok'])
        self.assertTrue(self.window.authorized_quit)

    # ---------- 状态 ----------

    def test_status_is_reported_on_change_only(self):
        self.managed.enter()
        self.window.wall.add_device(Device('10.0.0.1'))
        tile = self.window.wall.tiles[0]
        tile.player.status.setText('正在播放 · 1280 × 720 · H264')

        self.managed.report_status()
        self.managed.report_status()

        statuses = self.channel.of('status')
        self.assertEqual(1, len(statuses))
        self.assertEqual([{'ip': '10.0.0.1', 'state': 'playing'}], statuses[0]['cameras'])
        self.assertIn('config_version', statuses[0])
        self.assertIn('10.0.0.1', self.managed.snapshot_wanted, '第一次播起来自动补一张截图')

        tile.player.status.setText('视频认证失败，请检查摄像头用户名和密码。')
        self.managed.report_status()
        self.assertEqual('auth_failed', self.channel.of('status')[-1]['cameras'][0]['state'])

    def test_status_waits_while_offline(self):
        self.managed.enter()
        self.channel.online = False

        self.managed.report_status()
        self.channel.online = True
        self.managed.report_status()

        self.assertEqual(1, len(self.channel.of('status')), '离线那一拍没发出去，联网后要补')

    # ---------- 断线提示、逃生口 ----------

    def test_the_offline_notice_waits_before_showing(self):
        self.managed.enter()
        self.channel.online = False

        self.managed.on_online_changed(False)
        self.assertTrue(self.managed.offline.isHidden())
        self.assertTrue(self.managed.offline_timer.isActive())

        self.managed.show_offline()
        self.assertFalse(self.managed.offline.isHidden())

        self.channel.online = True
        self.managed.on_online_changed(True)
        self.assertTrue(self.managed.offline.isHidden())

    def test_escape_quit(self):
        self.managed.enter()
        closed = []
        self.window.close = lambda: closed.append(1)
        self.choice = EscapeDialog.QUIT

        self.managed.open_escape()

        self.assertTrue(self.window.authorized_quit)
        self.assertEqual([1], closed)

    def test_escape_unbind(self):
        self.managed.enter()
        unbinds = []
        self.window.unbind_cloud = lambda message='': unbinds.append(message)
        self.choice = EscapeDialog.UNBIND

        self.managed.open_escape()

        self.assertEqual([''], unbinds)

    def test_a_cancelled_escape_does_nothing(self):
        self.managed.enter()
        self.choice = None

        self.managed.open_escape()

        self.assertFalse(self.window.authorized_quit)


class EscapeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_wrong_password_keeps_the_dialog_open(self):
        lock = SimpleNamespace(verify=lambda password: password == '246810')
        dialog = EscapeDialog(lock)
        self.addCleanup(dialog.deleteLater)

        dialog.password.setText('000000')
        dialog.quit_button.click()
        self.assertIsNone(dialog.choice)
        self.assertEqual('密码不正确。', dialog.error.text())

        dialog.password.setText('246810')
        dialog.unbind_button.click()
        self.assertEqual(EscapeDialog.UNBIND, dialog.choice)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_mode.py' -v`
Expected: ERROR `ModuleNotFoundError: No module named 'camera_monitor.managed_mode'`

- [ ] **Step 3: 实现**

`camera_monitor/managed_mode.py`：

```python
"""傻瓜模式：现场只输一次设备码，之后一切听云端的。

界面锁死、全屏与否按云端的设置、执行后台下发的动作、把运行状态和截图报上去。
维护人员唯一的出口是组合键加维护密码。
"""
from __future__ import annotations

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

ESCAPE_KEYS = 'Ctrl+Shift+Alt+Q'
STATUS_TICK_MS = 1_000
STATUS_HEARTBEAT_SECONDS = 60
OFFLINE_NOTICE_MS = 10_000


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
        self.snapshot_wanted = set()
        self.seen_playing = set()
        self.last_status = None
        self.last_status_at = 0.0

        root = window.centralWidget()
        self.waiting = QLabel(root)
        self.waiting.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.waiting.setStyleSheet('background:#0f1829;color:#c9d3e6;font-size:20px;')
        self.waiting.hide()
        self.offline = QLabel('云端连接中断，画面正常播放中 · 正在自动重连', root)
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

        self.shortcut = QShortcut(QKeySequence(ESCAPE_KEYS), window)
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
        self.status_timer.start()
        self.on_online_changed(self.channel.online)
        self.refresh_overlays()

    def leave(self):
        if not self.active:
            return
        self.active = False
        self.status_timer.stop()
        self.offline_timer.stop()
        self.shortcut.setEnabled(False)
        self.waiting.hide()
        self.offline.hide()
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
        try:
            session = self.window.cloud.session.load_session() or {}
        except CredentialError:
            session = {}
        code = ''.join(ch for ch in str(session.get('auth_code', '')) if ch.isalnum()).upper()
        return code[-4:] or '----'

    def on_online_changed(self, online):
        if online:
            self.offline_timer.stop()
            self.offline.hide()
            self.last_status = None
        elif self.active and not self.offline_timer.isActive() and self.offline.isHidden():
            # 抖一下就重连上是常事，10 秒内恢复的不必让现场看到
            self.offline_timer.start()

    def show_offline(self):
        if not self.active or self.channel.online:
            return
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
        handler(command_id, args)

    def ack(self, command_id, ok, error=''):
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
        if not self.window.run_scan(target_ip=target or None):
            self.scan_command = None
            self.ack(command_id, False, 'BUSY')
            return
        self.ack(command_id, True)

    def on_scan_completed(self, cancelled):
        command_id, self.scan_command = self.scan_command, None
        if command_id is None:
            return
        devices = list(self.window.devices.values())
        self.channel.send({'type': 'scan_result', 'id': command_id, 'devices': [
            {'ip': d.ip, 'model': d.model or '', 'manufacturer': d.manufacturer or '',
             'protocols': list(d.protocols or []), 'onvif_urls': list(d.urls or [])}
            for d in devices]})
        on_wall = {tile.player.device.ip for tile in self.window.wall.tiles}
        for device in devices:
            if device.ip not in on_wall:
                self.want_snapshot(device)

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
        self.window.authorized_quit = True
        # 留一点时间让 ack 发出去
        QTimer.singleShot(300, self.restart)

    def on_thumbnail(self, ip, image, status):
        if ip not in self.snapshot_wanted or image is None or image.isNull():
            return
        self.snapshot_wanted.discard(ip)
        self.uploader.submit('camera', image, ip)

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
        payload = self.status_payload()
        for camera in payload['cameras']:
            if camera['state'] == PLAYING and camera['ip'] not in self.seen_playing:
                self.seen_playing.add(camera['ip'])
                tile = next(t for t in self.window.wall.tiles if t.player.device.ip == camera['ip'])
                self.want_snapshot(tile.player.device)
        self.refresh_overlays()
        now = self.clock()
        if payload == self.last_status and now - self.last_status_at < STATUS_HEARTBEAT_SECONDS:
            return
        if self.channel.send(payload):
            self.last_status, self.last_status_at = payload, now
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_mode.py' -v`
Expected: 22 tests OK

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/managed_mode.py tests/test_managed_mode.py
git commit -m "feat: 傻瓜模式控制器——锁界面、执行远程动作、回报状态、维护人员入口

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: 把它们接进主窗口

**Files:**
- Modify: `camera_monitor/app.py`、`tests/support.py`
- Test: `tests/test_managed_window.py`（新建）

- [ ] **Step 1: 写失败的测试**

```python
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from support import make_window
from test_cloud_sync import snapshot
from test_credentials import MemoryVault


def managed(version=1, fullscreen=True):
    snap = dict(snapshot(version=version), mode='managed')
    snap['layout'] = dict(snap['layout'], fullscreen=fullscreen)
    return snap


class ManagedWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = make_window(self)
        self.window.thumbnails.request = lambda *args, **kwargs: None

    def test_a_managed_snapshot_switches_the_window_over(self):
        self.window.cloud._apply(managed())

        self.assertTrue(self.window.managed.active)
        self.assertTrue(self.window.wall.locked)
        self.assertTrue(self.window.presentation)
        self.assertTrue(self.window.managed_fullscreen)

    def test_windowed_is_respected(self):
        self.window.cloud._apply(managed(fullscreen=False))

        self.assertFalse(self.window.managed_fullscreen)

    def test_back_to_full_mode_restores_the_sidebar(self):
        self.window.cloud._apply(managed())

        self.window.cloud._apply(snapshot())

        self.assertFalse(self.window.managed.active)
        self.assertFalse(self.window.presentation)
        self.assertFalse(self.window.wall.locked)

    def test_config_changed_fetches_only_newer_versions(self):
        fetches = []
        self.window.cloud.refresh = lambda: fetches.append(1)
        self.window.cloud.settings.setValue('cloud/version', 4)

        self.window.on_channel_message({'type': 'config_changed', 'version': 4})
        self.window.on_channel_message({'type': 'config_changed', 'version': 5})
        self.window.on_channel_message({'type': 'welcome', 'version': 6})

        self.assertEqual([1, 1], fetches)

    def test_commands_go_to_the_controller(self):
        seen = []
        self.window.managed.run_command = seen.append

        self.window.on_channel_message({'type': 'command', 'id': 'c', 'name': 'scan'})

        self.assertEqual('c', seen[0]['id'])

    def test_being_revoked_returns_to_the_welcome_page(self):
        self.window.cloud.settings.setValue('cloud/enabled', True)
        self.window.cloud._apply(managed())

        self.window.on_channel_message({'type': 'revoked', 'failure_code': 'PROFILE_DISABLED'})

        self.assertFalse(self.window.welcome.isHidden())
        self.assertIn('联系管理员', self.window.welcome.error.text())
        self.assertFalse(self.window.cloud.enabled())
        self.assertFalse(self.window.managed.active)
        self.assertEqual('false', str(self.window.cloud.settings.value('cloud/standalone')).lower(),
                         '解绑后明确记为「要看欢迎页」，下次启动不再按旧配置判成单机')

    def test_hello_needs_a_session(self):
        self.assertIsNone(self.window.channel_hello())

        self.window.cloud.token = 'cm1.t'
        hello = self.window.channel_hello()
        self.assertEqual('cm1.t', hello['token'])
        self.assertEqual(self.window.cloud.client_uid(), hello['client_uid'])
        self.assertIn('app_version', hello)

    def test_an_expired_token_on_the_channel_triggers_a_silent_relogin(self):
        relogins = []
        self.window.cloud.relogin = lambda: relogins.append(1)

        self.window.on_channel_denied('INVALID_TOKEN')

        self.assertEqual([1], relogins)

    def test_a_managed_computer_whose_session_vanished_shows_the_welcome_page(self):
        settings = self.window.cloud.settings
        settings.setValue('cloud/enabled', True)
        settings.setValue('cloud/mode', 'managed')

        self.window.start_cloud()

        self.assertFalse(self.window.managed.active)
        self.assertFalse(self.window.welcome.isHidden())


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_window.py' -v`
Expected: ERROR `AttributeError: 'Window' object has no attribute 'managed'`

- [ ] **Step 3: 实现**

修改 `camera_monitor/app.py`：

1. 导入部分在 `from .welcome import WelcomePage` 下面加：

```python
from .autostart import AutoStart
from .cloud import DEFAULT_BASE_URL
from .cloud_channel import CloudChannel, channel_url
from .managed_mode import ManagedController
from .snapshots import SnapshotUploader
```

2. `Window.__init__` 的签名改成：

```python
    def __init__(self, device_names=None, cloud_settings=None, connection_options=None, autostart=None):
```

3. 在 `self.cloud.login_result.connect(self.cloud_login_result)` 之后插入：

```python
        self.cloud.mode_changed.connect(self.on_cloud_mode)
        self.channel=CloudChannel(channel_url(getattr(self.cloud.client,'base_url',DEFAULT_BASE_URL)),
            self.channel_hello,parent=self)
        self.channel.message.connect(self.on_channel_message)
        self.channel.denied.connect(self.on_channel_denied)
        self.snapshot_uploader=SnapshotUploader(self.upload_snapshot,self)
        self.managed=ManagedController(self,self.channel,self.snapshot_uploader,
            autostart if autostart is not None else AutoStart())
```

4. `self.cloud_start_timer.timeout.connect(self.cloud.start)` 改成 `self.cloud_start_timer.timeout.connect(self.start_cloud)`。

5. `position_overlay` 末尾（欢迎页那几行之前）加：

```python
        if hasattr(self,'managed'):self.managed.refresh_overlays()
```

6. `cloud_session_changed` 替换成：

```python
    def cloud_session_changed(self,connected):
        self.cloud_panel.set_connected(connected,self.cloud.profile_name())
        if connected:
            self.channel.start()
            return
        self.channel.stop()
        if self.managed.active:
            # 傻瓜模式没有侧边栏可以重新登录，只能回到欢迎页
            self.managed.leave()
            self.show_welcome('云端登录已失效，请重新输入设备码。')
```

7. 在 `use_standalone` 之后加：

```python
    def start_cloud(self):
        # 先进傻瓜模式再铺缓存：锁好界面之后才把全屏之类的设置落下去
        if self.cloud.enabled() and self.cloud.mode()=='managed':self.managed.enter()
        self.cloud.start()
        if self.cloud.token:self.channel.start()

    def show_welcome(self,message=''):
        self.welcome.set_busy(False)
        self.welcome.show_error(message)
        self.welcome.show()
        self.position_overlay()

    def unbind_cloud(self,message=''):
        """维护人员解绑，或后台收回了设备码：回到输入设备码的那一页。本机摄像头配置保留。"""
        self.channel.stop()
        self.managed.leave()
        self.cloud.logout()
        startup.clear_standalone(self.cloud.settings)
        if self.wall is not None:self.wall.stop_everything()
        self.show_welcome(message)

    def on_cloud_mode(self,mode):
        if mode=='managed':self.managed.enter()
        else:self.managed.leave()

    def channel_hello(self):
        if not self.cloud.token:return None
        return {'token':self.cloud.token,'client_uid':self.cloud.client_uid(),'app_version':__version__}

    def on_channel_message(self,payload):
        kind=payload.get('type')
        if kind in ('welcome','config_changed'):self.cloud.note_remote_version(payload.get('version',0))
        elif kind=='command':self.managed.run_command(payload)
        elif kind=='revoked':self.unbind_cloud('该设备码已被管理员停用或收回，请联系管理员。')

    def on_channel_denied(self,failure_code):
        if failure_code=='INVALID_TOKEN':self.cloud.relogin()
        else:self.cloud.refresh()

    def upload_snapshot(self,kind,jpeg,ip=''):
        token=self.cloud.token
        if not token:raise RuntimeError('尚未登录云端')
        return self.cloud.client.upload_snapshot(token,kind,jpeg,ip)
```

8. `apply_cloud_config` 最后一行 `self.wall.connect_all()` 之后加：

```python
        if result.mode=='managed':self.managed.apply(result)
```

9. `closeEvent` 中，`self.cloud.stop()` 之后加 `self.channel.stop()`；并把

```python
        if self.thumbnails.busy() or self.cloud.busy():
```

改成

```python
        if self.thumbnails.busy() or self.cloud.busy() or self.snapshot_uploader.busy():
```

10. `tests/support.py` 的 `shut_down()` 中，在 `window.cloud.stop()` 之后加：

```python
        window.channel.stop()
        window.managed.status_timer.stop()
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests -p 'test_managed_window.py' -v`
Expected: 9 tests OK

再跑全量：`QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests`
Expected: 全部 OK

- [ ] **Step 5: 提交**

```bash
git add camera_monitor/app.py tests/support.py tests/test_managed_window.py
git commit -m "feat: 主窗口接入傻瓜模式——下行通道、远程动作、解绑回欢迎页

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: 打包、版本号、文档

**Files:**
- Modify: `packaging/CameraMonitor.spec`、`packaging/CameraMonitor-windows.spec`、`packaging/launcher.py`、`camera_monitor/app.py`（`main`）、`camera_monitor/__init__.py`、`README.md`、`docs/superpowers/specs/2026-10-03-managed-mode-design.md`

- [ ] **Step 1: 打包时带上 QtWebSockets**

`packaging/CameraMonitor.spec` 中，`hiddenimports=['keyring.backends.macOS']` 改成：

```python
hiddenimports=['keyring.backends.macOS','PySide6.QtWebSockets'],
```

`packaging/CameraMonitor-windows.spec` 中，`hiddenimports=['keyring.backends.Windows', 'win32ctypes.pywin32.win32cred'],` 改成：

```python
    hiddenimports=['keyring.backends.Windows', 'win32ctypes.pywin32.win32cred', 'PySide6.QtWebSockets'],
```

确认两个 spec 的 `excludes` 列表里都没有 `PySide6.QtNetwork` 和 `PySide6.QtWebSockets`（现在没有，以后也不要加）。

- [ ] **Step 2: 冒烟测试检查 TLS 和 WebSocket**

`packaging/launcher.py` 的 `smoke_test` 中，在 `app = QApplication([])` 之后加（QWebSocket 需要先有 QApplication）：

```python
    from PySide6.QtNetwork import QSslSocket
    from PySide6.QtWebSockets import QWebSocket
    # 下行通道走 wss：打包漏了 TLS 后端插件，现场会永远连不上，且没有任何报错
    assert QSslSocket.supportsSsl(), 'TLS backend missing from the bundle'
    assert QWebSocket() is not None
```

- [ ] **Step 3: 关机、注销时放行**

`camera_monitor/app.py` 的 `main()` 中，`window = Window()` 之后加：

```python
    # 关机、注销时系统要求所有程序退出；傻瓜模式平时拦着关闭，这时必须放行
    app.commitDataRequest.connect(lambda manager: setattr(window, 'authorized_quit', True))
```

- [ ] **Step 4: 版本号**

`camera_monitor/__init__.py`：`__version__ = '0.9.0'`

- [ ] **Step 5: README**

在 `README.md` 介绍云端同步的那一节之后加一节（如果找不到这一节，就放在"下载"表格之后）：

```markdown
## 傻瓜模式（云端托管）

管理员在后台把设备码设为「傻瓜模式」后，现场电脑只需输入一次设备码：

- 界面只剩监控画面，点击、拖动、快捷键都不起作用；
- 是否全屏、分屏、顺序、摄像头账号密码全部由后台远程设置，约 1 秒生效；
- 自动设为开机运行，断电重启后自动恢复画面；
- 维护人员按 `Ctrl+Shift+Alt+Q`（Mac 为 `⌘⇧⌥Q`）并输入维护密码，可以退出软件或解除绑定。

外网断开时画面照常播放，只是暂时收不到后台的新设置。
```

- [ ] **Step 6: 把实现层面的调整写回 spec**

在 `docs/superpowers/specs/2026-10-03-managed-mode-design.md` 末尾加一节：

```markdown
## 12. 实现时的调整（客户端计划）

- **默认摄像头账号存进钥匙串**：原设计是"只放内存"，现在改为：`apply_snapshot` 把账号存到钥匙串账户 `__default__` 下；后台没单独填账号的摄像头，也写入这组账号。这样断网重启后照样能播放。离线缓存只保留默认账号的用户名，不保留密码。
- **播放状态不新增信号**：没有给 `PlayerWindow` 加 `state_changed`，改为用 `player_state()` 把现有的状态文字归类，`ManagedController` 每秒比对一次、有变化才上报，自然做到了"1 秒内的变化合成一条"。
- **不加 `force` 参数**：§4.6 设想过给 `set_fill_width` / `set_grid_columns` 加 `force` 参数。实际上云端配置直接把值写进设置和 `_fill_width`，演示模式拦不到这条路，所以不需要 `force`。
- **`refresh_snapshots` 的 `command_done` 含义**：表示截图请求都已经排进队列，不代表每张都已上传。后台以截图时间的变化为准。
```

- [ ] **Step 7: 全量测试**

Run: `QT_QPA_PLATFORM=offscreen $PY -m unittest discover -s tests`
Expected: 全部 OK

- [ ] **Step 8: 本机实际跑一遍**

Run: `$PY -m camera_monitor.app`

需要确认：

- 用一个全新的设置文件（或者真的在一台新电脑上）打开时，出现欢迎页；
- 点"单机使用"后进入原来的界面；
- 在 yunqi 计划 A 还没部署之前，输入设备码会得到现有的错误提示，不会崩溃。

> 注意：开发机上已经有真实配置（老用户），所以不会出现欢迎页。要看欢迎页，就临时把 `QSettings('CameraMonitor','Cloud')` 的 `cloud/standalone` 删掉，同时把 DeviceNames 的配置挪到别处。看完一定要恢复。

- [ ] **Step 9: 提交**

```bash
git add packaging/CameraMonitor.spec packaging/CameraMonitor-windows.spec packaging/launcher.py \
        camera_monitor/app.py camera_monitor/__init__.py README.md \
        docs/superpowers/specs/2026-10-03-managed-mode-design.md
git commit -m "chore: 0.9.0——打包带上 WebSocket 与 TLS 检查，补充傻瓜模式说明

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 10: Windows 打包验证（推到远端后由 CI 跑）**

推送分支后，GitHub Actions 的 `windows-exe.yml` 会跑测试、打包和冒烟测试，`QSslSocket.supportsSsl()` 的检查也在其中。然后在一台 Windows 真机上验证：

1. 解压后运行，看到欢迎页；
2. 输入一个 managed 设备码（需要计划 A 已部署）后，进入全屏监控墙；
3. 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 里出现 `CameraMonitor`；
4. 重启电脑后自动打开并恢复画面；
5. `Ctrl+Shift+Alt+Q` 加维护密码能退出软件。
