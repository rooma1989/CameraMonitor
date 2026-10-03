import logging
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
        self.created = False

    def OpenKey(self, root, path, reserved, access):
        return self._Key()

    def CreateKey(self, root, path):
        # 真 winreg 里 CreateKey 是"有就打开、没有就建"，返回的句柄可写
        self.created = True
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

    def test_enable_creates_the_run_key_instead_of_requiring_it(self):
        # Run 键在精简系统上可能不存在，OpenKey 会直接 FileNotFoundError
        self.assertTrue(self.auto.enable())

        self.assertTrue(self.registry.created)

    def test_enable_failure_is_logged_and_returns_false(self):
        def boom(*args):
            raise PermissionError('denied')
        self.registry.CreateKey = boom

        with self.assertLogs('camera_monitor.autostart', level=logging.WARNING) as logs:
            self.assertFalse(self.auto.enable())

        self.assertIn('PermissionError', '\n'.join(logs.output))

    def test_disable_failure_is_logged_and_returns_false(self):
        def boom(*args):
            raise PermissionError('denied')
        self.registry.OpenKey = boom

        with self.assertLogs('camera_monitor.autostart', level=logging.WARNING) as logs:
            self.assertFalse(self.auto.disable())

        self.assertIn('PermissionError', '\n'.join(logs.output))


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


    def test_is_enabled_is_false_when_plist_points_at_another_executable(self):
        self.agents.mkdir(parents=True)
        with open(self.agents / f'{MAC_LABEL}.plist', 'wb') as fh:
            plistlib.dump({'Label': MAC_LABEL, 'ProgramArguments': ['/old/place/app', FLAG]}, fh)

        self.assertFalse(self.auto.is_enabled(), '程序挪了位置要重新写')

    def test_is_enabled_is_false_when_plist_is_corrupt(self):
        self.agents.mkdir(parents=True)
        (self.agents / f'{MAC_LABEL}.plist').write_bytes(b'not a plist')

        self.assertFalse(self.auto.is_enabled())

    def _auto_at(self, executable):
        return AutoStart(platform='darwin', executable=executable,
                         frozen=True, launch_agents=self.agents)

    def test_enable_refuses_app_translocation_path(self):
        auto = self._auto_at('/private/var/folders/ab/xyz/T/AppTranslocation/'
                             '1234-UUID/d/Camera Monitor.app/Contents/MacOS/Camera Monitor')

        with self.assertLogs('camera_monitor.autostart', level=logging.WARNING) as logs:
            self.assertFalse(auto.enable())

        self.assertFalse((self.agents / f'{MAC_LABEL}.plist').exists())
        self.assertIn('/Applications', '\n'.join(logs.output))

    def test_enable_refuses_mounted_dmg_path(self):
        auto = self._auto_at('/Volumes/Camera Monitor/Camera Monitor.app/Contents/MacOS/Camera Monitor')

        with self.assertLogs('camera_monitor.autostart', level=logging.WARNING):
            self.assertFalse(auto.enable())

        self.assertFalse((self.agents / f'{MAC_LABEL}.plist').exists())

    def test_enable_accepts_applications_path(self):
        self.assertTrue(self.auto.enable())


class SourceRunTests(unittest.TestCase):
    def test_running_from_source_never_touches_the_system(self):
        registry = FakeRegistry()
        auto = AutoStart(platform='win32', executable='python.exe', frozen=False, registry=registry)

        self.assertFalse(auto.enable())
        self.assertFalse(auto.disable())
        self.assertEqual({}, registry.values)


if __name__ == '__main__':
    unittest.main()
