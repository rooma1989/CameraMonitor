"""开机自启时的自动全屏：晚几秒再进、被系统挤出来就悄悄拉回去、压住任务栏。

现场（Windows 11）勾了开机自启和自动全屏，开机后界面是演示布局，窗口却是带标题栏的
最大化，任务栏也露着：登录时桌面、任务栏还在起，系统把窗口从全屏里挤了出来。
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from camera_monitor import app as app_module
from camera_monitor.device_names import DeviceNames
from camera_monitor.discovery import Device
from support import isolated_settings, make_window
from test_credentials import MemoryVault


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def settle(times=5):
    for _ in range(times):
        QApplication.processEvents()


class BootFullscreenCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = FakeClock()
        self.topmost = []

    def window(self, boot_launch=False, cameras=1, start_fullscreen=True):
        names = DeviceNames(isolated_settings(self)('names.ini'))
        names.settings.setValue('monitor/start_fullscreen', start_fullscreen)
        window = make_window(self, device_names=names, boot_launch=boot_launch, clock=self.clock,
                             topmost=lambda hwnd, on: self.topmost.append((hwnd, on)))
        window.thumbnails.request = lambda *args, **kwargs: None
        window.cloud_start_timer.stop()
        window.channel.start = lambda: None
        window.cloud.refresh = lambda: None
        for index in range(cameras):
            window.wall.add_device(Device(f'10.0.0.{index + 1}'))
        window.show()
        settle()
        return window

    def launch(self, window):
        window.start_cloud()
        settle()

    def kicked_out(self, window):
        # 系统把窗口挤出全屏：不经过 exit_fullscreen，authorized_exit 是 False
        window.showMaximized()
        settle(10)


class BootDelayTests(BootFullscreenCase):
    def test_a_boot_launch_waits_before_going_fullscreen(self):
        window = self.window(boot_launch=True)

        self.launch(window)

        self.assertFalse(window.presentation, '桌面和任务栏还在起，先别进全屏')
        self.assertTrue(window.auto_fullscreen_timer.isActive())
        self.assertEqual(app_module.BOOT_FULLSCREEN_DELAY_MS, window.auto_fullscreen_timer.interval())
        self.assertGreaterEqual(app_module.BOOT_FULLSCREEN_DELAY_MS, 3000)

        window.auto_fullscreen_timer.stop()
        window.auto_fullscreen()
        settle()
        self.assertTrue(window.presentation)
        self.assertTrue(window.isFullScreen())

    def test_a_manual_launch_goes_fullscreen_right_away(self):
        window = self.window(boot_launch=False)

        self.launch(window)

        self.assertTrue(window.presentation)
        self.assertTrue(window.isFullScreen())

    def test_boot_launch_is_read_from_the_command_line(self):
        self.assertTrue(app_module.launched_at_boot(['CameraMonitor.exe', '--autostart']))
        self.assertFalse(app_module.launched_at_boot(['CameraMonitor.exe']))

    def test_the_window_asks_the_command_line_by_default(self):
        with patch('camera_monitor.app.launched_at_boot', return_value=True):
            window = make_window(self)
        self.assertTrue(window.boot_launch)

        with patch('camera_monitor.app.launched_at_boot', return_value=False):
            window = make_window(self)
        self.assertFalse(window.boot_launch)


class GuardTests(BootFullscreenCase):
    def setUp(self):
        super().setUp()
        self.prompt = MagicMock(return_value=False)
        patcher = patch('camera_monitor.app.request_unlock', self.prompt)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_being_kicked_out_soon_after_is_undone_without_a_password(self):
        window = self.window()
        self.launch(window)
        self.clock.now += 30

        with self.assertLogs('camera_monitor.app', 'INFO') as logs:
            self.kicked_out(window)

        self.assertTrue(window.presentation)
        self.assertTrue(window.isFullScreen(), '系统挤出来的要悄悄拉回全屏')
        self.prompt.assert_not_called()
        self.assertTrue(any('重新全屏' in line for line in logs.output), logs.output)

    def test_after_the_guard_window_the_password_is_asked_again(self):
        window = self.window()
        self.launch(window)
        self.clock.now += app_module.FULLSCREEN_GUARD_SECONDS + 1

        self.kicked_out(window)

        self.prompt.assert_called()
        self.assertTrue(window.presentation)
        self.assertTrue(window.isFullScreen(), '密码没输对，照旧留在全屏')

    def test_a_manual_f11_gets_no_guard(self):
        window = self.window(start_fullscreen=False)
        self.launch(window)
        window.toggle_fullscreen()
        settle()

        self.kicked_out(window)

        self.prompt.assert_called()

    def test_an_authorized_exit_is_not_undone(self):
        window = self.window()
        self.launch(window)
        self.prompt.return_value = True

        self.assertTrue(window.exit_fullscreen())
        settle(10)
        window.check_fullscreen()
        settle()

        self.assertFalse(window.presentation)
        self.assertFalse(window.isFullScreen())

    def test_managed_mode_is_left_alone(self):
        window = self.window()
        self.launch(window)

        window.set_managed_fullscreen(False)
        settle(10)
        window.check_fullscreen()
        settle()

        self.assertFalse(window.isFullScreen(), '傻瓜模式全屏与否由远程页决定')
        self.prompt.assert_not_called()

    def test_a_check_reasserts_a_drop_nobody_reported(self):
        window = self.window()
        self.launch(window)
        shown = []
        window.isFullScreen = lambda: False
        window.showFullScreen = lambda: shown.append(1)

        window.check_fullscreen()

        self.assertEqual([1], shown)

    def test_a_check_after_the_guard_window_does_nothing(self):
        window = self.window()
        self.launch(window)
        self.clock.now += app_module.FULLSCREEN_GUARD_SECONDS + 1
        shown = []
        window.isFullScreen = lambda: False
        window.showFullScreen = lambda: shown.append(1)

        window.check_fullscreen()

        self.assertEqual([], shown)

    def test_checks_follow_auto_fullscreen(self):
        self.assertEqual((1000, 3000, 10000), app_module.FULLSCREEN_CHECKS_MS)
        window = self.window()
        checks = []
        window.check_fullscreen = lambda: checks.append(1)

        with patch('camera_monitor.app.FULLSCREEN_CHECKS_MS', (0, 20, 40)):
            self.launch(window)
        # Windows 测试机慢，最多等 5 秒
        for _ in range(250):
            if len(checks) == 3:
                break
            QTest.qWait(20)

        self.assertEqual(3, len(checks))

    def test_auto_fullscreen_and_reasserts_bring_the_window_to_the_front(self):
        window = self.window()
        raised, activated = [], []
        window.raise_ = lambda: raised.append(1)
        window.activateWindow = lambda: activated.append(1)

        self.launch(window)
        self.assertEqual((1, 1), (len(raised), len(activated)), '开机时窗口常常不在前台，压不住任务栏')

        self.kicked_out(window)
        self.assertEqual((2, 2), (len(raised), len(activated)))


class TopmostTests(BootFullscreenCase):
    def last(self):
        return self.topmost[-1] if self.topmost else None

    def test_presentation_fullscreen_is_topmost(self):
        window = self.window()

        self.launch(window)

        self.assertEqual((int(window.winId()), True), self.last())

    def test_a_manual_f11_is_topmost_too(self):
        window = self.window(start_fullscreen=False)
        self.launch(window)

        window.toggle_fullscreen()

        self.assertEqual((int(window.winId()), True), self.last())

    def test_an_authorized_exit_drops_it(self):
        window = self.window()
        self.launch(window)

        with patch('camera_monitor.app.request_unlock', return_value=True):
            self.assertTrue(window.exit_fullscreen())

        self.assertEqual((int(window.winId()), False), self.last())

    def test_a_reassert_puts_it_back(self):
        window = self.window()
        self.launch(window)
        count = len(self.topmost)

        with patch('camera_monitor.app.request_unlock', return_value=False):
            self.kicked_out(window)

        self.assertGreater(len(self.topmost), count)
        self.assertEqual((int(window.winId()), True), self.last())

    def test_managed_fullscreen_follows_the_cloud(self):
        window = self.window(start_fullscreen=False)
        wid = int(window.winId())

        # offscreen 的窗口状态是晚到的，每一步都要等它落定
        window.set_managed_fullscreen(True)
        self.assertEqual((wid, True), self.last())
        settle(10)
        window.set_managed_fullscreen(False)
        self.assertEqual((wid, False), self.last(), '傻瓜模式窗口状态不能压住别的程序')
        settle(10)
        window.set_managed_fullscreen(True)
        settle(10)
        window.leave_managed_window()
        self.assertEqual((wid, False), self.last())
        settle(10)

    def test_the_real_switch_is_the_win32_one(self):
        from camera_monitor import win_topmost
        window = make_window(self, topmost=None)
        self.assertIs(win_topmost.set_topmost, window.topmost)


class StateLogTests(BootFullscreenCase):
    def test_each_window_state_change_is_logged(self):
        window = self.window()
        self.launch(window)

        with patch('camera_monitor.app.request_unlock', return_value=False), \
             self.assertLogs('camera_monitor.app', 'INFO') as logs:
            self.kicked_out(window)

        lines = [line for line in logs.output if '窗口状态' in line]
        self.assertTrue(any('fullscreen → maximized' in line for line in lines), logs.output)
        self.assertTrue(all('presentation=' in line and 'managed_fullscreen=' in line
                            and 'authorized_exit=' in line for line in lines), lines)


if __name__ == '__main__':
    unittest.main()
