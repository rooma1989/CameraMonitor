import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest

from PySide6.QtCore import QSettings

from camera_monitor import cloud_state
from camera_monitor.connection_options import ConnectionOptions
from camera_monitor.credentials import CredentialStore
from camera_monitor.device_names import DeviceNames
from test_credentials import MemoryVault


def managed_snapshot(**overrides):
    snap = {
        'version': 3,
        'mode': 'managed',
        'escape_password_hash': 'pbkdf2_sha256$240000$00$11',
        'default_credentials': {'username': 'admin', 'password': 'dflt'},
        'profile': {'id': 1, 'name': '一楼大厅'},
        'layout': {'capacity': 4, 'columns': {}, 'fill_width': False,
                   'organization': '', 'fullscreen': True},
        'cameras': [
            {'ip': '10.0.0.1', 'slot_index': 0, 'username': '', 'password': ''},
            {'ip': '10.0.0.2', 'slot_index': 1, 'username': 'own', 'password': 'pw'},
        ],
    }
    snap.update(overrides)
    return snap


class ManagedSnapshotTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        ini = lambda name: QSettings(os.path.join(folder.name, name), QSettings.Format.IniFormat)
        self.names = DeviceNames(ini('names.ini'))
        self.options = ConnectionOptions(ini('conn.ini'))
        self.store = CredentialStore(MemoryVault())

    def apply(self, snap):
        return cloud_state.apply_snapshot(snap, self.names, self.options, self.store)

    def test_mode_defaults_to_full_and_rejects_unknown_values(self):
        self.assertEqual('full', cloud_state.profile_mode({}))
        self.assertEqual('full', cloud_state.profile_mode({'mode': 'kiosk'}))
        self.assertEqual('managed', cloud_state.profile_mode({'mode': 'managed'}))

    def test_managed_fields_reach_the_applied_config(self):
        applied = self.apply(managed_snapshot())

        self.assertEqual('managed', applied.mode)
        self.assertTrue(applied.fullscreen)
        self.assertEqual('pbkdf2_sha256$240000$00$11', applied.escape_password_hash)

    def test_a_plain_snapshot_is_full_mode_and_windowed(self):
        applied = self.apply({'version': 1, 'layout': {'capacity': 4}, 'cameras': []})

        self.assertEqual('full', applied.mode)
        self.assertFalse(applied.fullscreen)
        self.assertEqual('', applied.escape_password_hash)

    def test_cameras_without_their_own_login_get_the_default_one(self):
        self.apply(managed_snapshot())

        self.assertEqual(('admin', 'dflt'), self.store.load('10.0.0.1'))
        self.assertEqual(('own', 'pw'), self.store.load('10.0.0.2'), '单路另填的优先')
        self.assertEqual(('admin', 'dflt'),
                         self.store.load(cloud_state.DEFAULT_CREDENTIAL_ACCOUNT),
                         '默认账号单独存一份，给还没上墙的摄像头抓图用')

    def test_without_defaults_an_empty_login_still_means_anonymous(self):
        self.store.save('10.0.0.1', 'old', 'old')

        self.apply(managed_snapshot(default_credentials=None))

        self.assertIsNone(self.store.load('10.0.0.1'))

    def test_the_offline_cache_keeps_mode_and_hash_but_no_default_password(self):
        cached = cloud_state.cacheable(managed_snapshot())

        self.assertEqual('managed', cached['mode'])
        self.assertEqual('pbkdf2_sha256$240000$00$11', cached['escape_password_hash'])
        self.assertTrue(cached['layout']['fullscreen'])
        self.assertEqual({'username': 'admin'}, cached['default_credentials'],
                         '缓存是明文 ini，密码只能留在钥匙串')

    def test_replaying_the_cache_leaves_the_vault_alone(self):
        self.apply(managed_snapshot())

        self.apply(cloud_state.cacheable(managed_snapshot()))

        self.assertEqual(('admin', 'dflt'), self.store.load('10.0.0.1'))
        self.assertEqual(('admin', 'dflt'),
                         self.store.load(cloud_state.DEFAULT_CREDENTIAL_ACCOUNT))


if __name__ == '__main__':
    unittest.main()
