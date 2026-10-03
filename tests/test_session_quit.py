"""关机、注销时傻瓜模式必须放行退出。

在 Qt 6.8.3 上实测过（cocoa 平台、真实的 kAEQuitApplication 事件，退出原因是注销）：
macOS 的 ⌘Q、程序坞「退出」、注销、关机都走 applicationShouldTerminate，Qt 先发
commitDataRequest、再发 Quit 事件关所有窗口；Windows 的 WM_QUERYENDSESSION 也先发
commitDataRequest。所以只要在 commitDataRequest 里放行，两个平台都不会被托管窗口拦住。
测试跑在 offscreen 平台上，那里没有会话管理，这里只验证接线。
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from camera_monitor import app as app_module

from support import make_window
from test_credentials import MemoryVault


class FakeApp(QObject):
    # 真正的 QApplication 每个进程只能有一个，测试共用的那个不能拿来发假的会话信号
    commitDataRequest = Signal(object)


class SessionQuitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = make_window(self)
        self.window.set_managed_fullscreen(True)

    def test_a_managed_window_lets_the_session_end(self):
        fake = FakeApp()
        app_module.allow_session_quit(fake, self.window)
        refused = QCloseEvent()
        self.window.closeEvent(refused)
        self.assertFalse(refused.isAccepted())

        fake.commitDataRequest.emit(None)

        self.assertTrue(self.window.authorized_quit)
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())


class MainWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # main() 里要建 QIcon，得先有一个真的 QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_main_hooks_the_session_request_to_the_window(self):
        class FakeQApplication(FakeApp):
            def __init__(self, argv):
                super().__init__()
                created.append(self)

            def setApplicationName(self, name): pass
            def setWindowIcon(self, icon): pass
            def setStyle(self, style): pass
            def exec(self): return 0

        class FakeWindow:
            authorized_quit = False
            def show(self): pass

        created = []
        windows = []
        with patch.object(app_module, 'QApplication', FakeQApplication), \
                patch.object(app_module, 'Window', lambda: windows.append(FakeWindow()) or windows[-1]), \
                patch.object(app_module.sys, 'exit'):
            app_module.main()

        created[0].commitDataRequest.emit(None)

        self.assertTrue(windows[0].authorized_quit)


if __name__ == '__main__':
    unittest.main()
