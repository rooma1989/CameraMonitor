"""不登录云端的电脑：本机存一份快照，重启后把上次的监控墙铺回来。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import logging
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from camera_monitor.connection_options import ConnectionOptions
from camera_monitor.credentials import CredentialStore
from camera_monitor.device_names import DeviceNames
from camera_monitor.discovery import Device
from support import isolated_settings, make_window
from test_credentials import MemoryVault

KEY = 'local/snapshot'


class CountingVault(MemoryVault):
    def __init__(self):
        super().__init__()
        self.reads = 0

    def get_password(self, service, account):
        self.reads += 1
        return super().get_password(service, account)


class LocalSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.vault = CountingVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: self.vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 两个窗口共用同一份设置文件，模拟同一台电脑关掉再打开
        self.ini = isolated_settings(self)

    def window(self, **kwargs):
        kwargs.setdefault('device_names', DeviceNames(self.ini('names.ini')))
        kwargs.setdefault('connection_options', ConnectionOptions(self.ini('conn.ini')))
        window = make_window(self, **kwargs)
        window.thumbnails.request = lambda *args, **kwargs: None
        window.cloud_start_timer.stop()
        window.channel.start = lambda: None
        window.cloud.refresh = lambda: None
        return window

    def flush(self, window):
        """不真等一秒：确认排上了、间隔是一秒，再当场跑掉。"""
        timer = window.local_snapshot_timer
        self.assertTrue(timer.isActive(), '墙变了要排一次本机快照')
        self.assertTrue(timer.isSingleShot())
        self.assertEqual(1000, timer.interval())
        timer.stop()
        timer.timeout.emit()

    def stored(self, window):
        raw = window.device_names.settings.value(KEY, '')
        return json.loads(raw) if raw else None

    def build_wall(self, window):
        CredentialStore(self.vault).save('10.0.0.1', 'admin', 'secret-pw')
        for ip in ('10.0.0.1', '10.0.0.2'):
            window.add_device(Device(ip, model='M'))
            window.wall.add_device(window.devices[ip])
        window.wall.change_layout(9)
        # 第二路挪到第 5 格，中间留空：回放时空格也要留着
        window.wall.move_tile(window.wall.tiles[1], 4)
        window.device_names.save('10.0.0.2', '厨房')

    def slots(self, window):
        return [tile.player.device.ip if tile else '' for tile in window.wall.slots[:window.wall.capacity]]

    def test_a_wall_change_writes_a_snapshot_without_passwords(self):
        window = self.window()
        self.build_wall(window)
        reads = self.vault.reads

        self.flush(window)

        snap = self.stored(window)
        self.assertEqual({'10.0.0.1', '10.0.0.2'}, {c['ip'] for c in snap['cameras']})
        self.assertEqual(9, snap['layout']['capacity'])
        for camera in snap['cameras']:
            self.assertNotIn('password', camera, '快照落在明文设置里')
        self.assertNotIn('secret-pw', window.device_names.settings.value(KEY))
        self.assertEqual(reads, self.vault.reads, '写本机快照不需要读钥匙串')

    def test_a_restart_restores_the_same_wall(self):
        first = self.window()
        self.build_wall(first)
        before = self.slots(first)
        self.flush(first)

        second = self.window()
        second.start_cloud()

        self.assertEqual(before, self.slots(second))
        self.assertEqual(['10.0.0.1', '', '', '', '10.0.0.2', '', '', '', ''], self.slots(second))
        self.assertEqual(9, second.wall.capacity)
        self.assertEqual('厨房', second.device_names.get('10.0.0.2'))
        self.assertEqual({'10.0.0.1', '10.0.0.2'}, set(second.devices))
        self.assertEqual([1], second.wall.connect_all_calls, '恢复后直接开始播放')
        self.assertEqual(('admin', 'secret-pw'), CredentialStore(self.vault).load('10.0.0.1'),
                         '快照里没有密码，回放不能把钥匙串里的抹掉')

    def test_replaying_does_not_schedule_another_write(self):
        first = self.window()
        self.build_wall(first)
        self.flush(first)
        written = first.device_names.settings.value(KEY)

        second = self.window()
        second.start_cloud()
        QApplication.processEvents()

        self.assertFalse(second.local_snapshot_timer.isActive(), '回放时自己写的不算墙变化')
        self.assertEqual(written, second.device_names.settings.value(KEY))

    def test_the_switches_are_not_replayed(self):
        first = self.window()
        self.build_wall(first)
        first.device_names.settings.setValue('monitor/autostart', True)
        self.flush(first)
        # 快照最多晚一秒：比如刚取消勾选就关了软件
        first.device_names.settings.setValue('monitor/autostart', False)

        second = self.window()
        second.start_cloud()

        self.assertEqual('false', str(second.device_names.settings.value('monitor/autostart')).lower())
        self.assertNotIn('enable', second.autostart.calls)

    def test_a_logged_in_computer_writes_no_local_snapshot(self):
        window = self.window()
        window.cloud.settings.setValue('cloud/enabled', True)

        self.build_wall(window)

        self.assertFalse(window.local_snapshot_timer.isActive())
        window.save_local_snapshot()
        self.assertIsNone(self.stored(window), '登录云端后以云端为准')

    def test_a_logged_in_computer_does_not_replay_it(self):
        first = self.window()
        self.build_wall(first)
        self.flush(first)

        second = self.window()
        second.cloud.settings.setValue('cloud/enabled', True)
        second.cloud.session.load_session = lambda: {'token': 'cm1.t'}
        second.start_cloud()

        self.assertEqual([], second.wall.tiles)

    def test_not_replayed_under_the_welcome_page(self):
        first = self.window()
        self.build_wall(first)
        self.flush(first)

        second = self.window()
        second.set_welcome_visible(True)
        second.start_cloud()

        self.assertEqual([], second.wall.tiles)

    def test_logging_out_saves_the_wall_on_screen(self):
        # 退出云端后缓存就清了，不马上存一份的话，重启后墙是空的
        window = self.window()
        self.build_wall(window)
        window.local_snapshot_timer.stop()

        window.cloud_session_changed(False)

        self.flush(window)
        self.assertEqual(2, len(self.stored(window)['cameras']))

    def test_closing_writes_a_pending_snapshot_right_away(self):
        window = self.window()
        self.build_wall(window)
        self.assertTrue(window.local_snapshot_timer.isActive())

        window.close()
        QApplication.processEvents()

        self.assertIsNotNone(self.stored(window), '改完不到一秒就关了，也要存下来')

    def test_a_corrupt_snapshot_is_ignored(self):
        names = DeviceNames(self.ini('names.ini'))
        names.settings.setValue(KEY, '{"cameras": [')
        window = self.window(device_names=names)

        with self.assertLogs('camera_monitor.app', level=logging.WARNING):
            window.start_cloud()

        self.assertEqual([], window.wall.tiles)

    def test_a_restored_wall_still_goes_fullscreen_when_asked(self):
        first = self.window()
        self.build_wall(first)
        self.flush(first)
        first.device_names.settings.setValue('monitor/start_fullscreen', True)

        second = self.window()
        second.show()
        QApplication.processEvents()
        second.start_cloud()
        for _ in range(3):
            QApplication.processEvents()

        self.assertTrue(second.presentation, '先恢复墙，再判断要不要全屏')


if __name__ == '__main__':
    unittest.main()
