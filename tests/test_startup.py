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

    def test_a_screen_password_from_an_earlier_version_counts_as_used_before(self):
        # 以前认为这个键不算数，因为 ScreenLock 一构造就会写入初始密码。但判断只做一次，
        # 而且 Window 在构造 ScreenLock 之前就判断完了，所以本次进程写的密码根本看不到；
        # 看得到它，说明这台电脑以前（v0.8 及更早）打开过本程序——那时一启动就写下初始
        # 密码。哪怕从没存过名称、分屏，也是老用户，升级后不该突然冒出欢迎页
        self.names.setValue('fullscreen/password', 'pbkdf2_sha256$...')

        self.assertEqual(startup.STANDALONE, self.route())

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
        self.assertEqual('true', str(self.cloud.value(startup.STANDALONE_KEY)).lower(), '要记下来，下次不再判断')

    def test_foreign_keys_from_the_platform_are_not_a_previous_install(self):
        # macOS 的 QSettings 会把系统全局键（AppleLanguages 等）也算进 allKeys()
        self.names.setValue('AppleLanguages', ['zh-Hans-CN'])

        self.assertEqual(startup.WELCOME, self.route())

    def test_unbinding_a_legacy_computer_returns_to_the_welcome_page(self):
        # 解绑后相机配置有意保留，名称键还在，但这次必须回到欢迎页
        self.names.setValue('monitor/capacity', 9)
        self.assertEqual(startup.STANDALONE, self.route())

        self.assertTrue(startup.clear_standalone(self.cloud))

        self.assertEqual(startup.WELCOME, self.route())

    def test_the_fresh_computer_decision_is_remembered(self):
        self.assertEqual(startup.WELCOME, self.route())
        self.assertEqual('false', str(self.cloud.value(startup.STANDALONE_KEY)).lower())

        self.names.setValue('monitor/capacity', 9)  # 之后写入的名称键不能把它翻成单机

        self.assertEqual(startup.WELCOME, self.route())

    def test_each_legacy_group_counts_as_used_before(self):
        for key in ('names/192.168.1.2', 'appearance/192.168.1.2/color', 'monitor/order'):
            with self.subTest(key=key):
                self.cloud.remove(startup.STANDALONE_KEY)
                self.names.clear()
                self.names.setValue(key, 'x')
                self.assertEqual(startup.STANDALONE, self.route())

    def test_choose_and_clear_report_success(self):
        self.assertTrue(startup.choose_standalone(self.cloud))
        self.assertTrue(startup.clear_standalone(self.cloud))

    def test_choose_and_clear_report_write_failure(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        # 父目录不存在又建不出来（父路径是个文件），同步必然失败
        blocker = os.path.join(folder.name, 'blocker')
        open(blocker, 'w').close()
        broken = QSettings(os.path.join(blocker, 'cloud.ini'), QSettings.Format.IniFormat)

        self.assertFalse(startup.choose_standalone(broken))
        self.assertFalse(startup.clear_standalone(broken))


if __name__ == '__main__':
    unittest.main()
