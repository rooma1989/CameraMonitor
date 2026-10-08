"""完整模式的两个开关：开机自动启动、启动后自动全屏。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from camera_monitor.device_names import DeviceNames
from camera_monitor.discovery import Device
from support import FakeAutoStart, isolated_settings, make_window
from test_cloud_sync import FakeClient, camera, snapshot
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



class EchoClient(FakeClient):
    """和 ConfigService::apply 一样：收下上传，版本号加一，把存下的那份原样返回。"""

    def __init__(self):
        super().__init__()
        self.version = 1

    def push(self, token, version, layout, cameras):
        self.calls.append(('push', token, version, layout, cameras))
        self.version += 1
        return {'version': self.version, 'profile': {'id': 1, 'name': '一楼大厅'}, 'mode': 'full',
                'layout': dict(layout, fullscreen=False), 'cameras': [dict(c) for c in cameras]}


class CloudRoundTripTests(unittest.TestCase):
    """真窗口 + 假服务端：两个开关在后台改过之后，本机再改回去要传得上去。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = make_window(self, autostart=FakeAutoStart())
        self.window.thumbnails.request = lambda *args, **kwargs: None
        self.window.cloud_start_timer.stop()
        self.window.channel.start = lambda: None
        self.window.wall.add_device(Device('10.0.0.5'))
        self.client = EchoClient()
        cloud = self.window.cloud
        cloud.client = self.client
        cloud.settings.setValue('cloud/enabled', True)
        cloud.settings.setValue('cloud/version', 1)
        cloud.token = 'cm1.token'

    def settle(self):
        cloud = self.window.cloud
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and cloud.calls:
            QApplication.processEvents()
            time.sleep(0.01)
        QApplication.processEvents()
        cloud.push_timer.stop()

    def pushes(self):
        return [call for call in self.client.calls if call[0] == 'push']

    def admin_turns_autostart_on(self):
        # 先传一次（开机启动没勾），后台编辑页再把它打开、推一次 config_changed
        self.window.cloud.push_now()
        self.settle()
        uploaded = self.pushes()[0][3]
        self.client.version += 1
        self.client.fetch_result = {'version': self.client.version, 'mode': 'full',
                                    'profile': {'id': 1, 'name': '一楼大厅'},
                                    'layout': dict(uploaded, fullscreen=False, autostart=True),
                                    'cameras': [dict(c) for c in self.pushes()[0][4]]}
        self.window.cloud.refresh()
        self.settle()
        self.assertTrue(self.window.autostart_toggle.isChecked())

    def test_unticking_after_the_admin_switched_it_on_is_uploaded(self):
        self.admin_turns_autostart_on()

        self.window.autostart_toggle.setChecked(False)
        self.window.cloud.push_now()
        self.settle()

        self.assertEqual(2, len(self.pushes()), '去掉勾要传上去，否则后台一直显示开着')
        self.assertFalse(self.pushes()[1][3]['autostart'])

    def test_the_downloaded_config_is_not_uploaded_back(self):
        # 下发的 layout 比本机多了 fullscreen 之类的键：去重要和本机上传时的样子比，
        # 不然每收一次下发都会多传一次
        self.admin_turns_autostart_on()

        self.window.cloud.push_now()
        self.settle()

        self.assertEqual(1, len(self.pushes()))


