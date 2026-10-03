"""开机自动运行。

只在打包后的程序里真正改系统：源码运行（开发、测试）时什么都不做，免得把开发
机的 python 写进启动项，或者删掉已安装那份程序的启动项。
"""
import plistlib
import sys
from pathlib import Path

APP_NAME = 'CameraMonitor'
MAC_LABEL = 'com.cameramonitor.desktop'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
FLAG = '--autostart'


class AutoStart:
    def __init__(self, platform=None, executable=None, frozen=None,
                 launch_agents=None, registry=None):
        self.platform = platform or sys.platform
        self.executable = executable or sys.executable
        self.frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
        self.launch_agents = Path(launch_agents) if launch_agents else Path.home() / 'Library' / 'LaunchAgents'
        self.registry = registry

    @property
    def plist_path(self):
        return self.launch_agents / f'{MAC_LABEL}.plist'

    def command(self):
        return f'"{self.executable}" {FLAG}'

    def _winreg(self):
        if self.registry is None:
            import winreg
            self.registry = winreg
        return self.registry

    def enable(self):
        if not self.frozen:
            return False
        try:
            if self.platform == 'win32':
                reg = self._winreg()
                with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                    reg.SetValueEx(key, APP_NAME, 0, reg.REG_SZ, self.command())
                return True
            if self.platform == 'darwin':
                self.launch_agents.mkdir(parents=True, exist_ok=True)
                with open(self.plist_path, 'wb') as fh:
                    plistlib.dump({'Label': MAC_LABEL,
                                   'ProgramArguments': [self.executable, FLAG],
                                   'RunAtLoad': True}, fh)
                return True
        except OSError:
            pass
        return False

    def disable(self):
        if not self.frozen:
            return False
        try:
            if self.platform == 'win32':
                reg = self._winreg()
                with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                    reg.DeleteValue(key, APP_NAME)
            elif self.platform == 'darwin':
                self.plist_path.unlink(missing_ok=True)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def is_enabled(self):
        try:
            if self.platform == 'win32':
                reg = self._winreg()
                with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
                    value, _ = reg.QueryValueEx(key, APP_NAME)
                return value == self.command()
            if self.platform == 'darwin':
                return self.plist_path.exists()
        except OSError:
            pass
        return False
