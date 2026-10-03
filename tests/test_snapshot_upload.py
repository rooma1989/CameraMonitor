import unittest

from camera_monitor.cloud import CloudClient, CloudError
from test_cloud import FakeResponse, FakeSession, fail, ok


class SnapshotUploadTests(unittest.TestCase):
    def client(self, *responses):
        session = FakeSession(*responses)
        return CloudClient('https://example.com/api/camera-monitor', session=session), session

    def test_a_camera_snapshot_is_posted_as_multipart(self):
        client, session = self.client(ok({'captured_at': '2026-10-03 10:00:00'}))

        result = client.upload_snapshot('cm1.t', 'camera', b'\xff\xd8jpeg', ip='10.0.0.1')

        call = session.calls[0]
        self.assertEqual('POST', call['method'])
        self.assertEqual('https://example.com/api/camera-monitor/snapshots', call['url'])
        self.assertEqual('cm1.t', call['headers']['token'])
        self.assertEqual({'kind': 'camera', 'ip': '10.0.0.1'}, call['data'])
        self.assertEqual(('snapshot.jpg', b'\xff\xd8jpeg', 'image/jpeg'), call['files']['image'])
        self.assertNotIn('json', call, 'multipart 请求不能再带 JSON 体')
        self.assertEqual('2026-10-03 10:00:00', result['captured_at'])

    def test_a_screen_capture_has_no_ip(self):
        client, session = self.client(ok({'captured_at': 'x'}))

        client.upload_snapshot('cm1.t', 'screen', b'\xff\xd8')

        self.assertEqual({'kind': 'screen'}, session.calls[0]['data'])

    def test_failures_are_reported_without_the_payload(self):
        client, _ = self.client(fail(422, 'INVALID_SNAPSHOT', '截图格式不正确'))

        with self.assertRaises(CloudError) as error:
            client.upload_snapshot('cm1.t', 'camera', b'secret-bytes', ip='10.0.0.1')

        self.assertNotIn('secret-bytes', str(error.exception))

    def test_json_calls_still_send_json(self):
        client, session = self.client(ok({'version': 1}))

        client.ping('cm1.t')

        self.assertIn('json', session.calls[0])
        self.assertNotIn('files', session.calls[0])


if __name__ == '__main__':
    unittest.main()
