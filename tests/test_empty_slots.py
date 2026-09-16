import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from unittest.mock import Mock,patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings,QMimeData
from camera_monitor.multiview import MultiView
from camera_monitor.device_names import DeviceNames
from camera_monitor.discovery import Device
from camera_monitor.credentials import CredentialStore
from camera_monitor.wall_layout import wall_rectangles
from test_credentials import MemoryVault

class EmptySlotTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.names=DeviceNames(QSettings(self.tmp.name+'/prefs.ini',QSettings.Format.IniFormat))
 def wall(self):
  w=MultiView([],device_names=self.names,credential_store=CredentialStore(MemoryVault()))
  self.addCleanup(w.close);return w
 def test_drop_on_empty_preserves_players_and_leaves_old_slot_empty(self):
  w=self.wall();w.change_layout(10);w.show()
  for ip in ('one','two'):w.add_device(Device(ip))
  original=w.tiles[0];target=next(p for p in w.placeholders if p.slot_index==9)
  mime=QMimeData();mime.setData('application/x-camera-monitor-tile',b'move')
  event=Mock();event.source.return_value=original;event.mimeData.return_value=mime
  with patch.object(original.player,'connect_camera') as connect:
   target.dragEnterEvent(event);target.dropEvent(event);connect.assert_not_called()
  self.assertIs(w.slots[9],original);self.assertIsNone(w.slots[0])
  self.assertEqual(len(w.placeholders),8)
  self.assertEqual(self.names.slot_order()[9],'one')
  again=self.wall();again.change_layout(10)
  again.add_device(Device('two'));again.add_device(Device('one'))
  self.assertEqual(again.slots[9].player.device.ip,'one');self.assertIsNone(again.slots[0])
  w.toggle_focus(original);self.assertEqual(len(w.placeholders),0)
  w.toggle_focus(original);self.assertIs(w.slots[9],original)
 def test_shrink_moves_overflow_into_available_slots_without_reconnecting(self):
  w=self.wall();w.change_layout(25)
  w.add_device(Device('one'));w.move_tile(w.tiles[0],24)
  p=w.tiles[0].player
  self.assertTrue(w.change_layout(4));self.assertIs(w.slots[0].player,p)
  self.assertEqual(len(w.placeholders),3)
  w.remove_tile(w.tiles[0]);self.assertEqual(len(w.placeholders),4)
 def test_ten_tile_ring_has_equal_neighbors_and_shared_bottom_corner(self):
  rects=wall_rectangles(10,1920,1080)
  self.assertEqual(rects[0],(0,0,1536,864))
  self.assertEqual({r[2:] for r in rects[1:]},{(384,216)})
  self.assertEqual(rects[-1],(1536,864,384,216))
  self.assertEqual(sum(w*h for x,y,w,h in rects),1920*1080)
