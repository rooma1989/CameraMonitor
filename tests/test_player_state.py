import unittest

from camera_monitor.player_state import (AUTH_FAILED, CONNECTING, PLAYING, STOPPED,
                                         UNREACHABLE, player_state)


class PlayerStateTests(unittest.TestCase):
    def test_the_texts_playback_really_shows(self):
        cases = {
            '正在播放 · 1280 × 720 · H264': PLAYING,
            '画面正在播放 · 密码未保存，请查看连接设置': PLAYING,
            '视频认证失败，请检查摄像头用户名和密码。': AUTH_FAILED,
            '正在获取可播放通道…': CONNECTING,
            '正在连接视频流，等待首帧…': CONNECTING,
            '视频中断，3 秒后自动重连 · 第 1 次（可点击停止）': CONNECTING,
            '已停止': STOPPED,
            '正在停止，等待网络连接结束…': STOPPED,
            '待连接': STOPPED,
            '': STOPPED,
            '视频连接失败或超时，请检查账号密码、RTSP 服务、网络和所选通道。': UNREACHABLE,
            '获取通道失败，请检查摄像头 ONVIF 设置。': UNREACHABLE,
            '视频地址无效，请检查地址和端口。': UNREACHABLE,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, player_state(text))

    def test_errors_from_streams_and_decoder_reach_the_right_class(self):
        # 这些文字经 error.emit(str(exc)) 或 Decoder.error 进入 set_status。
        cases = {
            '认证失败，请检查摄像头的 ONVIF 用户名和密码。': AUTH_FAILED,
            '认证失败，请检查 ONVIF 账号密码及设备时间。': AUTH_FAILED,
            '没有找到此视频流，请尝试其他通道或检查 RTSP 地址。': UNREACHABLE,
            '视频流已结束，请重新连接。': UNREACHABLE,
            '已连接，但没有收到可解码的视频帧。': UNREACHABLE,
            '请输入有效的 RTSP 视频地址。': UNREACHABLE,
            '设备返回了无效或指向其他主机的地址，请检查设备网络配置。': UNREACHABLE,
            '设备没有返回有效的 ONVIF 数据。': UNREACHABLE,
            '设备不支持此 ONVIF 操作，或尚未开启服务。': UNREACHABLE,
            '设备服务不可用（HTTP 503）。': UNREACHABLE,
            '无法连接 ONVIF 服务，请检查网络、服务端口及协议是否开启。': UNREACHABLE,
            '没有取得可播放通道。可以使用手动 RTSP 地址连接。': UNREACHABLE,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, player_state(text))


if __name__ == '__main__':
    unittest.main()
