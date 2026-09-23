"""Read-only ONVIF media access and RTSP addressing; never log credentials."""
from __future__ import annotations
import base64
import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, quote
from xml.sax.saxutils import escape

import requests
from requests.auth import HTTPDigestAuth
from defusedxml import ElementTree as ET

DEVICE = 'http://www.onvif.org/ver10/device/wsdl'
MEDIA1 = 'http://www.onvif.org/ver10/media/wsdl'
MEDIA2 = 'http://www.onvif.org/ver20/media/wsdl'
SCHEMA = 'http://www.onvif.org/ver10/schema'


class StreamError(Exception):
    """Only application-authored, credential-free messages may be used."""


class AuthError(StreamError):
    pass


@dataclass
class Stream:
    name: str
    url: str = field(repr=False)


def same_device_url(url, ip, schemes=('rtsp', 'rtsps')):
    try:
        parts = urlsplit(url.strip())
        port = parts.port
        if parts.scheme not in schemes or not parts.hostname:
            raise ValueError()
        if parts.hostname in ('0.0.0.0', 'localhost', '127.0.0.1') and ip != '127.0.0.1':
            host = f'{ip}:{port}' if port else ip
            parts = parts._replace(netloc=host)
        elif parts.hostname != ip:
            raise ValueError()
        return urlunsplit(parts)
    except ValueError:
        raise StreamError('设备返回了无效或指向其他主机的地址，请检查设备网络配置。') from None


def authenticated_url(url, username, password):
    parts = urlsplit(url)
    if parts.scheme not in ('rtsp', 'rtsps') or not parts.hostname:
        raise StreamError('请输入有效的 RTSP 视频地址。')
    if not username:
        return url
    host = f'[{parts.hostname}]' if ':' in parts.hostname else parts.hostname
    if parts.port:
        host += f':{parts.port}'
    return urlunsplit(parts._replace(netloc=f'{quote(username, safe="")}:{quote(password, safe="")}@{host}'))


def dahua_streams(ip, channel=1):
    return [Stream('流畅 · 子码流', f'rtsp://{ip}:554/cam/realmonitor?channel={channel}&subtype=1'),
            Stream('高清 · 主码流', f'rtsp://{ip}:554/cam/realmonitor?channel={channel}&subtype=0')]


