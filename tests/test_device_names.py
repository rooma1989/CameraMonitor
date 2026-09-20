import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from camera_monitor.device_names import DeviceNames
from camera_monitor.app import Window
from camera_monitor.discovery import Device
from camera_monitor.credentials import CredentialStore
from test_credentials import MemoryVault


class DeviceNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = os.path.join(self.folder.name, 'names.ini')
        self.names = DeviceNames(QSettings(self.path, QSettings.Format.IniFormat))

    def test_names_persist_are_isolated_and_can_be_cleared(self):
        self.names.save('192.168.1.234', '  大门  ')
        again = DeviceNames(QSettings(self.path, QSettings.Format.IniFormat))
        self.assertEqual(again.get('192.168.1.234'), '大门')
        self.assertEqual(again.get('192.168.1.108'), '')
        again.save('192.168.1.234', '')
        self.assertEqual(again.get('192.168.1.234'), '')

    def test_transport_preference_is_per_camera_and_persists(self):
        from camera_monitor.connection_options import ConnectionOptions
        store = ConnectionOptions(QSettings(self.path, QSettings.Format.IniFormat))
        self.assertEqual(store.transport('one'), 'tcp')
        store.save_transport('one', 'udp')
        again = ConnectionOptions(QSettings(self.path, QSettings.Format.IniFormat))
        self.assertEqual(again.transport('one'), 'udp')
        self.assertEqual(again.transport('two'), 'tcp')
        with self.assertRaises(ValueError):store.save_transport('one', 'invalid')

    def test_player_restores_transport_and_passes_it_to_decoder(self):
        from camera_monitor.connection_options import ConnectionOptions
        from camera_monitor.playback import PlayerWindow
        from camera_monitor.streams import Stream
        store = ConnectionOptions(QSettings(self.path, QSettings.Format.IniFormat))
        player = PlayerWindow(Device('camera-test'), connection_options=store,
                              device_names=self.names, credential_store=CredentialStore(MemoryVault()))
        player.transport.setCurrentIndex(1)
        self.assertEqual(store.transport('camera-test'), 'udp')
        player.close()
        again = PlayerWindow(Device('camera-test'), connection_options=store,
                             device_names=self.names, credential_store=CredentialStore(MemoryVault()))
        self.addCleanup(again.close)
        self.assertEqual(again.transport.currentData(), 'udp')
        with patch('camera_monitor.playback.Decoder') as decoder:
            again.start_decoder(Stream('test', 'rtsp://camera-test/live'))
            self.assertEqual(decoder.call_args.kwargs['transport'], 'udp')
            again.decoder = None

    def test_rename_updates_list_and_live_title_without_reconnecting(self):
        window = Window(device_names=self.names,
            cloud_settings=QSettings(os.path.join(self.folder.name, 'cloud.ini'), QSettings.Format.IniFormat))
        self.addCleanup(window.close)
        window.wall.credential_store = CredentialStore(MemoryVault())
        window.add_device(Device('192.168.1.234', name='MT5'))
        window.table.selectRow(0)
        window.open_player(force_settings=True)
        tile = window.wall.tiles[0]
        with patch.object(tile.player, 'stop_playback') as stop:
            tile.player.custom_name.setText('门口 <东>')
            tile.player.save_name_button.click()
            stop.assert_not_called()
        self.assertIn('门口 <东>', tile.title.text())
        self.assertEqual(window.table.item(0, 1).text(), '门口 <东>')
        window.add_device(Device('192.168.1.234', name='MT5 rediscovered'))
        self.assertEqual(window.table.item(0, 1).text(), '门口 <东>')
        self.assertEqual(window.devices['192.168.1.234'].name, 'MT5 rediscovered')
        window.device_filter.setText('门口')
        self.assertFalse(window.table.rows[0].isHidden())

    def test_save_failure_does_not_report_success(self):
        from camera_monitor.playback import PlayerWindow
        player = PlayerWindow(Device('test-name'), device_names=self.names,
                              credential_store=CredentialStore(MemoryVault()))
        self.addCleanup(player.close)
        with patch.object(self.names, 'save', side_effect=OSError('storage unavailable')):
            player.custom_name.setText('大门')
            player.save_name_button.click()
        self.assertIn('未保存', player.name_note.text())
