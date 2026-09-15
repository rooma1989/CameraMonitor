import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
import av
from PySide6.QtWidgets import QApplication
from camera_monitor.playback import Decoder, PlayerWindow
from camera_monitor.discovery import Device

class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from camera_monitor.credentials import CredentialStore
        from test_credentials import MemoryVault
        self.store=CredentialStore(MemoryVault())

    def test_decoder_produces_actual_frame_from_encoded_video(self):
        with tempfile.TemporaryDirectory() as folder:
            video_path = os.path.join(folder, 'sample.mp4')
            with av.open(video_path, 'w') as container:
                stream = container.add_stream('mpeg4', rate=10)
                stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
                for _ in range(5):
                    frame = av.VideoFrame(64,48,'yuv420p')
                    for plane in frame.planes: plane.update(bytes([128]) * plane.buffer_size)
                    for packet in stream.encode(frame): container.mux(packet)
                for packet in stream.encode(): container.mux(packet)
            worker = Decoder(video_path)
            worker.start()
            self.assertTrue(worker.wait(5000))
            image = worker.take_frame()
            self.assertIsNotNone(image)
            self.assertEqual((image.width(),image.height()), (64,48))

    def test_player_masks_password_and_does_not_claim_playing_before_frame(self):
        w = PlayerWindow(Device('192.168.1.108', protocols=['大华 DHIP']),credential_store=self.store)
        self.assertEqual(w.password.echoMode(), w.password.EchoMode.Password)
        self.assertNotIn('正在播放', w.status.text())
        self.assertFalse(w.stop_button.isEnabled())
        w.close()

    def test_invalid_rtsp_fails_without_echoing_credentials(self):
        worker = Decoder('rtsp://sensitive:never_print@127.0.0.1:1/live')
        messages=[]
        worker.error.connect(messages.append)
        worker.start()
        self.assertTrue(worker.wait(8000))
        self.app.processEvents()
        self.assertTrue(messages)
        self.assertNotIn('never_print', ' '.join(messages))

    def test_escape_cleans_credentials_through_normal_close_path(self):
        w = PlayerWindow(Device('192.168.1.108'),credential_store=self.store)
        w.password.setText('erase_on_close')
        w.show()
        w.reject()
        self.assertEqual(w.password.text(), '')
        self.assertFalse(w.isVisible())

    def test_remember_only_after_first_frame_and_forget(self):
        from camera_monitor.credentials import CredentialStore
        from test_credentials import MemoryVault
        store=CredentialStore(MemoryVault())
        w=PlayerWindow(Device('camera-test'),credential_store=store)
        w.username.setText('viewer');w.password.setText('secret')
        w.remember.setChecked(True)
        w.report_error('认证失败')
        self.assertIsNone(store.load('camera-test'))
        w.on_ready('1280 × 720')
        self.assertEqual(store.load('camera-test'),('viewer','secret'))
        w.close()
        again=PlayerWindow(Device('camera-test'),credential_store=store)
        self.assertEqual(again.password.text(),'secret')
        self.assertTrue(again.remember.isChecked())
        again.remember.setChecked(False)
        self.assertIsNone(store.load('camera-test'))
        again.close()

    def test_remember_defaults_on_and_can_save_after_playback(self):
        w=PlayerWindow(Device('remember-test'),credential_store=self.store)
        self.assertTrue(w.remember.isChecked())
        w.remember.setChecked(False)
        w.username.setText('viewer');w.password.setText('test-secret')
        w.set_busy(True);w.on_ready('H264')
        self.assertTrue(w.remember.isEnabled())
        w.remember.setChecked(True)
        self.assertEqual(self.store.load('remember-test'),('viewer','test-secret'))
        w.close()

    def test_hosted_success_hides_settings_but_failure_keeps_them(self):
        from unittest.mock import patch
        w=PlayerWindow(Device('host-test',protocols=['大华 DHIP']),credential_store=self.store)
        w.hosted=True;w.show()
        with patch.object(w,'start_decoder',side_effect=lambda _:w.on_ready('H264')):
            w.connect_camera()
        self.assertFalse(w.isVisible())
        w.show();w.on_ready('reconnected')
        self.assertTrue(w.isVisible())
        w.hosted=False;w.close()

    def test_save_failure_keeps_connection_settings_visible(self):
        from unittest.mock import patch
        from camera_monitor.credentials import CredentialError
        w=PlayerWindow(Device('save-fail',protocols=['大华 DHIP']),credential_store=self.store)
        w.hosted=True;w.username.setText('viewer');w.password.setText('test-secret');w.show()
        with patch.object(self.store,'save',side_effect=CredentialError('保存失败')):
            with patch.object(w,'start_decoder',side_effect=lambda _:w.on_ready('H264')):
                w.connect_camera()
        self.assertTrue(w.isVisible())
        self.assertIn('密码未保存',w.status.text())
        w.hosted=False;w.close()

    def test_fill_scales_without_distortion_and_crops_to_viewport(self):
        from PySide6.QtGui import QImage,QColor
        from camera_monitor.playback import VideoSurface
        w=VideoSurface();w.resize(600,300)
        self.assertFalse(w.fill)
        w.set_fill(True)
        frame=QImage(300,300,QImage.Format.Format_RGB32);frame.fill(QColor('red'))
        # Mark image edges that should be cropped vertically, not stretched.
        for y in range(50):
            for x in range(300):frame.setPixelColor(x,y,QColor('blue'))
        w.show_frame(frame)
        self.assertEqual(w.pixmap().size(),w.size())
        self.assertEqual(w.pixmap().toImage().pixelColor(300,0),QColor('red'))
        w.set_fill(False)
        self.assertEqual(w.pixmap().width(),300)
        self.assertEqual(w.pixmap().height(),300)
        w.close()

    def test_default_preserves_entire_frame_without_black_background(self):
        from PySide6.QtGui import QImage,QColor
        from camera_monitor.playback import VideoSurface
        w=VideoSurface();w.resize(600,300)
        frame=QImage(300,300,QImage.Format.Format_RGB32);frame.fill(QColor('red'))
        frame.setPixelColor(0,0,QColor('blue'))
        frame.setPixelColor(299,299,QColor('green'))
        w.show_frame(frame)
        result=w.pixmap().toImage()
        self.assertEqual(result.size(),frame.size())
        self.assertEqual(result.pixelColor(0,0),QColor('blue'))
        self.assertEqual(result.pixelColor(299,299),QColor('green'))
        self.assertIn('transparent',w.styleSheet())
        w.close()
