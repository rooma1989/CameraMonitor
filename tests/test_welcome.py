import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from camera_monitor.welcome import WelcomePage


class WelcomePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.page = WelcomePage()
        self.addCleanup(self.page.deleteLater)
        self.addCleanup(self.page.hide)
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

    def test_the_code_box_gets_focus_when_shown(self):
        # 模拟主窗口里的遮罩：焦点原本在别的控件上，欢迎页后来才显示出来
        host = QWidget()
        self.addCleanup(host.deleteLater)
        self.addCleanup(host.hide)
        other = QLineEdit(host)
        page = WelcomePage(host)
        page.hide()
        host.show()
        host.activateWindow()
        other.setFocus()
        QApplication.processEvents()
        self.assertTrue(other.hasFocus())

        page.show()
        QApplication.processEvents()

        self.assertTrue(page.code.hasFocus())

    def test_focus_returns_to_the_code_after_busy(self):
        self.page.code.setText('7k2m')
        # offscreen 下只有激活的窗口里控件才会真的拿到焦点
        self.page.show()
        self.page.activateWindow()
        QApplication.processEvents()
        self.page.start.setFocus()
        self.page.set_busy(True)
        self.page.set_busy(False)

        self.assertTrue(self.page.code.hasFocus())
        self.assertEqual('7k2m', self.page.code.selectedText(), '登录失败后能直接重输')

    def test_an_error_can_be_shown_and_cleared(self):
        self.page.show_error('授权码不正确')
        self.assertEqual('授权码不正确', self.page.error.text())

        self.page.show_error('')
        self.assertTrue(self.page.error.isHidden())


if __name__ == '__main__':
    unittest.main()
