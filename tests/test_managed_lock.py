import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest

from PySide6.QtCore import QSettings

from camera_monitor.screen_lock import ScreenLock

# 盐 = 00..0f，密码 = 246810，迭代 240000。yunqi 的 PHP 测试里有同一条。
VECTOR = ('pbkdf2_sha256$240000$000102030405060708090a0b0c0d0e0f$'
          '6779a48d726cc20519aa5f663a475da180d9ddcf213ec794eef28f7ed9f9bc4e')


class CloudEscapePasswordTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = QSettings(os.path.join(folder.name, 'lock.ini'), QSettings.Format.IniFormat)
        self.lock = ScreenLock(self.settings)

    def test_a_cloud_record_is_adopted_verbatim(self):
        self.assertTrue(self.lock.set_record(VECTOR))

        self.assertTrue(self.lock.verify('246810'))
        self.assertFalse(self.lock.verify('000000'))
        self.assertEqual(VECTOR, self.settings.value(ScreenLock.KEY))

    def test_malformed_records_are_refused_and_the_local_password_survives(self):
        for bad in ('', 'md5$1$aa$bb', 'pbkdf2_sha256$1000$00$11', VECTOR[:-2], None):
            self.assertFalse(self.lock.set_record(bad), bad)
        self.assertTrue(self.lock.verify('000000'))

    def test_a_record_that_cannot_be_written_is_reported_and_the_old_password_survives(self):
        folder = tempfile.TemporaryDirectory()

        def restore():
            os.chmod(folder.name, 0o755)
            path = os.path.join(folder.name, 'ro.ini')
            if os.path.exists(path):
                os.chmod(path, 0o644)
            folder.cleanup()

        self.addCleanup(restore)
        path = os.path.join(folder.name, 'ro.ini')
        settings = QSettings(path, QSettings.Format.IniFormat)
        lock = ScreenLock(settings)  # 先写下默认密码，再把文件变成只读
        settings.sync()
        os.chmod(path, 0o444)
        os.chmod(folder.name, 0o555)

        self.assertFalse(lock.set_record(VECTOR))

        fresh = ScreenLock(QSettings(path, QSettings.Format.IniFormat))
        self.assertTrue(fresh.verify('000000'))
        self.assertFalse(fresh.verify('246810'))


if __name__ == '__main__':
    unittest.main()
