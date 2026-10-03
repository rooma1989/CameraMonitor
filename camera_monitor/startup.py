"""打开软件后先去哪：欢迎页、完整界面，还是直接按云端登录态走。

`cloud/standalone` 是三态：
- 没写过：还没判断过，按「以前有没有用过」判断一次，并把结果记下来；
- true：单机，直接进完整界面；
- false：明确要看欢迎页（新电脑，或者托管电脑解绑之后）。

判断只做一次，是因为解绑后相机配置会有意保留，云端下发的布局也会写进
DeviceNames；如果每次启动都重新看这些键，解绑的电脑就永远回不到欢迎页。

「用过」看两样：本程序自己的名称、外观、分屏分组，以及大屏密码 fullscreen/password。
后者 ScreenLock 一构造就会写入初始密码，但 Window 在构造 ScreenLock 之前就做完了
这次判断，而判断又只做一次，所以本次进程写下的密码永远不会被算进去；能看到它，
说明以前的版本（v0.8 及更早，一启动就写）在这台电脑上运行过。
"""

WELCOME, STANDALONE, CLOUD = 'welcome', 'standalone', 'cloud'
STANDALONE_KEY = 'cloud/standalone'
# 只认本程序自己写进 DeviceNames 的分组（device_names.py / multiview.py / cloud_state.py）。
# 不能直接看 allKeys()：macOS 的 QSettings 默认带回退，会把系统全局键
# （AppleLanguages、AppleLocale 等）也列出来，全新的 Mac 会被误判成老用户。
_LEGACY_GROUPS = ('names', 'appearance', 'monitor')
# 大屏密码不在上面的分组里，单独认（见模块说明：判断赶在 ScreenLock 写入之前）
_SCREEN_PASSWORD_KEY = 'fullscreen/password'


def _truthy(value):
    return str(value).lower() in ('true', '1')


def _used_before(names_settings):
    if names_settings.contains(_SCREEN_PASSWORD_KEY):
        return True
    return any(key.split('/', 1)[0] in _LEGACY_GROUPS for key in names_settings.allKeys())


def _save(cloud_settings, standalone):
    cloud_settings.setValue(STANDALONE_KEY, standalone)
    cloud_settings.sync()
    return cloud_settings.status() == cloud_settings.Status.NoError


def startup_route(cloud_settings, names_settings):
    if _truthy(cloud_settings.value('cloud/enabled', False)):
        return CLOUD
    decided = cloud_settings.value(STANDALONE_KEY)
    if decided is not None:
        return STANDALONE if _truthy(decided) else WELCOME
    # 第一次判断：老用户升级上来，以前存过名称、顺序、分屏，就当他是单机用户
    standalone = _used_before(names_settings)
    _save(cloud_settings, standalone)
    return STANDALONE if standalone else WELCOME


def choose_standalone(cloud_settings):
    return _save(cloud_settings, True)


def clear_standalone(cloud_settings):
    """托管电脑解绑后调用：明确写 False，下次启动出欢迎页，不再按旧配置重新判断。"""
    return _save(cloud_settings, False)
