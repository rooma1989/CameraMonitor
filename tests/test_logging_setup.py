import logging
import os
import tempfile
import unittest

from camera_monitor import __version__
from camera_monitor.app import log_startup, setup_logging


class LoggingSetupTests(unittest.TestCase):
    """现场出了问题只能靠日志文件；测试一律写到临时目录，绝不碰用户目录下的真日志。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        root = logging.getLogger()
        level = root.level
        self.addCleanup(root.setLevel, level)

    def install(self, directory):
        handler = setup_logging(directory)
        if handler is not None:
            def remove():
                logging.getLogger().removeHandler(handler)
                handler.close()
            self.addCleanup(remove)
        return handler

    def read(self, handler):
        handler.flush()
        with open(handler.baseFilename, encoding='utf-8') as fh:
            return fh.read()

    def test_warnings_from_the_cloud_channel_land_in_the_file(self):
        handler = self.install(os.path.join(self.folder, 'logs'))

        logging.getLogger('camera_monitor.cloud_channel').warning('下行通道断开：%s', '超时')

        self.assertEqual(os.path.join(self.folder, 'logs', 'camera_monitor.log'), handler.baseFilename)
        content = self.read(handler)
        self.assertIn('camera_monitor.cloud_channel', content)
        self.assertIn('下行通道断开：超时', content)

    def test_the_file_rotates_instead_of_growing_forever(self):
        handler = self.install(self.folder)

        self.assertEqual(3, handler.backupCount)
        self.assertLessEqual(handler.maxBytes, 2 * 1024 * 1024)
        self.assertGreater(handler.maxBytes, 0)

    def test_startup_is_logged_with_the_version_and_how_it_was_launched(self):
        handler = self.install(self.folder)

        log_startup(['CameraMonitor', '--autostart'])
        log_startup(['CameraMonitor'])

        lines = [line for line in self.read(handler).splitlines() if __version__ in line]
        self.assertEqual(2, len(lines), lines)
        self.assertIn('开机自启', lines[0])
        self.assertNotIn('开机自启', lines[1])

    def test_an_unwritable_directory_means_no_file_rather_than_a_crash(self):
        blocker = os.path.join(self.folder, 'blocker')
        open(blocker, 'w').close()
        before = list(logging.getLogger().handlers)

        self.assertIsNone(self.install(os.path.join(blocker, 'logs')))
        self.assertEqual(before, logging.getLogger().handlers)


if __name__ == '__main__':
    unittest.main()
