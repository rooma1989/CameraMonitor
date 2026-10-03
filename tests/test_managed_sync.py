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
        self.sync.token = 'old-token'

        self.sync.relogin()
        self.assertTrue(self.settled())

        self.assertEqual('code-in-vault', self.client.calls[0][1])

    def test_switching_to_managed_drops_a_stale_pending_upload(self):
        self.sync.token = 'cm1.t'
        self.settings.setValue('cloud/version', 1)
        self.sync.pending_changes = True
        self.sync.push_timer.start()
        statuses = []
        self.sync.status.connect(statuses.append)

        self.sync._apply(managed())
        self.assertFalse(self.sync.pending_changes)
        self.assertFalse(self.sync.push_timer.isActive())

        self.sync.check_for_updates()
        self.assertTrue(self.settled())

        self.assertFalse(self.sync.pending_changes)
        self.assertFalse([s for s in statuses if '补传' in s], statuses)

    def test_concurrent_relogins_dispatch_one_login(self):
        self.session_store.save_session('code-in-vault', 'old-token')
        self.sync.token = 'old-token'

        self.sync.relogin()
        self.sync.relogin()
        self.sync.relogin()
        self.assertTrue(self.settled())

        self.assertEqual(1, self.kinds().count('login'))

    def test_relogin_is_allowed_again_after_success(self):
        self.session_store.save_session('code-in-vault', 'old-token')
        self.sync.token = 'old-token'

        self.sync.relogin()
        self.assertTrue(self.settled())
        self.sync.relogin()
        self.assertTrue(self.settled())

        self.assertEqual(2, self.kinds().count('login'))

    def test_relogin_is_allowed_again_after_failure(self):
        self.session_store.save_session('code-in-vault', 'old-token')
        self.sync.token = 'old-token'
        self.client.raises['login'] = CloudError('网络不通。', 'NETWORK')

        self.sync.relogin()
        self.assertTrue(self.settled())
        self.sync.relogin()
        self.assertTrue(self.settled())

        self.assertEqual(2, self.kinds().count('login'))

    def test_relogin_after_stop_dispatches_nothing(self):
        self.session_store.save_session('code-in-vault', 'old-token')
        self.sync.token = 'old-token'

        self.sync.stop()
        self.sync.relogin()

        self.assertEqual([], self.kinds())
        self.assertEqual([], self.sync.calls)

    def test_relogin_without_a_token_dispatches_nothing(self):
        self.session_store.save_session('code-in-vault', 'old-token')

        self.sync.relogin()

        self.assertEqual([], self.kinds())

    def test_mode_changed_handlers_run_inside_the_applying_guard(self):
        self.settings.setValue('cloud/enabled', True)
        self.sync.token = 'cm1.t'
        self.sync._apply(managed())
        self.sync.mode_changed.connect(lambda _mode: self.sync.schedule_push())

        self.sync._apply(snapshot(version=2))

        self.assertEqual('full', self.sync.mode())
        self.assertFalse(self.sync.pending_changes)
        self.assertFalse(self.sync.push_timer.isActive())


    # ---------- 永久拒绝 ----------

    def test_permanent_denials_announce_a_revocation(self):
        for code in ('PROFILE_DISABLED', 'PROFILE_IN_USE', 'INVALID_AUTH_CODE'):
            with self.subTest(code=code):
                revoked = []
                self.sync.revoked.connect(revoked.append)
                self.sync.token = 'cm1.t'
                self.client.raises['fetch'] = CloudError('不让用了。', code)

                self.sync.refresh()
                self.assertTrue(self.settled())

                self.assertEqual([code], revoked)
                self.assertFalse(self.sync.token)
                self.sync.revoked.disconnect()

    def test_network_failures_are_not_a_revocation(self):
        revoked = []
        self.sync.revoked.connect(revoked.append)
        self.sync.token = 'cm1.t'
        self.client.raises['fetch'] = CloudError('网络不通。', 'NETWORK')

        self.sync.refresh()
        self.assertTrue(self.settled())

        self.assertEqual([], revoked)

    def test_a_silent_relogin_that_is_refused_for_good_announces_a_revocation(self):
        # 令牌过期后静默重登撞上「已停用」：托管电脑没有侧边栏，不报出去就永远锁在旧画面上
        self.session_store.save_session('code-in-vault', 'old-token')
        self.sync.token = 'old-token'
        revoked = []
        self.sync.revoked.connect(revoked.append)
        self.client.raises['login'] = CloudError('该设备码已停用。', 'PROFILE_DISABLED')

        self.sync.relogin()
        self.assertTrue(self.settled())

        self.assertEqual(['PROFILE_DISABLED'], revoked)

    def test_a_manual_login_refused_is_not_a_revocation(self):
        revoked = []
        self.sync.revoked.connect(revoked.append)
        self.client.raises['login'] = CloudError('设备码不正确。', 'INVALID_AUTH_CODE')

        self.sync.login('code12345')
        self.assertTrue(self.settled())

        self.assertEqual([], revoked)

    # ---------- 换码 ----------

    def test_a_new_full_code_clears_a_leftover_managed_mode_and_uploads(self):
        # 上一次托管会话没退干净（钥匙串条目丢了），设置里还留着 managed
        self.settings.setValue('cloud/mode', 'managed')
        self.client.login_result = dict(snapshot(), cameras=[], token='cm1.token')

        self.sync.login('code12345')
        self.assertTrue(self.settled())

        self.assertEqual('full', self.sync.mode())
        self.assertTrue(self.sync.pending_changes)
        self.assertTrue(self.sync.push_timer.isActive())
        self.sync.push_timer.stop()
        self.sync.push_now()
        self.assertTrue(self.settled())
        self.assertIn('push', self.kinds())

    # ---------- 退出后到达的结果 ----------

    def test_a_fetch_that_lands_after_logout_is_ignored(self):
        self.settings.setValue('cloud/enabled', True)
        self.sync.token = 'cm1.t'
        applied = []
        self.sync.applied.connect(applied.append)
        self.client.fetch_result = managed(version=9)

        self.sync.refresh()
        self.sync.logout()
        self.assertTrue(self.settled())

        self.assertEqual([], applied)
        self.assertIsNone(self.sync.cached_snapshot())
        self.assertEqual('full', self.sync.mode())
        self.assertEqual(0, self.sync.version())

    def test_a_ping_or_push_that_lands_after_logout_is_ignored(self):
        self.settings.setValue('cloud/enabled', True)
        self.sync.token = 'cm1.t'
        self.client.ping_result = {'version': 99}
        applied = []
        self.sync.applied.connect(applied.append)

        self.sync.check_for_updates()
        self.sync.push_now()
        self.sync.logout()
        self.assertTrue(self.settled())

        self.assertNotIn('fetch', self.kinds(), '已退出，不该再去拉')
        self.assertEqual([], applied)
        self.assertIsNone(self.sync.cached_snapshot())


    def test_a_silent_relogin_that_lands_after_logout_is_ignored(self):
        # 维护人员解绑时静默重登还在路上：它晚到的成功结果不能把这台电脑又绑回去
        self.settings.setValue('cloud/enabled', True)
        self.session_store.save_session('code-in-vault', 'old-token')
        self.sync.token = 'old-token'
        self.client.login_result = dict(managed(), token='cm1.new')
        sessions = []
        self.sync.session_changed.connect(sessions.append)

        self.sync.relogin()
        self.sync.logout()
        self.assertTrue(self.settled())

        self.assertFalse(self.sync.enabled())
        self.assertFalse(self.sync.token)
        self.assertIsNone(self.session_store.load_session())
        self.assertEqual('full', self.sync.mode())
        self.assertIsNone(self.sync.cached_snapshot())
        self.assertNotIn(True, sessions)

    def test_a_manual_login_after_logout_is_honoured(self):
        self.settings.setValue('cloud/enabled', True)
        self.session_store.save_session('code-in-vault', 'old-token')
        self.sync.token = 'old-token'

        self.sync.relogin()
        self.sync.logout()
        self.sync.login('new-code')
        self.assertTrue(self.settled())

        self.assertTrue(self.sync.enabled())
        self.assertEqual('cm1.token', self.sync.token)
        self.assertEqual('new-code', self.session_store.load_session()['auth_code'])

    def test_a_failed_call_that_lands_after_logout_is_ignored(self):
        # 退出前发出的请求晚到一个「令牌失效」：不能再去静默重登、再报一次未连接
        self.settings.setValue('cloud/enabled', True)
        self.session_store.save_session('code-in-vault', 'old-token')
        self.sync.token = 'old-token'
        self.client.raises['fetch'] = CloudError('登录已失效', 'INVALID_TOKEN')
        sessions = []

        self.sync.refresh()
        self.sync.logout()
        self.sync.session_changed.connect(sessions.append)
        self.assertTrue(self.settled())

        self.assertEqual([], sessions)
        self.assertNotIn('login', self.kinds())

    def test_a_conflict_without_a_session_is_ignored(self):
        # 被别处占用后令牌已清掉（没走 logout）；这时晚到的冲突结果同样不能落到本机
        applied = []
        self.sync.applied.connect(applied.append)
        self.sync.token = ''

        self.sync._on_conflict('push', managed(version=7))

        self.assertEqual([], applied)
        self.assertIsNone(self.sync.cached_snapshot())
        self.assertEqual('full', self.sync.mode())


if __name__ == '__main__':
    unittest.main()
