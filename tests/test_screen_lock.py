import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from PySide6.QtCore import QSettings, QTimer, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog
from camera_monitor.screen_lock import ScreenLock, UnlockDialog, PasswordSettingsDialog, request_unlock


class ScreenLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'lock.ini')
        self.settings = QSettings(self.path, QSettings.Format.IniFormat)
        self.lock = ScreenLock(self.settings)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_password_and_wrong_password(self):
        self.assertTrue(self.lock.verify('000000'))
        self.assertFalse(self.lock.verify(''))
        self.assertFalse(self.lock.verify('123456'))

    def test_change_is_authenticated_confirmed_and_persistent(self):
        for args in [('wrong', 'new-secret', 'new-secret'), ('000000', '', ''),
                     ('000000', 'new-secret', 'mismatch')]:
            with self.assertRaises(ValueError):
                self.lock.change_password(*args)
        self.assertTrue(self.lock.verify('000000'))
        self.lock.change_password('000000', 'new-secret', 'new-secret')
        other = ScreenLock(QSettings(self.path, QSettings.Format.IniFormat))
        self.assertFalse(other.verify('000000'))
        self.assertTrue(other.verify('new-secret'))
        contents = Path(self.path).read_text()
        self.assertNotIn('new-secret', contents)
        self.assertNotIn('000000', contents)

    def test_reset_requires_current_password(self):
        self.lock.change_password('000000', 'custom', 'custom')
        with self.assertRaises(ValueError):
            self.lock.reset_password('000000')
        self.assertTrue(self.lock.verify('custom'))
        self.lock.reset_password('custom')
        self.assertTrue(ScreenLock(QSettings(self.path, QSettings.Format.IniFormat)).verify('000000'))

    def test_corrupt_record_does_not_restore_default(self):
        self.settings.setValue(ScreenLock.KEY, 'broken')
        self.assertFalse(self.lock.verify('000000'))

    def test_cancel_and_wrong_password_do_not_accept_dialog(self):
        dialog = UnlockDialog(self.lock)
        dialog.password.setText('wrong')
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertTrue(dialog.error.text())
        dialog.reject()
        dialog.deleteLater()
        self.app.processEvents()
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        self.assertFalse(request_unlock(None, self.lock))

    def test_correct_password_accepts_dialog(self):
        def enter():
            dialog = QApplication.activeModalWidget()
            dialog.password.setText('000000')
            dialog.accept()
        QTimer.singleShot(0, enter)
        self.assertTrue(request_unlock(None, self.lock))

    def test_settings_dialog_change_and_reset(self):
        dialog = PasswordSettingsDialog(self.lock)
        dialog.current_password.setText('000000')
        dialog.new_password.setText('changed')
        dialog.confirm_password.setText('changed')
        dialog.change_button.click()
        self.assertTrue(self.lock.verify('changed'))
        self.assertEqual(dialog.current_password.text(), '')
        dialog.current_password.setText('wrong')
        dialog.reset_button.click()
        self.assertFalse(self.lock.verify('000000'))
        dialog.current_password.setText('changed')
        dialog.reset_button.click()
        self.assertTrue(self.lock.verify('000000'))
        dialog.close()

    def test_repeated_dialog_close_and_deferred_destruction(self):
        from shiboken6 import isValid
        for _ in range(12):
            dialog = UnlockDialog(self.lock)
            QTimer.singleShot(0, dialog.reject)
            self.assertEqual(dialog.exec(), QDialog.DialogCode.Rejected)
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertFalse(isValid(dialog))
