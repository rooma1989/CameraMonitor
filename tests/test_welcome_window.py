import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from camera_monitor.discovery import Device
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

    def test_the_content_underneath_is_disabled_while_the_page_is_up(self):
        window = make_window(self, fresh_install=True)

        self.assertFalse(window.split.isEnabled())
        self.assertFalse(window.settings_tab.isEnabled())
        self.assertFalse(window.fullscreen_shortcut.isEnabled())
        self.assertFalse(window.escape_shortcut.isEnabled())
        self.assertTrue(window.welcome.isEnabled(), '欢迎页自己不能被一起禁掉')

    def test_f11_cannot_lock_the_window_behind_the_page(self):
        window = make_window(self, fresh_install=True)
        window.show()
        self.app.processEvents()  # 窗口要先被激活，快捷键才会响应

        QTest.keyClick(window, Qt.Key.Key_F11)
        self.app.processEvents()

        self.assertFalse(window.presentation)
        self.assertFalse(window.isFullScreen())

        # 对照：欢迎页走了以后同一个按键必须能用，否则上面的断言证明不了什么
        window.welcome.alone.click()
        with patch.object(window, 'toggle_fullscreen') as toggle:
            window.fullscreen_shortcut.activated.disconnect()
            window.fullscreen_shortcut.activated.connect(toggle)
            QTest.keyClick(window, Qt.Key.Key_F11)
            self.app.processEvents()
        toggle.assert_called_once()

    def test_choosing_standalone_gives_the_keyboard_back(self):
        window = make_window(self, fresh_install=True)

        window.welcome.alone.click()

        self.assertTrue(window.split.isEnabled())
        self.assertTrue(window.settings_tab.isEnabled())
        self.assertTrue(window.fullscreen_shortcut.isEnabled())
        self.assertTrue(window.escape_shortcut.isEnabled())

    def test_a_successful_login_gives_the_keyboard_back(self):
        window = make_window(self, fresh_install=True)

        window.cloud_login_result(True, '已连接')

        self.assertTrue(window.split.isEnabled())
        self.assertTrue(window.fullscreen_shortcut.isEnabled())

    def test_tab_cannot_leave_the_page(self):
        window = make_window(self, fresh_install=True)
        window.show()
        self.app.processEvents()

        # 沿 Tab 顺序走一整圈：能拿到焦点的控件必须全都在欢迎页里。
        # 用“可 Tab 且启用”判断而不是真的按键，离屏平台下焦点不稳。
        w = window.welcome.alone
        for _ in range(500):
            w = w.nextInFocusChain()
            if w.isEnabled() and w.isVisibleTo(window) and w.focusPolicy() & Qt.FocusPolicy.TabFocus:
                self.assertTrue(window.welcome.isAncestorOf(w), f'焦点漏到了欢迎页之外：{w!r}')

    def test_settings_do_not_open_over_the_page(self):
        window = make_window(self, fresh_install=True)
        window.show()
        window.wall.add_device(Device('test-one'))
        player = window.wall.tiles[0].player

        window.show_settings(player)
        window.show_batch_settings()

        self.assertFalse(window.settings_panel.isVisible())
        self.assertTrue(window.welcome.isVisible())


if __name__ == '__main__':
    unittest.main()
