import json
import unittest

import requests

from camera_monitor.cloud import (CloudAuthError, CloudClient, CloudConflict,
                                  CloudError)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


class FakeSession:
    """Records calls so tests can assert on what crossed the network."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, **kwargs):
        self.calls.append({'method': method, 'url': url, **kwargs})
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass


def snapshot(version=1, cameras=None):
    return {
        'version': version,
        'profile': {'id': 1, 'name': '一楼大厅', 'institution_id': 2, 'institution_name': '阳光养老院'},
        'layout': {'capacity': 4, 'columns': {}, 'fill_width': False, 'organization': ''},
        'cameras': cameras if cameras is not None else [],
    }


def ok(data, message='成功'):
    return FakeResponse(200, {'code': 1, 'message': message, 'data': data})


def fail(status, failure_code, message='出错了', data=None):
    body = dict(data or {})
    body['failure_code'] = failure_code
    return FakeResponse(status, {'code': 0, 'message': message, 'data': body})


class CloudClientTest(unittest.TestCase):
    def client(self, *responses, base_url='https://example.com/api/camera-monitor'):
        session = FakeSession(*responses)
        return CloudClient(base_url=base_url, session=session), session

    # ---------- 登录 ----------

    def test_login_returns_token_and_configuration(self):
        payload = dict(snapshot(), token='cm1.abc.def', expires_in=2592000)
        client, session = self.client(ok(payload, '登录成功'))

        result = client.login('yg-1f-2026', 'client-uid-1', device_name='前台', app_version='0.8.0')

        self.assertEqual('cm1.abc.def', result['token'])
        self.assertEqual('一楼大厅', result['profile']['name'])
        self.assertEqual(1, result['version'])

    def test_login_sends_the_identifying_fields(self):
        client, session = self.client(ok(dict(snapshot(), token='t')))

        client.login('code12345', 'client-uid-1', device_name='前台', app_version='0.8.0')

        sent = session.calls[0]
        self.assertEqual('POST', sent['method'])
        self.assertEqual('https://example.com/api/camera-monitor/auth/login', sent['url'])
        self.assertEqual('code12345', sent['json']['auth_code'])
        self.assertEqual('client-uid-1', sent['json']['client_uid'])
        self.assertEqual('前台', sent['json']['device_name'])
        self.assertEqual('0.8.0', sent['json']['app_version'])

    def test_a_wrong_code_raises_an_auth_error_with_the_server_message(self):
        client, _ = self.client(fail(401, 'INVALID_AUTH_CODE', '授权码不正确，请核对后重新输入'))

        with self.assertRaises(CloudAuthError) as caught:
            client.login('nope1234', 'client-uid-1')

        self.assertIn('授权码不正确', str(caught.exception))
        self.assertEqual('INVALID_AUTH_CODE', caught.exception.failure_code)

    def test_a_locked_code_is_reported_as_such(self):
        client, _ = self.client(fail(429, 'AUTH_CODE_LOCKED', '该授权码尝试次数过多，请稍后再试'))

        with self.assertRaises(CloudAuthError) as caught:
            client.login('code12345', 'client-uid-1')

        self.assertEqual('AUTH_CODE_LOCKED', caught.exception.failure_code)

    def test_a_disabled_profile_is_reported_as_such(self):
        client, _ = self.client(fail(403, 'PROFILE_DISABLED', '该监控档案已停用，请联系管理员'))

        with self.assertRaises(CloudAuthError) as caught:
            client.login('code12345', 'client-uid-1')

        self.assertEqual('PROFILE_DISABLED', caught.exception.failure_code)

    # ---------- 拉取与心跳 ----------

    def test_fetch_returns_the_configuration(self):
        client, session = self.client(ok(snapshot(version=7)))

        result = client.fetch('token-abc')

        self.assertEqual(7, result['version'])
        self.assertEqual('GET', session.calls[0]['method'])
        self.assertEqual('token-abc', session.calls[0]['headers']['token'])

    def test_ping_returns_only_the_version(self):
        client, session = self.client(ok({'version': 9, 'profile_name': '一楼大厅'}))

        result = client.ping('token-abc')

        self.assertEqual(9, result['version'])
        self.assertTrue(session.calls[0]['url'].endswith('/ping'))

    def test_an_expired_token_raises_an_auth_error(self):
        client, _ = self.client(fail(401, 'INVALID_TOKEN', '登录已失效，请重新使用授权码登录'))

        with self.assertRaises(CloudAuthError):
            client.fetch('stale-token')

    # ---------- 提交 ----------

    def test_push_sends_the_version_and_returns_the_new_one(self):
        client, session = self.client(ok(snapshot(version=8)))

        result = client.push('token-abc', 7, {'capacity': 4}, [{'ip': '10.0.0.1'}])

        self.assertEqual(8, result['version'])
        sent = session.calls[0]
        self.assertEqual('PUT', sent['method'])
        self.assertEqual(7, sent['json']['version'])
        self.assertEqual([{'ip': '10.0.0.1'}], sent['json']['cameras'])

    def test_a_stale_version_raises_a_conflict_carrying_the_latest_configuration(self):
        latest = snapshot(version=12, cameras=[{'ip': '10.0.0.9'}])
        client, _ = self.client(fail(409, 'VERSION_CONFLICT', '配置已在别处更新', latest))

        with self.assertRaises(CloudConflict) as caught:
            client.push('token-abc', 7, {}, [])

        self.assertEqual(12, caught.exception.snapshot['version'])
        self.assertEqual([{'ip': '10.0.0.9'}], caught.exception.snapshot['cameras'])

    # ---------- 网络与协议异常 ----------

    def test_a_network_failure_becomes_an_authored_message(self):
        client, _ = self.client(requests.ConnectionError('getaddrinfo failed for internal.host'))

        with self.assertRaises(CloudError) as caught:
            client.fetch('token-abc')

        message = str(caught.exception)
        self.assertIn('无法连接', message)
        self.assertNotIn('getaddrinfo', message, '底层错误可能含主机名与路径，不应透传给用户')

    def test_a_timeout_becomes_an_authored_message(self):
        client, _ = self.client(requests.Timeout('read timed out'))

        with self.assertRaises(CloudError) as caught:
            client.ping('token-abc')

        self.assertIn('超时', str(caught.exception))

    def test_a_non_json_response_is_rejected(self):
        client, _ = self.client(FakeResponse(200, None, text='<html>502 Bad Gateway</html>'))

        with self.assertRaises(CloudError) as caught:
            client.fetch('token-abc')

        self.assertNotIn('<html>', str(caught.exception))

    def test_rate_limiting_is_reported_in_our_own_words(self):
        # 路由限流由框架返回英文 Too Many Attempts.，不能原样显示给现场
        client, _ = self.client(FakeResponse(429, {'message': 'Too Many Attempts.'}))

        with self.assertRaises(CloudError) as caught:
            client.login('code12345', 'client-uid-1')

        self.assertIn('过于频繁', str(caught.exception))
        self.assertNotIn('Too Many Attempts', str(caught.exception))

    def test_a_server_side_crash_does_not_leak_its_message(self):
        client, _ = self.client(FakeResponse(500, {'message': 'SQLSTATE[42S02]: Base table not found'}))

        with self.assertRaises(CloudError) as caught:
            client.fetch('token-abc')

        self.assertNotIn('SQLSTATE', str(caught.exception))

    def test_a_server_error_without_a_failure_code_is_still_reported(self):
        client, _ = self.client(FakeResponse(500, {'message': 'Server Error'}))

        with self.assertRaises(CloudError):
            client.fetch('token-abc')

    def test_a_success_body_missing_its_data_is_rejected(self):
        client, _ = self.client(FakeResponse(200, {'code': 1, 'message': '成功'}))

        with self.assertRaises(CloudError):
            client.fetch('token-abc')

    # ---------- 其他 ----------

    def test_a_trailing_slash_in_the_base_url_does_not_double_up(self):
        client, session = self.client(ok({'version': 1}), base_url='https://example.com/api/camera-monitor/')

        client.ping('token-abc')

        self.assertEqual('https://example.com/api/camera-monitor/ping', session.calls[0]['url'])

    def test_every_request_carries_a_bounded_timeout(self):
        client, session = self.client(ok(snapshot()))

        client.fetch('token-abc')

        timeout = session.calls[0]['timeout']
        self.assertIsInstance(timeout, tuple)
        self.assertTrue(all(0 < value <= 30 for value in timeout))

    def test_the_client_never_repeats_credentials_in_its_repr(self):
        client, _ = self.client()

        self.assertNotIn('token', repr(client).lower())


if __name__ == '__main__':
    unittest.main()
