"""Non-secret connection preferences stored on this computer."""
from urllib.parse import quote
from PySide6.QtCore import QSettings


class ConnectionOptions:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings('CameraMonitor', 'Connections')

    def key(self, ip):
        return 'transport/' + quote(ip, safe='')

    def transport(self, ip):
        value = self.settings.value(self.key(ip), 'tcp')
        return value if value in ('tcp', 'udp') else 'tcp'

    def save_transport(self, ip, transport):
        if transport not in ('tcp', 'udp'):
            raise ValueError('Unsupported transport')
        key = self.key(ip)
        previous = self.transport(ip)
        self.settings.setValue(key, transport)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            self.settings.setValue(key, previous)
            raise OSError('Could not save transport')
