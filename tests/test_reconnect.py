import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile,unittest,threading
from PySide6.QtCore import Qt
from unittest.mock import patch
import av
from PySide6.QtWidgets import QApplication
from camera_monitor.playback import Decoder

class ReconnectTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def setUp(self):
  self.folder=tempfile.TemporaryDirectory()
  self.addCleanup(self.folder.cleanup)
  self.video_path=os.path.join(self.folder.name,'sample.mp4')
  with av.open(self.video_path,'w') as c:
   s=c.add_stream('mpeg4',rate=10);s.width=64;s.height=48;s.pix_fmt='yuv420p'
   f=av.VideoFrame(64,48,'yuv420p')
   for p in f.planes:p.update(bytes([128])*p.buffer_size)
   for packet in s.encode(f):c.mux(packet)
   for packet in s.encode():c.mux(packet)
 def test_live_eof_reopens_after_decoding_and_stop_cancels(self):
  worker=Decoder('rtsp://127.0.0.1/live');real_open=av.open;opens=[]
  def source(*a,**k):
   opens.append(1)
   if len(opens)==2:worker.cancel.set()
   return real_open(self.video_path)
  with patch('camera_monitor.playback.av.open',side_effect=source):
   worker.start();done=worker.wait(6000)
   if not done:worker.cancel.set();worker.wait(5000)
  self.assertTrue(done)
  self.assertEqual(len(opens),2)
  self.assertEqual(worker._url,'')
 def test_cancel_during_retry_wait_does_not_reopen(self):
  worker=Decoder('rtsp://127.0.0.1/live');real_open=av.open;opens=[]
  recovering=[]
  worker.recovering.connect(recovering.append,Qt.ConnectionType.DirectConnection)
  def source(*a,**k):opens.append(1);return real_open(self.video_path)
  with patch('camera_monitor.playback.av.open',side_effect=source):
   worker.start()
   threading.Timer(.3,worker.cancel.set).start()
   self.assertTrue(worker.wait(3000))
  self.assertEqual(len(opens),1)
  self.assertEqual(len(recovering),1)
 def test_initial_authentication_failure_does_not_retry(self):
  worker=Decoder('rtsp://127.0.0.1/live')
  with patch('camera_monitor.playback.av.open',side_effect=av.error.HTTPUnauthorizedError(0,'Unauthorized')) as op:
   worker.start();self.assertTrue(worker.wait(2000))
  self.assertEqual(op.call_count,1)

 def test_authentication_failure_after_success_stops_recovery(self):
  worker=Decoder('rtsp://127.0.0.1/live');real_open=av.open;opens=[]
  def source(*a,**k):
   opens.append(1)
   if len(opens)>1:raise av.error.HTTPUnauthorizedError(0,'Unauthorized')
   return real_open(self.video_path)
  with patch('camera_monitor.playback.av.open',side_effect=source):
   worker.start();done=worker.wait(4000)
   if not done:worker.cancel.set();worker.wait(5000)
  self.assertTrue(done)
  self.assertEqual(len(opens),2)

 def test_timeout_with_status_digits_in_url_still_recovers(self):
  worker=Decoder('rtsp://127.0.0.1/live');real_open=av.open;opens=[]
  def source(*a,**k):
   opens.append(1)
   if len(opens)==2:raise av.error.TimeoutError(110,'Connection timed out','rtsp://user:401@127.0.0.1/404')
   if len(opens)==3:worker.cancel.set()
   return real_open(self.video_path)
  with patch('camera_monitor.playback.av.open',side_effect=source):
   worker.start();done=worker.wait(8000)
   if not done:worker.cancel.set();worker.wait(5000)
  self.assertTrue(done)
  self.assertEqual(len(opens),3)

 def test_every_disconnect_uses_three_second_wait(self):
  worker=Decoder('rtsp://127.0.0.1/live');real_open=av.open;delays=[]
  def wait(delay):
   delays.append(delay)
   return len(delays)==4
  with patch('camera_monitor.playback.av.open',side_effect=lambda *a,**k:real_open(self.video_path)):
   with patch.object(worker.cancel,'wait',side_effect=wait):
    worker.start();done=worker.wait(2000)
    if not done:worker.cancel.set();worker.wait(5000)
  self.assertTrue(done)
  self.assertEqual(delays,[3,3,3,3])
