"""追帧策略：画面落后实时太多时，决定丢什么、什么时候重连。

监控墙要的是「现在」，不是「完整」。网络抖一下、机器忙一阵，解码就会掉到
实时线以下；我们原来一帧不丢，全都解出来，欠下的账只增不减，画面越来越旧，
而且再也回不来——现场看到的就是「刚接上好好的，过一会儿延迟十秒」。

这里只做判断，不碰解码器和界面，好单独测。
"""
from dataclasses import dataclass

# 界面每 33 毫秒取一次画面，比这更密地转换纯属白做
PUBLISH_INTERVAL = 1.0 / 30
# 60 fps 的摄像头，两帧的间隔正好是一个出帧周期。浮点算下来经常差那么一丁点，
# 于是每两次就漏一次，实际出帧掉到 22 fps。留一点余量。
PUBLISH_TOLERANCE = 0.002
# 落后超过这么多秒就开始追：让解码器跳过非参考帧，并且不再往界面送
CATCH_UP_ENTER = 1.0
# 追回到这个程度就恢复正常
CATCH_UP_LEAVE = 0.3
# 追不回来就断开重连，从直播最前沿重新开始
RESYNC_AFTER = 6.0
# 追帧期间也要偶尔出一帧，否则画面像是卡死了
CATCH_UP_GLIMPSE = 0.5
# 两次重连之间至少隔这么久。机器慢到追不回来时，不停重连只会更糟——
# 每次重连本身就要一秒多，什么都看不到。
RESYNC_COOLDOWN = 30.0


@dataclass
class Decision:
    publish: bool = True      # 这一帧要不要缩放、转成图像、送到界面
    skip_nonref: bool = False # 要不要让解码器跳过非参考帧，尽快把积压烧掉
    resync: bool = False      # 要不要断开重连


class LiveEdge:
    """跟踪「我们比实时落后多少」，并给出每一帧的处置。

    落后的算法：从这一路开始播算起，墙上时钟走过的时间，减去画面时间戳走过
    的时间。解码跟得上时两者同步，差值稳定；跟不上时差值一路变大。
    """

    def __init__(self, enter=CATCH_UP_ENTER, leave=CATCH_UP_LEAVE, resync_after=RESYNC_AFTER):
        self.enter, self.leave, self.resync_after = enter, leave, resync_after
        self.started_at = None
        self.first_pts = None
        self.catching_up = False
        self.last_publish = None
        self.last_publish_pts = None
        self.last_resync = None
        self.behind = 0.0

    def observe(self, now, pts):
        """喂一帧进来，拿回这一帧该怎么处置。pts 为 None 表示这一帧没有时间戳。"""
        if self.last_publish is None:
            # 第一帧要立刻出，别让人对着空画面等一个出帧周期
            self.last_publish = now - PUBLISH_INTERVAL

        if self.first_pts is None:
            # 实时流的第一帧往往没有时间戳（实测大华就是 None）。必须等到真正
            # 带时间戳的那一帧才能开始算账，否则起点锁成 None，落后永远算不出来，
            # 整套追帧就静默失效了。
            if pts is not None:
                self.started_at, self.first_pts = now, pts
        elif pts is not None:
            self.behind = (now - self.started_at) - (pts - self.first_pts)

        if self.behind >= self.resync_after and self._may_resync(now):
            self.last_resync = now
            return Decision(publish=False, skip_nonref=True, resync=True)

        if self.catching_up:
            # 追的过程中也隔一会儿出一帧，让人看得出它在动
            if self.behind <= self.leave:
                self.catching_up = False
            else:
                glimpse = now - self.last_publish >= CATCH_UP_GLIMPSE
                if glimpse:
                    self.last_publish = now
                return Decision(publish=glimpse, skip_nonref=True)
        elif self.behind >= self.enter:
            self.catching_up = True
            return Decision(publish=False, skip_nonref=True)

        # 正常播放：界面 30Hz 取一次，比这更密地转换是白转。
        #
        # 按画面时间戳算间隔，不按墙上时钟：解码器是一阵一阵吐帧的，两帧常常
        # 前后脚到，用墙钟量就会把第二帧当成「太密」丢掉——实测 25 fps 的流
        # 只剩一半帧送到界面，画面直接掉到 12 fps。
        if pts is not None and self.last_publish_pts is not None:
            if pts - self.last_publish_pts < PUBLISH_INTERVAL - PUBLISH_TOLERANCE:
                return Decision(publish=False)
        elif pts is None and now - self.last_publish < PUBLISH_INTERVAL - PUBLISH_TOLERANCE:
            return Decision(publish=False)
        self.last_publish = now
        self.last_publish_pts = pts
        return Decision(publish=True)

    def _may_resync(self, now):
        return self.last_resync is None or now - self.last_resync >= RESYNC_COOLDOWN

    def resynced(self):
        """重连之后从头开始算。"""
        # last_resync 要留着，否则重连之后冷却就白设了
        self.started_at = self.first_pts = self.last_publish = self.last_publish_pts = None
        self.catching_up = False
        self.behind = 0.0
