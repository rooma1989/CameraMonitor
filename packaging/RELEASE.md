新增“按 IP 添加”：当摄像头网页可访问、但自动搜索未显示时，可输入 IPv4 地址进行定向 ONVIF / 大华 DHIP 探测。识别成功后自动选中设备并打开连接设置，填写账号密码即可尝试播放。

- 定向添加保留当前设备列表，同一 IP 不重复添加。
- 支持停止探测、地址校验及无回复提示。
- 修复定向探测结束后原选中设备操作按钮未恢复的问题，并调整状态提示布局。

验证：117 项自动化测试通过；局域网实测已通过定向探测识别原本自动搜索遗漏的大华 IPC-HDP2230C-SA。Windows 包由 GitHub Actions 构建，并验证 x64 格式及应用启动。Windows 实机摄像头播放仍需在目标电脑输入账号密码后确认。

Windows 11 x64：下载 CameraMonitor-Windows11-x64.zip，解压运行 CameraMonitor.exe，无需安装 Python。
macOS 14+ Apple M 系列：解压将 Camera Monitor.app 拖入应用程序。
