import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from camera_monitor.app import Window
from camera_monitor.device_names import DeviceNames
from camera_monitor.discovery import Device
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

        names = DeviceNames(QSettings(os.path.join(folder.name, 'names.ini'),
                                      QSettings.Format.IniFormat))
        # 云端设置必须隔离：否则测试会读到这台机器真实的登录态并去联网
        cloud_settings = QSettings(os.path.join(folder.name, 'cloud.ini'),
                                   QSettings.Format.IniFormat)
        self.window = Window(device_names=names, cloud_settings=cloud_settings)
        self.assertFalse(self.window.cloud.enabled())
        self.addCleanup(self.window.cloud.stop)
        self.addCleanup(self.window.thumbnail_timer.stop)
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
