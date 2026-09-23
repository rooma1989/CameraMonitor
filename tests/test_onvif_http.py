import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from camera_monitor.discovery import Device
from camera_monitor.streams import OnvifClient, MEDIA1, MEDIA2
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


class OnvifProfileChoiceTests(unittest.TestCase):
    """实测那台 ONVIF 相机：Media1 只报一路主码流，704×576 的子码流全在 Media2 里。

    原来的代码从第一个有结果的服务拿完就收手，于是现场永远只能播主码流，
    下拉框里连子码流这个选项都没有。
    """

    def serve(self, handler_body):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_body)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()

        def shut_down():
            server.shutdown()
            thread.join()
            server.server_close()

        self.addCleanup(shut_down)
        return server

    def camera(self, media1_profiles, media2_profiles):
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length']))
                root = ET.fromstring(data)
                operation = list(next(n for n in root.iter() if n.tag.endswith('Body')))[0].tag.rsplit('}', 1)[-1]
                port = self.server.server_port
                if operation == 'GetSystemDateAndTime':
                    body = '<GetSystemDateAndTimeResponse/>'
                elif operation == 'GetServices':
                    body = ('<GetServicesResponse>'
                            f'<Service><Namespace>{MEDIA1}</Namespace><XAddr>http://127.0.0.1:{port}/media1</XAddr></Service>'
                            f'<Service><Namespace>{MEDIA2}</Namespace><XAddr>http://127.0.0.1:{port}/media2</XAddr></Service>'
                            '</GetServicesResponse>')
                elif operation == 'GetProfiles':
                    source = media1_profiles if self.path.endswith('media1') else media2_profiles
                    entries = ''.join(
                        f'<Profiles token="{token}"><Name>{token}</Name>'
                        f'<Resolution><Width>{w}</Width><Height>{h}</Height></Resolution></Profiles>'
                        for token, w, h in source)
                    body = f'<GetProfilesResponse>{entries}</GetProfilesResponse>'
                elif operation == 'GetStreamUri':
                    token = next(n.text for n in root.iter() if n.tag.endswith('ProfileToken'))
                    body = f'<GetStreamUriResponse><Uri>rtsp://127.0.0.1:554/{token}</Uri></GetStreamUriResponse>'
                else:
                    body = '<Fault/>'
                encoded = (f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
                           f'<s:Body>{body}</s:Body></s:Envelope>').encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = test.serve(Handler)
        client = OnvifClient(Device('127.0.0.1', urls=[f'http://127.0.0.1:{server.server_port}/onvif']))
        test.addCleanup(client.close)
        return client

    def test_a_substream_that_only_media2_reports_is_still_found(self):
        client = self.camera(media1_profiles=[('main', 1920, 1080)],
                             media2_profiles=[('main', 1920, 1080), ('sub', 704, 576)])

        streams = client.streams()

        self.assertEqual(2, len(streams), '一路等于没得选，就该接着问下一个服务')
        self.assertIn('704×576', streams[0].name, '默认该播小的那路')

    def test_the_first_service_is_enough_when_it_already_offers_a_choice(self):
        client = self.camera(media1_profiles=[('main', 1920, 1080), ('sub', 640, 480)],
                             media2_profiles=[('other', 2560, 1440)])

        streams = client.streams()

        self.assertEqual(['rtsp://127.0.0.1:554/sub', 'rtsp://127.0.0.1:554/main'],
                         [s.url for s in streams], '有得挑就别再多跑一轮')

    def test_the_main_stream_is_still_offered(self):
        client = self.camera(media1_profiles=[('main', 1920, 1080), ('sub', 704, 576)],
                             media2_profiles=[])

        names = [s.name for s in client.streams()]

        self.assertTrue(any('1920×1080' in name for name in names), '主码流要留在下拉框里')

    def test_it_does_not_go_hunting_when_the_stream_it_has_is_already_small(self):
        # 多问一轮网络往返实测要多等近两秒，一面墙十几格都得等。已经拿到 720P
        # 这种放进格子绰绰有余的档次，就别为了找更小的再跑一趟。
        client = self.camera(media1_profiles=[('ok', 1280, 720)],
                             media2_profiles=[('tiny', 704, 576)])

        streams = client.streams()

        self.assertEqual(['rtsp://127.0.0.1:554/ok'], [s.url for s in streams])

    def test_it_does_go_hunting_when_all_it_has_is_a_main_stream(self):
        client = self.camera(media1_profiles=[('main', 2560, 1440)],
                             media2_profiles=[('sub', 704, 576)])

        streams = client.streams()

        self.assertEqual('rtsp://127.0.0.1:554/sub', streams[0].url, '只有主码流时就该去找子码流')
