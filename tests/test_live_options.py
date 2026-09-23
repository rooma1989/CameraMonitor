import unittest

from camera_monitor.playback import PROBE_BYTES, PROBE_MICROSECONDS, live_options


class LiveOptionsTests(unittest.TestCase):
    """FFmpeg 默认要探测 5MB / 5 秒才开始解，监控画面用不着这么谨慎。"""

    def test_it_does_not_wait_for_the_default_five_second_probe(self):
        options = live_options('tcp')

        self.assertEqual(PROBE_BYTES, options['probesize'])
        self.assertEqual(PROBE_MICROSECONDS, options['analyzeduration'])
        self.assertLess(int(PROBE_MICROSECONDS), 5_000_000, '要比 FFmpeg 的默认值小才有意义')

    def test_the_full_probe_is_the_ffmpeg_default(self):
        # 探测调小之后认不出流的相机，要能退回默认行为
        options = live_options('tcp', full_probe=True)

        self.assertNotIn('probesize', options)
        self.assertNotIn('analyzeduration', options)

    def test_the_transport_and_read_timeout_are_always_carried(self):
        for transport in ('tcp', 'udp'):
            for full in (False, True):
                options = live_options(transport, full_probe=full)
                self.assertEqual(transport, options['rtsp_transport'])
                self.assertIn('rw_timeout', options, '读超时不能因为换了探测方式就丢了')
