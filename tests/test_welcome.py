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
