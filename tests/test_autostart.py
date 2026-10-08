import logging
import plistlib
import tempfile
import unittest
from pathlib import Path

from camera_monitor import autostart
from camera_monitor.autostart import APP_NAME, FLAG, MAC_LABEL, WANTED_KEY, AutoStart


RUN = r'Software\Microsoft\Windows\CurrentVersion\Run'
APPROVED = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'


class FakeRegistry:
    """只实现 autostart 用到的那几个 winreg 接口，按键路径分开存。

    和真 winreg 一样：打开不存在的键、读删不存在的值都抛 FileNotFoundError。
    Run 键一开始就在（正常系统上都有），StartupApproved\Run 要任务管理器动过才有。
    """
    HKEY_CURRENT_USER = 'HKCU'
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_BINARY = 3

    class _Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def __init__(self):
        self.keys = {RUN: {}}
        self.created = False

    @property
    def values(self):
        """Run 键里的值。"""
        return self.keys[RUN]

    def OpenKey(self, root, path, reserved=0, access=KEY_READ):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self._Key(self.keys[path])

    def CreateKey(self, root, path):
        # 真 winreg 里 CreateKey 是"有就打开、没有就建"，返回的句柄可写
        self.created = True
        return self._Key(self.keys.setdefault(path, {}))

    def SetValueEx(self, key, name, reserved, kind, value):
        key.values[name] = value

    def DeleteValue(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        value = key.values[name]
        return value, self.REG_BINARY if isinstance(value, bytes) else self.REG_SZ

    def task_manager(self, first_byte):
        """任务管理器「启动应用」里切换启用 / 禁用时写下的值：12 字节，后 8 字节是时间。"""
        self.keys.setdefault(APPROVED, {})[APP_NAME] = bytes([first_byte]) + bytes(11)


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
        self.assertEqual(autostart.WRITE_FAILED, self.auto.last_error)

    def test_a_later_success_clears_the_failure_reason(self):
        self.auto.last_error = autostart.WRITE_FAILED

        self.assertTrue(self.auto.enable())

        self.assertEqual('', self.auto.last_error)

    # ---------- 任务管理器「启动应用」 ----------

    def test_disabled_in_task_manager_counts_as_not_enabled(self):
        self.auto.enable()
        for first_byte in (0x03, 0x07, 0x01):
            with self.subTest(first_byte=first_byte):
                self.registry.task_manager(first_byte)

                self.assertFalse(self.auto.is_enabled(), '任务管理器里禁用了，开机不会启动')

    def test_enabled_in_task_manager_still_counts(self):
        self.auto.enable()
        for first_byte in (0x02, 0x06):
            with self.subTest(first_byte=first_byte):
                self.registry.task_manager(first_byte)

                self.assertTrue(self.auto.is_enabled())

    def test_enabling_clears_the_task_manager_switch(self):
        self.registry.task_manager(0x03)

        self.assertTrue(self.auto.enable())

        self.assertNotIn(APP_NAME, self.registry.keys[APPROVED], '勾上就要真的开机启动')
        self.assertTrue(self.auto.is_enabled())

    def test_enabling_without_any_task_manager_record_is_fine(self):
        self.assertNotIn(APPROVED, self.registry.keys)

        self.assertTrue(self.auto.enable())
        self.registry.keys[APPROVED] = {}
        self.assertTrue(self.auto.enable(), '键在、值不在也一样')

    def test_failing_to_clear_the_task_manager_switch_is_a_failed_enable(self):
        self.registry.task_manager(0x03)
        delete = self.registry.DeleteValue

        def guarded(key, name):
            if key.values is self.registry.keys[APPROVED]:
                raise PermissionError('denied')
            return delete(key, name)
        self.registry.DeleteValue = guarded

        with self.assertLogs('camera_monitor.autostart', level=logging.WARNING):
            self.assertFalse(self.auto.enable())

        self.assertEqual(autostart.WRITE_FAILED, self.auto.last_error)

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
        self.assertEqual(autostart.TEMPORARY_LOCATION, auto.last_error)
        self.assertIn('应用程序', autostart.failure_message(auto.last_error),
                      '现场要知道该怎么做，不能只说失败')

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
        self.assertEqual(autostart.FROM_SOURCE, auto.last_error)
        self.assertFalse(auto.disable())
        self.assertEqual({}, registry.values)


class FailureMessageTests(unittest.TestCase):
    def test_every_reason_has_its_own_message(self):
        reasons = (autostart.FROM_SOURCE, autostart.TEMPORARY_LOCATION,
                   autostart.WRITE_FAILED, autostart.UNSUPPORTED)
        messages = [autostart.failure_message(reason) for reason in reasons]

        self.assertEqual(len(reasons), len(set(messages)))
        for message in messages + [autostart.failure_message('')]:
            self.assertTrue(message.startswith('开机自动启动没有设置成功'), message)


class FakeSettings:
    def __init__(self, **values):
        self.values = dict(values)

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        pass


class RecordingAutoStart:
    def __init__(self, works=True):
        self.works = works
        self.calls = []
        self.last_error = ''

    def enable(self):
        self.calls.append('enable')
        if not self.works:
            self.last_error = autostart.WRITE_FAILED
        return self.works

    def disable(self):
        self.calls.append('disable')
        return True


class ReconcileTests(unittest.TestCase):
    def test_wanted_but_missing_is_put_back(self):
        auto = RecordingAutoStart()

        self.assertTrue(autostart.reconcile(auto, FakeSettings(**{WANTED_KEY: 'true'})))

        self.assertEqual(['enable'], auto.calls)

    def test_not_wanted_is_removed(self):
        for settings in (FakeSettings(), FakeSettings(**{WANTED_KEY: 'false'})):
            auto = RecordingAutoStart()

            self.assertTrue(autostart.reconcile(auto, settings))

            self.assertEqual(['disable'], auto.calls, '从没勾过的电脑也要删掉留下的启动项')

    def test_a_failed_enable_writes_the_wish_back_to_false(self):
        auto = RecordingAutoStart(works=False)
        settings = FakeSettings(**{WANTED_KEY: True})

        with self.assertLogs('camera_monitor.autostart', level=logging.WARNING):
            self.assertFalse(autostart.reconcile(auto, settings))

        self.assertIs(False, settings.values[WANTED_KEY],
                      '设置要和实际一致，下一次同步后台才看得到没设上')


if __name__ == '__main__':
    unittest.main()
