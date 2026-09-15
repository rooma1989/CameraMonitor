# Camera Monitor · 内网监控播放器

一个轻量的跨品牌 **RTSP 摄像头监控播放器**。自动发现局域网摄像头，在一个窗口里查看多个位置的实时画面。

## 功能

- 自动搜索 ONVIF / 大华兼容设备，也可为已发现设备手动填写 RTSP 地址。
- 自动、4 画面、9 画面布局，保持原始比例，支持单路放大。
- 自定义摄像头名称，账号密码保存在系统安全存储中。
- TCP / UDP 传输可选，断线后每次等待 3 秒自动重连。

当前支持 RTSP 视频预览，**暂不支持 RTMP、音频和录像**。自动发现需要设备支持并开启相应协议。

## 下载

| 系统 | 下载 | 使用方式 |
| --- | --- | --- |
| Windows 11 · 64 位 Intel / AMD | [下载 Windows 版](https://github.com/rooma1989/CameraMonitor/releases/latest/download/CameraMonitor-Windows11-x64.zip) | 解压后运行 `CameraMonitor.exe` |
| macOS 14+ · Apple 芯片 M 系列 | [下载 Mac 版](https://github.com/rooma1989/CameraMonitor/releases/latest/download/CameraMonitor-macOS-AppleSilicon.zip) | 解压后将 `Camera Monitor.app` 拖入“应用程序” |

两个版本均无需安装 Python。[查看全部版本](https://github.com/rooma1989/CameraMonitor/releases)。

首次运行请允许局域网访问；Windows 防火墙请选择信任的专用网络。Mac 版尚未经过 Apple 公证，如被系统拦截，确认下载来源后按 [Apple 官方说明](https://support.apple.com/zh-cn/102445)在“系统设置 → 隐私与安全性”中允许打开。

## 快速使用

1. 电脑与摄像头连接同一局域网，点击“搜索设备”。
2. 双击设备，按需输入账号密码；可在设置里保存位置名称。
3. 若频繁断流，停止后切换到“UDP（兼容模式）”再连接。

## 支持项目

如果觉得好用，欢迎点个 Star ⭐，也可以请我喝杯咖啡 ☕️

<img src="assets/donate.jpg" alt="微信赞赏码" width="260" />

## 从源码运行

```sh
python3 -m venv .venv
# 激活虚拟环境后执行：
python -m pip install -r requirements.txt
python -m camera_monitor.app
```

打包说明：[Windows](packaging/Windows使用说明.md) · [macOS](packaging/macOS使用说明.md)。
