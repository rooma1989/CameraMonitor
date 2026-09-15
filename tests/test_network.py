import socket
import threading
import unittest
from camera_monitor.discovery import Interface, scan
from test_discovery import ONVIF

class NetworkTests(unittest.TestCase):
    def test_real_udp_response_is_collected_and_duplicate_merged(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 37810))
        server.settimeout(2)
        errors = []
        def reply():
            try:
                _, address = server.recvfrom(8192)
                body = b'{"method":"client.notifyDevInfo","params":{"deviceInfo":{"DeviceType":"IPC-test"}}}'
                server.sendto(body, address)
                server.sendto(body, address)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=reply)
        thread.start()
        try:
            found = scan([Interface('loopback', '127.0.0.1', '127.0.0.1')], duration=0.4)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].model, 'IPC-test')
        finally:
            thread.join(3)
            server.close()
        self.assertEqual(errors, [])

    def test_cancelled_scan_does_not_wait_for_timeout(self):
        event = threading.Event()
        event.set()
        self.assertEqual(scan([Interface('loopback','127.0.0.1','127.0.0.1')], cancel=event), [])

    def test_no_network_provides_actionable_status(self):
        messages = []
        self.assertEqual(scan([], on_status=messages.append), [])
        self.assertTrue(messages)
