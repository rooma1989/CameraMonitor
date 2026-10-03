import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QFocusEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from camera_monitor.discovery import Device
from support import make_window
from test_credentials import MemoryVault


def mouse(kind, button=Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, QPointF(5, 5), QPointF(5, 5), button, button,
                       Qt.KeyboardModifier.NoModifier)


class FakeDrop:
    def __init__(self, source):
        self._source = source
        self.ignored = False
        self.accepted = False

    def source(self):
        return self._source

    def ignore(self):
        self.ignored = True

    def acceptProposedAction(self):
        self.accepted = True


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

    def test_a_locked_wall_swallows_move_menu_and_wheel_on_the_picture(self):
        self.wall.set_locked(True)
        surface = self.tile.player.surface
        events = (
            mouse(QEvent.Type.MouseMove),
            QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(1, 1)),
            QWheelEvent(QPointF(1, 1), QPointF(1, 1), QPoint(0, 0), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False),
        )

        for event in events:
            self.assertTrue(self.tile.eventFilter(surface, event), event.type())

    def test_a_drop_accepted_before_locking_does_not_swap_tiles(self):
        self.wall.add_device(Device('10.0.0.2'))
        other = self.wall.tiles[1]
        before = list(self.wall.slots)
        self.wall.set_locked(True)
        drop = FakeDrop(other)

        self.tile.dropEvent(drop)

        self.assertEqual(before, self.wall.slots)
        self.assertTrue(drop.ignored)
        self.assertFalse(drop.accepted)

    def test_a_drop_on_an_empty_slot_after_locking_does_not_move_the_tile(self):
        self.wall.relayout()
        slot = self.wall.placeholders[0]
        before = list(self.wall.slots)
        self.wall.set_locked(True)
        drop = FakeDrop(self.tile)

        slot.dropEvent(drop)

        self.assertEqual(before, self.wall.slots)
        self.assertTrue(drop.ignored)
        self.assertFalse(drop.accepted)

    def test_focus_does_not_reveal_controls_on_a_locked_wall(self):
        self.wall.set_locked(True)
        self.tile.controls.hide()

        self.tile.eventFilter(self.tile.start, QFocusEvent(QEvent.Type.FocusIn))

        self.assertTrue(self.tile.controls.isHidden())

    def test_focus_reveals_controls_on_an_unlocked_wall(self):
        self.tile.controls.hide()

        self.tile.eventFilter(self.tile.start, QFocusEvent(QEvent.Type.FocusIn))

        self.assertFalse(self.tile.controls.isHidden())


if __name__ == '__main__':
    unittest.main()
