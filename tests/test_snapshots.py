import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from camera_monitor.snapshots import SnapshotUploader, encode_jpeg


def picture(width=2000, height=1000):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.darkGreen)
    return image


def pump(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class EncodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_wide_picture_is_shrunk_into_a_small_jpeg(self):
        data = encode_jpeg(picture(), 480, 200_000)

        self.assertTrue(data.startswith(b'\xff\xd8'))
        self.assertLessEqual(len(data), 200_000)
        self.assertLessEqual(QImage.fromData(data).width(), 480)

    def test_a_small_picture_keeps_its_size(self):
        data = encode_jpeg(picture(320, 180), 480, 200_000)

        self.assertEqual(320, QImage.fromData(data).width())

    def test_nothing_to_encode(self):
        self.assertEqual(b'', encode_jpeg(None, 480, 200_000))
        self.assertEqual(b'', encode_jpeg(QImage(), 480, 200_000))


class UploaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()

        def upload(kind, jpeg, ip):
            self.gate.wait(3)
            self.calls.append((kind, ip, jpeg[:2]))

        self.uploader = SnapshotUploader(upload)
        self.addCleanup(self.uploader.stop)

    def test_uploads_run_off_the_ui_thread_in_order(self):
        self.uploader.submit('camera', picture(), '10.0.0.1')
        self.uploader.submit('screen', picture())

        self.assertTrue(pump(lambda: len(self.calls) == 2 and not self.uploader.busy()))
        self.assertEqual([('camera', '10.0.0.1', b'\xff\xd8'), ('screen', '', b'\xff\xd8')], self.calls)

    def test_the_latest_picture_of_the_same_camera_wins(self):
        self.gate.clear()
        self.uploader.submit('camera', picture(), '10.0.0.9')
        self.uploader.submit('camera', picture(), '10.0.0.1')
        self.uploader.submit('camera', picture(), '10.0.0.1')

        self.assertEqual(1, len(self.uploader.queue), '排队中的同一路只留最新一张')
        self.gate.set()
        self.assertTrue(pump(lambda: len(self.calls) == 2))

    def test_a_failed_upload_does_not_stop_the_queue(self):
        outcomes = iter([RuntimeError('boom'), None])

        def flaky(kind, jpeg, ip):
            error = next(outcomes)
            if error:
                raise error
            self.calls.append(ip)

        uploader = SnapshotUploader(flaky)
        self.addCleanup(uploader.stop)
        uploader.submit('camera', picture(), '10.0.0.1')
        uploader.submit('camera', picture(), '10.0.0.2')

        self.assertTrue(pump(lambda: self.calls == ['10.0.0.2']))

    def test_an_empty_image_is_refused(self):
        self.assertFalse(self.uploader.submit('camera', QImage(), '10.0.0.1'))


if __name__ == '__main__':
    unittest.main()
