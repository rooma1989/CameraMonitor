"""Credentials are stored only in the platform's native secure vault."""
import json
import sys

SERVICE = 'CameraMonitor.camera-login.v1'
CLOUD_SERVICE = 'CameraMonitor.cloud-session.v1'
CLOUD_ACCOUNT = 'session'


class CredentialError(Exception):
    pass


class CredentialStore:
    def __init__(self, backend=None):
        self.backend=backend

    def vault(self):
        if self.backend is None:
            try:
                if sys.platform == 'darwin':
                    from keyring.backends.macOS import Keyring
                    self.backend=Keyring()
                elif sys.platform == 'win32':
                    from keyring.backends.Windows import WinVaultKeyring
                    self.backend=WinVaultKeyring()
                else:
                    raise CredentialError('此系统尚未配置安全密码存储。')
            except CredentialError:raise
            except Exception:
                raise CredentialError('无法使用系统安全存储，密码不会保存。') from None
        return self.backend

    def load(self,device):
        try:
            value=self.vault().get_password(SERVICE,device)
            if value is None:return None
            obj=json.loads(value)
            if not isinstance(obj,dict) or not all(isinstance(obj.get(k),str) for k in ('username','password')):
                raise ValueError()
            return obj['username'],obj['password']
        except Exception:
            raise CredentialError('无法读取已保存密码，请手动输入或检查系统钥匙串。') from None

    def save(self,device,username,password):
        try:
            self.vault().set_password(SERVICE,device,json.dumps({'username':username,'password':password},ensure_ascii=False))
        except Exception:
            raise CredentialError('密码未保存：系统安全存储不可用或访问未获允许。') from None

    def forget(self,device):
        try:
            vault=self.vault()
            if vault.get_password(SERVICE,device) is not None:
                vault.delete_password(SERVICE,device)
        except Exception:
            raise CredentialError('未能删除已保存密码，请检查系统安全存储。') from None


class CloudSessionStore(CredentialStore):
    """云端授权码与令牌。授权码要能静默重登，所以必须和摄像头密码一样进安全存储。"""

    def load_session(self):
        try:
            value=self.vault().get_password(CLOUD_SERVICE,CLOUD_ACCOUNT)
            if value is None:return None
            obj=json.loads(value)
            if not isinstance(obj,dict):raise ValueError()
            return {'auth_code':str(obj.get('auth_code','')),'token':str(obj.get('token',''))}
        except Exception:
            raise CredentialError('无法读取云端登录信息，请重新输入授权码。') from None

    def save_session(self,auth_code,token):
        try:
            self.vault().set_password(CLOUD_SERVICE,CLOUD_ACCOUNT,
                json.dumps({'auth_code':auth_code,'token':token},ensure_ascii=False))
        except Exception:
            raise CredentialError('云端登录信息未保存：系统安全存储不可用或访问未获允许。') from None

    def clear_session(self):
        try:
            vault=self.vault()
            if vault.get_password(CLOUD_SERVICE,CLOUD_ACCOUNT) is not None:
                vault.delete_password(CLOUD_SERVICE,CLOUD_ACCOUNT)
        except Exception:
            raise CredentialError('未能清除云端登录信息，请检查系统安全存储。') from None
