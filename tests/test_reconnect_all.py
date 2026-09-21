import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import unittest

from PySide6.QtWidgets import QApplication

from camera_monitor import multiview
from camera_monitor.discovery import Device
from camera_monitor.multiview import MultiView
from test_credentials import MemoryVault


class FakePlayer:
    """只记动作，不碰网络。"""

    def __init__(self):
        self.running = False
        self.log = []

    def busy(self):
        return self.running

    def connect_camera(self):
        self.log.append('连接')
        self.running = True

    def stop_playback(self):
        self.log.append('停止')


class FakeTile:
    def __init__(self):
        self.player = FakePlayer()


class ReconnectAllTests(unittest.TestCase):
    """「一键重连」要真的先断后连。

    connect_camera 看到还有网络线程在跑就原地返回，所以直接「停一下再连」
    什么都不会发生——停的那一瞬间线程还没收尾。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wall(self):
        view = MultiView([], embedded=True, credential_store=MemoryVault())
        self.addCleanup(view.deleteLater)
        return view

    def pump(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        QApplication.processEvents()
        return predicate()

    def test_it_waits_for_the_running_workers_before_connecting_again(self):
        view = self.wall()
        tile = FakeTile()
        tile.player.running = True
        view.tiles = [tile]

        view.reconnect_all()
        QApplication.processEvents()
        self.assertEqual(['停止'], tile.player.log, '线程还在跑的时候不该急着连')

        tile.player.running = False
        self.assertTrue(self.pump(lambda: '连接' in tile.player.log))
        self.assertEqual(['停止', '连接'], tile.player.log)

    def test_one_stuck_camera_does_not_keep_the_others_black(self):
        view = self.wall()
        stuck, free = FakeTile(), FakeTile()
        stuck.player.running = True   # 掉线的那台，stop 要等读超时跑完
        view.tiles = [stuck, free]

        original = multiview.RECONNECT_WAIT_SECONDS
        multiview.RECONNECT_WAIT_SECONDS = 0.2
        self.addCleanup(setattr, multiview, 'RECONNECT_WAIT_SECONDS', original)

        view.reconnect_all()

        self.assertTrue(self.pump(lambda: '连接' in free.player.log),
                        '一格卡住不该拖着其他格一起黑着')
        self.assertNotIn('连接', stuck.player.log, '卡住的那格连不了，也别假装连上')

    def test_disconnecting_cancels_a_reconnect_that_is_still_waiting(self):
        view = self.wall()
        tile = FakeTile()
        tile.player.running = True
        view.tiles = [tile]

        view.reconnect_all()
        view.stop_everything()
        tile.player.running = False

        self.assertFalse(self.pump(lambda: '连接' in tile.player.log, timeout=1.0),
                         '人点了断开，就别再自己连回来')

    def test_the_buttons_are_there_in_the_main_window(self):
        view = self.wall()
        self.assertTrue(view.start_all.isVisibleTo(view), '主界面也要能一键重连')
        self.assertTrue(view.stop_all.isVisibleTo(view))
        self.assertEqual('一键重连', view.start_all.text())
        self.assertEqual('一键断开', view.stop_all.text())
