import unittest

from camera_monitor.live_edge import CATCH_UP_GLIMPSE, LiveEdge


class LiveEdgeTests(unittest.TestCase):
    """监控墙要的是「现在」。落后太多就得丢帧追上去，而不是老老实实全解出来。"""

    def feed(self, edge, pairs):
        return [edge.observe(now, pts) for now, pts in pairs]

    def realtime(self, count, start_now=0.0, start_pts=100.0, step=0.04):
        """解码跟得上：墙钟和画面时间戳同步往前走。"""
        return [(start_now + i * step, start_pts + i * step) for i in range(count)]

    def test_a_stream_that_keeps_up_is_never_told_to_drop_anything(self):
        edge = LiveEdge()
        decisions = self.feed(edge, self.realtime(200))

        self.assertFalse(any(d.skip_nonref for d in decisions))
        self.assertFalse(any(d.resync for d in decisions))
        self.assertLess(edge.behind, 0.01)

    def test_it_converts_at_the_screen_rate_not_at_the_camera_rate(self):
        # 60 fps 的摄像头，界面只有 30Hz，多转的每一帧都是白费
        edge = LiveEdge()
        decisions = self.feed(edge, self.realtime(120, step=1 / 60))

        published = sum(1 for d in decisions if d.publish)
        self.assertLessEqual(published, 65, '不该按摄像头的帧率去转换')
        self.assertGreaterEqual(published, 55, '也不能少到画面卡顿')

    def test_falling_behind_switches_to_catching_up(self):
        edge = LiveEdge()
        # 每帧多花 40 毫秒：墙钟走两倍，画面时间戳只走一倍
        pairs = [(i * 0.08, 100 + i * 0.04) for i in range(40)]
        decisions = self.feed(edge, pairs)

        self.assertTrue(any(d.skip_nonref for d in decisions), '落后了就该让解码器丢帧')
        self.assertTrue(edge.catching_up)

    def test_catching_up_still_shows_something_so_it_does_not_look_frozen(self):
        edge = LiveEdge()
        self.feed(edge, [(i * 0.08, 100 + i * 0.04) for i in range(40)])
        self.assertTrue(edge.catching_up)

        start = 40 * 0.08
        during = self.feed(edge, [(start + i * 0.08, 101.6 + i * 0.04) for i in range(40)])

        self.assertTrue(any(d.publish for d in during), '追帧期间也得偶尔出一帧')
        gaps = [d for d in during if d.publish]
        self.assertLess(len(gaps), 10, f'追帧期间不该照常出帧，那就追不上了（出了 {len(gaps)} 帧）')

    def test_it_goes_back_to_normal_once_it_has_caught_up(self):
        edge = LiveEdge()
        self.feed(edge, [(i * 0.08, 100 + i * 0.04) for i in range(40)])
        self.assertTrue(edge.catching_up)

        # 追回来了：画面时间戳比墙钟跑得快
        now = 40 * 0.08
        pts = 101.6
        for i in range(60):
            decision = edge.observe(now + i * 0.01, pts + i * 0.04)
        self.assertFalse(edge.catching_up, '追上了就该恢复正常')
        self.assertFalse(decision.skip_nonref)

    def test_a_stream_that_cannot_be_caught_up_is_reconnected(self):
        edge = LiveEdge(resync_after=6.0)
        # 一直只有一半的速度，落后会一路涨过 6 秒
        decisions = self.feed(edge, [(i * 0.08, 100 + i * 0.04) for i in range(200)])

        self.assertTrue(any(d.resync for d in decisions), '追不回来就该重连，从最新的画面重新开始')

    def test_a_reconnect_starts_the_accounting_over(self):
        edge = LiveEdge(resync_after=6.0)
        self.feed(edge, [(i * 0.08, 100 + i * 0.04) for i in range(200)])
        self.assertGreater(edge.behind, 6.0)

        edge.resynced()
        self.assertEqual(0.0, edge.behind)
        self.assertFalse(edge.catching_up)
        decisions = self.feed(edge, self.realtime(60, start_now=999.0, start_pts=500.0))
        self.assertFalse(any(d.skip_nonref for d in decisions), '重连之后不该还当成落后')

    def test_a_first_frame_without_a_timestamp_does_not_disable_the_whole_thing(self):
        # 实测：大华实时流解出来的第一帧 frame.time 就是 None。拿它当起点的话，
        # 落后永远算不出来，追帧整套静默失效——这个 bug 真出现过。
        edge = LiveEdge()
        pairs = [(0.0, None)] + [(i * 0.08, 100 + i * 0.04) for i in range(1, 60)]
        decisions = self.feed(edge, pairs)

        self.assertGreater(edge.behind, 1.0, '第一帧没时间戳，也得从后面的帧开始算账')
        self.assertTrue(any(d.skip_nonref for d in decisions))

    def test_a_stream_that_never_has_timestamps_is_left_alone(self):
        # 有些设备一帧时间戳都不给。没有依据就不要瞎丢帧。
        edge = LiveEdge()
        decisions = self.feed(edge, [(i * 0.08, None) for i in range(200)])

        self.assertFalse(any(d.skip_nonref or d.resync for d in decisions))
        self.assertTrue(any(d.publish for d in decisions), '照常出帧，只是不做追帧判断')

    def test_the_very_first_frame_is_shown_at_once(self):
        edge = LiveEdge()
        self.assertTrue(edge.observe(0.0, None).publish, '别让人对着空画面多等一个周期')

    def test_it_does_not_reconnect_over_and_over(self):
        # 机器慢到怎么追都追不回来时，不停重连只会更糟：每次重连要一秒多，
        # 那一秒里什么都看不到。
        edge = LiveEdge(resync_after=6.0)
        resyncs = 0
        now, pts = 0.0, 100.0
        for _ in range(3000):
            decision = edge.observe(now, pts)
            if decision.resync:
                resyncs += 1
                edge.resynced()
            now += 0.08
            pts += 0.04

        self.assertGreaterEqual(resyncs, 1, '该重连的时候还是要重连')
        self.assertLessEqual(resyncs, 1 + int(now / 30) + 1,
                             f'两次重连之间至少隔 30 秒，{now:.0f} 秒里重连了 {resyncs} 次')
