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
