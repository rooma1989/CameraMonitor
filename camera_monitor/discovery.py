"""Read-only ONVIF and DHIP discovery. No device login or configuration writes."""
from __future__ import annotations

import ipaddress
import json
import selectors
import socket
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from urllib.parse import unquote, urlsplit

import psutil
from defusedxml import ElementTree as ET


@dataclass
class Interface:
    name: str
    ip: str
    broadcast: str


@dataclass
class Device:
    ip: str
    name: str = ''
    model: str = ''
    manufacturer: str = ''
    protocols: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    interface: str = ''


def interfaces() -> list[Interface]:
    found = []
    stats = psutil.net_if_stats()
    for name, addresses in psutil.net_if_addrs().items():
        if name not in stats or not stats[name].isup:
            continue
        # Tunnel/virtual links are deliberately excluded from automatic LAN discovery.
        if name.lower().startswith(('utun', 'tun', 'tap', 'lo', 'awdl', 'llw')):
            continue
        for address in addresses:
            if address.family != socket.AF_INET or not address.netmask:
                continue
            ip = ipaddress.ip_address(address.address)
            if ip.is_loopback or ip.is_unspecified:
                continue
            network = ipaddress.ip_network(f'{ip}/{address.netmask}', strict=False)
            found.append(Interface(name, str(ip), str(network.broadcast_address)))
    return found


def onvif_probe() -> bytes:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
<s:Header><a:MessageID>urn:uuid:{uuid.uuid4()}</a:MessageID><a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To><a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action></s:Header>
<s:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></s:Body></s:Envelope>'''.encode()


def dahua_probe() -> bytes:
    body = json.dumps({'method': 'DHDiscover.search', 'params': {'mac': '', 'uni': 1}}, separators=(',', ':')).encode()
    return struct.pack('<I4s6I', 32, b'DHIP', 0, 0, len(body), 0, len(body), 0) + body


def _text(element, local_name):
    return ' '.join((node.text or '') for node in element.iter() if node.tag.rsplit('}', 1)[-1] == local_name).strip()


def parse_onvif(data: bytes, source: str) -> list[Device]:
    if len(data) > 65535:
        return []
    try:
        root = ET.fromstring(data)
    except Exception:
        return []
    result = []
    for match in root.iter():
        if match.tag.rsplit('}', 1)[-1] != 'ProbeMatch':
            continue
        types, scopes = _text(match, 'Types'), _text(match, 'Scopes')
        if 'NetworkVideoTransmitter' not in types and '/type/video_encoder' not in scopes:
            continue
        urls = []
        for url in _text(match, 'XAddrs').split():
            try:
                parsed = urlsplit(url)
                if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username:
                    urls.append(url)
            except ValueError:
                continue
        properties = {}
        for scope in scopes.split():
            parts = scope.split('/')
            if len(parts) >= 5 and parts[3] in ('name', 'hardware'):
                properties[parts[3]] = unquote('/'.join(parts[4:]))[:256]
        result.append(Device(source, name=properties.get('name', ''), model=properties.get('hardware', ''), protocols=['ONVIF'], urls=urls))
    return result


def parse_dahua(data: bytes, source: str) -> list[Device]:
    try:
        if data[4:8] == b'DHIP':
            if len(data) < 32:
                return []
            length = struct.unpack_from('<I', data, 16)[0]
            if length > 65503 or len(data) < length + 32:
                return []
            data = data[32:32 + length]
        obj = json.loads(data.rstrip(b'\x00\r\n'))
        if not isinstance(obj, dict) or obj.get('method') == 'DHDiscover.search':
            return []
        params = obj.get('params', {})
        if not isinstance(params, dict):
            return []
        info = params.get('deviceInfo', params)
        if not isinstance(info, dict) or not any(key in info for key in ('DeviceType', 'DeviceClass', 'SerialNo')):
            return []
        def value(key):
            v = info.get(key, '')
            return v[:256] if isinstance(v, str) else ''
        # Use the responding address; advertised addresses may belong to a different NIC.
        return [Device(source, name=value('MachineName'), model=value('DeviceType'), manufacturer=value('Manufacturer'), protocols=['大华 DHIP'])]
    except (ValueError, UnicodeError, struct.error):
        return []


def merge_device(records: dict[str, Device], incoming: Device) -> Device:
    if incoming.ip not in records:
        records[incoming.ip] = replace(incoming, protocols=list(incoming.protocols), urls=list(incoming.urls))
    else:
        existing = records[incoming.ip]
        for key in ('name', 'model', 'manufacturer', 'interface'):
            if not getattr(existing, key):
                setattr(existing, key, getattr(incoming, key))
        existing.protocols = list(dict.fromkeys(existing.protocols + incoming.protocols))
        existing.urls = list(dict.fromkeys(existing.urls + incoming.urls))
    return replace(records[incoming.ip], protocols=list(records[incoming.ip].protocols), urls=list(records[incoming.ip].urls))


def scan(networks: list[Interface], on_device=lambda device: None, on_status=lambda text: None,
         cancel: threading.Event | None = None, duration: float = 8.0) -> list[Device]:
    cancel = cancel or threading.Event()
    records: dict[str, Device] = {}
    channels = []
    selector = selectors.DefaultSelector()
    try:
        for network in networks:
            for protocol, port, group, packet, parser in (
                ('ONVIF', 3702, '239.255.255.250', onvif_probe(), parse_onvif),
                ('大华 DHIP', 37810, '239.255.255.251', dahua_probe(), parse_dahua),
            ):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(network.ip))
                    sock.bind((network.ip, 0))
                    sock.setblocking(False)
                    # A dedicated socket on each interface ensures responses return to that NIC.
                    destinations = [(group, port)]
                    if protocol == '大华 DHIP':
                        destinations += [(network.broadcast, port)]
                    channel = (sock, network, protocol, packet, destinations, parser)
                    selector.register(sock, selectors.EVENT_READ, channel)
                    channels.append(channel)
                except OSError as exc:
                    on_status(f'{network.name} / {protocol} 无法启用：{exc}')
                    sock.close()
        if not channels:
            on_status('没有可用于搜索的网络，请连接 Wi-Fi 或有线网络。')
            return []
        start, sent = time.monotonic(), 0
        while time.monotonic() - start < duration and not cancel.is_set():
            elapsed = time.monotonic() - start
            if sent < 3 and elapsed >= sent * 1.2:
                for sock, network, protocol, packet, destinations, parser in channels:
                    for destination in destinations:
                        try:
                            sock.sendto(packet, destination)
                        except OSError as exc:
                            if sent == 0:
                                on_status(f'{network.name} / {protocol} 发送失败：{exc}')
                sent += 1
            for key, _ in selector.select(timeout=min(0.15, max(0, duration - elapsed))):
                sock, network, protocol, packet, destinations, parser = key.data
                try:
                    data, address = sock.recvfrom(65535)
                except (BlockingIOError, ConnectionResetError):
                    continue
                except OSError as exc:
                    on_status(f'{network.name} 接收失败：{exc}')
                    continue
                for device in parser(data, address[0]):
                    device.interface = f'{network.name} · {network.ip}'
                    on_device(merge_device(records, device))
        return list(records.values())
    finally:
        selector.close()
        for sock, *_ in channels:
            sock.close()
