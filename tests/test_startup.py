import os
import tempfile
import unittest

from PySide6.QtCore import QSettings

from camera_monitor import startup


class StartupRouteTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cloud = QSettings(os.path.join(folder.name, 'cloud.ini'), QSettings.Format.IniFormat)
        self.names = QSettings(os.path.join(folder.name, 'names.ini'), QSettings.Format.IniFormat)

    def route(self):
        return startup.startup_route(self.cloud, self.names)

    def test_a_fresh_computer_sees_the_welcome_page(self):
        self.assertEqual(startup.WELCOME, self.route())

    def test_the_default_screen_password_alone_is_not_a_previous_install(self):
        # ScreenLock 一构造就会写入初始密码，不能因此把新电脑当成老用户
        self.names.setValue('fullscreen/password', 'pbkdf2_sha256$...')

        self.assertEqual(startup.WELCOME, self.route())

    def test_a_logged_in_computer_goes_straight_to_the_cloud(self):
        self.cloud.setValue('cloud/enabled', True)

        self.assertEqual(startup.CLOUD, self.route())

    def test_choosing_standalone_is_remembered(self):
        startup.choose_standalone(self.cloud)

        self.assertEqual(startup.STANDALONE, self.route())

        startup.clear_standalone(self.cloud)
        self.assertEqual(startup.WELCOME, self.route())

    def test_an_upgraded_computer_skips_the_welcome_page_for_good(self):
        self.names.setValue('monitor/capacity', 9)

        self.assertEqual(startup.STANDALONE, self.route())
        self.assertTrue(self.cloud.value(startup.STANDALONE_KEY), '要记下来，下次不再判断')


if __name__ == '__main__':
    unittest.main()
