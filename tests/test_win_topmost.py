"""Windows 置顶：只在 win32 上调 SetWindowPos，其他系统什么都不做。"""
import unittest

from camera_monitor import win_topmost


class FakeUser32:
    def __init__(self, result=1, error=None):
        self.calls = []
        self.result = result
        self.error = error

    def SetWindowPos(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.result


FLAGS = win_topmost.SWP_NOMOVE | win_topmost.SWP_NOSIZE | win_topmost.SWP_NOACTIVATE


class SetTopmostTests(unittest.TestCase):
    def test_turning_on_puts_the_window_in_the_topmost_band(self):
        user32 = FakeUser32()

        self.assertTrue(win_topmost.set_topmost(1234, True, user32=user32, platform='win32'))

        self.assertEqual([(1234, -1, 0, 0, 0, 0, FLAGS)], user32.calls)

    def test_turning_off_takes_it_back_out(self):
        user32 = FakeUser32()

        self.assertTrue(win_topmost.set_topmost(1234, False, user32=user32, platform='win32'))

        self.assertEqual([(1234, -2, 0, 0, 0, 0, FLAGS)], user32.calls)

    def test_the_flags_neither_move_resize_nor_activate(self):
        self.assertEqual(0x0013, FLAGS)

    def test_does_nothing_off_windows(self):
        user32 = FakeUser32()

        for platform in ('darwin', 'linux'):
            self.assertFalse(win_topmost.set_topmost(1234, True, user32=user32, platform=platform))

        self.assertEqual([], user32.calls)

    def test_a_failed_call_is_logged_not_raised(self):
        with self.assertLogs('camera_monitor.win_topmost', 'WARNING'):
            self.assertFalse(win_topmost.set_topmost(1234, True, user32=FakeUser32(result=0), platform='win32'))

    def test_an_exception_is_logged_not_raised(self):
        user32 = FakeUser32(error=OSError('boom'))

        with self.assertLogs('camera_monitor.win_topmost', 'WARNING'):
            self.assertFalse(win_topmost.set_topmost(1234, True, user32=user32, platform='win32'))


if __name__ == '__main__':
    unittest.main()
