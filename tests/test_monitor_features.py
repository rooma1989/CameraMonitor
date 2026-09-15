import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from camera_monitor.app import Window
from camera_monitor.device_names import DeviceNames
from camera_monitor.discovery import Device
from camera_monitor.credentials import CredentialStore
from test_credentials import MemoryVault

class MonitorFeatures(unittest.TestCase):
 @classmethod
 def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.names=DeviceNames(QSettings(self.tmp.name+'/prefs.ini',QSettings.Format.IniFormat))
 def window(self):
  with patch('camera_monitor.app.interfaces',return_value=[]):w=Window(device_names=self.names)
  w.wall.credential_store=CredentialStore(MemoryVault())
  self.addCleanup(w.close);return w
 def test_fullscreen_hides_chrome_restores_without_replacing_players(self):
  w=self.window();w.show();w.wall.add_device(Device('test-one'))
  p=w.wall.tiles[0].player
  w.toggle_fullscreen();self.app.processEvents()
  self.assertTrue(w.wall.presentation)
  self.assertFalse(w.sidebar.isVisible());self.assertFalse(w.settings_tab.isVisible())
  self.assertFalse(w.wall.toolbar_widget.isVisible());self.assertFalse(w.wall.hint.isVisible())
  self.assertTrue(w.wall.tiles[0].name_overlay.isVisible())
  w.toggle_fullscreen();self.app.processEvents()
  self.assertFalse(w.wall.presentation);self.assertTrue(w.sidebar.isVisible())
  self.assertIs(w.wall.tiles[0].player,p)
 def test_drag_reorders_existing_players_and_persists(self):
  w=self.window()
  for ip in ('one','two','three'):w.wall.add_device(Device(ip))
  first,last=w.wall.tiles[0],w.wall.tiles[-1]
  self.assertTrue(w.wall.swap_tiles(first,last))
  self.assertEqual([t.player.device.ip for t in w.wall.tiles],['three','two','one'])
  self.assertEqual(self.names.order(),['three','two','one'])
  self.assertIs(w.wall.tiles[-1],first)
 def test_overlay_style_persists_and_name_is_plain_text(self):
  self.names.save_appearance('one','#ffff00','bottom-right')
  again=DeviceNames(QSettings(self.tmp.name+'/prefs.ini',QSettings.Format.IniFormat))
  self.assertEqual(again.appearance('one'),('#ffff00','bottom-right'))
  w=self.window();w.wall.add_device(Device('one'));self.names.save('one','<大门>')
  t=w.wall.tiles[0];w.show();w.toggle_fullscreen();self.app.processEvents()
  self.assertEqual(t.name_overlay.text(),'<大门>')
  self.assertEqual(t.name_overlay.textFormat(),Qt.TextFormat.PlainText)
  self.assertGreater(t.name_overlay.x(),t.player.surface.width()/2)
 def test_batch_only_changes_selected_and_defers_active_player(self):
  w=self.window()
  for ip in ('one','two'):w.add_device(Device(ip));w.wall.add_device(Device(ip))
  p=w.wall.tiles[0].player
  with patch.object(p,'busy',return_value=True):
   result=w.apply_batch_credentials(['one'],'operator','test-password',True)
  self.assertEqual(result,(1,[]))
  self.assertEqual(p.pending_credentials,('operator','test-password',True))
  self.assertEqual(w.wall.credential_store.load('one'),('operator','test-password'))
  self.assertIsNone(w.wall.credential_store.load('two'))
 def test_stretch_preserves_entire_image(self):
  from camera_monitor.playback import VideoSurface
  s=VideoSurface();s.setMinimumSize(1,1);s.resize(320,180);s.set_stretch(True)
  image=QImage(100,100,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.red)
  s.show_frame(image)
  self.assertEqual(s.pixmap().size(),s.size())

 def test_every_featured_layout_covers_canvas_once_at_different_sizes(self):
  from camera_monitor.wall_layout import wall_rectangles
  for count in (6,10,15):
   for width,height in ((1920,1080),(1365,767),(1000,1200)):
    with self.subTest(count=count,size=(width,height)):
     rects=wall_rectangles(count,width,height)
     self.assertEqual(len(rects),count)
     self.assertEqual(sum(w*h for x,y,w,h in rects),width*height)
     self.assertGreater(rects[0][2]*rects[0][3],max(w*h for x,y,w,h in rects[1:]))
     for i,(x,y,w,h) in enumerate(rects):
      self.assertTrue(x>=0 and y>=0 and x+w<=width and y+h<=height)
      for xx,yy,ww,hh in rects[i+1:]:
       self.assertFalse(max(x,xx)<min(x+w,xx+ww) and max(y,yy)<min(y+h,yy+hh))
 def test_all_fifteen_tiles_visible_in_presentation_and_order_survives_readd(self):
  w=self.window();w.wall.change_layout(15);w.show()
  for i in range(15):self.assertTrue(w.wall.add_device(Device(str(i))))
  self.assertFalse(w.wall.add_device(Device('overflow')))
  w.toggle_fullscreen();self.app.processEvents()
  self.assertTrue(all(t.isVisible() for t in w.wall.tiles))
  self.assertFalse(any(t.controls.isVisible() for t in w.wall.tiles))
  w.wall.swap_tiles(w.wall.tiles[0],w.wall.tiles[-1])
  again=self.window();again.wall.change_layout(15)
  for i in range(15):again.wall.add_device(Device(str(i)))
  self.assertEqual([t.player.device.ip for t in again.wall.tiles],self.names.order())
 def test_batch_save_failure_does_not_change_device_or_echo_secret(self):
  w=self.window();w.add_device(Device('one'));w.wall.add_device(Device('one'))
  from camera_monitor.credentials import CredentialError
  with patch.object(w.wall.credential_store,'save',side_effect=CredentialError('Storage unavailable')):
   result=w.apply_batch_credentials(['one'],'operator','private-test',True)
  self.assertEqual(result,(0,['one']))
  self.assertNotIn('one',w.session_credentials)
  self.assertEqual(w.wall.tiles[0].player.password.text(),'')
 def test_batch_unsaved_does_not_overwrite_vault_and_old_stream_cannot_save_over_new(self):
  w=self.window();w.add_device(Device('one'));w.wall.add_device(Device('one'))
  p=w.wall.tiles[0].player
  w.wall.credential_store.save('one','old','old-test')
  w.apply_batch_credentials(['one'],'session','session-test',False)
  self.assertEqual(w.wall.credential_store.load('one'),('old','old-test'))
  self.assertEqual(p.password.text(),'session-test')
  with patch.object(p,'busy',return_value=True):
   w.apply_batch_credentials(['one'],'new','new-test',True)
  p.on_ready('test')
  self.assertEqual(w.wall.credential_store.load('one'),('new','new-test'))
  with patch.object(p,'load_streams'):
   p.mode.setCurrentIndex(1);p.connect_camera()
  self.assertIsNone(p.pending_credentials)
  self.assertEqual(p.username.text(),'new')
 def test_escape_exits_presentation(self):
  from PySide6.QtTest import QTest
  w=self.window();w.show();w.toggle_fullscreen();w.activateWindow();self.app.processEvents()
  QTest.keyClick(w,Qt.Key.Key_Escape);self.app.processEvents()
  self.assertFalse(w.presentation);self.assertFalse(w.wall.presentation)
 def test_drag_gesture_and_drop_swap_without_reconnect(self):
  from PySide6.QtCore import QEvent,QPointF
  from PySide6.QtGui import QMouseEvent
  from unittest.mock import Mock
  w=self.window();w.show()
  for ip in ('one','two'):w.wall.add_device(Device(ip))
  a,b=w.wall.tiles
  press=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(20,20),QPointF(20,20),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
  move=QMouseEvent(QEvent.Type.MouseMove,QPointF(120,20),QPointF(120,20),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
  with patch('camera_monitor.multiview.QDrag') as drag:
   QApplication.sendEvent(a.player.surface,press);QApplication.sendEvent(a.player.surface,move)
   drag.assert_called_once_with(a);drag.return_value.exec.assert_called_once()
  event=Mock();event.source.return_value=a
  with patch.object(a.player,'connect_camera') as connect:
   b.dropEvent(event)
   event.acceptProposedAction.assert_called_once();connect.assert_not_called()
  self.assertIs(w.wall.tiles[0],b)
  outside=Mock();outside.source.return_value=None
  b.dropEvent(outside);outside.ignore.assert_called_once()
 def test_batch_panel_selection_and_no_selection(self):
  from camera_monitor.batch_settings import BatchSettings
  from unittest.mock import Mock
  apply=Mock(return_value=(1,[]))
  panel=BatchSettings([Device('one'),Device('two')],self.names,apply)
  panel.select_all(False);panel.submit_credentials();apply.assert_not_called()
  panel.checks[1][1].setChecked(True);panel.username.setText('viewer');panel.password.setText('test-only')
  panel.submit_credentials();apply.assert_called_once_with(['two'],'viewer','test-only',True)
  self.assertEqual(panel.password.text(),'');panel.close()

 def test_fixed_slots_ratio_click_and_organization(self):
  from PySide6.QtTest import QTest
  w=self.window();w.resize(1500,900);w.show()
  for ip in ('one','two'):w.wall.add_device(Device(ip))
  for count in (4,9,12,16,20,25):
   self.assertTrue(w.wall.change_layout(count));self.app.processEvents();w.wall.relayout()
   self.assertEqual(len(w.wall.placeholders),count-2)
   sizes=[]
   for tile in w.wall.tiles:
    surface=tile.player.surface
    self.assertAlmostEqual(surface.height(),surface.width()*9/16,delta=1)
    sizes.append(surface.size())
   self.assertEqual(sizes[0],sizes[1])
  first=w.wall.tiles[0]
  QTest.mouseClick(first.player.surface,Qt.MouseButton.LeftButton)
  self.assertIs(w.wall.focused_tile,first);self.assertEqual(len(w.wall.placeholders),0)
  QTest.mouseClick(first.player.surface,Qt.MouseButton.LeftButton)
  self.assertIsNone(w.wall.focused_tile);self.assertEqual(len(w.wall.placeholders),23)
  w.wall.organization_input.setText('<阳光养老院>');w.wall.save_organization()
  w.toggle_fullscreen();self.app.processEvents();w.wall.relayout()
  self.assertTrue(w.wall.organization_header.isVisible())
  self.assertEqual(w.wall.organization_header.textFormat(),Qt.TextFormat.PlainText)
  self.assertAlmostEqual(first.player.surface.height(),first.player.surface.width()*9/16,delta=1)
  again=self.window()
  self.assertEqual(again.wall.organization_input.text(),'<阳光养老院>')
  w.wall.organization_input.clear();w.wall.save_organization()
  self.assertFalse(w.wall.organization_header.isVisible())
