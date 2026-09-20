"""Cloud configuration sync over HTTPS. Only application-authored messages reach the UI.

The configuration carries camera passwords, so nothing from this module may print,
log or embed a payload: network errors are classified by type, exactly like playback.py
does for FFmpeg.
"""
from __future__ import annotations

import requests

DEFAULT_BASE_URL = 'https://tj.xqq.zhongkangyang.cn/api/camera-monitor'

# 监控屏无人值守，宁可等一会儿也不要因为一次抖动就判失败
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 8.0


class CloudError(Exception):
    """Only credential-free, application-authored messages may be used."""

    def __init__(self, message, failure_code=''):
        super().__init__(message)
        self.failure_code = failure_code


class CloudAuthError(CloudError):
    """授权码或令牌失效，需要重新登录。"""


class CloudConflict(CloudError):
    """本机版本落后，服务端已把最新配置一并返回。"""

    def __init__(self, message, snapshot):
        super().__init__(message, 'VERSION_CONFLICT')
        self.snapshot = snapshot


# 这些失败码代表「重新登录才能恢复」，与网络抖动区分开
_AUTH_FAILURES = ('INVALID_AUTH_CODE', 'AUTH_CODE_LOCKED', 'PROFILE_DISABLED',
                  'INVALID_TOKEN', 'INVALID_CLIENT_UID')


class CloudClient:
    def __init__(self, base_url=None, session=None, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip('/')
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()
        if session is None:
            # 代理环境变量可能把内网流量导去别处，这里只走明确配置的地址
            self.session.trust_env = False

    def __repr__(self):
        return f'<CloudClient {self.base_url}>'

    def close(self):
        self.session.close()

    # ---------- 接口 ----------

    def login(self, auth_code, client_uid, device_name='', app_version=''):
        return self._call('POST', '/auth/login', body={
            'auth_code': auth_code,
            'client_uid': client_uid,
            'device_name': device_name,
            'app_version': app_version,
        })

    def ping(self, token):
        return self._call('GET', '/ping', token=token)

    def fetch(self, token):
        return self._call('GET', '/config', token=token)

    def push(self, token, version, layout, cameras):
        return self._call('PUT', '/config', token=token, body={
            'version': version,
            'layout': layout,
            'cameras': cameras,
        })

    # ---------- 内部 ----------

    def _call(self, method, path, token=None, body=None):
        headers = {'Accept': 'application/json'}
        if token:
            headers['token'] = token

        try:
            response = self.session.request(
                method, self.base_url + path,
                json=body, headers=headers, timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            raise CloudError('连接云端超时，请检查网络后重试。') from None
        except requests.RequestException:
            # 底层信息可能含主机名、路径甚至地址里的凭据，一律不透传
            raise CloudError('无法连接云端服务，请检查网络或稍后再试。') from None

        return self._unpack(response)

    def _unpack(self, response):
        try:
            payload = response.json()
        except Exception:
            raise CloudError(f'云端返回了无法识别的内容（HTTP {response.status_code}）。') from None

        if not isinstance(payload, dict):
            raise CloudError(f'云端返回了无法识别的内容（HTTP {response.status_code}）。')

        data = payload.get('data')
        data = data if isinstance(data, dict) else {}
        failure_code = str(data.get('failure_code') or '')
        # 服务端的提示语是我们自己写的中文，可以直接展示
        message = str(payload.get('message') or '')

        if payload.get('code') == 1 and response.status_code == 200:
            if not data:
                raise CloudError('云端返回的配置为空，请稍后重试。')
            return data

        if failure_code == 'VERSION_CONFLICT':
            raise CloudConflict(message or '配置已在别处更新。', data)

        if failure_code in _AUTH_FAILURES or response.status_code in (401, 403):
            raise CloudAuthError(message or '云端登录已失效，请重新输入授权码。', failure_code)

        raise CloudError(message or f'云端服务暂时不可用（HTTP {response.status_code}）。', failure_code)
