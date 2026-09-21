import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from camera_monitor import cloud_state
from camera_monitor.app import Window
from camera_monitor.device_names import DeviceNames
from camera_monitor.discovery import Device
from support import make_window
from test_credentials import MemoryVault


class CloudWindowTests(unittest.TestCase):
    """Window 与云端同步之间的接线。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault',
                        lambda _self: self.vault)
        patcher.start()
        self.addCleanup(patcher.stop)

        # 设置全部注入：QSettings 在 macOS 上没法靠环境变量隔离
        self.window = make_window(self)
        # 这些用例只验接线，不该去抓真实画面（会留下跑着的网络线程）
        self.window.thumbnails.request = lambda *args, **kwargs: None

    def device(self, ip, **kwargs):
        base = dict(name='Cam', model='OLD-MODEL', manufacturer='OldCorp',
                    protocols=['大华 DHIP'], urls=['http://old/onvif'])
        base.update(kwargs)
        return Device(ip, **base)

    def test_rediscovering_a_camera_refreshes_the_tile_metadata(self):
        old = self.device('192.168.1.68')
        self.window.add_device(old)
        self.window.wall.add_device(old)

        fresh = self.device('192.168.1.68', name='CS-H9c', model='CS-H9c-V105-8H55WFL',
                            manufacturer='Ezviz', protocols=['ONVIF'],
                            urls=['http://192.168.1.68/onvif/device_service'])
        self.window.add_device(fresh)

        tile = self.window.wall.tiles[0]
        self.assertEqual('CS-H9c-V105-8H55WFL', tile.player.device.model,
                         '换了摄像头之后画面里那份也要更新')
        self.assertEqual(['ONVIF'], tile.player.device.protocols)
        self.assertEqual(['http://192.168.1.68/onvif/device_service'], tile.player.device.urls)

    def test_the_upload_payload_uses_the_refreshed_device(self):
        old = self.device('192.168.1.68')
        self.window.add_device(old)
        self.window.wall.add_device(old)
        self.window.add_device(self.device('192.168.1.68', model='CS-H9c-V105-8H55WFL',
                                           protocols=['ONVIF'],
                                           urls=['http://192.168.1.68/onvif/device_service']))

        payload = self.window.collect_cloud_payload()

        camera = payload['cameras'][0]
        self.assertEqual('CS-H9c-V105-8H55WFL', camera['model'])
        self.assertEqual(['ONVIF'], camera['protocols'])

    def test_the_upload_payload_mirrors_names_slots_and_layout(self):
        for ip in ('10.0.0.1', '10.0.0.2'):
            device = self.device(ip)
            self.window.add_device(device)
            self.window.wall.add_device(device)
        self.window.device_names.save('10.0.0.2', '厨房')
        self.window.wall.change_layout(9)
        self.window.wall.move_tile(self.window.wall.tiles[0], 3)

        payload = self.window.collect_cloud_payload()

        self.assertEqual(9, payload['layout']['capacity'])
        by_ip = {c['ip']: c for c in payload['cameras']}
        self.assertEqual('厨房', by_ip['10.0.0.2']['display_name'])
        self.assertEqual(3, by_ip['10.0.0.1']['slot_index'])

    def test_a_camera_without_saved_credentials_omits_the_password(self):
        device = self.device('10.0.0.1')
        self.window.add_device(device)
        self.window.wall.add_device(device)

        camera = self.window.collect_cloud_payload()['cameras'][0]

        self.assertNotIn('password', camera, '本机没存密码时不该把云端的清掉')

    def test_a_camera_with_saved_credentials_carries_them(self):
        device = self.device('10.0.0.1')
        self.window.add_device(device)
        self.window.wall.add_device(device)
        self.window.wall.credential_store.save('10.0.0.1', 'admin', 'secret')

        camera = self.window.collect_cloud_payload()['cameras'][0]

        self.assertEqual('admin', camera['username'])
        self.assertEqual('secret', camera['password'])

    def test_every_local_save_asks_for_a_sync(self):
        """保存即同步：这些动作以前都不会触发上传。"""
        device = self.device('10.0.0.1')
        self.window.add_device(device)
        self.window.wall.add_device(device)
        player = self.window.wall.tiles[0].player

        pushes = []
        self.window.cloud.schedule_push = lambda: pushes.append(1)

        def count(action):
            before = len(pushes)
            action()
            return len(pushes) > before

        self.assertTrue(count(lambda: self.window.device_names.save('10.0.0.1', '大门')), '改名')
        self.assertTrue(count(lambda: self.window.device_names.save_appearance(
            '10.0.0.1', '#ffff00', 'top-right')), '名称颜色/位置')
        self.assertTrue(count(lambda: self.window.wall.change_layout(9)), '换布局')
        self.assertTrue(count(lambda: player.transport.setCurrentIndex(1)), '传输方式')
        # 这台设备协议是大华，构造时下拉已在第 2 项，改到「手动 RTSP」才是真的变了
        self.assertEqual(1, player.mode.currentIndex())
        self.assertTrue(count(lambda: player.mode.setCurrentIndex(2)), '取流方式')
        self.assertTrue(count(lambda: player.channel.setValue(4)), '大华通道')
        self.assertTrue(count(lambda: self.window.wall.add_device(self.device('10.0.0.2'))), '添加摄像头')

    def test_saving_camera_credentials_asks_for_a_sync(self):
        device = self.device('10.0.0.1')
        self.window.add_device(device)
        self.window.wall.add_device(device)
        player = self.window.wall.tiles[0].player

        pushes = []
        self.window.cloud.schedule_push = lambda: pushes.append(1)

        player.username.setText('admin')
        player.password.setText('secret')
        player._verified_credentials = ('admin', 'secret')
        player.remember.setChecked(True)
        self.assertTrue(player.save_credentials())

        self.assertTrue(pushes, '保存摄像头密码后必须同步，否则换台电脑还要重新输')

    def test_applying_cloud_config_does_not_bounce_straight_back_as_an_upload(self):
        from camera_monitor.cloud_state import AppliedConfig

        pushes = []
        self.window.cloud.schedule_push = lambda: pushes.append(1)

        applied = AppliedConfig(devices=[self.device('10.0.0.5')], capacity=4,
                                organization='阳光养老院', fill_width=False,
                                streams={'10.0.0.5': {'mode_index': 1, 'dahua_channel': 2,
                                                      'manual_url': '', 'transport': 'udp'}})
        self.window.apply_cloud_config(applied)

        self.assertEqual([], pushes, '应用云端配置的过程不能反过来触发上传')
        self.assertEqual(1, self.window.table.rowCount())
        tile = self.window.wall.tiles[0]
        self.assertEqual(1, tile.player.mode.currentIndex())
        self.assertEqual(2, tile.player.channel.value())
        self.assertEqual('udp', tile.player.transport.currentData())


if __name__ == '__main__':
    unittest.main()


class CloudApplyDoesNotEchoBackTests(unittest.TestCase):
    """真机上抓到的：登录之后版本号自己往上滚，界面一直写着「配置已上传」。

    云端配置落到本机时会去写 DeviceNames，写就会发「变了」的信号，界面接着
    排一次上传；服务端每收一次就把版本号加一，返回的新配置又被落到本机……
    一圈接一圈，谁都没动过配置，它却一直在传。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault',
                        lambda _self: self.vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = make_window(self)
        self.window.thumbnails.request = lambda *args, **kwargs: None

    def snapshot(self):
        return {'version': 5,
                'profile': {'id': 1, 'name': '测试', 'institution_id': 1, 'institution_name': 'X'},
                'layout': {'capacity': 4, 'columns': {}, 'fill_width': False, 'organization': ''},
                'cameras': [{'ip': '192.168.1.108', 'slot_index': 0, 'display_name': '大门',
                             'model': 'M', 'manufacturer': 'X', 'protocols': ['ONVIF'],
                             'onvif_urls': [], 'stream_mode': 'onvif', 'dahua_channel': 1,
                             'manual_url': '', 'transport': 'tcp', 'username': 'admin',
                             'password': 'pw', 'name_color': '#ffffff', 'name_corner': 'top-left'}]}

    def test_applying_the_cloud_configuration_does_not_queue_an_upload(self):
        cloud = self.window.cloud
        cloud.settings.setValue('cloud/enabled', True)
        cloud.token = 'cm1.token'
        cloud._apply(self.snapshot())
        for _ in range(50):
            QApplication.processEvents()

        self.assertFalse(cloud.push_timer.isActive(),
                         '云端配置是我们自己写进本机的，不该反过来排一次上传')
        self.assertFalse(cloud.pending_changes,
                         '也不该被记成「离线期间的改动」，那样一联网还是会传')

    def test_an_unchanged_configuration_is_never_uploaded_twice(self):
        cloud = self.window.cloud
        cloud.settings.setValue('cloud/enabled', True)
        cloud.token = 'cm1.token'
        sent = []
        cloud._dispatch = lambda kind, run, context='': sent.append(kind)

        cloud.push_now()
        self.assertEqual(['push'], sent, '第一次总该传')

        # 服务端确认了这一份
        payload = cloud.collector()
        cloud.last_pushed = cloud_state.config_fingerprint(payload['layout'], payload['cameras'])

        cloud.push_now()
        self.assertEqual(['push'], sent, '内容没变就不该再传一次，版本号不能白白往上滚')
