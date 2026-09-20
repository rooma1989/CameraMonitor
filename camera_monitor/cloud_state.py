"""Translate between the cloud configuration and this computer's own settings.

Kept free of widgets so the mapping can be tested on its own; the orchestration
(threads, timers, UI) lives in cloud_sync.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .connection_options import ConnectionOptions
from .credentials import CredentialError
from .device_names import DeviceNames
from .discovery import Device

# 与 playback.PlayerWindow 的取流下拉顺序一致
STREAM_MODES = ('onvif', 'dahua', 'manual')

MAX_SLOTS = 25
EQUAL_CAPACITIES = (4, 9, 12, 16, 20, 25)
CAPACITIES = (4, 6, 9, 10, 12, 15, 16, 20, 25)


def mode_index(stream_mode) -> int:
    try:
        return STREAM_MODES.index(str(stream_mode))
    except ValueError:
        return 0


def stream_mode(index) -> str:
    try:
        return STREAM_MODES[int(index)]
    except (ValueError, TypeError, IndexError):
        return STREAM_MODES[0]


def camera_entry(device, *, slot_index, display_name='', name_color='#ffffff',
                 name_corner='top-left', transport='tcp', username='', password=None,
                 stream_mode='onvif', dahua_channel=1, manual_url='') -> dict:
    """密码留 None 表示「不改动云端已存的值」，传空串才是清空。"""
    entry = {
        'ip': device.ip,
        'slot_index': int(slot_index),
        'display_name': display_name or '',
        'model': device.model or '',
        'manufacturer': device.manufacturer or '',
        'protocols': list(device.protocols or []),
        'onvif_urls': list(device.urls or []),
        'stream_mode': stream_mode,
        'dahua_channel': int(dahua_channel or 1),
        'manual_url': manual_url or '',
        'transport': transport or 'tcp',
        'username': username or '',
        'name_color': name_color,
        'name_corner': name_corner,
    }
    if password is not None:
        entry['password'] = password
    return entry


def device_from_camera(camera) -> Device:
    return Device(
        ip=str(camera.get('ip', '')),
        name=str(camera.get('display_name') or ''),
        model=str(camera.get('model') or ''),
        manufacturer=str(camera.get('manufacturer') or ''),
        protocols=list(camera.get('protocols') or []),
        urls=list(camera.get('onvif_urls') or []),
    )


def slot_order(cameras) -> list:
    """还原 QSettings monitor/slots 的形状：下标即格子，空位是空串。"""
    positions = {}
    for camera in cameras:
        try:
            index = int(camera.get('slot_index', 0))
        except (TypeError, ValueError):
            continue
        if 0 <= index < MAX_SLOTS:
            positions[index] = str(camera.get('ip', ''))
    if not positions:
        return []
    return [positions.get(i, '') for i in range(max(positions) + 1)]


def layout_entry(capacity, columns, fill_width, organization) -> dict:
    normalised = {}
    for key, value in (columns or {}).items():
        try:
            key, value = int(key), int(value)
        except (TypeError, ValueError):
            continue
        if key in EQUAL_CAPACITIES and 1 <= value <= key:
            normalised[str(key)] = value
    return {
        'capacity': int(capacity) if int(capacity) in CAPACITIES else 4,
        'columns': normalised,
        'fill_width': bool(fill_width),
        'organization': (organization or '')[:80],
    }


def first_sync_direction(snapshot, local_camera_count) -> str:
    """云端还是空的而本机已经配好，就把本机这份当作模板传上去。"""
    if not (snapshot.get('cameras') or []) and local_camera_count > 0:
        return 'upload'
    return 'download'


@dataclass
class AppliedConfig:
    devices: list = field(default_factory=list)
    slots: list = field(default_factory=list)
    streams: dict = field(default_factory=dict)
    capacity: int = 4
    organization: str = ''
    fill_width: bool = False
    credential_failures: list = field(default_factory=list)


def apply_snapshot(snapshot, names: DeviceNames, options: ConnectionOptions, store) -> AppliedConfig:
    """把云端配置落到本机存储，并返回界面需要的部分。

    单台摄像头写钥匙串失败（系统安全存储不可用）不应该让整次同步作废，
    失败的 IP 会记在 credential_failures 里交给调用方提示。
    """
    cameras = list(snapshot.get('cameras') or [])
    layout = layout_entry(
        (snapshot.get('layout') or {}).get('capacity', 4),
        (snapshot.get('layout') or {}).get('columns', {}),
        (snapshot.get('layout') or {}).get('fill_width', False),
        (snapshot.get('layout') or {}).get('organization', ''),
    )

    applied = AppliedConfig(capacity=layout['capacity'],
                            organization=layout['organization'],
                            fill_width=layout['fill_width'])

    for camera in cameras:
        ip = str(camera.get('ip', ''))
        if not ip:
            continue

        try:
            names.save(ip, str(camera.get('display_name') or ''))
        except (OSError, ValueError):
            pass
        try:
            names.save_appearance(ip, camera.get('name_color', '#ffffff'),
                                  camera.get('name_corner', 'top-left'))
        except (OSError, ValueError):
            pass
        try:
            options.save_transport(ip, camera.get('transport', 'tcp'))
        except (OSError, ValueError):
            pass

        username = str(camera.get('username') or '')
        password = str(camera.get('password') or '')
        try:
            if username or password:
                store.save(ip, username, password)
            else:
                # 匿名摄像头不在钥匙串里留空记录
                store.forget(ip)
        except CredentialError:
            applied.credential_failures.append(ip)

        applied.devices.append(device_from_camera(camera))
        applied.streams[ip] = {
            'mode_index': mode_index(camera.get('stream_mode', 'onvif')),
            'dahua_channel': int(camera.get('dahua_channel') or 1),
            'manual_url': str(camera.get('manual_url') or ''),
            'transport': str(camera.get('transport') or 'tcp'),
        }

    applied.slots = slot_order(cameras)
    try:
        names.save_slot_order(applied.slots)
    except OSError:
        pass

    _save_layout(names, layout)
    return applied


def _save_layout(names: DeviceNames, layout) -> None:
    settings = names.settings
    settings.setValue('monitor/organization', layout['organization'])
    settings.setValue('monitor/fill_width', layout['fill_width'])
    settings.setValue('monitor/capacity', layout['capacity'])
    for capacity in EQUAL_CAPACITIES:
        key = f'monitor/columns/{capacity}'
        value = layout['columns'].get(str(capacity))
        settings.setValue(key, value) if value else settings.remove(key)
    settings.sync()
