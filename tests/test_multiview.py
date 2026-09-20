import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication,QAbstractItemView
from camera_monitor.multiview import MultiView
from camera_monitor.discovery import Device
from camera_monitor.credentials import CredentialStore
from camera_monitor.device_names import DeviceNames
from test_credentials import MemoryVault

class MultiViewTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def window(self):
  # 必须注入设置：否则读写的是这台机器真实的偏好，测试结果会随使用者的配置漂移
  folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
  names=DeviceNames(QSettings(os.path.join(folder.name,'prefs.ini'),QSettings.Format.IniFormat))
  return MultiView([],credential_store=CredentialStore(MemoryVault()),device_names=names)
 def test_independent_players_duplicate_prevention_and_layout(self):
  w=self.window()
  self.assertTrue(w.add_device(Device('192.168.1.1')))
  self.assertTrue(w.add_device(Device('192.168.1.2')))
  self.assertFalse(w.add_device(Device('192.168.1.1')))
  a,b=w.active_tiles()
  self.assertIsNot(a.player,b.player)
  self.assertEqual(len(w.tiles),2)
  self.assertEqual(len(w.placeholders),2)
  self.assertTrue(w.change_layout(9))
  self.assertEqual(len(w.tiles),2)
  self.assertIs(w.active_tiles()[0].player,a.player)
  self.assertEqual(w.findChildren(QAbstractItemView),[])
  w.close()
 def test_shrinking_does_not_hide_active_cameras(self):
  w=self.window();w.change_layout(9)
  for i in range(5):w.add_device(Device(f'192.168.1.{i+1}'))
  self.assertFalse(w.change_layout(4))
  self.assertEqual(len(w.active_tiles()),5)
  w.close()
 def test_settings_close_keeps_hosted_player_and_wall_close_cleans_secret(self):
  w=self.window();w.add_device(Device('192.168.1.1'))
  p=w.active_tiles()[0].player
  p.password.setText('local-test')
  p.show();p.close()
  self.assertFalse(p.isVisible())
  self.assertTrue(p.timer.isActive())
  w.close()
  self.assertEqual(p.password.text(),'')
  self.assertFalse(p.timer.isActive())

 def test_auto_layout_stretches_sources_into_equal_slots(self):
  from PySide6.QtGui import QImage
  w=self.window();w.resize(1400,850);w.show()
  for ip in ('a','b'):w.add_device(Device(ip))
  a,b=w.tiles
  a.player.surface.show_frame(QImage(704,576,QImage.Format.Format_RGB32))
  b.player.surface.show_frame(QImage(1280,720,QImage.Format.Format_RGB32))
  self.app.processEvents();w.relayout()
  self.assertEqual(len(w.placeholders),2)
  self.assertTrue(a.player.surface.stretch)
  self.assertEqual(a.player.surface.pixmap().size(),a.player.surface.size())
  self.assertEqual(b.player.surface.pixmap().size(),b.player.surface.size())
  self.assertAlmostEqual(a.width(),b.width(),delta=2)
  self.assertAlmostEqual(a.player.surface.height(),b.player.surface.height(),delta=2)
  w.toggle_focus(a);self.assertFalse(b.isVisible())
  w.toggle_focus(a);self.assertTrue(b.isVisible())
  w.close()
