"""Shared test helpers.

Tests must never read or write the developer's real preferences. On macOS
QSettings goes through CFPreferences, which ignores $HOME — neither an
environment variable nor QSettings.setPath isolates it. The only reliable way
is to inject the QSettings objects, which is what these helpers do.

Without isolation a Window() also picks up a real cloud session and starts
talking to the server mid-test.
"""
import os
import tempfile

from PySide6.QtCore import QSettings


def isolated_settings(case):
    """Return a factory for temp-file QSettings that die with the test."""
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)

    def make(name):
        return QSettings(os.path.join(folder.name, name), QSettings.Format.IniFormat)

    return make


def make_window(case, **kwargs):
    """Build a Window whose settings are isolated and whose timers stop on cleanup."""
    from camera_monitor.app import Window
    from camera_monitor.device_names import DeviceNames

    ini = isolated_settings(case)
    kwargs.setdefault('device_names', DeviceNames(ini('names.ini')))
    kwargs.setdefault('cloud_settings', ini('cloud.ini'))

    window = Window(**kwargs)
    assert not window.cloud.enabled(), '测试不得继承这台机器上真实的云端登录态'
    case.addCleanup(window.cloud.stop)
    case.addCleanup(window.thumbnail_timer.stop)
    return window