def envelope(body, username='', password='', offset=0):
    security = ''
    if username:
        nonce = os.urandom(20)
        created = datetime.fromtimestamp(time.time() + offset, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
        security = f'''<wsse:Security s:mustUnderstand="1" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"><wsse:UsernameToken><wsse:Username>{escape(username)}</wsse:Username><wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password><wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce><wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>'''
    return f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tt="{SCHEMA}"><s:Header>{security}</s:Header><s:Body>{body}</s:Body></s:Envelope>'.encode()


def local(node):
    return node.tag.rsplit('}', 1)[-1]


def text_of(node, name):
    return next(((n.text or '').strip() for n in node.iter() if local(n) == name), '')


# 小于这个面积的档次多半是给缩略图用的（320×240 之类），放到监控墙上太糊。
# 按面积不按宽度：实测有相机把 Width/Height 报反（Width=1080 Height=1920）。
MIN_USABLE_PIXELS = 640 * 360
# 监控墙一格通常也就四五百像素宽，720P 放进去已经绰绰有余。已经拿到这个档次
# 就别再为了找更小的去多问一轮——实测多问一轮要多等近两秒。
PREFERRED_MAX_PIXELS = 1280 * 720


def profile_size(node):
    """从一个 profile 里取出编码分辨率。取不到就返回 (0, 0)。"""
    resolution = next((n for n in node.iter() if local(n) == 'Resolution'), None)
    if resolution is None:
        return 0, 0
    try:
        return int(text_of(resolution, 'Width') or 0), int(text_of(resolution, 'Height') or 0)
    except ValueError:
        return 0, 0


def parse_profiles(root):
    profiles = []
    for n in root.iter():
        if local(n) == 'Profiles' and 'token' in n.attrib:
            width, height = profile_size(n)
            profiles.append((n.attrib['token'], text_of(n, 'Name') or n.attrib['token'], width, height))
    return profiles


def profile_rank(width, height):
    """排序用：能用的里面挑最小的排前面。

    监控墙一格才几百像素宽，却默认播主码流，是现场机器带不动、延迟越积越多的
    根源。小的排前面，主码流仍然留在下拉框里随时可选。
    """
    pixels = width * height
    if not pixels:
        return (1, 0)          # 相机没报分辨率，保持它自己给的顺序
    if pixels < MIN_USABLE_PIXELS:
        return (2, pixels)     # 太糊，排到最后
    return (0, pixels)


def parse_services(root):
    return [(text_of(n, 'Namespace'), text_of(n, 'XAddr')) for n in root.iter()
            if local(n) == 'Service' and text_of(n, 'Namespace') in (MEDIA1, MEDIA2)]


class OnvifClient:
    def __init__(self, device, username='', password='', cancel=None):
        self.ip = device.ip
        candidates = device.urls or [f'http://{device.ip}/onvif/device_service']
        self.endpoint = same_device_url(candidates[0], self.ip, ('http', 'https'))
        self.username, self.password = username, password
        self.cancel = cancel or threading.Event()
        self.offset = 0
        self.session = requests.Session()
        self.session.trust_env = False
        if username:
            self.session.auth = HTTPDigestAuth(username, password)

    def close(self):
        self.session.close()
        self.password = ''
        self.session.auth = None

    def call(self, endpoint, namespace, operation, inner='', anonymous=False):
        if self.cancel.is_set():
            raise StreamError('连接已取消。')
        endpoint = same_device_url(endpoint, self.ip, ('http','https'))
        body = f'<m:{operation} xmlns:m="{namespace}">{inner}</m:{operation}>'
        action = f'{namespace}/{operation}'
        try:
            # Redirects are forbidden to keep authentication on the selected camera.
            with self.session.post(endpoint, data=envelope(body, '' if anonymous else self.username, self.password, self.offset),
                    headers={'Content-Type': f'application/soap+xml; charset=utf-8; action="{action}"', 'SOAPAction': f'"{action}"'},
                    timeout=(3, 4), allow_redirects=False, stream=True) as response:
                if response.status_code in (401, 403):
                    raise AuthError('认证失败，请检查摄像头的 ONVIF 用户名和密码。')
                payload = bytearray()
                for chunk in response.iter_content(16384):
                    if self.cancel.is_set():
                        raise StreamError('连接已取消。')
                    payload.extend(chunk)
                    if len(payload) > 2_000_000:
                        raise StreamError('设备返回的数据过大。')
                try:
                    root = ET.fromstring(payload)
                except Exception:
                    raise StreamError('设备没有返回有效的 ONVIF 数据。') from None
                fault = next((n for n in root.iter() if local(n) == 'Fault'), None)
                if fault is not None:
                    values = ' '.join(n.text or '' for n in fault.iter())
                    if any(word in values.lower() for word in ('notauthorized', 'unauthorized', 'failedauthentication')):
                        raise AuthError('认证失败，请检查 ONVIF 账号密码及设备时间。')
                    raise StreamError('设备不支持此 ONVIF 操作，或尚未开启服务。')
                if not response.ok or response.is_redirect:
                    raise StreamError(f'设备服务不可用（HTTP {response.status_code}）。')
                return root
        except requests.RequestException:
            raise StreamError('无法连接 ONVIF 服务，请检查网络、服务端口及协议是否开启。') from None

    @staticmethod
    def _small_enough(found):
        pixels = min(rank[1] for rank in (entry[0] for entry in found))
        return 0 < pixels <= PREFERRED_MAX_PIXELS

    def streams(self):
        # Correct WS-Security clock skew without changing the camera clock.
        try:
            root = self.call(self.endpoint, DEVICE, 'GetSystemDateAndTime', anonymous=True)
            utc = next((n for n in root.iter() if local(n) == 'UTCDateTime'), None)
            if utc is not None:
                parts = [int(text_of(utc, key)) for key in ('Year','Month','Day','Hour','Minute','Second')]
                self.offset = datetime(*parts, tzinfo=timezone.utc).timestamp() - time.time()
        except (StreamError, ValueError):
            pass
        try:
            services = parse_services(self.call(self.endpoint, DEVICE, 'GetServices', '<m:IncludeCapability>false</m:IncludeCapability>'))
        except AuthError:
            raise
        except StreamError:
            services = []
        if not services:
            root = self.call(self.endpoint, DEVICE, 'GetCapabilities', '<m:Category>Media</m:Category>')
            services = [(MEDIA1, text_of(n, 'XAddr')) for n in root.iter() if local(n) == 'Media' and text_of(n, 'XAddr')]
        best = []
        last_error = None
        for namespace, endpoint in services:
            if best and self._small_enough(best):
                # 已经有一路够小的了，再找也省不下多少。这个判断必须在发请求
                # 之前做——放到 GetProfiles 后面的话，那一轮往返照样白跑。
                break

            try:
                profiles = parse_profiles(self.call(endpoint, namespace, 'GetProfiles'))
            except AuthError:
                raise
            except StreamError as exc:
                last_error = exc
                continue

            if best:
                # 第一个服务已经给了地址，只是没得挑。再问下一个服务时，只挑
                # 「比现在最小的还小、又不至于太糊」的那一档去问——每问一次都是
                # 一轮网络往返，实测多问一轮要多等两秒半，一面墙十几格都得等。
                smallest = min(rank for rank, _, _ in best)
                profiles = sorted(
                    (entry for entry in profiles if profile_rank(entry[2], entry[3]) < smallest),
                    key=lambda entry: profile_rank(entry[2], entry[3]))[:1]
                if not profiles:
                    continue

            streams = []
            try:
                for token, name, width, height in profiles[:16]:
                    if self.cancel.is_set():
                        raise StreamError('连接已取消。')
                    if namespace == MEDIA1:
                        inner = f'<m:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></m:StreamSetup><m:ProfileToken>{escape(token)}</m:ProfileToken>'
                    else:
                        inner = f'<m:Protocol>RTSP</m:Protocol><m:ProfileToken>{escape(token)}</m:ProfileToken>'
                    try:
                        root = self.call(endpoint, namespace, 'GetStreamUri', inner)
                        url = same_device_url(text_of(root, 'Uri'), self.ip)
                    except AuthError:
                        raise
                    except StreamError as exc:
                        if self.cancel.is_set():
                            raise
                        last_error = exc
                        continue
                    known = [found[2] for found in best] + [found[2] for found in streams]
                    if url not in known:
                        label = f'{name} · {width}×{height}' if width and height else name
                        streams.append((profile_rank(width, height), label, url))
            except AuthError:
                raise
            except StreamError as exc:
                last_error = exc

            best.extend(streams)
            if len(best) > 1:
                # 有得挑了就不必再问别的服务
                break
        if best:
            # 能用的里面最小的排第一：默认播它，主码流仍在下拉框里
            best.sort(key=lambda found: found[0])
            return [Stream(label, url) for _, label, url in best]
        raise last_error or StreamError('没有取得可播放通道。可以使用手动 RTSP 地址连接。')
