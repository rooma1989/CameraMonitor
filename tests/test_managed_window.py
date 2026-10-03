import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from support import make_window
from test_cloud_sync import FakeClient, snapshot
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
        # 启动定时器会在 processEvents() 时跑 start_cloud，和测试自己摆的云端状态打架
        self.window.cloud_start_timer.stop()
        # 真去连服务器的话测试就依赖网络了：只记下启停
        self.channel_starts = []
        self.window.channel.start = lambda: self.channel_starts.append(1)

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
        QApplication.processEvents()

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
        QApplication.processEvents()

        self.window.on_channel_message({'type': 'revoked', 'failure_code': 'PROFILE_DISABLED'})

        self.assertFalse(self.window.welcome.isHidden())
        self.assertIn('联系管理员', self.window.welcome.error.text())
        self.assertFalse(self.window.cloud.enabled())
        self.assertFalse(self.window.managed.active)
        self.assertEqual('false', str(self.window.cloud.settings.value('cloud/standalone')).lower(),
                         '解绑后明确记为「要看欢迎页」，下次启动不再按旧配置判成单机')

    def test_being_revoked_over_the_channel_uses_the_specific_message(self):
        self.enter_managed()

        self.window.on_channel_message({'type': 'revoked', 'failure_code': 'PROFILE_IN_USE'})

        self.assert_fully_unbound('该设备码已在另一台电脑上使用，请联系管理员解绑后重试。')

    def test_being_revoked_over_the_channel_leaves_a_full_mode_computer_bound(self):
        # 完整模式有侧边栏：通道说收回了，只停通道，交给 HTTP 那边去问清楚、如实显示
        settings = self.window.cloud.settings
        settings.setValue('cloud/enabled', True)
        self.window.cloud.token = 'cm1.t'
        self.window.cloud._apply(snapshot())
        QApplication.processEvents()
        stops, wall_stops, refreshes = [], [], []
        self.window.channel.stop = lambda: stops.append(1)
        self.window.wall.stop_everything = lambda: wall_stops.append(1)
        self.window.cloud.refresh = lambda: refreshes.append(1)

        self.window.on_channel_message({'type': 'revoked', 'failure_code': 'PROFILE_IN_USE'})

        self.assertEqual([1], stops)
        self.assertEqual([1], refreshes, '要让 HTTP 拿到服务端原话显示在侧边栏')
        self.assertEqual([], wall_stops, '完整模式的画面不能因为通道一句话就停掉')
        self.assertTrue(self.window.cloud.enabled())
        self.assertEqual('cm1.t', self.window.cloud.token)
        self.assertTrue(self.window.welcome.isHidden())

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
        QApplication.processEvents()

        self.assertFalse(self.window.managed.active)
        self.assertFalse(self.window.welcome.isHidden())

    # ---------- 通道启停 ----------

    def broken_vault(self):
        from camera_monitor.credentials import CredentialError

        def refuse():
            raise CredentialError('无法读取云端登录信息，请重新输入授权码。')
        self.window.cloud.session.load_session = refuse

    def test_a_managed_computer_whose_vault_is_briefly_unreadable_stays_locked(self):
        # 钥匙串一时读不出来（刚开机、系统服务还没起来）不等于登录失效：
        # 托管电脑不能因此删掉开机自启、退回欢迎页，画面照缓存继续放
        settings = self.window.cloud.settings
        settings.setValue('cloud/enabled', True)
        self.window.cloud._remember(managed())
        settings.setValue('cloud/mode', 'managed')
        disabled = []
        self.window.managed.autostart.disable = lambda: disabled.append(1)
        self.broken_vault()

        self.window.start_cloud()
        QApplication.processEvents()

        self.assertTrue(self.window.managed.active)
        self.assertTrue(self.window.managed_fullscreen, '缓存里的全屏设置要照样落下去')
        self.assertTrue(self.window.welcome.isHidden())
        self.assertEqual([], disabled)
        self.assertTrue(self.window.cloud.enabled())
        self.assertEqual('managed', self.window.cloud.mode())
        self.assertEqual([], self.channel_starts)

    def test_a_full_mode_computer_whose_vault_is_unreadable_shows_disconnected(self):
        settings = self.window.cloud.settings
        settings.setValue('cloud/enabled', True)
        self.window.cloud._remember(snapshot())
        self.window.cloud_panel.set_connected(True, '一楼大厅')
        self.broken_vault()

        self.window.start_cloud()
        QApplication.processEvents()

        self.assertFalse(self.window.cloud_panel.connected, '按钮要如实显示未连接')
        self.assertIn('无法读取', self.window.cloud_panel.status.text())
        self.assertTrue(self.window.welcome.isHidden())
        self.assertTrue(self.window.cloud.enabled())

    def test_a_new_session_starts_the_channel_and_losing_it_stops_it(self):
        stops = []
        self.window.channel.stop = lambda: stops.append(1)

        self.window.cloud_session_changed(True)
        self.window.cloud_session_changed(False)

        self.assertEqual([1], self.channel_starts)
        self.assertEqual([1], stops)

    def test_starting_with_a_saved_session_opens_the_channel(self):
        self.window.cloud.start = lambda: setattr(self.window.cloud, 'token', 'cm1.t')

        self.window.start_cloud()

        self.assertEqual([1], self.channel_starts)

    def test_starting_without_a_session_leaves_the_channel_closed(self):
        self.window.cloud.start = lambda: None

        self.window.start_cloud()

        self.assertEqual([], self.channel_starts)

    # ---------- 被别处顶掉 ----------

    def test_being_replaced_stops_the_channel_and_says_why(self):
        stops = []
        self.window.channel.stop = lambda: stops.append(1)

        self.window.channel.replaced.emit()

        self.assertEqual([1], stops)
        self.assertIn('另一台电脑', self.window.cloud_panel.status.text())

    def test_a_silent_relogin_after_being_replaced_does_not_reconnect(self):
        # 静默重登也会发 session_changed(True)；这时重连会把另一台顶掉，两边来回抢
        self.window.channel.replaced.emit()

        self.window.cloud_session_changed(True)

        self.assertEqual([], self.channel_starts)

    def test_logging_in_again_by_hand_reconnects_after_being_replaced(self):
        self.window.channel.replaced.emit()
        self.window.cloud.login = lambda *args, **kwargs: None

        self.window.cloud_login('ABCD-1234')
        self.window.cloud_session_changed(True)

        self.assertEqual([1], self.channel_starts)

    def test_a_failed_manual_login_keeps_the_channel_away(self):
        self.window.channel.replaced.emit()
        self.window.cloud.login = lambda *args, **kwargs: None

        self.window.cloud_login('WRONG-CODE')
        self.window.cloud._on_failed('login', '设备码不正确。', 'INVALID_AUTH_CODE')
        # 之后的静默重登成功，同样不能把另一台顶掉
        self.window.cloud_session_changed(True)

        self.assertEqual([], self.channel_starts)

    def test_being_replaced_while_managed_shows_it_on_the_wall(self):
        self.window.cloud._apply(managed())
        QApplication.processEvents()

        self.window.channel.replaced.emit()

        banner = self.window.managed.offline
        self.assertFalse(banner.isHidden())
        self.assertIn('另一台电脑', banner.text())
        self.assertFalse(self.window.managed.offline_timer.isActive())

    # ---------- 后台永久拒绝 ----------

    def enter_managed(self):
        settings = self.window.cloud.settings
        settings.setValue('cloud/enabled', True)
        self.window.cloud.session.save_session('ABCD-1234', 'cm1.t')
        self.window.cloud.token = 'cm1.t'
        self.window.cloud._apply(managed())
        self.window.cloud._remember(managed())
        QApplication.processEvents()
        self.assertTrue(self.window.managed.active)

    def assert_fully_unbound(self, message):
        settings = self.window.cloud.settings
        self.assertFalse(self.window.managed.active)
        self.assertFalse(self.window.cloud.enabled(), '不彻底解绑的话下次启动又锁回旧画面')
        self.assertEqual('full', self.window.cloud.mode())
        self.assertIsNone(self.window.cloud.cached_snapshot())
        self.assertIsNone(self.window.cloud.session.load_session())
        self.assertFalse(self.window.welcome.isHidden())
        self.assertEqual(message, self.window.welcome.error.text())
        self.assertEqual('false', str(settings.value('cloud/standalone')).lower())

    def test_a_silent_relogin_that_lands_after_unbinding_does_not_rebind(self):
        self.enter_managed()
        client = FakeClient()
        client.login_result = dict(managed(), token='cm1.new')
        self.window.cloud.client = client

        self.window.cloud.relogin()
        self.window.unbind_cloud()
        self.assertTrue(self.settle())

        self.assertFalse(self.window.managed.active)
        self.assertFalse(self.window.cloud.enabled())
        self.assertEqual('full', self.window.cloud.mode())
        self.assertIsNone(self.window.cloud.session.load_session())
        self.assertFalse(self.window.welcome.isHidden())

    def settle(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if not self.window.cloud.busy() and not self.window.cloud.calls:
                return True
            time.sleep(0.01)
        return False

    def test_permanent_http_denials_unbind_a_managed_computer(self):
        cases = {
            'PROFILE_DISABLED': '该设备码已被管理员停用，请联系管理员。',
            'PROFILE_IN_USE': '该设备码已在另一台电脑上使用，请联系管理员解绑后重试。',
            'INVALID_AUTH_CODE': '设备码已失效，请重新输入。',
        }
        for code, message in cases.items():
            with self.subTest(code=code):
                self.enter_managed()

                self.window.cloud._on_failed('fetch', '服务端原话', code)

                self.assert_fully_unbound(message)
                # 离开全屏的窗口状态 offscreen 下会晚到，不等它落定就再进托管会乱
                QApplication.processEvents()
                self.window.set_welcome_visible(False)

    def test_a_refused_silent_relogin_unbinds_a_managed_computer(self):
        self.enter_managed()
        self.window.cloud._relogin_pending = 'ABCD-1234'

        self.window.cloud._on_failed('login', '服务端原话', 'PROFILE_DISABLED')

        self.assert_fully_unbound('该设备码已被管理员停用，请联系管理员。')

    def test_permanent_channel_denials_unbind_a_managed_computer(self):
        self.enter_managed()

        self.window.on_channel_denied('PROFILE_IN_USE')

        self.assert_fully_unbound('该设备码已在另一台电脑上使用，请联系管理员解绑后重试。')

    def test_permanent_denials_leave_a_full_mode_computer_bound(self):
        # 完整模式有侧边栏可以重新登录，维持原来的处理：停同步、按钮显示未连接
        settings = self.window.cloud.settings
        settings.setValue('cloud/enabled', True)
        self.window.cloud.token = 'cm1.t'

        self.window.cloud._on_failed('fetch', '服务端原话', 'PROFILE_DISABLED')

        self.assertTrue(self.window.cloud.enabled())
        self.assertTrue(self.window.welcome.isHidden())

    # ---------- 退出 ----------

    def test_closing_stops_the_channel_before_waiting_for_cloud_calls(self):
        order = []
        self.window.channel.stop = lambda: order.append('channel')
        cloud_stop = self.window.cloud.stop
        self.window.cloud.stop = lambda: (order.append('cloud'), cloud_stop())

        self.window.close()

        self.assertEqual(['channel', 'cloud'], order[:2])

    def test_closing_stops_the_snapshot_uploader(self):
        self.window.close()

        self.assertFalse(self.window.snapshot_uploader.submit('screen', self.window.grab().toImage()))


if __name__ == '__main__':
    unittest.main()
