"""开机自动运行。

只在打包后的程序里真正改系统：源码运行（开发、测试）时什么都不做，免得把开发
机的 python 写进启动项，或者删掉已安装那份程序的启动项。
"""
import logging
import plistlib
import sys
from pathlib import Path

APP_NAME = 'CameraMonitor'
MAC_LABEL = 'com.cameramonitor.desktop'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
FLAG = '--autostart'

logger = logging.getLogger(__name__)


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

    def _is_temporary_mac_location(self):
        # 未签名的程序从下载目录或 DMG 里直接打开时，macOS 会把它放到随机的只读
        # AppTranslocation 路径，或者留在 /Volumes 挂载点上；重启后这些路径都不在了，
        # 写进 LaunchAgent 只会静默失败，所以干脆不写。
        return '/AppTranslocation/' in self.executable or self.executable.startswith('/Volumes/')

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
                # CreateKey 是"有就打开、没有就建"，返回的句柄可写；Run 键在精简系统上
                # 可能不存在，用 OpenKey 会直接失败。
                with reg.CreateKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
                    reg.SetValueEx(key, APP_NAME, 0, reg.REG_SZ, self.command())
                return True
            if self.platform == 'darwin':
                if self._is_temporary_mac_location():
                    logger.warning('开机自动运行未启用：程序在临时位置运行（%s），'
                                   '请先把它移动到 /Applications 再试', self.executable)
                    return False
                self.launch_agents.mkdir(parents=True, exist_ok=True)
                with open(self.plist_path, 'wb') as fh:
                    plistlib.dump({'Label': MAC_LABEL,
                                   'ProgramArguments': [self.executable, FLAG],
                                   'RunAtLoad': True}, fh)
                return True
        except OSError as exc:
            # 只记异常类型，足够排查，也不会把路径或系统返回的细节写进日志
            logger.warning('开机自动运行启用失败：%s', type(exc).__name__)
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
        except OSError as exc:
            logger.warning('开机自动运行关闭失败：%s', type(exc).__name__)
            return False

    def is_enabled(self):
        try:
            if self.platform == 'win32':
                reg = self._winreg()
                with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
                    value, _ = reg.QueryValueEx(key, APP_NAME)
                return value == self.command()
            if self.platform == 'darwin':
                # 只看文件在不在不够：程序挪过位置后旧 plist 还在，却指向不存在的路径
                with open(self.plist_path, 'rb') as fh:
                    plist = plistlib.load(fh)
                return plist.get('ProgramArguments', [None])[0] == self.executable
        except (OSError, plistlib.InvalidFileException, ValueError, AttributeError, IndexError, TypeError):
            # 文件不存在、损坏或结构不对，都按"没启用"处理
            pass
        return False
