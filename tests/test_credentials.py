import unittest
from camera_monitor.credentials import CredentialStore, CredentialError

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


class KeychainRetryTests(unittest.TestCase):
 """升级后签名变化会让旧记录覆盖不了，必须先删再写。"""

 def test_a_write_that_is_refused_is_retried_after_deleting_the_old_entry(self):
  class Guarded(MemoryVault):
   def __init__(self):
    super().__init__();self.refusals=1;self.deleted=[]
   def set_password(self,service,account,secret):
    if self.refusals:
     self.refusals-=1
     raise RuntimeError('errSecAuthFailed')
    super().set_password(service,account,secret)
   def delete_password(self,service,account):
    self.deleted.append((service,account));super().delete_password(service,account)
  vault=Guarded()
  store=CredentialStore(vault)
  store.save('192.168.1.8','admin','pw')
  self.assertEqual(store.load('192.168.1.8'),('admin','pw'))
  self.assertEqual(len(vault.deleted),1,'应当先删掉写不动的旧记录再重试')

 def test_a_vault_that_always_refuses_reports_the_upgrade_hint(self):
  class Broken(MemoryVault):
   def set_password(self,*args):raise RuntimeError('errSecAuthFailed')
  store=CredentialStore(Broken())
  with self.assertRaises(CredentialError) as error:store.save('cam','admin','pw')
  self.assertIn('钥匙串访问',str(error.exception),'要告诉人怎么自救')
  self.assertNotIn('pw',str(error.exception))

 def test_the_cloud_session_write_retries_the_same_way(self):
  from camera_monitor.credentials import CloudSessionStore
  class Guarded(MemoryVault):
   def __init__(self):
    super().__init__();self.refusals=1;self.deleted=[]
   def set_password(self,service,account,secret):
    if self.refusals:
     self.refusals-=1
     raise RuntimeError('errSecAuthFailed')
    super().set_password(service,account,secret)
   def delete_password(self,service,account):
    self.deleted.append((service,account));super().delete_password(service,account)
  vault=Guarded()
  store=CloudSessionStore(vault)
  store.save_session('YG1F2026','cm1.token')
  self.assertEqual(store.load_session(),{'auth_code':'YG1F2026','token':'cm1.token'})
  self.assertEqual(len(vault.deleted),1)
