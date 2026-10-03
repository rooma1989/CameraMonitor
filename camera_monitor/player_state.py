"""把播放器给人看的状态文字，归成云端能统计的几类。"""

PLAYING = 'playing'
CONNECTING = 'connecting'
AUTH_FAILED = 'auth_failed'
UNREACHABLE = 'unreachable'
STOPPED = 'stopped'


def player_state(text):
    text = text or ''
    if text.startswith('正在播放') or text.startswith('画面正在播放'):
        return PLAYING
    if '认证失败' in text:
        return AUTH_FAILED
    if text in ('', '待连接') or text.startswith('已停止') or text.startswith('正在停止'):
        return STOPPED
    if text.startswith('正在') or '重连' in text:
        return CONNECTING
    return UNREACHABLE
