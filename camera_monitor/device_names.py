"""Local display names, separate from device settings and credentials."""
from urllib.parse import quote
from PySide6.QtCore import QObject, QSettings, Signal


class DeviceNames(QObject):
    changed = Signal(str, str)

    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings('CameraMonitor', 'DeviceNames')

    def key(self, ip):
        return 'names/' + quote(ip, safe='')

    def get(self, ip):
        value = self.settings.value(self.key(ip), '')
        return value if isinstance(value, str) else ''

    def display(self, device):
        return self.get(device.ip) or device.name or device.model or '摄像头'

    def save(self, ip, name):
        name = name.strip()
        if len(name) > 80:
            raise ValueError('名称最多 80 个字符')
        key = self.key(ip)
        previous = self.get(ip)
        self.settings.setValue(key, name) if name else self.settings.remove(key)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            self.settings.setValue(key, previous) if previous else self.settings.remove(key)
            raise OSError('Could not save device name')
        self.changed.emit(ip, name)
