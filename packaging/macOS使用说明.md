# Camera Monitor · macOS Apple 芯片版

适用于 macOS 14 或更高版本、Apple M 系列芯片，不适用于 Intel Mac。

解压后将 Camera Monitor.app 拖入“应用程序”，双击打开，无需安装 Python。
允许局域网访问，搜索摄像头后双击连接；密码保存在当前用户的钥匙串。
本版未经过 Apple 公证。若系统拦截，确认来源后按照 https://support.apple.com/zh-cn/102445 在“系统设置 → 隐私与安全性”中允许打开。

## 从源码打包

在 Apple 芯片 Mac 上创建并激活 .venv，安装 requirements.txt 和 packaging/requirements-build.txt。
执行 `zsh packaging/build-macos.sh`，输出 dist/Camera Monitor.app。
发布前验证应用签名、arm64 架构及脱离项目目录的启动；同时检查真实设备发现、播放及停止。
