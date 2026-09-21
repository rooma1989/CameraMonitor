import json
import unittest

from camera_monitor.credentials import (CLOUD_ACCOUNT, CLOUD_SERVICE, SERVICE,
                                        CloudSessionStore, CredentialError,
                                        CredentialStore)

class MemoryVault:
 def __init__(self):self.items={}
 def get_password(self,service,account):return self.items.get((service,account))
 def set_password(self,service,account,secret):self.items[service,account]=secret
 def delete_password(self,service,account):self.items.pop((service,account),None)

class CredentialTests(unittest.TestCase):
 def test_credentials_are_isolated_by_device_and_can_be_forgotten(self):
  vault=MemoryVault();store=CredentialStore(vault)
  store.save('192.168.1.108','viewer','p@ss特殊')
  self.assertEqual(store.load('192.168.1.108'),('viewer','p@ss特殊'))
  self.assertIsNone(store.load('192.168.1.68'))
  store.forget('192.168.1.108')
  self.assertIsNone(store.load('192.168.1.108'))
 def test_vault_errors_do_not_echo_secret_or_fallback(self):
  class Broken(MemoryVault):
   def set_password(self,*args):raise RuntimeError('secret-in-provider-error')
  store=CredentialStore(Broken())
  with self.assertRaises(CredentialError) as error:store.save('camera','viewer','secret')
  self.assertNotIn('secret',str(error.exception))


class LockedVault(MemoryVault):
    """照搬 macOS 钥匙串实测出来的规矩。

    某条记录的 ACL 属于上一版签名时，写和删都会被拒；只有先把它读出来，
    接下来的删和写才放行。测试里必须还原这一条，否则「先删再写」这种
    错的修法也能过。
    """

    def __init__(self):
        super().__init__()
        self.locked = set()
        self.read = set()
        self.deleted = []

    def lock(self, service, account):
        self.locked.add((service, account))

    def _refused(self, key):
        return key in self.locked and key not in self.read

    def get_password(self, service, account):
        value = super().get_password(service, account)
        if value is not None:
            self.read.add((service, account))
        return value

    def set_password(self, service, account, secret):
        if self._refused((service, account)):
            raise RuntimeError("(-25244, 'Unknown Error')")
        super().set_password(service, account, secret)

    def delete_password(self, service, account):
        if self._refused((service, account)):
            raise RuntimeError("(-25244, 'Unknown Error')")
        self.deleted.append((service, account))
        self.locked.discard((service, account))
        super().delete_password(service, account)


class KeychainRetryTests(unittest.TestCase):
    """升级后签名变了，旧记录就覆盖不了，必须读一遍再删掉重写。"""

    def test_a_write_that_is_refused_is_retried_after_deleting_the_old_entry(self):
        vault = LockedVault()
        vault.set_password(SERVICE, '192.168.1.8',
                           json.dumps({'username': 'old', 'password': 'old'}))
        vault.lock(SERVICE, '192.168.1.8')

        store = CredentialStore(vault)
        store.save('192.168.1.8', 'admin', 'pw')

        self.assertEqual(('admin', 'pw'), store.load('192.168.1.8'))
        self.assertEqual([(SERVICE, '192.168.1.8')], vault.deleted,
                         '写不动的旧记录要先删掉')

    def test_the_cloud_session_write_recovers_the_same_way(self):
        vault = LockedVault()
        vault.set_password(CLOUD_SERVICE, CLOUD_ACCOUNT,
                           json.dumps({'auth_code': 'OLD', 'token': 'old'}))
        vault.lock(CLOUD_SERVICE, CLOUD_ACCOUNT)

        store = CloudSessionStore(vault)
        store.save_session('YG1F2026', 'cm1.token')

        self.assertEqual({'auth_code': 'YG1F2026', 'token': 'cm1.token'},
                         store.load_session())

    def test_a_vault_that_always_refuses_reports_the_upgrade_hint(self):
        class Broken(MemoryVault):
            def set_password(self, *args):
                raise RuntimeError('errSecAuthFailed')

        store = CredentialStore(Broken())
        with self.assertRaises(CredentialError) as error:
            store.save('cam', 'admin', 'pw')

        self.assertIn('钥匙串访问', str(error.exception), '要告诉人怎么自救')
        self.assertNotIn('pw', str(error.exception))


if __name__ == '__main__':
    unittest.main()
