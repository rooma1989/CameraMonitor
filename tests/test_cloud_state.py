import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest

from PySide6.QtCore import QSettings

from camera_monitor import cloud_state
from camera_monitor.connection_options import ConnectionOptions
from camera_monitor.credentials import CredentialStore
from camera_monitor.device_names import DeviceNames
from camera_monitor.discovery import Device
from test_credentials import MemoryVault


class PureMappingTests(unittest.TestCase):
    def test_stream_mode_maps_to_the_player_dropdown_order(self):
        # playback.PlayerWindow 的下拉顺序：ONVIF / 大华 / 手动
        self.assertEqual(0, cloud_state.mode_index('onvif'))
        self.assertEqual(1, cloud_state.mode_index('dahua'))
        self.assertEqual(2, cloud_state.mode_index('manual'))
        self.assertEqual(0, cloud_state.mode_index('rtmp'), '未知取流方式回落到 ONVIF')

        self.assertEqual('onvif', cloud_state.stream_mode(0))
        self.assertEqual('dahua', cloud_state.stream_mode(1))
        self.assertEqual('manual', cloud_state.stream_mode(2))
        self.assertEqual('onvif', cloud_state.stream_mode(9))

    def test_a_camera_entry_carries_the_discovery_result(self):
        device = Device('192.168.2.216', name='Gate', model='IPC-HDP2230C-SA',
                        manufacturer='Dahua', protocols=['ONVIF'],
                        urls=['http://192.168.2.216/onvif/device_service'])

        entry = cloud_state.camera_entry(device, slot_index=3, display_name='大门入口')

        self.assertEqual('192.168.2.216', entry['ip'])
        self.assertEqual(3, entry['slot_index'])
        self.assertEqual('大门入口', entry['display_name'])
        self.assertEqual('IPC-HDP2230C-SA', entry['model'])
        self.assertEqual(['ONVIF'], entry['protocols'])
        self.assertEqual(['http://192.168.2.216/onvif/device_service'], entry['onvif_urls'])

    def test_an_omitted_password_stays_out_of_the_payload(self):
        device = Device('10.0.0.1')

        entry = cloud_state.camera_entry(device, slot_index=0, password=None)

        self.assertNotIn('password', entry,
                         '缺省表示不改动；带上空串会把云端密码清掉')

    def test_an_explicit_password_is_included_even_when_empty(self):
        device = Device('10.0.0.1')

        self.assertEqual('', cloud_state.camera_entry(device, slot_index=0, password='')['password'])
        self.assertEqual('pw', cloud_state.camera_entry(device, slot_index=0, password='pw')['password'])

    def test_a_camera_round_trips_back_into_a_device(self):
        device = Device('192.168.2.216', name='Gate', model='M1', manufacturer='Dahua',
                        protocols=['ONVIF', '大华 DHIP'], urls=['http://192.168.2.216/x'])

        restored = cloud_state.device_from_camera(cloud_state.camera_entry(device, slot_index=0))

        self.assertEqual(device.ip, restored.ip)
        self.assertEqual(device.model, restored.model)
        self.assertEqual(device.manufacturer, restored.manufacturer)
        self.assertEqual(device.protocols, restored.protocols)
        self.assertEqual(device.urls, restored.urls)

    def test_slot_order_leaves_gaps_where_the_wall_has_empty_tiles(self):
        cameras = [{'ip': '10.0.0.1', 'slot_index': 0}, {'ip': '10.0.0.2', 'slot_index': 3}]

        self.assertEqual(['10.0.0.1', '', '', '10.0.0.2'], cloud_state.slot_order(cameras))

    def test_slot_order_is_empty_without_cameras(self):
        self.assertEqual([], cloud_state.slot_order([]))

    def test_layout_entry_normalises_the_column_map_keys(self):
        layout = cloud_state.layout_entry(12, {12: 4, '20': 5}, True, '阳光养老院')

        self.assertEqual(12, layout['capacity'])
        self.assertEqual({'12': 4, '20': 5}, layout['columns'], '契约里 columns 的键是字符串')
        self.assertTrue(layout['fill_width'])
        self.assertEqual('阳光养老院', layout['organization'])

    def test_first_sync_uploads_when_the_cloud_profile_is_still_empty(self):
        empty_cloud = {'cameras': []}
        filled_cloud = {'cameras': [{'ip': '10.0.0.1'}]}

        self.assertEqual('upload', cloud_state.first_sync_direction(empty_cloud, local_camera_count=3))
        self.assertEqual('download', cloud_state.first_sync_direction(filled_cloud, local_camera_count=3))
        self.assertEqual('download', cloud_state.first_sync_direction(empty_cloud, local_camera_count=0),
                         '两边都空时没有可上传的东西，按拉取处理')


class ApplyToLocalTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, 'prefs.ini')
        self.names = DeviceNames(QSettings(path, QSettings.Format.IniFormat))
        self.options = ConnectionOptions(QSettings(os.path.join(folder.name, 'conn.ini'),
                                                   QSettings.Format.IniFormat))
        self.store = CredentialStore(MemoryVault())

    def snapshot(self, cameras=None, layout=None):
        return {
            'version': 4,
            'profile': {'id': 1, 'name': '一楼大厅'},
            'layout': layout if layout is not None else {
                'capacity': 12, 'columns': {'12': 4}, 'fill_width': True, 'organization': '阳光养老院'},
            'cameras': cameras if cameras is not None else [],
        }

    def camera(self, **overrides):
        base = {
            'ip': '192.168.2.216', 'slot_index': 2, 'display_name': '大门入口',
            'model': 'M1', 'manufacturer': 'Dahua', 'protocols': ['ONVIF'],
            'onvif_urls': ['http://192.168.2.216/x'], 'stream_mode': 'dahua',
            'dahua_channel': 3, 'manual_url': '', 'transport': 'udp',
            'username': 'admin', 'password': 'p@ss 密码', 'name_color': '#00ccff',
            'name_corner': 'bottom-right',
        }
        base.update(overrides)
        return base

    def test_applying_a_snapshot_restores_names_appearance_and_transport(self):
        cloud_state.apply_snapshot(self.snapshot([self.camera()]), self.names, self.options, self.store)

        self.assertEqual('大门入口', self.names.get('192.168.2.216'))
        self.assertEqual(('#00ccff', 'bottom-right'), self.names.appearance('192.168.2.216'))
        self.assertEqual('udp', self.options.transport('192.168.2.216'))

    def test_applying_a_snapshot_stores_credentials_in_the_system_vault(self):
        cloud_state.apply_snapshot(self.snapshot([self.camera()]), self.names, self.options, self.store)

        self.assertEqual(('admin', 'p@ss 密码'), self.store.load('192.168.2.216'))

    def test_a_camera_without_a_username_does_not_get_an_empty_credential(self):
        camera = self.camera(username='', password='')

        cloud_state.apply_snapshot(self.snapshot([camera]), self.names, self.options, self.store)

        self.assertIsNone(self.store.load('192.168.2.216'),
                          '匿名摄像头不该在钥匙串里留一条空记录')

    def test_applying_a_snapshot_restores_the_slot_order(self):
        cameras = [self.camera(ip='10.0.0.1', slot_index=0), self.camera(ip='10.0.0.2', slot_index=2)]

        cloud_state.apply_snapshot(self.snapshot(cameras), self.names, self.options, self.store)

        self.assertEqual(['10.0.0.1', '', '10.0.0.2'], self.names.slot_order())

    def test_applying_a_snapshot_restores_the_layout_preferences(self):
        cloud_state.apply_snapshot(self.snapshot(), self.names, self.options, self.store)

        settings = self.names.settings
        self.assertEqual('阳光养老院', settings.value('monitor/organization'))
        self.assertEqual(True, str(settings.value('monitor/fill_width')).lower() in ('true', '1'))
        self.assertEqual(4, int(settings.value('monitor/columns/12')))

    def test_applying_a_snapshot_returns_devices_and_per_camera_stream_settings(self):
        result = cloud_state.apply_snapshot(self.snapshot([self.camera()]), self.names,
                                            self.options, self.store)

        self.assertEqual(['192.168.2.216'], [d.ip for d in result.devices])
        self.assertEqual(12, result.capacity)
        stream = result.streams['192.168.2.216']
        self.assertEqual(1, stream['mode_index'], '大华取流对应下拉第 2 项')
        self.assertEqual(3, stream['dahua_channel'])

    def test_a_vault_failure_does_not_abort_the_whole_sync(self):
        class Broken(MemoryVault):
            def set_password(self, *args):
                raise RuntimeError('vault down')

        store = CredentialStore(Broken())

        result = cloud_state.apply_snapshot(self.snapshot([self.camera()]), self.names,
                                            self.options, store)

        self.assertEqual(['192.168.2.216'], [d.ip for d in result.devices])
        self.assertIn('192.168.2.216', result.credential_failures)

    def test_the_offline_cache_carries_no_passwords(self):
        cached = cloud_state.cacheable(self.snapshot([self.camera()]))

        self.assertNotIn('password', cached['cameras'][0],
                         '缓存落在明文 ini 里，密码只能留在钥匙串')
        self.assertEqual('admin', cached['cameras'][0]['username'])
        self.assertEqual(4, cached['version'])

    def test_replaying_the_offline_cache_keeps_the_stored_credentials(self):
        cloud_state.apply_snapshot(self.snapshot([self.camera()]), self.names, self.options, self.store)
        cached = cloud_state.cacheable(self.snapshot([self.camera()]))

        cloud_state.apply_snapshot(cached, self.names, self.options, self.store)

        self.assertEqual(('admin', 'p@ss 密码'), self.store.load('192.168.2.216'),
                         '缓存里没有密码，重放时不该把钥匙串里的抹掉')

    def test_applying_an_empty_snapshot_clears_the_slot_order(self):
        self.names.save_slot_order(['10.0.0.9'])

        cloud_state.apply_snapshot(self.snapshot([]), self.names, self.options, self.store)

        self.assertEqual([], self.names.slot_order())


if __name__ == '__main__':
    unittest.main()
