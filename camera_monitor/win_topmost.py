"""Windows 上把全屏的监控墙放进置顶层，压住任务栏。

开机自启时窗口常常不在前台，不在前台的全屏窗口压不住任务栏，底下一条露着。
不用 Qt 的 WindowStaysOnTopHint：改窗口标志会让 Qt 重建原生窗口，全屏状态和
句柄都会丢一次。这里直接对现有句柄调 SetWindowPos，只改 Z 序，不动位置大小、
不抢焦点。大屏密码框、维护入口都以监控墙为父窗口，在 Windows 上是它拥有的窗口，
始终在它上面，不会被压住。

其他系统什么都不做；user32、platform 可注入，macOS 上的测试用假的验调用。
"""
import functools
import logging
import sys

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=None)
def _user32():
    import ctypes
    from ctypes import wintypes
    # 自己开一份 user32，不改全局 windll.user32 的 argtypes。句柄按指针宽度传，
    # -1、-2 才能在 64 位上正确地变成 HWND_TOPMOST、HWND_NOTOPMOST
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    return user32


def set_topmost(hwnd, on, *, user32=None, platform=None):
    """把句柄为 hwnd 的窗口放进（on=True）或移出置顶层。成功返回 True。

    失败只记日志：置顶不上顶多任务栏露着，不能让监控墙因此出错。
    """
    if (platform or sys.platform) != 'win32':
        return False
    try:
        user32 = user32 if user32 is not None else _user32()
        ok = user32.SetWindowPos(hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        logger.warning('窗口%s置顶失败', '' if on else '取消', exc_info=True)
        return False
    if not ok:
        logger.warning('窗口%s置顶失败（SetWindowPos 返回 0）', '' if on else '取消')
        return False
    return True
