"""云端下行通道：后台的操作 1 秒内送到这台电脑。

连不上不是故障：配置照样靠 60 秒轮询同步，画面照样放。这里只负责尽快连上、
断了就退避重连，收到的消息原样交给上层。
"""
from __future__ import annotations

import json
import random
import time
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

BACKOFF_SECONDS = (1, 2, 4, 8, 16, 32, 60)
HEARTBEAT_MS = 30_000
SILENCE_LIMIT_SECONDS = 90
MAX_MESSAGE_BYTES = 256 * 1024
# 这些拒绝重试也没用，只能等人重新输入设备码
PERMANENT_DENIALS = ('PROFILE_DISABLED', 'INVALID_AUTH_CODE', 'PROFILE_IN_USE')


def channel_url(base_url):
    parts = urlsplit(base_url)
    scheme = {'https': 'wss', 'http': 'ws'}.get(parts.scheme, parts.scheme)
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip('/') + '/ws', '', ''))


class CloudChannel(QObject):
    online_changed = Signal(bool)
    message = Signal(dict)
    denied = Signal(str)
    replaced = Signal()

    def __init__(self, url, hello, parent=None, backoff=BACKOFF_SECONDS,
                 heartbeat_ms=HEARTBEAT_MS, silence_limit=SILENCE_LIMIT_SECONDS,
                 jitter=lambda: random.uniform(0.8, 1.2), clock=time.monotonic):
        super().__init__(parent)
        self.url = url
        # 每次连上时现取：令牌会被静默重登换掉
        self.hello = hello
        self.backoff = tuple(backoff)
        self.silence_limit = silence_limit
        self.jitter = jitter
        self.clock = clock
        self.running = False
        self.online = False
        self.attempt = 0
        self.last_heard = 0.0

        self.socket = QWebSocket()
        self.socket.setParent(self)
        self.socket.connected.connect(self._on_connected)
        self.socket.disconnected.connect(self._on_disconnected)
        self.socket.errorOccurred.connect(self._on_error)
        self.socket.textMessageReceived.connect(self._on_text)

        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self._open)
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(heartbeat_ms)
        self.heartbeat.timeout.connect(self._beat)

    # ---------- 对外 ----------

    def start(self):
        if self.running:
            return
        self.running = True
        self.attempt = 0
        self._open()

    def stop(self):
        self.running = False
        self.retry_timer.stop()
        self.heartbeat.stop()
        self.socket.abort()
        self._set_online(False)

    def send(self, payload):
        if not self.online:
            return False
        self.socket.sendTextMessage(json.dumps(payload, ensure_ascii=False))
        return True

    # ---------- 内部 ----------

    def _open(self):
        if not self.running:
            return
        if self.socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            self.socket.abort()
        self.socket.open(QUrl(self.url))

    def _on_connected(self):
        hello = self.hello()
        if not hello:
            # 还没登录：断开，等下一轮退避再试
            self.socket.close()
            return
        self.last_heard = self.clock()
        self.socket.sendTextMessage(json.dumps(dict(hello, type='hello'), ensure_ascii=False))

    def _on_text(self, text):
        self.last_heard = self.clock()
        if len(text.encode('utf-8')) > MAX_MESSAGE_BYTES:
            return
        try:
            payload = json.loads(text)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        kind = payload.get('type')
        if kind == 'welcome':
            self.attempt = 0
            self.heartbeat.start()
            self._set_online(True)
            self.message.emit(payload)
        elif kind == 'denied':
            code = str(payload.get('failure_code') or '')
            if code in PERMANENT_DENIALS:
                self.running = False
            self.denied.emit(code)
            self.socket.close()
        elif kind == 'replaced':
            # 同一个设备码在别处连上了：这一条让位，不再重连，免得两边来回抢
            self.running = False
            self.replaced.emit()
            self.socket.close()
        elif kind == 'pong':
            pass
        else:
            self.message.emit(payload)

    def _beat(self):
        if self.clock() - self.last_heard > self.silence_limit:
            # 半开连接：TCP 看着还在，其实早断了。主动掐掉，走重连
            self.socket.abort()
            return
        self.send({'type': 'ping'})

    def _on_error(self, *args):
        if self.socket.state() == QAbstractSocket.SocketState.UnconnectedState:
            self._on_disconnected()

    def _on_disconnected(self):
        self.heartbeat.stop()
        self._set_online(False)
        if not self.running or self.retry_timer.isActive():
            return
        delay = self.backoff[min(self.attempt, len(self.backoff) - 1)] * self.jitter()
        self.attempt += 1
        self.retry_timer.start(max(1, int(delay * 1000)))

    def _set_online(self, online):
        if online != self.online:
            self.online = online
            self.online_changed.emit(online)
