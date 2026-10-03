import plistlib
import tempfile
import unittest
from pathlib import Path

from camera_monitor.autostart import APP_NAME, FLAG, MAC_LABEL, AutoStart


class FakeRegistry:
    """只实现 autostart 用到的那几个 winreg 接口。"""
    HKEY_CURRENT_USER = 'HKCU'
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, path, reserved, access):
        return self._Key()

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ


class WindowsAutoStartTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.auto = AutoStart(platform='win32', executable=r'C:\CM\CameraMonitor.exe',
                              frozen=True, registry=self.registry)

    def test_enable_writes_the_run_key_with_the_flag(self):
        self.assertTrue(self.auto.enable())

        self.assertEqual(rf'"C:\CM\CameraMonitor.exe" {FLAG}', self.registry.values[APP_NAME])
        self.assertTrue(self.auto.is_enabled())

    def test_disable_is_idempotent(self):
        self.auto.enable()

        self.assertTrue(self.auto.disable())
        self.assertTrue(self.auto.disable())
        self.assertFalse(self.auto.is_enabled())

    def test_a_moved_program_counts_as_not_enabled(self):
        self.registry.values[APP_NAME] = r'"D:\old\CameraMonitor.exe" --autostart'

        self.assertFalse(self.auto.is_enabled(), '程序挪了位置要重新写')


class MacAutoStartTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.agents = Path(folder.name) / 'LaunchAgents'
        self.auto = AutoStart(platform='darwin',
                              executable='/Applications/Camera Monitor.app/Contents/MacOS/Camera Monitor',
                              frozen=True, launch_agents=self.agents)

    def test_enable_writes_a_run_at_load_agent(self):
        self.assertTrue(self.auto.enable())

        with open(self.agents / f'{MAC_LABEL}.plist', 'rb') as fh:
            plist = plistlib.load(fh)
        self.assertEqual(MAC_LABEL, plist['Label'])
        self.assertTrue(plist['RunAtLoad'])
        self.assertEqual(['/Applications/Camera Monitor.app/Contents/MacOS/Camera Monitor', FLAG],
                         plist['ProgramArguments'])
        self.assertTrue(self.auto.is_enabled())

    def test_disable_removes_it_and_tolerates_absence(self):
        self.auto.enable()

        self.assertTrue(self.auto.disable())
        self.assertTrue(self.auto.disable())
        self.assertFalse(self.auto.is_enabled())


class SourceRunTests(unittest.TestCase):
    def test_running_from_source_never_touches_the_system(self):
        registry = FakeRegistry()
        auto = AutoStart(platform='win32', executable='python.exe', frozen=False, registry=registry)

        self.assertFalse(auto.enable())
        self.assertFalse(auto.disable())
        self.assertEqual({}, registry.values)


if __name__ == '__main__':
    unittest.main()
