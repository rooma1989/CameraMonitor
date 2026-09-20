import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from camera_monitor.wall_layout import wall_rectangles
from camera_monitor.device_names import DeviceNames
from camera_monitor.multiview import MultiView

class GridColumnsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def test_defaults_and_custom_rows_keep_count_and_aspect(self):
        for count,columns in ((12,3),(20,4)):
            rects=wall_rectangles(count,1600,900,False)
            self.assertEqual(len({r[1] for r in rects}),count//columns)
            self.assertEqual(len({r[0] for r in rects}),columns)
        for count in (4,9,12,16,20,25):
            for columns in range(1,count+1):
                rects=wall_rectangles(count,1600,900,False,columns=columns)
                self.assertEqual(len(rects),count)
                self.assertEqual(len({r[0] for r in rects}),columns)
                for x,y,w,h in rects:
                    self.assertLessEqual(abs(w-h*16/9),3)
                    self.assertTrue(0<=x and x+w<=1600 and 0<=y and y+h<=900)

    def test_columns_persist_per_layout_and_do_not_change_slots(self):
        with tempfile.TemporaryDirectory() as folder:
            names=DeviceNames(QSettings(folder+'/settings.ini',QSettings.Format.IniFormat))
            wall=MultiView([],device_names=names)
            wall.change_layout(12)
            self.assertEqual(wall.grid_columns(),3)
            self.assertTrue(wall.set_grid_columns(4))
            self.assertEqual(len(wall.placeholders),12)
            wall.change_layout(20)
            self.assertEqual(wall.grid_columns(),4)
            wall.set_grid_columns(5)
            wall.change_layout(12)
            self.assertEqual(wall.grid_columns(),4)
            wall.set_grid_columns(None)
            self.assertEqual(wall.grid_columns(),3)
            wall.change_layout(10)
            self.assertFalse(wall.columns_choice.isEnabled())
            self.assertFalse(wall.set_grid_columns(3))
            wall.close()
            other=MultiView([],device_names=DeviceNames(QSettings(folder+'/settings.ini',QSettings.Format.IniFormat)))
            other.change_layout(20)
            self.assertEqual(other.grid_columns(),5)
            other.set_presentation(True)
            self.assertFalse(other.set_grid_columns(4))
            other.set_presentation(False)
            other.close()

    def test_invalid_saved_columns_use_default(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=QSettings(folder+'/settings.ini',QSettings.Format.IniFormat)
            for value in ('broken',0,100):
                settings.setValue('monitor/columns/12',value)
                wall=MultiView([],device_names=DeviceNames(settings))
                wall.change_layout(12)
                self.assertEqual(wall.grid_columns(),3)
                wall.close()


class DummyStore:
    def load(self, ip): return None
    def save(self, *args): pass
    def forget(self, ip): pass


class LayoutPersistenceTests(unittest.TestCase):
    """分屏数量要跨重启保留——纯单机用户同样受益，不只是云端同步。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self, path):
        from camera_monitor.device_names import DeviceNames
        from camera_monitor.multiview import MultiView
        names = DeviceNames(QSettings(path, QSettings.Format.IniFormat))
        view = MultiView([], device_names=names, credential_store=DummyStore())
        self.addCleanup(view.deleteLater)
        return view

    def test_the_chosen_layout_comes_back_next_time(self):
        import tempfile, os
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, 'prefs.ini')

        first = self.make(path)
        self.assertEqual(4, first.capacity, '初次使用仍然是 4 格')
        self.assertTrue(first.change_layout(16))

        again = self.make(path)
        self.assertEqual(16, again.capacity)
        self.assertEqual(16, again.layout_mode, '按钮高亮也要跟着走')

    def test_a_nonsense_saved_value_falls_back_to_four(self):
        import tempfile, os
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, 'prefs.ini')
        settings = QSettings(path, QSettings.Format.IniFormat)
        settings.setValue('monitor/capacity', 7)
        settings.sync()

        self.assertEqual(4, self.make(path).capacity)
