import unittest
from camera_monitor.streams import authenticated_url, same_device_url, dahua_streams, StreamError, parse_profiles, parse_services, envelope, MEDIA1
from defusedxml import ElementTree as ET

class StreamTests(unittest.TestCase):
    def test_credentials_special_characters_preserve_rtsp_path(self):
        self.assertEqual(authenticated_url('rtsp://192.168.1.2:554/live?a=1', 'a@b', 'p:# /'), 'rtsp://a%40b:p%3A%23%20%2F@192.168.1.2:554/live?a=1')

    def test_does_not_forward_password_to_other_host_or_accept_file(self):
        for url in ('http://example.com/service', 'file:///etc/passwd', 'http://192.168.1.3/service'):
            with self.assertRaises(StreamError): same_device_url(url, '192.168.1.2', ('http','https'))
        self.assertEqual(same_device_url('http://0.0.0.0:8000/onvif/media', '192.168.1.2', ('http','https')), 'http://192.168.1.2:8000/onvif/media')

    def test_preserves_camera_embedded_rtsp_credentials_when_user_blank(self):
        url = 'rtsp://given:password@192.168.1.2/live'
        self.assertEqual(authenticated_url(url, '', ''), url)
        self.assertEqual(authenticated_url(url, 'viewer', 'new'), 'rtsp://viewer:new@192.168.1.2/live')

    def test_dahua_main_and_sub_stream_channel(self):
        result = dahua_streams('192.168.1.2', 3)
        self.assertEqual(len(result), 2)
        self.assertIn('channel=3&subtype=1', result[0].url)
        self.assertIn('channel=3&subtype=0', result[1].url)
        self.assertNotIn('rtsp', repr(result[0]))

    def test_profile_and_media_endpoint_parsing(self):
        doc = ET.fromstring('<r xmlns:m="http://www.onvif.org/ver10/media/wsdl"><m:Profiles token="p1"><Name>Main</Name></m:Profiles><m:Profiles token="p2"><Name>Sub</Name></m:Profiles></r>')
        self.assertEqual(parse_profiles(doc), [('p1','Main'), ('p2','Sub')])
        services = ET.fromstring(f'<r><Service><Namespace>{MEDIA1}</Namespace><XAddr>http://192.168.1.2/media</XAddr></Service></r>')
        self.assertEqual(parse_services(services), [(MEDIA1,'http://192.168.1.2/media')])

    def test_wsse_escapes_username_and_never_sends_plain_password(self):
        body = envelope('<GetProfiles/>', 'a<&', 'secret123', 0)
        root = ET.fromstring(body)
        self.assertIn(b'a&lt;&amp;', body)
        self.assertNotIn(b'secret123', body)
        self.assertTrue(any(n.tag.endswith('Password') for n in root.iter()))
