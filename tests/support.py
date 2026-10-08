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


class FakeAutoStart:
    """窗口用的假启动项：只记账，绝不碰注册表和 LaunchAgents。

    works=False 时 enable() 失败，last_error 记下 error。
    """

    def __init__(self, enabled=False, works=True, error='temporary_location'):
        self.enabled = enabled
        self.works = works
        self.error = error
        self.last_error = ''
        self.calls = []

    def enable(self):
        self.calls.append('enable')
        if not self.works:
            self.last_error = self.error
            return False
        self.enabled, self.last_error = True, ''
        return True

    def disable(self):
        self.calls.append('disable')
        self.enabled = False
        return True

    def is_enabled(self):
        return self.enabled


def no_topmost(hwnd, on):
    """窗口用的假置顶：Windows 测试机上也不能拿 offscreen 的假句柄去调 SetWindowPos。"""


def make_window(case, fresh_install=False, **kwargs):
    """Build a Window whose settings are isolated and whose timers stop on cleanup.

    fresh_install=False 时模拟老用户（选过单机），否则所有窗口都会盖着一层欢迎页。
    调用方自己传了 cloud_settings 时也一样：除非 fresh_install=True，否则
    cloud/standalone 会被写进那份设置里。
    """
    from camera_monitor.app import Window
    from camera_monitor.connection_options import ConnectionOptions
    from camera_monitor.device_names import DeviceNames

    ini = isolated_settings(case)
    kwargs.setdefault('device_names', DeviceNames(ini('names.ini')))
    cloud = kwargs.setdefault('cloud_settings', ini('cloud.ini'))
    if not fresh_install:
        cloud.setValue('cloud/standalone', True)
    kwargs.setdefault('connection_options', ConnectionOptions(ini('conn.ini')))
    # 真的 AutoStart 源码运行时虽然不写，is_enabled() 仍会去读本机的启动项
    kwargs.setdefault('autostart', FakeAutoStart())
    kwargs.setdefault('topmost', no_topmost)

    window = Window(**kwargs)
    assert not window.cloud.enabled(), '测试不得继承这台机器上真实的云端登录态'

    # 落地云端配置之后窗口会自动连接摄像头。测试里那是真的开解码线程去连一个
    # 不存在的地址，线程还没结束窗口就被销毁，Qt 直接 abort——而且往往是在后面
    # 某个完全无关的用例里炸。这里换成只记账，要验自动连接就读 connect_all_calls。
    window.wall.connect_all_calls = []
    window.wall.connect_all = lambda: window.wall.connect_all_calls.append(1)

    def shut_down():
        # 闭包必须抓住 window 本身：只注册 window.cloud.stop 这类绑定方法的话，
        # 存活的是子对象而不是窗口。
        #
        # 而且必须把窗口真正删掉并把事件队列跑空。测试不跑事件循环，窗口析构后
        # 队列里残留的事件会在后面某个测试调用 processEvents() 时才触发，那时对象
        # 早已释放——表现就是在完全无关的用例里段错误。
        from PySide6.QtWidgets import QApplication

        window.cloud.stop()
        window.channel.stop()
        window.managed.status_timer.stop()
        window.thumbnail_timer.stop()
        window.thumbnails.cancel_all()
        window.hide()
        window.setParent(None)
        window.deleteLater()
        QApplication.processEvents()

    case.addCleanup(shut_down)
    return window
