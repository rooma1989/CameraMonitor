import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from camera_monitor.discovery import Device
from camera_monitor.streams import OnvifClient, MEDIA2
from defusedxml import ElementTree as ET

class OnvifHttpTests(unittest.TestCase):
    def test_media2_camera_returns_playable_uri_over_real_http(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length']))
                root = ET.fromstring(data)
                operation = list(next(n for n in root.iter() if n.tag.endswith('Body')))[0].tag.rsplit('}',1)[-1]
                if operation == 'GetSystemDateAndTime':
                    body = '<GetSystemDateAndTimeResponse/>'
                elif operation == 'GetServices':
                    body = f'<GetServicesResponse><Service><Namespace>{MEDIA2}</Namespace><XAddr>http://127.0.0.1:{self.server.server_port}/media2</XAddr></Service></GetServicesResponse>'
                elif operation == 'GetProfiles':
                    body = '<GetProfilesResponse><Profiles token="unavailable"><Name>Unavailable</Name></Profiles><Profiles token="p1"><Name>Camera one</Name></Profiles></GetProfilesResponse>'
                elif operation == 'GetStreamUri':
                    protocol = next(n.text for n in root.iter() if n.tag.endswith('Protocol'))
                    token = next(n.text for n in root.iter() if n.tag.endswith('ProfileToken'))
                    if protocol != 'RTSP' or token == 'unavailable':
                        body = '<Fault><Reason>Invalid Protocol</Reason></Fault>'
                    else:
                        body = '<GetStreamUriResponse><Uri>rtsp://127.0.0.1:554/live</Uri></GetStreamUriResponse>'
                else: body = '<Fault/>'
                encoded=f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body>{body}</s:Body></s:Envelope>'.encode()
                self.send_response(200)
                self.send_header('Content-Length',str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        client = OnvifClient(Device('127.0.0.1',urls=[f'http://127.0.0.1:{server.server_port}/onvif']))
        try:
            streams=client.streams()
            self.assertEqual(len(streams),1)
            self.assertEqual(streams[0].name,'Camera one')
            self.assertEqual(streams[0].url,'rtsp://127.0.0.1:554/live')
        finally:
            client.close()
            server.shutdown()
            thread.join()
            server.server_close()
