import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtWidgets import QApplication
from camera_monitor.app import Window
from camera_monitor.discovery import Device

class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_device_update_keeps_one_row_and_copies_ip(self):
        window = Window()
        window.add_device(Device('192.168.1.20', name='Front door', protocols=['ONVIF']))
        window.add_device(Device('192.168.1.20', name='Front door', model='IPC', protocols=['ONVIF', '大华 DHIP']))
        self.assertEqual(window.table.rowCount(), 1)
        self.assertEqual(window.table.item(0, 2).text(), 'IPC')
        window.table.selectRow(0)
        window.copy_ip()
        self.assertEqual(self.app.clipboard().text(), '192.168.1.20')
        window.close()

    def test_scan_finish_reenables_controls_and_zero_result_explains_limit(self):
        window = Window()
        window.search.setEnabled(False)
        window.finish_scan()
        self.assertTrue(window.search.isEnabled())
        self.assertFalse(window.stop.isEnabled())
        self.assertIn('未收到', window.status.text())
        window.close()

    def test_selected_device_update_keeps_accessible_table_items_alive(self):
        window = Window()
        window.add_device(Device('192.168.1.20', name='Door', protocols=['ONVIF']))
        window.table.selectRow(0)
        status_item = window.table.item(0, 4)
        name_item = window.table.item(0, 1)
        window.video_verified('192.168.1.20')
        self.assertIs(window.table.item(0, 4), status_item)
        window.add_device(Device('192.168.1.20', name='Door updated', protocols=['ONVIF']))
        self.assertIs(window.table.item(0, 1), name_item)
        window.close()

    def test_monitor_and_connection_settings_stay_inside_main_window(self):
        from camera_monitor.credentials import CredentialStore
        from test_credentials import MemoryVault
        window=Window()
        self.assertIsNotNone(window.wall)
        self.assertFalse(window.wall.isWindow())
        window.wall.credential_store=CredentialStore(MemoryVault())
        window.add_device(Device('embedded-test',name='Door'))
        window.table.selectRow(0)
        window.show();window.open_player(force_settings=True)
        tile=window.wall.tiles[0]
        self.assertFalse(tile.player.isWindow())
        self.assertIs(tile.player.window(),window)
        self.assertTrue(window.settings_panel.isVisible())
        self.assertEqual(window.players,[])
        window.close_settings()
        self.assertFalse(window.settings_panel.isVisible())
        self.assertIn(tile,window.wall.tiles)
        window.close()

    def test_remove_embedded_settings_releases_page_and_keeps_other_tile(self):
        from camera_monitor.credentials import CredentialStore
        from test_credentials import MemoryVault
        window=Window();window.wall.credential_store=CredentialStore(MemoryVault())
        for ip in ('test-a','test-b'):window.add_device(Device(ip))
        window.show()
        window.table.selectRow(0);window.open_player(force_settings=True)
        first=window.wall.tiles[0]
        window.table.selectRow(1);window.open_player(force_settings=True)
        second=window.wall.tiles[1]
        window.wall.remove_tile(first)
        self.assertEqual(window.settings_stack.count(),1)
        self.assertIs(window.current_settings,second.player)
        window.wall.remove_tile(second)
        self.assertEqual(window.settings_stack.count(),0)
        self.assertIsNone(window.current_settings)
        self.assertFalse(window.settings_panel.isVisible())
        window.close()

    def test_escape_does_not_destroy_embedded_monitor(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        window=Window();window.show()
        wall=window.wall
        wall.stop_all.setFocus()
        QTest.keyClick(wall.stop_all,Qt.Key.Key_Escape)
        self.assertIs(window.wall,wall)
        self.assertTrue(wall.isVisible())
        window.close()

    def test_settings_overlay_does_not_reduce_video_area(self):
        from camera_monitor.credentials import CredentialStore
        from test_credentials import MemoryVault
        window=Window();window.wall.credential_store=CredentialStore(MemoryVault())
        window.add_device(Device('overlay-test'));window.show();self.app.processEvents()
        before=window.wall.geometry()
        window.table.selectRow(0);window.open_player(force_settings=True);self.app.processEvents()
        self.assertEqual(window.wall.geometry(),before)
        self.assertFalse(window.settings_panel.isWindow())
        self.assertTrue(window.settings_panel.isVisible())
        window.close()

    def test_device_activation_connects_without_saved_password(self):
        from unittest.mock import patch
        from camera_monitor.credentials import CredentialStore
        from test_credentials import MemoryVault
        window=Window();window.wall.credential_store=CredentialStore(MemoryVault())
        window.add_device(Device('anonymous-test'));window.table.selectRow(0)
        window.wall.add_device(window.devices['anonymous-test'])
        player=window.wall.tiles[0].player
        with patch.object(player,'connect_camera') as connect:
            window.open_player()
            connect.assert_called_once()
        self.assertFalse(window.settings_panel.isVisible())
        window.close()
