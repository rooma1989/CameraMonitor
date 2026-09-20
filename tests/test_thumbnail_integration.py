import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal, QSettings
from PySide6.QtGui import QImage, QCloseEvent
from PySide6.QtWidgets import QApplication
from camera_monitor.app import Window
from camera_monitor.device_names import DeviceNames
from camera_monitor.discovery import Device


class IdleSearch(QObject):
    device=Signal(object)
    message=Signal(str)
    finished=Signal()
    def __init__(self, networks, parent=None):
        super().__init__(parent)
        self.cancel=threading.Event()
    def start(self): pass
    def isRunning(self): return False


class ThumbnailIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.settings=QSettings(os.path.join(self.folder.name,'test.ini'),QSettings.Format.IniFormat)
        with patch('camera_monitor.app.interfaces',return_value=[]):
            self.window=Window(device_names=DeviceNames(self.settings),
                cloud_settings=QSettings(os.path.join(self.folder.name,'cloud.ini'),QSettings.Format.IniFormat))
        self.window.thumbnail_timer.stop()

    def tearDown(self):
        with patch('camera_monitor.app.request_unlock',return_value=True):
            self.window.close()
            self.app.processEvents()
        self.folder.cleanup()

    def add(self,ip):
        device=Device(ip,name='Camera '+ip)
        self.window.add_device(device)
        return device

    def test_finish_scan_requests_each_device_with_session_credentials(self):
        first=self.add('192.168.1.1');second=self.add('192.168.1.2')
        credentials=('viewer','test-secret',False)
        self.window.session_credentials[first.ip]=credentials
        with patch.object(self.window.thumbnails,'request') as request:
            self.window.finish_scan()
        self.assertEqual(request.call_count,2)
        request.assert_any_call(first,credentials)
        request.assert_any_call(second,None)

    def test_rescan_cancels_generation_and_old_result_cannot_replace_new_row(self):
        device=self.add('192.168.1.1')
        controller=self.window.thumbnails
        controller.tokens[device.ip]=7
        with patch('camera_monitor.app.SearchWorker',IdleSearch):
            self.window.start_scan()
        self.assertFalse(controller.tokens)
        self.assertEqual(self.window.table.rowCount(),0)
        self.add(device.ip)
        image=QImage(32,24,QImage.Format.Format_RGB888)
        controller._accept(device.ip,7,image,'old')
        self.assertIsNone(self.window.table.rows[0].thumbnail_image)

    def test_thumbnail_click_uses_clicked_row_even_with_different_selection(self):
        self.add('192.168.1.1');second=self.add('192.168.1.2')
        image=QImage(32,24,QImage.Format.Format_RGB888)
        self.window.update_thumbnail(second.ip,image,'抓拍画面')
        self.window.table.selectRow(0)
        with patch('camera_monitor.app.ThumbnailPreview') as dialog:
            self.window.table.rows[1].thumbnail.click()
        dialog.assert_called_once()
        args=dialog.call_args.args
        self.assertIn(second.ip,args[1])
        self.assertEqual(args[0].size(),image.size())
        dialog.return_value.exec.assert_called_once()
        self.assertEqual(self.window.table.currentRow(),0)

    def test_active_surface_refresh_does_not_create_network_or_read_vault(self):
        device=self.add('192.168.1.1')
        image=QImage(32,24,QImage.Format.Format_RGB888)
        player=SimpleNamespace(device=device,surface=SimpleNamespace(_image=image))
        tile=SimpleNamespace(player=player)
        original=self.window.wall.tiles
        self.window.wall.tiles=[tile]
        try:
            with patch.object(self.window.thumbnails,'worker_factory') as factory, \
                 patch.object(self.window.thumbnails.store,'load') as load:
                self.window.refresh_live_thumbnails()
            factory.assert_not_called();load.assert_not_called()
            self.assertEqual(self.window.table.rows[0].thumbnail_image.size(),image.size())
        finally:
            self.window.wall.tiles=original

    def test_close_waits_for_thumbnail_workers_before_closing_wall(self):
        event=QCloseEvent()
        with patch.object(self.window.thumbnails,'cancel_all') as cancel, \
             patch.object(self.window.thumbnails,'busy',return_value=True), \
             patch.object(self.window.wall,'close') as close_wall, \
             patch('camera_monitor.app.QTimer.singleShot') as later:
            self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        cancel.assert_called_once();close_wall.assert_not_called();later.assert_called_once()
        self.assertFalse(self.window.thumbnail_timer.isActive())

    def test_discovery_finishing_during_close_does_not_start_thumbnails(self):
        self.add('192.168.1.1')
        self.window.worker=IdleSearch([],self.window)
        event=QCloseEvent()
        with patch.object(self.window.thumbnails,'busy',return_value=True), \
             patch.object(self.window.worker,'isRunning',return_value=True), \
             patch('camera_monitor.app.QTimer.singleShot'):
            self.window.closeEvent(event)
        self.assertTrue(self.window.closing)
        self.assertTrue(self.window.worker.cancel.is_set())
        with patch.object(self.window.thumbnails,'request') as request:
            self.window.finish_scan()
        request.assert_not_called()
