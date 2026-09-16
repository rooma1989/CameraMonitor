import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from camera_monitor.wall_layout import wall_rectangles
from camera_monitor.device_names import DeviceNames
from camera_monitor.multiview import MultiView

class HorizontalFillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_fill_removes_side_margins_without_changing_height_or_slot_count(self):
        for count in (1,4,6,9,10,12,15,16,20,25):
            original=wall_rectangles(count,1920,1080)
            filled=wall_rectangles(count,1920,1080,fill_width=True)
            self.assertEqual(len(filled),count)
            self.assertEqual(min(r[0] for r in filled),0)
            self.assertEqual(max(x+w for x,y,w,h in filled),1920)
            self.assertEqual([(y,h) for x,y,w,h in original],[(y,h) for x,y,w,h in filled])
            for i,(x,y,w,h) in enumerate(filled):
                for xx,yy,ww,hh in filled[i+1:]:
                    self.assertFalse(min(x+w,xx+ww)>max(x,xx) and min(y+h,yy+hh)>max(y,yy))

    def test_toggle_restores_geometry_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=QSettings(tmp+'/prefs.ini',QSettings.Format.IniFormat)
            wall=MultiView([],device_names=DeviceNames(settings))
            wall.resize(1600,900);wall.show();wall.change_layout(12);self.app.processEvents();wall.relayout()
            before=[p.geometry() for p in wall.placeholders]
            self.assertFalse(wall.fill_width.isChecked())
            wall.fill_width.click()
            self.assertEqual(min(p.x() for p in wall.placeholders),0)
            self.assertEqual(max(p.x()+p.width() for p in wall.placeholders),wall.canvas.width())
            wall.fill_width.click()
            self.assertEqual([p.geometry() for p in wall.placeholders],before)
            wall.fill_width.click();wall.close()
            other=MultiView([],device_names=DeviceNames(QSettings(tmp+'/prefs.ini',QSettings.Format.IniFormat)))
            self.assertTrue(other.fill_width.isChecked())
            other.set_presentation(True)
            other.fill_width.click()
            self.assertTrue(other.fill_width.isChecked())
            other.set_presentation(False);other.close()
