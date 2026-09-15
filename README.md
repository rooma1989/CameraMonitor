# Camera Monitor · 摄像头发现

无需事先知道 IP，在局域网内搜索摄像头与录像机。已支持设备发现和实时视频预览，不包含音频播放、录像或设备配置修改。

## macOS 启动

双击项目目录的 **启动摄像头搜索.command**，再点击 **搜索摄像头**。当前开发环境已安装依赖。

- 运行环境：macOS 14+、Python 3.11+。首次使用其他电脑需要安装 Python，脚本会建立项目虚拟环境并安装依赖。
- 搜索约 8 秒，结果实时出现；支持停止、重新搜索、网络选择和复制 IP。
- 如果系统询问局域网访问权限，请允许启动程序访问局域网。
- 这是开发版启动入口，尚未打包成无需 Python 的独立 .app。

## 监控工作台

- 在连接设置中填写“自定义名称”并点击“保存名称”，例如“大门”“仓库”。设备列表和监控标题同步更新，重启后保留；清空并保存恢复设备名称。名称按 IP 保存在当前电脑，不会修改摄像头配置。
- 断线时可打开连接设置，点击“复制连接诊断”，查看本次连接持续时间、帧数、超时或流结束等原因；记录不包含账号密码或视频地址。
- 传输默认 TCP。对 TCP 模式频繁断流的设备，可停止播放后在连接设置中选择“UDP（兼容模式）”再连接；按 IP 自动保存，下次连接及断线重试继续使用该方式。UDP 需要局域网及防火墙允许媒体数据返回，网络丢包时可能出现花屏，可切回 TCP。

- 左侧搜索局域网设备，可按名称或 IP 筛选。双击设备直接尝试连接；已保存密码时自动使用，允许匿名访问的设备直接播放，需要认证时在右侧填写连接设置。
- 默认 **自动** 布局：两路同高并排，按各自视频原始比例分配宽度，不裁切、不拉伸，不显示空占位格。也可选择 4画面 / 9画面。
- 鼠标移入视频显示连接、停止、设置、放大和移除操作；窄画面收纳到“操作”菜单。双击视频可放大单路，再双击返回，其他连接继续播放。
- 右侧“连接设置”浮层不会改变视频区域尺寸，连接成功后自动收起。网络、设备详情和搜索日志收纳在左下方。
- 默认记住密码，首次成功出图后保存到 macOS 钥匙串 / Windows 凭据管理器。保存失败保留设置提示；取消勾选可删除。记录按 IP 区分。
- 大华默认流畅子码流，可停止后切换高清主码流。其他设备读取 ONVIF Media/Media2 通道。
- 已成功播放的视频断流后自动重连，每次固定等待 3 秒；认证或地址错误需手动处理。停止、移除、关闭主窗口均取消重连并释放连接。
- 当前不录制、不播放音频；设备端主动断流仍可能造成短暂画面间断。

## Windows

分发目标为 **Windows 11 x64（Intel / AMD）免安装 exe**。在 Windows 编译电脑安装 Python 3.12 x64 后，双击 `build-windows.bat`，通过测试和打包后生成 `dist-win/CameraMonitor.exe`。使用成品的电脑无需安装 Python。

当前已准备 Windows 打包配置，尚未在 Windows 环境生成或验收 exe；macOS 不能直接通过 PyInstaller 生成 Windows 成品。详见 [Windows 使用与编译说明](packaging/Windows使用说明.md)。开发时仍可使用 `start-windows.bat`。

云端编译入口：GitHub 仓库 **Actions → Build Windows x64 EXE → Run workflow**。
成功后从该次运行的 **Artifacts → CameraMonitor-Windows11-x64** 下载 exe 压缩包。
向 main 分支推送程序或打包配置改动也会自动编译。云端启动检查通过后才上传成品。

## 发现方式

- **ONVIF WS-Discovery**：按活动 IPv4 网卡发送 UDP 3702 组播，解析视频设备回复。
- **大华 DHIP**：每张网卡发送 UDP 37810 组播及子网广播，读取设备信息；大华兼容/OEM 设备也可能回复，因此协议不等同于品牌确认。
- 同一 IP 的重复回复和两种协议结果合并。IP 显示实际响应源地址。
- 未知品牌只有支持相应发现协议并启用服务时才能自动发现。没有回复不表示不存在摄像头。
- 发现阶段服务地址是设备自报信息，未自动访问；点击连接后才访问该摄像头的媒体服务。发现状态不代表视频已验证。

## 搜不到时

检查电脑和设备是否在相同广播网络、系统局域网权限是否允许、访客 Wi-Fi 或 VLAN 是否隔离、摄像头是否开启 ONVIF。可以选择具体网卡重试。发现阶段不做通用端口扫描或登录；播放时使用你输入的账号认证，不修改设备配置。

## 开发与验证

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m camera_monitor.app
.venv/bin/python -m unittest discover -s tests -v
```

测试包含协议解析、恶意/损坏报文、非视频设备过滤、去重、界面复制、完成状态、取消及本地 UDP 真实收发。

## 协议参考

- [ONVIF Core / WS-Discovery](https://www.onvif.org/specs/core/ONVIF-Core-Specification-v250.pdf)
- [DHIP 发现格式参考](https://github.com/OpenIPC/python-dhip/blob/master/dahua/discovery.py)
- [Qt for Python](https://doc.qt.io/qtforpython-6/)

## 播放实现与测试

- ONVIF 支持 WS-Security PasswordDigest、HTTP Digest 和设备时间偏差校准；逐个通道取流，单通道故障不阻塞其他通道。
- PyAV/FFmpeg 采用 RTSP over TCP，后台解码 H.264/H.265，仅保留最新画面。
- 关闭及 Escape 均先取消网络工作再释放窗口；错误信息不包含带密码的视频地址。
- 自动测试额外覆盖特殊字符密码、跨设备地址拒绝、真实 HTTP Media2 通道交互、视频帧解码、认证失败提示和窗口关闭。
- 项目固定 PySide6 6.8.3；本机 6.11.2 曾在 macOS 辅助功能查询时触发 libqcocoa 崩溃，启动器会校验并安装固定版本。

参考：[ONVIF Media2](https://www.onvif.org/specs/srv/media/ONVIF-Media2-Service-Spec-v1612.pdf)、[大华 RTSP](https://www.dahuawiki.com/Remote_Access/RTSP_via_VLC)、[PyAV](https://pyav.org/docs/develop/api/_globals.html)。

## macOS 26 兼容处理补充

2026-09-14 用户切换大华摄像头后仍出现 Qt 6.8.3 的 QMacAccessibilityElement 释放崩溃，证明仅固定旧版 Qt 不足以解决。设备行现使用普通可访问按钮，网络/连接方式/通道选择改用原生菜单动作，避免 QTableWidget 和 QComboBox 内部 item view 生成的 Cocoa 表格辅助功能对象；系统辅助功能保持启用。

视频停止兼容修复：解码采用单线程，避免 PyAV/FFmpeg frame-thread 析构持有 GIL 导致界面卡死。真实视频连续3轮连接和停止已验证。