class FirstLoginTests(unittest.TestCase):
    """首次登录：两个开关各自「开着的一边说了算」，不能被谁也没选过的缺省 false 抹掉。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)

    def window(self, fake, **preset):
        names = DeviceNames(isolated_settings(self)('names.ini'))
        for key, value in preset.items():
            names.settings.setValue(f'monitor/{key}', value)
        self.autostart = fake
        window = make_window(self, device_names=names, autostart=fake)
        window.thumbnails.request = lambda *args, **kwargs: None
        window.cloud_start_timer.stop()
        window.channel.start = lambda: None
        self.client = EchoClient()
        window.cloud.client = self.client
        return window

    def login(self, window, cameras, **switches):
        snap = dict(snapshot(cameras=cameras), token='cm1.token', mode='full')
        # 新后台的 normalizeLayout 总带着这两个键，缺省 false
        snap['layout'] = dict(snap['layout'], fullscreen=False,
                              **dict({'autostart': False, 'start_fullscreen': False}, **switches))
        self.client.login_result = snap
        window.cloud.login('code12345')
        self.settle(window)

    def settle(self, window):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and window.cloud.calls:
            QApplication.processEvents()
            time.sleep(0.01)
        QApplication.processEvents()

    def upload_now(self, window):
        """不等防抖两秒：确认排上了上传，当场传掉。"""
        cloud = window.cloud
        self.assertTrue(cloud.pending_changes, '要把本机这一边传上去')
        cloud.push_timer.stop()
        cloud.push_now()
        self.settle(window)
        pushes = [call for call in self.client.calls if call[0] == 'push']
        self.assertEqual(1, len(pushes))
        return pushes[0][3]

    def setting(self, window, key):
        return truthy(window.device_names.settings.value(f'monitor/{key}', False))

    def test_downloading_keeps_a_local_tick(self):
        window = self.window(FakeAutoStart(enabled=True), autostart=True, start_fullscreen=True)

        self.login(window, cameras=[camera()])

        self.assertEqual(1, len(window.wall.tiles), '云端有摄像头，走下发方向')
        self.assertTrue(self.setting(window, 'autostart'))
        self.assertTrue(self.setting(window, 'start_fullscreen'))
        self.assertTrue(self.autostart.enabled, '启动项不能被删')
        self.assertNotIn('disable', self.autostart.calls)
        self.assertTrue(window.autostart_toggle.isChecked())
        self.assertTrue(window.start_fullscreen_toggle.isChecked())
        layout = self.upload_now(window)
        self.assertTrue(layout['autostart'])
        self.assertTrue(layout['start_fullscreen'])

    def test_downloading_still_takes_a_cloud_tick(self):
        window = self.window(FakeAutoStart())

        self.login(window, cameras=[camera()], autostart=True)

        self.assertTrue(self.setting(window, 'autostart'))
        self.assertTrue(self.autostart.enabled)
        self.assertFalse(window.cloud.pending_changes, '两边一致，没什么要传的')

    def test_uploading_takes_a_cloud_tick(self):
        window = self.window(FakeAutoStart())
        window.wall.add_device(Device('10.0.0.9'))

        self.login(window, cameras=[], autostart=True, start_fullscreen=True)

        self.assertTrue(self.setting(window, 'autostart'))
        self.assertTrue(self.setting(window, 'start_fullscreen'))
        self.assertTrue(self.autostart.enabled, '云端开着的开机启动要在本机落下去')
        self.assertTrue(window.autostart_toggle.isChecked())
        self.assertTrue(window.start_fullscreen_toggle.isChecked())
        layout = self.upload_now(window)
        self.assertTrue(layout['autostart'], '上传的是两边取「或」')
        self.assertTrue(layout['start_fullscreen'])

    def test_uploading_keeps_a_local_tick(self):
        window = self.window(FakeAutoStart(enabled=True), autostart=True)
        window.wall.add_device(Device('10.0.0.9'))

        self.login(window, cameras=[])

        layout = self.upload_now(window)
        self.assertTrue(layout['autostart'])
        self.assertFalse(layout['start_fullscreen'])
        self.assertTrue(self.autostart.enabled)

    def test_a_silent_relogin_takes_the_cloud_side(self):
        # 令牌过期后的静默重登不是首次登录：这时云端那份是后台刚改过的（比如管理员
        # 刚把开机启动关掉），本机还开着只是还没拉到，不能反过来把它顶回去
        window = self.window(FakeAutoStart(enabled=True), autostart=True)
        window.cloud.settings.setValue('cloud/enabled', True)
        window.cloud.session.save_session('code12345', 'cm1.old')
        window.cloud.token = 'cm1.old'
        snap = dict(snapshot(cameras=[camera()]), token='cm1.token', mode='full')
        snap['layout'] = dict(snap['layout'], fullscreen=False, autostart=False, start_fullscreen=False)
        self.client.login_result = snap

        window.cloud.relogin()
        self.settle(window)

        self.assertEqual('cm1.token', window.cloud.token)
        self.assertFalse(self.setting(window, 'autostart'))
        self.assertFalse(self.autostart.enabled)
        self.assertFalse(window.cloud.pending_changes)

    def test_a_managed_profile_is_not_merged(self):
        window = self.window(FakeAutoStart(enabled=True), start_fullscreen=True)
        snap = dict(snapshot(cameras=[camera()]), token='cm1.token', mode='managed')
        snap['layout'] = dict(snap['layout'], fullscreen=True, autostart=False, start_fullscreen=False)
        self.client.login_result = snap

        window.cloud.login('code12345')
        self.settle(window)

        self.assertFalse(self.setting(window, 'start_fullscreen'), '傻瓜模式以云端为准')
        self.assertFalse(window.cloud.pending_changes)


class AutoFullscreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)

    def window(self, start_fullscreen=True, cameras=0):
        names = DeviceNames(isolated_settings(self)('names.ini'))
        names.settings.setValue('monitor/start_fullscreen', start_fullscreen)
        window = make_window(self, device_names=names)
        window.thumbnails.request = lambda *args, **kwargs: None
        window.cloud_start_timer.stop()
        window.channel.start = lambda: None
        window.cloud.refresh = lambda: None
        for index in range(cameras):
            window.wall.add_device(Device(f'10.0.0.{index + 1}'))
        window.show()
        QApplication.processEvents()
        return window

    def launch(self, window):
        # 和真正启动一样：启动定时器跑 start_cloud，之后才轮到自动全屏的那一拍
        window.start_cloud()
        for _ in range(3):
            QApplication.processEvents()

    def test_opens_in_fullscreen_when_switched_on(self):
        window = self.window(cameras=1)

        self.launch(window)

        self.assertTrue(window.presentation)
        self.assertTrue(window.isFullScreen())
        self.assertTrue(window.was_maximized, '退出全屏后回到最大化，而不是启动时那个窗口大小')

    def test_stays_windowed_when_switched_off(self):
        window = self.window(start_fullscreen=False, cameras=1)

        self.launch(window)

        self.assertFalse(window.presentation)

    def test_an_empty_wall_does_not_go_fullscreen(self):
        window = self.window(cameras=0)

        self.launch(window)

        self.assertFalse(window.presentation, '一打开就是一块黑屏，还得输密码才能出来')

    def test_not_while_the_welcome_page_is_up(self):
        window = self.window(cameras=1)
        window.set_welcome_visible(True)

        self.launch(window)

        self.assertFalse(window.presentation)

    def cloud_cache(self, window, mode='full', fullscreen=False):
        settings = window.cloud.settings
        settings.setValue('cloud/enabled', True)
        settings.setValue('cloud/mode', mode)
        snap = dict(snapshot(cameras=[camera()]), mode=mode)
        snap['layout'] = dict(snap['layout'], start_fullscreen=True, fullscreen=fullscreen)
        window.cloud._remember(snap)
        window.cloud.session.load_session = lambda: {'token': 'cm1.t'}

    def test_waits_for_the_cached_cloud_wall(self):
        window = self.window(cameras=0)
        self.cloud_cache(window)

        self.launch(window)

        self.assertEqual(1, len(window.wall.tiles))
        self.assertTrue(window.presentation, '缓存铺好之后墙上就有摄像头了')

    def test_managed_mode_follows_the_cloud_instead(self):
        window = self.window(cameras=0)
        self.cloud_cache(window, mode='managed', fullscreen=False)

        self.launch(window)

        self.assertTrue(window.managed.active)
        self.assertFalse(window.managed_fullscreen)
        self.assertFalse(window.isFullScreen(), '傻瓜模式全屏与否由远程页决定')

    def test_a_late_cloud_config_does_not_trigger_it(self):
        window = self.window(cameras=0)
        self.launch(window)

        window.cloud._apply(dict(snapshot(cameras=[camera()]), mode='full'))
        for _ in range(3):
            QApplication.processEvents()

        self.assertEqual(1, len(window.wall.tiles))
        self.assertFalse(window.presentation)

    def test_fires_only_once(self):
        window = self.window(cameras=1)
        self.launch(window)
        with patch('camera_monitor.app.request_unlock', return_value=True):
            self.assertTrue(window.exit_fullscreen())
        for _ in range(3):
            QApplication.processEvents()

        window.auto_fullscreen()
        QApplication.processEvents()

        self.assertFalse(window.presentation, '退出全屏后不会被拉回去')


if __name__ == '__main__':
    unittest.main()
