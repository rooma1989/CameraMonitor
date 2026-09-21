import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from PySide6.QtWidgets import QApplication, QDialog

from camera_monitor import cloud_panel
from camera_monitor.cloud_panel import CloudPanel


class ReentrantLoginTest(unittest.TestCase):
    """登录框用的是模态 exec()，它自己会跑一个嵌套事件循环。

    期间按钮还能再被触发（连点、辅助功能重发按键、定时器里发出的点击），
    于是 toggle() 会在自己还没返回时被再调用一次。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = CloudPanel()
        self.addCleanup(self.panel.deleteLater)
        self.opened = []
        self.codes = []
        self.panel.login_requested.connect(self.codes.append)

    def install_dialog(self, during_exec, result=QDialog.DialogCode.Accepted, code='YG1F-2026'):
        opened, panel = self.opened, self.panel

        class FakeDialog:
            def __init__(self, parent=None):
                opened.append(self)

            def exec(self):
                during_exec()
                return result

            def auth_code(self):
                return code

            def raise_(self):
                pass

            def activateWindow(self):
                pass

            def deleteLater(self):
                pass

        self.addCleanup(setattr, cloud_panel, 'CloudLoginDialog', cloud_panel.CloudLoginDialog)
        cloud_panel.CloudLoginDialog = FakeDialog

    def test_a_second_click_while_the_dialog_is_open_does_not_crash(self):
        self.install_dialog(during_exec=self.panel.toggle)

        self.panel.toggle()

        self.assertEqual(1, len(self.opened), '嵌套的那一次不该再开一个登录框')
        self.assertEqual(['YG1F-2026'], self.codes, '外层这一次仍要正常提交授权码')
        self.assertIsNone(self.panel.dialog)

    def test_the_panel_can_be_opened_again_after_the_dialog_closes(self):
        self.install_dialog(during_exec=lambda: None)

        self.panel.toggle()
        self.panel.toggle()

        self.assertEqual(2, len(self.opened))

    def test_a_cancelled_dialog_asks_for_nothing(self):
        self.install_dialog(during_exec=lambda: None, result=QDialog.DialogCode.Rejected)

        self.panel.toggle()

        self.assertEqual([], self.codes)
        self.assertIsNone(self.panel.dialog)

    def test_while_connected_the_button_logs_out_instead(self):
        logouts = []
        self.panel.logout_requested.connect(lambda: logouts.append(True))
        self.panel.set_connected(True, '一楼大厅')

        self.panel.toggle()

        self.assertEqual([True], logouts)
        self.assertIn('一楼大厅', self.panel.button.text())
