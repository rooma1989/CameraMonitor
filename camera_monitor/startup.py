"""打开软件后先去哪：欢迎页、完整界面，还是直接按云端登录态走。"""

WELCOME, STANDALONE, CLOUD = 'welcome', 'standalone', 'cloud'
STANDALONE_KEY = 'cloud/standalone'
# ScreenLock 构造时就会写入初始密码，它不能算作「以前用过」
_IGNORED_KEYS = ('fullscreen/password',)


def _truthy(value):
    return str(value).lower() in ('true', '1')


def startup_route(cloud_settings, names_settings):
    if _truthy(cloud_settings.value('cloud/enabled', False)):
        return CLOUD
    if _truthy(cloud_settings.value(STANDALONE_KEY, False)):
        return STANDALONE
    # 老用户升级上来：以前存过名称、顺序、分屏，就当他是单机用户，不出欢迎页
    if any(key not in _IGNORED_KEYS for key in names_settings.allKeys()):
        choose_standalone(cloud_settings)
        return STANDALONE
    return WELCOME


def choose_standalone(cloud_settings):
    cloud_settings.setValue(STANDALONE_KEY, True)
    cloud_settings.sync()


def clear_standalone(cloud_settings):
    cloud_settings.remove(STANDALONE_KEY)
    cloud_settings.sync()
