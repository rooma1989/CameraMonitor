import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from camera_monitor.app import Window
from camera_monitor.credentials import CredentialStore
from camera_monitor.device_names import DeviceNames
from camera_monitor.discovery import Device
from test_credentials import MemoryVault


class FullscreenGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        names = DeviceNames(QSettings(self.tmp.name + '/prefs.ini', QSettings.Format.IniFormat))
        with patch('camera_monitor.app.interfaces', return_value=[]):
            self.window = Window(device_names=names)
        self.window.wall.credential_store = CredentialStore(MemoryVault())
        self.addCleanup(self.close_window)
        self.window.show()
        self.window.toggle_fullscreen()
        self.app.processEvents()

    def close_window(self):
        with patch('camera_monitor.app.request_unlock', return_value=True):
            self.window.close()
            self.app.processEvents()

    def assert_locked(self):
        self.assertTrue(self.window.presentation)
        self.assertTrue(self.window.isFullScreen())
        self.assertFalse(self.window.sidebar.isVisible())
        self.assertFalse(self.window.settings_panel.isVisible())
        self.assertFalse(self.window.settings_tab.isVisible())
        self.assertFalse(self.window.wall.toolbar_widget.isVisible())

    def deny_prompt(self, wrong, activate=True):
        def answer():
            dialog = QApplication.activeModalWidget()
            self.assertIsNotNone(dialog)
            if wrong:
                dialog.password.setText('incorrect')
                dialog.accept()
                self.assertTrue(dialog.isVisible())
                self.assert_locked()
            dialog.reject()
        if activate:
            self.window.activateWindow()
            self.window.setFocus()
            self.app.processEvents()
        QTimer.singleShot(0, answer)

    def test_cancel_and_wrong_password_on_escape_keep_fullscreen(self):
        for wrong in (False, True):
            self.deny_prompt(wrong)
            QTest.keyClick(self.window, Qt.Key.Key_Escape)
            self.app.processEvents()
            self.assert_locked()

    def test_cancel_and_wrong_password_on_f11_keep_fullscreen(self):
        for wrong in (False, True):
            self.deny_prompt(wrong)
            QTest.keyClick(self.window, Qt.Key.Key_F11)
            self.app.processEvents()
            self.assert_locked()

    def test_cancel_and_wrong_password_on_close_keep_fullscreen(self):
        for wrong in (False, True):
            self.deny_prompt(wrong)
            self.assertFalse(self.window.close())
            self.app.processEvents()
            self.assertTrue(self.window.isVisible())
            self.assert_locked()

    def test_native_show_normal_restores_fullscreen_before_prompt(self):
        # Native state changes queue the authentication prompt for the next event loop.
        for wrong in (False, True):
            self.window.showNormal()
            self.deny_prompt(wrong, activate=False)
            self.app.processEvents()
            self.assert_locked()

    def test_correct_password_exits_and_restores_chrome(self):
        def answer():
            dialog = QApplication.activeModalWidget()
            dialog.password.setText('000000')
            dialog.accept()
        QTimer.singleShot(0, answer)
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(self.window.presentation)
        self.assertFalse(self.window.isFullScreen())
        self.assertTrue(self.window.sidebar.isVisible())
        self.assertTrue(self.window.wall.toolbar_widget.isVisible())

    def test_focus_and_reorder_are_allowed_without_unlocking(self):
        wall = self.window.wall
        for ip in ('one', 'two'):
            wall.add_device(Device(ip))
        first, second = wall.tiles
        self.app.processEvents()
        with patch('camera_monitor.app.request_unlock') as prompt:
            QTest.mouseClick(first.player.surface, Qt.MouseButton.LeftButton)
            self.assertIs(wall.focused_tile, first)
            QTest.mouseClick(first.player.surface, Qt.MouseButton.LeftButton)
            self.assertIsNone(wall.focused_tile)
            self.assertTrue(wall.swap_tiles(first, second))
            self.assertEqual(wall.tiles, [second, first])
            prompt.assert_not_called()
        self.assert_locked()

    def test_device_and_password_settings_are_inaccessible(self):
        self.window.wall.add_device(Device('one'))
        tile = self.window.wall.tiles[0]
        tile.show_settings()
        with patch('camera_monitor.app.PasswordSettingsDialog') as dialog:
            self.window.show_password_settings()
            dialog.assert_not_called()
        self.assertFalse(tile.controls.isVisible())
        self.assert_locked()

    def test_batch_settings_are_inaccessible(self):
        self.window.wall.add_device(Device('one'))
        self.window.show_batch_settings()
        self.assert_locked()
