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

    appearance_changed = Signal(str)
    COLORS = ('#ffffff', '#ffff00', '#00ff88', '#00ccff', '#ff6666')
    CORNERS = ('top-left', 'top-right', 'bottom-left', 'bottom-right')

    def appearance(self, ip):
        key = 'appearance/' + quote(ip, safe='') + '/'
        color = self.settings.value(key + 'color', '#ffffff')
        corner = self.settings.value(key + 'corner', 'top-left')
        return (color if color in self.COLORS else '#ffffff',
                corner if corner in self.CORNERS else 'top-left')

    def save_appearance(self, ip, color, corner):
        if color not in self.COLORS or corner not in self.CORNERS:
            raise ValueError('Invalid appearance')
        key = 'appearance/' + quote(ip, safe='') + '/'
        self.settings.setValue(key + 'color', color)
        self.settings.setValue(key + 'corner', corner)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError('Could not save appearance')
        self.appearance_changed.emit(ip)

    def order(self):
        value = self.settings.value('monitor/order', [])
        return value if isinstance(value, list) else ([value] if isinstance(value, str) else [])

    def save_order(self, ips):
        self.settings.setValue('monitor/order', list(ips))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError('Could not save order')

    def slot_order(self):
        value=self.settings.value('monitor/slots',None)
        if value is None:return self.order()
        return value if isinstance(value,list) else ([value] if isinstance(value,str) else [])

    def save_slot_order(self,ips):
        self.settings.setValue('monitor/slots',list(ips))
        self.settings.setValue('monitor/order',[ip for ip in ips if ip])
        self.settings.sync()
        if self.settings.status()!=QSettings.Status.NoError:
            raise OSError('Could not save slot positions')
