"""完整模式的两个开关：开机自动启动、启动后自动全屏。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from camera_monitor.device_names import DeviceNames
from support import FakeAutoStart, isolated_settings, make_window
from test_cloud_sync import snapshot
from test_credentials import MemoryVault


def truthy(value):
    return str(value).lower() in ('true', '1')


def with_switches(autostart=None, start_fullscreen=None, mode='full'):
    snap = dict(snapshot(), mode=mode)
    layout = dict(snap['layout'])
    if autostart is not None:
        layout['autostart'] = autostart
    if start_fullscreen is not None:
        layout['start_fullscreen'] = start_fullscreen
    snap['layout'] = layout
    return snap


class StartupSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)

    def window(self, fake=None, **preset):
        names = DeviceNames(isolated_settings(self)('names.ini'))
        for key, value in preset.items():
            names.settings.setValue(f'monitor/{key}', value)
        self.autostart = fake if fake is not None else FakeAutoStart()
        window = make_window(self, device_names=names, autostart=self.autostart)
        window.thumbnails.request = lambda *args, **kwargs: None
        # 启动流程由各用例自己调 start_cloud，别让定时器在 processEvents() 时插一脚
        window.cloud_start_timer.stop()
        window.channel.start = lambda: None
        self.pushes = []
        window.cloud.schedule_push = lambda: self.pushes.append(1)
        return window

    def setting(self, window, key):
        return truthy(window.device_names.settings.value(f'monitor/{key}', False))

    # ---------- 工具栏 ----------

    def test_both_switches_sit_on_the_wall_toolbar(self):
        window = self.window()
        toolbar = window.wall.toolbar_widget

        self.assertIs(toolbar, window.autostart_toggle.parentWidget())
        self.assertIs(toolbar, window.start_fullscreen_toggle.parentWidget())
        self.assertEqual('开机自动启动', window.autostart_toggle.text())
        self.assertEqual('启动后自动全屏', window.start_fullscreen_toggle.text())

    def test_the_autostart_box_shows_the_real_startup_entry(self):
        window = self.window(FakeAutoStart(enabled=True), autostart=False)

        self.assertTrue(window.autostart_toggle.isChecked(), '勾选框显示实际状态，不是设置')

    def test_the_fullscreen_box_shows_the_setting(self):
        window = self.window(start_fullscreen=True)

        self.assertTrue(window.start_fullscreen_toggle.isChecked())

    def test_ticking_autostart_enables_it_and_syncs(self):
        window = self.window()

        window.autostart_toggle.setChecked(True)

        self.assertTrue(self.setting(window, 'autostart'))
        self.assertEqual(['enable'], self.autostart.calls)
        self.assertTrue(window.autostart_toggle.isChecked())
        self.assertEqual([1], self.pushes)

    def test_a_failed_enable_unticks_the_box_and_says_why(self):
        window = self.window(FakeAutoStart(works=False, error='temporary_location'))

        window.autostart_toggle.setChecked(True)

        self.assertFalse(window.autostart_toggle.isChecked())
        self.assertFalse(self.setting(window, 'autostart'))
        self.assertIn('应用程序', window.status.text())

    def test_unticking_autostart_removes_it(self):
        window = self.window(FakeAutoStart(enabled=True), autostart=True)

        window.autostart_toggle.setChecked(False)

        self.assertFalse(self.setting(window, 'autostart'))
        self.assertEqual(['disable'], self.autostart.calls)
        self.assertEqual([1], self.pushes)

    def test_ticking_start_fullscreen_only_records_it(self):
        window = self.window()

        window.start_fullscreen_toggle.setChecked(True)
        QApplication.processEvents()

        self.assertTrue(self.setting(window, 'start_fullscreen'))
        self.assertFalse(window.presentation, '勾上当场不进全屏，下次打开才生效')
        self.assertFalse(window.isFullScreen())
        self.assertEqual([1], self.pushes)

    # ---------- 启动时对齐 ----------

    def test_startup_puts_back_a_wanted_entry(self):
        window = self.window(autostart=True)

        window.start_cloud()

        self.assertEqual(['enable'], self.autostart.calls)
        self.assertTrue(window.autostart_toggle.isChecked())

    def test_startup_removes_an_unwanted_entry(self):
        window = self.window(FakeAutoStart(enabled=True))

        window.start_cloud()

        self.assertEqual(['disable'], self.autostart.calls)
        self.assertFalse(window.autostart_toggle.isChecked())

    # ---------- 云端下发 ----------

    def connected(self, window):
        window.cloud.settings.setValue('cloud/enabled', True)
        window.cloud.token = 'cm1.token'
        del window.cloud.schedule_push  # 用回真的，看它有没有被排上

    def test_a_cloud_config_updates_the_boxes_and_the_startup_entry(self):
        window = self.window()
        self.connected(window)

        window.cloud._apply(with_switches(autostart=True, start_fullscreen=True))

        self.assertTrue(self.setting(window, 'autostart'))
        self.assertTrue(self.setting(window, 'start_fullscreen'))
        self.assertEqual(['enable'], self.autostart.calls)
        self.assertTrue(window.autostart_toggle.isChecked())
        self.assertTrue(window.start_fullscreen_toggle.isChecked())
        self.assertFalse(window.cloud.push_timer.isActive(), '云端刚下发的不能再传回去')
        self.assertFalse(window.cloud.pending_changes)
        self.assertFalse(window.presentation, 'start_fullscreen 只在下次打开时生效')

    def test_a_cloud_config_that_cannot_enable_reports_back(self):
        window = self.window(FakeAutoStart(works=False))
        self.connected(window)
        window.cloud.last_pushed = 'whatever'

        window.cloud._apply(with_switches(autostart=True))

        self.assertFalse(self.setting(window, 'autostart'))
        self.assertFalse(window.autostart_toggle.isChecked())
        self.assertTrue(window.cloud.pending_changes, '下一次心跳把实际状态传上去')
        self.assertEqual('', window.cloud.last_pushed, '不清的话会被当成重复而跳过')

    def test_an_old_backend_does_not_touch_the_startup_entry(self):
        window = self.window(autostart=True)
        self.connected(window)

        window.cloud._apply(with_switches())

        self.assertEqual([], self.autostart.calls)
        self.assertTrue(self.setting(window, 'autostart'))

    def test_managed_mode_only_records_the_switches(self):
        window = self.window()
        self.connected(window)

        window.cloud._apply(with_switches(autostart=False, start_fullscreen=True, mode='managed'))
        QApplication.processEvents()

        self.assertEqual(['enable'], self.autostart.calls, '傻瓜模式开机启动一直开着')
        self.assertFalse(self.setting(window, 'autostart'))
        self.assertTrue(self.setting(window, 'start_fullscreen'))


if __name__ == '__main__':
    unittest.main()
