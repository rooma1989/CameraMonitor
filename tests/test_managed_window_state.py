import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QWindowStateChangeEvent
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
        # 先让进全屏这一步落定。offscreen 平台会把每次请求的窗口状态排队、事后逐个
        # 回放；进出全屏挤在同一轮事件循环里时，迟到的"全屏"回放会落在已经退出傻瓜
        # 模式的窗口上，被当成用户自己按了全屏、再弹密码框。真实平台上云端切模式
        # 不会在同一轮事件循环里发生。
        for _ in range(5):QApplication.processEvents()

        with patch('camera_monitor.app.request_unlock', return_value=False) as prompt:
            self.window.leave_managed_window()
            for _ in range(5):QApplication.processEvents()

        prompt.assert_not_called()
        self.assertIsNone(self.window.managed_fullscreen)
        self.assertFalse(self.window.presentation)
        self.assertFalse(self.window.isFullScreen())
        self.assertTrue(self.window.sidebar.isVisible())

    def test_closing_the_welcome_page_does_not_bring_back_the_password_prompt(self):
        # 欢迎页收起时会重新启用 Esc / F11 快捷键；傻瓜模式下它们仍然不能弹密码框
        self.window.set_managed_fullscreen(True)
        self.window.set_welcome_visible(True)
        self.window.set_welcome_visible(False)

        # 信号触发的槽里抛异常会被 PySide 吞掉，所以不能靠 side_effect 报错，而是放行
        # 并记账：真弹了密码框，窗口就会退出演示，下面两条断言都会失败
        with patch('camera_monitor.app.request_unlock', return_value=True) as prompt:
            self.window.escape_shortcut.activated.emit()
            self.window.fullscreen_shortcut.activated.emit()
            self.window.native_exit_requested()
            QApplication.processEvents()

        prompt.assert_not_called()
        self.assertTrue(self.window.presentation)
        self.assertTrue(self.window.managed_fullscreen)

    def test_a_queued_native_exit_does_not_force_managed_windowed_mode_fullscreen(self):
        # 用户自己的演示模式里系统退出全屏会排队一个 native_exit_requested；
        # 它执行前云端切成了"窗口"，就不该再被拉回全屏、也不该弹密码
        self.window.set_managed_fullscreen(False)

        with patch('camera_monitor.app.request_unlock', return_value=False) as prompt:
            self.window.native_exit_requested()

        prompt.assert_not_called()
        self.assertFalse(self.window.isFullScreen())

    def deliver_late_state_change(self):
        # macOS 上窗口状态变化是异步回来的：authorized_exit 早已复位，事件才到
        self.window.changeEvent(QWindowStateChangeEvent(Qt.WindowState.WindowFullScreen))
        for _ in range(5):QApplication.processEvents()

    def test_a_late_state_change_in_managed_windowed_mode_does_not_force_fullscreen(self):
        self.window.set_managed_fullscreen(True)
        self.window.set_managed_fullscreen(False)
        calls = []
        self.window.showFullScreen = lambda: calls.append(1)

        with patch('camera_monitor.app.request_unlock', side_effect=AssertionError('不该弹密码框')):
            self.deliver_late_state_change()

        self.assertEqual([], calls)
        self.assertFalse(self.window.isFullScreen())
        self.assertTrue(self.window.presentation)

    def test_a_managed_fullscreen_window_pulled_out_of_fullscreen_goes_back_once(self):
        self.window.set_managed_fullscreen(True)
        self.window.showNormal()  # 模拟系统把窗口拉出了全屏
        for _ in range(5):QApplication.processEvents()

        self.assertTrue(self.window.isFullScreen())
        calls = []
        real = self.window.showFullScreen
        self.window.showFullScreen = lambda: (calls.append(1), real())

        with patch('camera_monitor.app.request_unlock', side_effect=AssertionError('不该弹密码框')):
            self.deliver_late_state_change()

        # 已经是全屏了，迟到的事件不该再触发任何 showFullScreen
        self.assertEqual([], calls)
        self.assertTrue(self.window.isFullScreen())


if __name__ == '__main__':
    unittest.main()
