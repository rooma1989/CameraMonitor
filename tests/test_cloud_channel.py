import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import unittest

from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer
from PySide6.QtWidgets import QApplication

from camera_monitor.cloud_channel import CloudChannel, channel_url


def pump(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    QApplication.processEvents()
    return predicate()


def idle(seconds):
    pump(lambda: False, timeout=seconds)


class FakeServer:
    def __init__(self):
        self.server = QWebSocketServer('test', QWebSocketServer.SslMode.NonSecureMode)
        assert self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0)
        self.server.newConnection.connect(self._accept)
        self.clients = []
        self.received = []
        self.reply_to_hello = {'type': 'welcome', 'version': 5}
        self.answer_pings = True

    def url(self):
        return f'ws://127.0.0.1:{self.server.serverPort()}/api/camera-monitor/ws'

    def _accept(self):
        socket = self.server.nextPendingConnection()
        self.clients.append(socket)
        socket.textMessageReceived.connect(lambda text, s=socket: self._on_text(s, text))

    def _on_text(self, socket, text):
        message = json.loads(text)
        self.received.append(message)
        if message['type'] == 'hello' and self.reply_to_hello:
            socket.sendTextMessage(json.dumps(self.reply_to_hello))
        elif message['type'] == 'ping' and self.answer_pings:
            socket.sendTextMessage('{"type":"pong"}')

    def send(self, payload):
        self.clients[-1].sendTextMessage(json.dumps(payload))

    def close(self):
        for socket in self.clients:
            socket.abort()
        self.server.close()


class ChannelUrlTests(unittest.TestCase):
    def test_https_becomes_wss_under_the_same_path(self):
        self.assertEqual('wss://tj.example.cn/api/camera-monitor/ws',
                         channel_url('https://tj.example.cn/api/camera-monitor'))
        self.assertEqual('ws://127.0.0.1:8000/api/camera-monitor/ws',
                         channel_url('http://127.0.0.1:8000/api/camera-monitor/'))


class CloudChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.server = FakeServer()
        self.addCleanup(self.server.close)
        self.hello = {'token': 'cm1.t', 'client_uid': 'uid12345', 'app_version': '0.9.0'}
        self.make()

    def make(self, **kwargs):
        kwargs.setdefault('backoff', (0.05, 0.05))
        kwargs.setdefault('jitter', lambda: 1.0)
        self.channel = CloudChannel(self.server.url(), lambda: self.hello, **kwargs)
        self.addCleanup(self.channel.stop)
        self.messages, self.denials, self.replaced = [], [], []
        self.channel.message.connect(self.messages.append)
        self.channel.denied.connect(self.denials.append)
        self.channel.replaced.connect(lambda: self.replaced.append(1))

    def test_hello_then_welcome_brings_it_online(self):
        self.channel.start()

        self.assertTrue(pump(lambda: self.channel.online))
        self.assertEqual(dict(self.hello, type='hello'), self.server.received[0])
        self.assertEqual('welcome', self.messages[0]['type'])

    def test_server_messages_are_passed_up(self):
        self.channel.start()
        pump(lambda: self.channel.online)

        self.server.send({'type': 'command', 'id': 'c1', 'name': 'scan', 'args': {}})

        self.assertTrue(pump(lambda: len(self.messages) == 2))
        self.assertEqual('c1', self.messages[1]['id'])

    def test_send_only_works_while_online(self):
        self.assertFalse(self.channel.send({'type': 'status'}))

        self.channel.start()
        pump(lambda: self.channel.online)
        self.assertTrue(self.channel.send({'type': 'status'}))
        self.assertTrue(pump(lambda: self.server.received[-1]['type'] == 'status'))

    def test_a_dropped_connection_is_retried(self):
        self.channel.start()
        pump(lambda: self.channel.online)

        self.server.clients[-1].close()

        self.assertTrue(pump(lambda: len(self.server.clients) == 2 and self.channel.online))

    def test_a_permanent_denial_stops_retrying(self):
        self.server.reply_to_hello = {'type': 'denied', 'failure_code': 'PROFILE_DISABLED'}
        self.channel.start()

        self.assertTrue(pump(lambda: self.denials == ['PROFILE_DISABLED']))
        idle(0.3)
        self.assertEqual(1, len(self.server.clients))
        self.assertFalse(self.channel.online)

    def test_an_expired_token_is_reported_and_retried(self):
        self.server.reply_to_hello = {'type': 'denied', 'failure_code': 'INVALID_TOKEN'}
        self.channel.start()

        self.assertTrue(pump(lambda: len(self.server.clients) >= 2))
        self.assertIn('INVALID_TOKEN', self.denials)

    def test_being_replaced_stops_for_good(self):
        self.channel.start()
        pump(lambda: self.channel.online)

        self.server.send({'type': 'replaced'})

        self.assertTrue(pump(lambda: self.replaced == [1]))
        idle(0.3)
        self.assertEqual(1, len(self.server.clients))

    def test_no_session_means_no_hello(self):
        self.hello = None
        self.channel.start()

        idle(0.3)
        self.assertEqual([], self.server.received)
        self.assertFalse(self.channel.online)

    def test_heartbeats_are_sent(self):
        self.channel.stop()
        self.make(heartbeat_ms=50)
        self.channel.start()

        self.assertTrue(pump(lambda: any(m['type'] == 'ping' for m in self.server.received)))

    def test_a_silent_server_is_dropped_and_redialled(self):
        self.channel.stop()
        self.server.answer_pings = False
        self.make(heartbeat_ms=50, silence_limit=0.15)
        self.channel.start()
        pump(lambda: self.channel.online)

        self.assertTrue(pump(lambda: len(self.server.clients) >= 2))


if __name__ == '__main__':
    unittest.main()
