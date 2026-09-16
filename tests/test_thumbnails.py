import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import tempfile
import unittest
from unittest.mock import patch, Mock
import av
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from camera_monitor.discovery import Device
from camera_monitor.thumbnails import ThumbnailWorker, ThumbnailController, first_frame, ThumbnailPreview
from camera_monitor.device_list import DeviceList


class FakeWorker(QObject):
    result = Signal(str, int, object, str)
    finished = Signal()
    def __init__(self, device, token, store, options, credentials, parent):
        super().__init__(parent)
        self.device, self.token = device, token
        self.cancel = threading.Event()
    def start(self): pass


class ThumbnailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_live_frame_is_reused_without_worker_or_credentials(self):
        image = QImage(64, 48, QImage.Format.Format_RGB888)
        store, factory = Mock(), Mock()
        controller = ThumbnailController(store=store, live_image=lambda ip: image, worker_factory=factory)
        results = []
        controller.updated.connect(lambda *args: results.append(args))
        controller.request(Device('192.168.1.2'))
        factory.assert_not_called()
        store.load.assert_not_called()
        self.assertEqual(results[-1][1].width(), 64)

    def test_missing_credentials_allow_anonymous_udp_preview(self):
        worker = ThumbnailWorker(Device('192.168.1.234'), 1,
            Mock(load=Mock(return_value=None)), {'transport': 'udp'})
        results=[]
        worker.result.connect(lambda *args: results.append(args))
        image = QImage(64, 48, QImage.Format.Format_RGB888)
        client = Mock()
        client.streams.return_value = [Mock(url='rtsp://192.168.1.234/live')]
        with patch('camera_monitor.thumbnails.OnvifClient', return_value=client) as resolve, \
             patch('camera_monitor.thumbnails.first_frame', return_value=image) as read:
            worker.run()
        resolve.assert_called_once_with(worker.device, '', '', worker.cancel)
        read.assert_called_once_with('rtsp://192.168.1.234/live', worker.cancel, 'udp')
        self.assertIs(results[-1][2], image)
        client.close.assert_called_once()

    def test_anonymous_auth_failure_prompts_for_credentials_without_retry(self):
        from camera_monitor.streams import AuthError
        worker = ThumbnailWorker(Device('192.168.1.2'), 1,
            Mock(load=Mock(return_value=None)), {})
        results=[]
        worker.result.connect(lambda *args: results.append(args))
        client = Mock()
        client.streams.side_effect = AuthError('secret must not appear')
        with patch('camera_monitor.thumbnails.OnvifClient', return_value=client), \
             patch('camera_monitor.thumbnails.first_frame') as read:
            worker.run()
        read.assert_not_called()
        client.streams.assert_called_once()
        self.assertEqual(results[-1][3], '认证失败，请检查账号密码')
        client.close.assert_called_once()

    def test_saved_udp_and_credentials_reach_preview_worker(self):
        factory = Mock(side_effect=FakeWorker)
        store = Mock()
        controller = ThumbnailController(store=store,
            connection_options=Mock(transport=Mock(return_value='udp')), worker_factory=factory)
        device = Device('192.168.1.234')
        controller.request(device)
        args = factory.call_args.args
        self.assertIs(args[2], store)
        self.assertEqual(args[3]['transport'], 'udp')
        self.assertIsNone(args[4])  # The worker loads the vault when no session is supplied.
        controller.cancel_all()

    def test_concurrency_cancellation_and_stale_results(self):
        controller = ThumbnailController(store=Mock(), worker_factory=FakeWorker)
        results=[]
        controller.updated.connect(lambda *args: results.append(args))
        for i in range(5): controller.request(Device(f'192.168.1.{i}'))
        self.assertEqual(len(controller.active), 2)
        self.assertEqual(len(controller.pending), 3)
        worker = next(iter(controller.active))
        worker.finished.emit()
        self.assertEqual(len(controller.active), 2)
        self.assertEqual(len(controller.pending), 2)
        active = list(controller.active)
        controller.cancel_all()
        before = len(results)
        for worker in active:
            self.assertTrue(worker.cancel.is_set())
            worker.result.emit(worker.device.ip, worker.token, None, 'stale')
            worker.finished.emit()
        self.assertEqual(len(results), before)
        self.assertFalse(controller.busy())

    def test_auth_errors_are_sanitized_and_manual_host_is_validated(self):
        for url in ('rtsp://192.168.1.2/live', 'rtsp://other-host/live'):
            worker = ThumbnailWorker(Device('192.168.1.2'), 1, Mock(),
                {'mode':'manual', 'url':url}, ('viewer','never_print'))
            results=[]
            worker.result.connect(lambda *args: results.append(args))
            with patch('camera_monitor.thumbnails.first_frame', side_effect=RuntimeError('never_print')) as read:
                worker.run()
            self.assertNotIn('never_print', str(results))
            self.assertIsNone(worker.credentials)
            if 'other-host' in url: read.assert_not_called()

    def test_actual_encoded_video_yields_one_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'sample.mp4')
            with av.open(path, 'w') as container:
                stream=container.add_stream('mpeg4',rate=10)
                stream.width,stream.height,stream.pix_fmt=64,48,'yuv420p'
                for _ in range(4):
                    frame=av.VideoFrame(64,48,'yuv420p')
                    for plane in frame.planes: plane.update(bytes([128])*plane.buffer_size)
                    for packet in stream.encode(frame):container.mux(packet)
                for packet in stream.encode():container.mux(packet)
            image=first_frame(path,threading.Event())
            self.assertEqual((image.width(),image.height()),(64,48))
            cancel=threading.Event();cancel.set()
            with patch('camera_monitor.thumbnails.av.open') as opening:
                self.assertIsNone(first_frame(path,cancel))
            opening.assert_not_called()

    def test_click_preview_preserves_row_selection_and_is_not_fullscreen(self):
        listing=DeviceList(compact=True)
        listing.insertRow(0)
        image=QImage(64,48,QImage.Format.Format_RGB888)
        listing.setThumbnail(0,image,'抓拍画面')
        clicked=[]
        listing.thumbnailClicked.connect(clicked.append)
        listing.rows[0].thumbnail.click()
        self.assertEqual(clicked,[0])
        self.assertEqual(listing.currentRow(),-1)
        preview=ThumbnailPreview(image,'测试',listing)
        preview.show();self.app.processEvents()
        self.assertFalse(preview.isFullScreen())
        preview.close();listing.close()
