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
