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
