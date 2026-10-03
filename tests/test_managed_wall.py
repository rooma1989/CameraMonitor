import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from camera_monitor.discovery import Device
from support import make_window
from test_credentials import MemoryVault


def mouse(kind, button=Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, QPointF(5, 5), QPointF(5, 5), button, button,
                       Qt.KeyboardModifier.NoModifier)


class LockedWallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = make_window(self)
        self.window.thumbnails.request = lambda *args, **kwargs: None
        self.wall = self.window.wall
        self.wall.add_device(Device('10.0.0.1'))
        self.tile = self.wall.tiles[0]

    def test_a_click_on_an_unlocked_wall_enlarges_the_tile(self):
        surface = self.tile.player.surface
        QApplication.sendEvent(surface, mouse(QEvent.Type.MouseButtonPress))
        QApplication.sendEvent(surface, mouse(QEvent.Type.MouseButtonRelease))

        self.assertIs(self.tile, self.wall.focused_tile)

    def test_a_locked_wall_swallows_clicks_and_double_clicks(self):
        self.wall.set_locked(True)
        surface = self.tile.player.surface

        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                     QEvent.Type.MouseButtonDblClick):
            QApplication.sendEvent(surface, mouse(kind))

        self.assertIsNone(self.wall.focused_tile)

    def test_locking_drops_an_enlarged_tile(self):
        self.wall.toggle_focus(self.tile)

        self.wall.set_locked(True)

        self.assertIsNone(self.wall.focused_tile)



if __name__ == '__main__':
    unittest.main()
