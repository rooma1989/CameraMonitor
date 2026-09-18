import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from camera_monitor.app import Window
from camera_monitor.discovery import Device
from test_thumbnail_integration import IdleSearch

class TargetSearch(IdleSearch):
    def __init__(self, networks, parent=None, target_ip=None):
        super().__init__(networks, parent)
        self.target_ip = target_ip

class TargetAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_target_add_preserves_existing_devices_and_reports_missing_target(self):
        window = Window()
        try:
            window.add_device(Device('192.168.2.220'))
            window.table.selectRow(0)
            with patch('camera_monitor.app.SearchWorker', TargetSearch):
                window.start_target_scan('192.168.2.216')
            self.assertIn('192.168.2.220', window.devices)
            self.assertEqual(window.worker.target_ip, '192.168.2.216')
            self.assertFalse(window.add_ip.isEnabled())
            with patch.object(window.thumbnails, 'request'):
                window.finish_scan()
            self.assertIn('192.168.2.216', window.status.text())
            self.assertIn('未收到', window.status.text())
            self.assertTrue(window.add_ip.isEnabled())
            self.assertTrue(window.copy.isEnabled())
        finally:
            window.close()

    def test_target_success_selects_device_and_opens_settings(self):
        window = Window()
        try:
            with patch('camera_monitor.app.SearchWorker', TargetSearch):
                window.start_target_scan('192.168.2.216')
            window.add_device(Device('192.168.2.216', protocols=['ONVIF']))
            with patch.object(window.thumbnails, 'request'), patch.object(window, 'open_selected_settings') as settings:
                window.finish_scan()
                settings.assert_called_once()
            self.assertEqual(window.table.item(window.table.currentRow(), 0).text(), '192.168.2.216')
        finally:
            window.close()

    def test_existing_target_without_new_reply_is_not_reported_as_success(self):
        window = Window()
        try:
            window.add_device(Device('192.168.2.216'))
            with patch('camera_monitor.app.SearchWorker', TargetSearch):
                window.start_target_scan('192.168.2.216')
            with patch.object(window.thumbnails, 'request'), patch.object(window, 'open_selected_settings') as settings:
                window.finish_scan()
                settings.assert_not_called()
            self.assertIn('未收到', window.status.text())
        finally:
            window.close()

    def test_invalid_input_does_not_start_search(self):
        window = Window()
        try:
            window.start_target_scan('invalid')
            self.assertIsNone(window.worker)
            self.assertIn('IPv4', window.status.text())
        finally:
            window.close()
