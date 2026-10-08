import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication

from camera_monitor import __version__
from camera_monitor.cloud import CloudAuthError, CloudConflict, CloudError
from camera_monitor.cloud_sync import CloudSync
from camera_monitor.connection_options import ConnectionOptions
from camera_monitor.credentials import CloudSessionStore, CredentialError, CredentialStore
from camera_monitor.device_names import DeviceNames
from test_credentials import MemoryVault


def snapshot(version=1, cameras=None, name='一楼大厅'):
    return {
        'version': version,
        'profile': {'id': 1, 'name': name, 'institution_id': 2, 'institution_name': '阳光养老院'},
        'layout': {'capacity': 9, 'columns': {'9': 3}, 'fill_width': True, 'organization': '阳光养老院'},
        'cameras': cameras if cameras is not None else [],
    }


def camera(ip='10.0.0.1', slot=0, password='pw'):
    return {'ip': ip, 'slot_index': slot, 'display_name': '入口', 'model': 'M',
            'manufacturer': 'X', 'protocols': ['ONVIF'], 'onvif_urls': [],
            'stream_mode': 'onvif', 'dahua_channel': 1, 'manual_url': '',
            'transport': 'tcp', 'username': 'admin', 'password': password,
            'name_color': '#ffffff', 'name_corner': 'top-left'}


def local(applied):
    """落下来的配置在本机 collector 眼里的样子（只取去重会看的部分）。"""
    return {'version': 0, 'layout': {'capacity': applied.capacity},
            'cameras': [{'ip': device.ip, 'slot_index': index}
                        for index, device in enumerate(applied.devices)]}


def local_of(snap):
    return {'version': 0, 'layout': {'capacity': snap['layout']['capacity']},
            'cameras': [{'ip': c['ip'], 'slot_index': index}
                        for index, c in enumerate(snap.get('cameras') or [])]}


class FakeClient:
    """Stand-in for CloudClient; records calls and replays scripted outcomes."""

    def __init__(self):
        self.login_result = dict(snapshot(), token='cm1.token', expires_in=99)
        self.login_versions = []
        self.fetch_result = snapshot()
        self.ping_result = {'version': 1, 'profile_name': '一楼大厅'}
        self.push_result = snapshot(version=2)
        self.raises = {}
        self.calls = []

    def _maybe_raise(self, kind):
        error = self.raises.pop(kind, None)
        if error:
            raise error

    def login(self, auth_code, client_uid, device_name='', app_version=''):
        self.calls.append(('login', auth_code, client_uid))
        self.login_versions.append(app_version)
        self._maybe_raise('login')
        return self.login_result

    def fetch(self, token):
        self.calls.append(('fetch', token))
        self._maybe_raise('fetch')
        return self.fetch_result

    def ping(self, token):
        self.calls.append(('ping', token))
        self._maybe_raise('ping')
        return self.ping_result

    def push(self, token, version, layout, cameras):
        self.calls.append(('push', token, version, layout, cameras))
        self._maybe_raise('push')
        return self.push_result


class CloudSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        ini = lambda name: QSettings(os.path.join(folder.name, name), QSettings.Format.IniFormat)

        self.names = DeviceNames(ini('names.ini'))
        self.options = ConnectionOptions(ini('conn.ini'))
        self.store = CredentialStore(MemoryVault())
        self.session_store = CloudSessionStore(MemoryVault())
        self.settings = ini('cloud.ini')
        self.client = FakeClient()
        self.collected = {'version': 1, 'layout': {'capacity': 4}, 'cameras': []}

        self.sync = CloudSync(self.names, self.options, self.store,
                              collector=lambda: self.collected,
                              client=self.client, settings=self.settings,
                              session_store=self.session_store)
        self.addCleanup(self.sync.stop)

        self.statuses = []
        self.applications = []
        self.sync.status.connect(self.statuses.append)
        self.sync.applied.connect(self.applications.append)

    def pump(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        QApplication.processEvents()
        return predicate()

    def settled(self):
        return self.pump(lambda: not self.sync.busy() and not self.sync.calls)

    def change_locally(self):
        # 登录时落下了云端那份，collector 原样不动就等于「没改过」，不会传
        self.collected = dict(self.collected, layout={'capacity': 9})

    # ---------- 登录 ----------

    def test_login_stores_the_session_and_enables_sync(self):
        results = []
        self.sync.login_result.connect(lambda ok, msg: results.append((ok, msg)))

        self.sync.login('yg-1f-2026', device_name='前台')
        self.assertTrue(self.settled())

        self.assertTrue(results and results[0][0])
        self.assertTrue(self.sync.enabled())
        self.assertEqual('yg-1f-2026', self.session_store.load_session()['auth_code'])
        self.assertEqual('cm1.token', self.session_store.load_session()['token'])

    def test_login_with_a_populated_cloud_profile_pulls_it_down(self):
        self.client.login_result = dict(snapshot(cameras=[camera()]), token='cm1.token')

        self.sync.login('code12345')
        self.assertTrue(self.settled())

        self.assertTrue(self.applications)
        self.assertEqual(['10.0.0.1'], [d.ip for d in self.applications[-1].devices])
        self.assertEqual(('admin', 'pw'), self.store.load('10.0.0.1'))

    def test_login_with_an_empty_cloud_profile_uploads_this_computer(self):
        # 方向判定要看「这次真要上传的内容」：刚添加的摄像头还没写进 slot_order
        self.collected = {'version': 0, 'layout': {'capacity': 4},
                          'cameras': [{'ip': '192.168.1.5', 'slot_index': 0}]}
        self.client.login_result = dict(snapshot(cameras=[]), token='cm1.token')

        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.sync.push_now()
        self.assertTrue(self.settled())

        self.assertTrue(any(call[0] == 'push' for call in self.client.calls),
                        '云端为空而本机已有配置时，应当把本机这份传上去')

    def test_uploading_never_applies_the_empty_cloud_configuration_first(self):
        # 真机上抓到的：登录后本机三台摄像头被云端的空配置抹掉了。
        # 上传方向的本意就是保留本机这份，绝不能先 apply 一遍云端的空配置。
        self.collected = {'version': 0, 'layout': {'capacity': 4},
                          'cameras': [{'ip': '10.0.0.1', 'slot_index': 0},
                                      {'ip': '10.0.0.2', 'slot_index': 1}]}
        self.names.save('10.0.0.1', '大门')
        self.names.save_slot_order(['10.0.0.1', '10.0.0.2'])
        self.client.login_result = dict(snapshot(cameras=[]), token='cm1.token')

        self.sync.login('code12345')
        self.assertTrue(self.settled())

        self.assertEqual([], self.applications,
                         '上传方向不该把云端配置应用到本机')
        self.assertEqual('大门', self.names.get('10.0.0.1'), '本机名称必须原封不动')
        self.assertEqual(['10.0.0.1', '10.0.0.2'], self.names.slot_order(),
                         '本机画面顺序必须原封不动')

    def test_a_machine_with_cameras_but_no_saved_slot_order_still_counts_as_configured(self):
        self.assertEqual([], self.names.slot_order())
        self.collected = {'version': 0, 'layout': {'capacity': 4},
                          'cameras': [{'ip': '10.0.0.1', 'slot_index': 0}]}
        self.client.login_result = dict(snapshot(cameras=[]), token='cm1.token')
        messages = []
        self.sync.login_result.connect(lambda ok, m: messages.append(m))

        self.sync.login('code12345')
        self.assertTrue(self.settled())

        self.assertTrue(any('上传' in m for m in messages),
                        '墙上有画面就不该被当成空机器、反被云端的空配置覆盖')

    def test_a_rejected_code_reports_the_failure_and_stays_disabled(self):
        results = []
        self.sync.login_result.connect(lambda ok, msg: results.append((ok, msg)))
        self.client.raises['login'] = CloudAuthError('授权码不正确', 'INVALID_AUTH_CODE')

        self.sync.login('wrong123')
        self.assertTrue(self.settled())

        self.assertEqual([(False, '授权码不正确')], results)
        self.assertFalse(self.sync.enabled())

    def test_a_session_that_cannot_be_saved_still_connects_this_time(self):
        # 真机上抓到的：服务端登录已经成功、档案也绑定了，只因为本机钥匙串写不进去，
        # 客户端就整个报失败。现场会以为没连上，而云端其实已经把这台机器占住了。
        def refuse(auth_code, token):
            raise CredentialError('授权码未能保存到系统安全存储。')
        self.session_store.save_session = refuse
        results = []
        self.sync.login_result.connect(lambda ok, msg: results.append((ok, msg)))

        self.sync.login('code12345')
        self.assertTrue(self.settled())

        self.assertTrue(results and results[0][0], '存不住授权码不该把登录判成失败')
        self.assertTrue(self.sync.enabled(), '这一次仍然要能同步')
        self.assertTrue(any('重新输入授权码' in s for s in self.statuses),
                        '要讲清楚代价：下次启动得重新输一遍')

    # ---------- 启动与离线 ----------

    def test_startup_applies_the_local_cache_before_contacting_the_cloud(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.client.raises['fetch'] = CloudError('无法连接云端服务，请检查网络或稍后再试。')

        fresh = CloudSync(self.names, self.options, self.store, collector=lambda: self.collected,
                          client=self.client, settings=self.settings,
                          session_store=self.session_store)
        self.addCleanup(fresh.stop)
        offline = []
        fresh.applied.connect(offline.append)
        messages = []
        fresh.status.connect(messages.append)

        fresh.start()
        self.pump(lambda: any('无法连接' in m for m in messages))

        self.assertTrue(offline, '断网时也要把本机缓存铺开，监控墙不能空着')
        self.assertTrue(any('无法连接' in m for m in messages))

    def test_enabled_without_a_stored_session_falls_back_to_disconnected(self):
        # 设置说已启用、钥匙串里却没有会话：条目被删，或配置迁移到了新机器而钥匙串没跟过来。
        # 按钮必须如实显示未连接，否则会写着「退出云端 · 某档案」而其实根本没登录。
        self.settings.setValue('cloud/enabled', True)
        self.settings.setValue('cloud/profile_name', '一楼大厅')
        self.settings.sync()
        states = []
        self.sync.session_changed.connect(states.append)

        self.sync.start()

        self.assertEqual([False], states)
        self.assertFalse(self.sync.enabled())
        self.assertTrue(any('重新登录' in m for m in self.statuses))

    def test_an_unreadable_vault_reports_disconnected_without_wiping_the_setting(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())

        class Broken:
            def load_session(self):
                from camera_monitor.credentials import CredentialError
                raise CredentialError('无法读取云端登录信息，请重新输入授权码。')

        fresh = CloudSync(self.names, self.options, self.store, collector=lambda: self.collected,
                          client=self.client, settings=self.settings, session_store=Broken())
        self.addCleanup(fresh.stop)
        states, unavailable, applied = [], [], []
        fresh.session_changed.connect(states.append)
        fresh.storage_unavailable.connect(unavailable.append)
        fresh.applied.connect(applied.append)

        fresh.start()

        # 不发 session_changed(False)：那会被界面当成「登录失效」，托管电脑就此解绑回欢迎页。
        # 改发专门的信号，界面据此把按钮显示成未连接，托管电脑则保持锁定
        self.assertEqual([], states)
        self.assertEqual(['无法读取云端登录信息，请重新输入授权码。'], unavailable)
        self.assertTrue(fresh.enabled(), '安全存储可能只是暂时不可用，不该清掉设置')
        self.assertEqual('', fresh.token)
        self.assertTrue(applied, '离线优先：读不到会话也要先把本机缓存铺上，画面不能空着')

    # ---------- 安全存储暂时不可用后自动恢复 ----------

    def dispose(self, sync):
        """确定地拆掉测试里另建的 CloudSync。

        它发过请求的话，_retire 会对请求线程 deleteLater；测试不跑事件循环，这些延迟删除
        一直挂着。等 Python 这边随手释放了 sync，挂着的事件会在后面某个用例 processEvents
        时才处理，那时对象早已不在——表现是在无关用例里段错误。所以在这里收尾：
        先把在路上的结果跑完，停掉，再当场删掉并把延迟删除跑空。
        """
        self.pump(lambda: not sync.busy() and not sync.calls)
        sync.stop()
        sync.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

    def flaky_sync(self, failures, retry_ms=10, retry_max_ms=40):
        """前 failures 次读会话都失败，之后读得到。托管现场无人值守，必须自己缓过来。"""
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        real = self.session_store
        attempts = []

        class Flaky:
            def load_session(self):
                attempts.append(1)
                if len(attempts) <= failures:
                    raise CredentialError('无法读取云端登录信息，请重新输入授权码。')
                return real.load_session()

            def clear_session(self):
                real.clear_session()

            def save_session(self, auth_code, token):
                real.save_session(auth_code, token)

        fresh = CloudSync(self.names, self.options, self.store, collector=lambda: self.collected,
                          client=self.client, settings=self.settings, session_store=Flaky(),
                          storage_retry_ms=retry_ms, storage_retry_max_ms=retry_max_ms)
        self.addCleanup(self.dispose, fresh)
        self.client.calls.clear()
        return fresh, attempts

    def test_an_unreadable_vault_is_retried_until_the_session_comes_back(self):
        fresh, attempts = self.flaky_sync(failures=2)
        states = []
        fresh.session_changed.connect(states.append)

        fresh.start()
        self.assertEqual('', fresh.token)
        self.assertTrue(self.pump(lambda: fresh.token == 'cm1.token'))
        self.assertTrue(self.pump(lambda: any(c[0] == 'fetch' for c in self.client.calls)))

        self.assertEqual(3, len(attempts))
        self.assertEqual([True], states, '恢复后和正常登录一样宣布已连接，界面据此接通下行通道')
        self.assertTrue(fresh.poll_timer.isActive())
        self.assertFalse(fresh.storage_retry_timer.isActive())

    def test_retries_back_off_up_to_a_ceiling(self):
        fresh, _ = self.flaky_sync(failures=99, retry_ms=1000, retry_max_ms=5000)

        fresh.start()
        delays = [fresh.storage_retry_timer.interval()]
        for _ in range(4):
            fresh._retry_storage()
            delays.append(fresh.storage_retry_timer.interval())

        self.assertEqual([1000, 2000, 4000, 5000, 5000], delays)
        self.assertTrue(fresh.storage_retry_timer.isActive())

    def test_the_default_retry_starts_at_half_a_minute_and_caps_at_five(self):
        fresh, _ = self.flaky_sync(failures=99, retry_ms=None, retry_max_ms=None)
        self.assertEqual(30_000, fresh.storage_retry_ms)
        self.assertEqual(300_000, fresh.storage_retry_max_ms)

    def test_logging_out_stopping_or_logging_in_ends_the_retries(self):
        for action in ('logout', 'stop', 'login'):
            with self.subTest(action=action):
                fresh, _ = self.flaky_sync(failures=99, retry_ms=60_000)
                fresh.start()
                self.assertTrue(fresh.storage_retry_timer.isActive())

                if action == 'login':
                    fresh.login('new-code')
                else:
                    getattr(fresh, action)()

                self.assertFalse(fresh.storage_retry_timer.isActive())
                self.assertTrue(self.pump(lambda: not fresh.busy() and not fresh.calls))

    def test_a_retry_that_fires_after_logout_does_nothing(self):
        fresh, attempts = self.flaky_sync(failures=1, retry_ms=60_000)
        fresh.start()
        fresh.logout()
        before = len(attempts)

        fresh._retry_storage()

        self.assertEqual(before, len(attempts))
        self.assertEqual('', fresh.token)

    def test_a_network_failure_keeps_the_session(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.client.raises['ping'] = CloudError('连接云端超时，请检查网络后重试。')

        self.sync.check_for_updates()
        self.assertTrue(self.settled())

        self.assertTrue(self.sync.enabled(), '网络问题不该把人踢下线')
        self.assertTrue(self.sync.token)

    # ---------- 轮询与提交 ----------

    def test_a_newer_cloud_version_triggers_a_full_fetch(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.client.calls.clear()
        self.client.ping_result = {'version': 99, 'profile_name': '一楼大厅'}
        self.client.fetch_result = snapshot(version=99, cameras=[camera(ip='10.0.0.7')])

        self.sync.check_for_updates()
        self.assertTrue(self.pump(lambda: any(c[0] == 'fetch' for c in self.client.calls)))
        self.assertTrue(self.settled())

        self.assertEqual(99, self.sync.version())
        self.assertEqual(['10.0.0.7'], [d.ip for d in self.applications[-1].devices])

    def test_the_same_version_does_not_refetch(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.client.calls.clear()

        self.sync.check_for_updates()
        self.assertTrue(self.settled())

        self.assertEqual(['ping'], [c[0] for c in self.client.calls])

    def test_pushes_are_debounced_rather_than_sent_per_change(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.change_locally()
        self.client.calls.clear()

        for _ in range(5):
            self.sync.schedule_push()

        self.assertTrue(self.pump(lambda: any(c[0] == 'push' for c in self.client.calls), timeout=6))
        self.assertTrue(self.settled())
        self.assertEqual(1, sum(1 for c in self.client.calls if c[0] == 'push'),
                         '连续五次改动只应触发一次上传')

    def test_a_version_conflict_adopts_the_configuration_the_server_returned(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.change_locally()
        latest = snapshot(version=50, cameras=[camera(ip='10.0.0.50')])
        self.client.raises['push'] = CloudConflict('配置已在别处更新', latest)

        self.sync.push_now()
        self.assertTrue(self.settled())

        self.assertEqual(50, self.sync.version())
        self.assertEqual(['10.0.0.50'], [d.ip for d in self.applications[-1].devices])
        self.assertTrue(any('已在别处更新' in m for m in self.statuses))

    # ---------- 下发之后的去重 ----------

    def follow_applied(self):
        """和真窗口一样：云端配置落下来之后，collector 读到的就是刚落下的那份。"""
        self.sync.applied.connect(lambda result: setattr(self, 'collected', local(result)))

    def pushes(self):
        return [call for call in self.client.calls if call[0] == 'push']

    def upload(self, uploaded):
        """登录后把 uploaded 传上去一次，服务端原样收下。"""
        self.follow_applied()
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.client.push_result = uploaded
        self.collected = local_of(uploaded)
        self.sync.push_now()
        self.assertTrue(self.settled())
        self.assertEqual(1, len(self.pushes()))

    def test_changing_back_to_the_last_upload_after_a_download_is_uploaded(self):
        # 上次传的是 A，后台改成了 B 并下发到本机；现场又改回 A，这次必须传上去，
        # 否则后台一直以为是 B
        uploaded = snapshot(version=2, cameras=[camera(ip='10.0.0.1')])
        self.upload(uploaded)
        self.client.fetch_result = snapshot(version=3, cameras=[camera(ip='10.0.0.2')])
        self.sync.refresh()
        self.assertTrue(self.settled())

        self.collected = local_of(uploaded)
        self.sync.push_now()
        self.assertTrue(self.settled())

        self.assertEqual(2, len(self.pushes()), '改回上次传过的内容也是一次真改动')

    def test_a_download_with_nothing_changed_locally_is_not_sent_back(self):
        self.upload(snapshot(version=2, cameras=[camera(ip='10.0.0.1')]))
        self.client.fetch_result = snapshot(version=3, cameras=[camera(ip='10.0.0.2')])
        self.sync.refresh()
        self.assertTrue(self.settled())

        self.sync.push_now()
        self.assertTrue(self.settled())

        self.assertEqual(1, len(self.pushes()), '本机这份就是云端刚下发的，传回去只会白白加版本号')

    def test_a_relogin_download_also_resets_what_counts_as_a_duplicate(self):
        uploaded = snapshot(version=2, cameras=[camera(ip='10.0.0.1')])
        self.upload(uploaded)
        self.client.login_result = dict(snapshot(version=3, cameras=[camera(ip='10.0.0.2')]),
                                        token='cm1.token')
        self.sync.login('code12345')
        self.assertTrue(self.settled())

        self.collected = local_of(uploaded)
        self.sync.push_now()
        self.assertTrue(self.settled())

        self.assertEqual(2, len(self.pushes()))

    def test_a_conflict_download_is_not_sent_back_unchanged(self):
        self.upload(snapshot(version=2, cameras=[camera(ip='10.0.0.1')]))
        self.client.raises['push'] = CloudConflict('配置已在别处更新',
                                                   snapshot(version=9, cameras=[camera(ip='10.0.0.9')]))
        self.collected = local_of(snapshot(cameras=[camera(ip='10.0.0.3')]))
        self.sync.push_now()
        self.assertTrue(self.settled())

        self.sync.push_now()
        self.assertTrue(self.settled())

        self.assertEqual(2, len(self.pushes()), '被顶回来的那份就是云端现在的样子')

    # ---------- 离线与占用 ----------

    def test_a_change_made_while_offline_is_resent_once_the_link_is_back(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.change_locally()
        self.client.raises['push'] = CloudError('无法连接云端服务，请检查网络或稍后再试。')

        self.sync.push_now()
        self.assertTrue(self.settled())
        self.assertTrue(self.sync.pending_changes, '断网时的改动必须记下来')
        self.assertTrue(any('自动补传' in m for m in self.statuses))

        self.client.calls.clear()
        self.sync.check_for_updates()
        self.assertTrue(self.pump(lambda: any(c[0] == 'push' for c in self.client.calls), timeout=6))
        self.assertTrue(self.settled())

        self.assertFalse(self.sync.pending_changes, '补传成功后标记要清掉')

    def test_changes_made_before_logging_in_are_not_pushed_anywhere(self):
        self.sync.schedule_push()

        self.assertFalse(self.sync.pending_changes, '没启用云同步时不该积压改动')
        self.assertEqual([], self.client.calls)

    def test_a_code_taken_by_another_computer_stops_syncing_and_says_so(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        states = []
        self.sync.session_changed.connect(states.append)
        self.client.raises['fetch'] = CloudAuthError(
            '该授权码已被「一楼值班台」占用，请在后台解绑后再使用。', 'PROFILE_IN_USE')

        self.sync.refresh()
        self.assertTrue(self.settled())

        self.assertEqual([False], states)
        self.assertFalse(self.sync.token, '被占用就不该继续用这个令牌')
        self.assertTrue(any('已被「一楼值班台」占用' in m for m in self.statuses))

    # ---------- 令牌过期 ----------

    def test_an_expired_token_triggers_a_silent_relogin(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.client.calls.clear()
        self.client.raises['fetch'] = CloudAuthError('登录已失效', 'INVALID_TOKEN')

        self.sync.refresh()
        self.assertTrue(self.pump(lambda: any(c[0] == 'login' for c in self.client.calls)))
        self.assertTrue(self.settled())

        self.assertTrue(self.sync.token, '应当用保存的授权码自动重登，不打扰现场')

    def test_the_silent_relogin_reports_the_app_version(self):
        self.sync.login('code12345', app_version=__version__)
        self.assertTrue(self.settled())
        self.client.login_versions.clear()
        self.client.raises['fetch'] = CloudAuthError('登录已失效', 'INVALID_TOKEN')

        self.sync.refresh()
        self.assertTrue(self.pump(lambda: self.client.login_versions))
        self.assertTrue(self.settled())

        # 后台靠版本号判断这台电脑能不能用傻瓜模式，自动重登也得带上
        self.assertEqual([__version__], self.client.login_versions)

    # ---------- 退出 ----------

    def test_logout_clears_the_session_but_leaves_local_settings_alone(self):
        self.sync.login('code12345')
        self.assertTrue(self.settled())
        self.names.save('10.0.0.1', '大门')

        self.sync.logout()

        self.assertFalse(self.sync.enabled())
        self.assertIsNone(self.session_store.load_session())
        self.assertEqual('大门', self.names.get('10.0.0.1'), '退出云端不该动本机配置')


if __name__ == '__main__':
    unittest.main()
