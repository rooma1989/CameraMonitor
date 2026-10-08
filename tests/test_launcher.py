"""打包冒烟测试（build-windows.bat / build-macos.sh 在打好的程序上跑）不能碰这台电脑的真东西。

它跑在打包机上，用的是真窗口：不隔离的话，会读写真实的设置、把开机启动项按设置删掉或补上，
设置里勾了「启动后自动全屏」还会一打开就全屏。
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from test_credentials import MemoryVault

LAUNCHER = Path(__file__).resolve().parent.parent / 'packaging' / 'launcher.py'


def load_launcher():
    spec = importlib.util.spec_from_file_location('camera_monitor_launcher', LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SmokeTestIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        # Windows 上冒烟测试会检查系统凭据库的 priority（keyring 后端都有），假的也得有
        vault.priority = 1
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 真的 AutoStart 一构造出来就算失败：冒烟测试必须换成什么都不做的那个
        patcher = patch('camera_monitor.app.AutoStart', side_effect=AssertionError('碰了真的启动项'))
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = os.path.realpath(folder.name)
        self.launcher = load_launcher()
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.cloud.stop()
            window.channel.stop()
            window.managed.status_timer.stop()
            window.thumbnail_timer.stop()
            window.thumbnails.cancel_all()
            window.hide()
            window.setParent(None)
            window.deleteLater()
        QApplication.processEvents()

    def build(self, folder):
        window = self.real_smoke_window(folder)
        self.windows.append(window)
        return window

    def inside(self, path):
        return os.path.realpath(path).startswith(self.folder + os.sep)

    def test_the_smoke_window_keeps_its_settings_in_the_given_folder(self):
        self.real_smoke_window = self.launcher.smoke_window
        window = self.build(self.folder)

        for settings in (window.device_names.settings, window.cloud.settings,
                         window.connection_options.settings, window.screen_lock.settings):
            self.assertTrue(self.inside(settings.fileName()), settings.fileName())

    def test_starting_it_touches_neither_the_startup_entry_nor_fullscreen(self):
        self.real_smoke_window = self.launcher.smoke_window
        window = self.build(self.folder)

        window.start_cloud()
        for _ in range(3):
            QApplication.processEvents()

        self.assertFalse(window.autostart.is_enabled())
        self.assertFalse(window.presentation)
        self.assertEqual([], window.wall.tiles)

    def test_the_smoke_test_builds_its_window_that_way(self):
        built = []
        self.real_smoke_window = self.launcher.smoke_window

        def recording(folder):
            built.append(folder)
            return self.build(folder)

        result = Path(self.folder) / 'result.txt'
        with patch.object(self.launcher, 'smoke_window', recording):
            self.launcher.smoke_test(str(result))

        self.assertEqual(1, len(built))
        self.assertEqual('ok', result.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
