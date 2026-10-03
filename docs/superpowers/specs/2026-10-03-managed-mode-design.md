# CameraMonitor 傻瓜模式（云端托管）· 设计

- 日期：2026-10-03
- 范围：桌面客户端（本仓库）+ yunqi 后端（`/Users/rooma/Projects/云栖/yunqi`）+ yunqi 后台页面。三处的设计都写在本文；yunqi 的实现阶段再在该仓库补一份指向本文的说明。
- 前置：云端配置同步（客户端 v0.8.x，yunqi spec `docs/superpowers/specs/2026-09-19-camera-monitor-cloud-sync-design.md`）。
- 面向产品的功能说明（Word，含界面示意图）已另行交付，本文只写实现层面的设计。

## 1. 背景与目标

v0.8 已经能把一个点位的配置（摄像头、账号密码、顺序、布局）存在云端，但配置仍然**以客户端为准**：现场要有人搜摄像头、填密码、拖顺序、开全屏，后台只能看。养老机构现场人员大多做不了这些，开通一个点位往往要上门。

目标：现场只输入一次设备码，之后所有配置和日常操作都由后台远程完成，现场电脑不需要、也不能再点任何东西。

成功标准：

1. 新点位开通时，现场只做"安装软件 + 输入设备码"，其余全部在后台完成。
2. 后台的操作 1 秒内到达在线的现场电脑。
3. 现场电脑断电重启后，不需要任何人操作就恢复到原来的画面（包括全屏）。
4. 和后台断开连接时，现场画面照常播放。
5. 现有的完整模式用户不受影响。

## 2. 已定决策

| 问题 | 结论 |
|---|---|
| 两个版本怎么分发 | **同一个安装包**，由云端决定模式。设备码多一个 `mode` 字段（`full` 或 `managed`），后台随时切换，现场不用重装 |
| 响应速度 | **1 秒内**。新建一条 WebSocket 下行通道，配置本身仍走现有的 HTTP 同步 |
| 现场还能做什么 | 什么都不能做。只留一个**密码逃生口**：组合键 + 维护密码，用来退出软件或解除绑定 |
| 开机启动 | 进入傻瓜模式后**自动设为开机启动**；解绑或切回完整模式时自动取消 |
| 后台怎么认出摄像头 | 客户端**上传截图**（单路截图 + 整个屏幕的截图）；后台不看实时视频 |
| 实现方案 | 方案 A：新开一个 Workerman 进程专门服务摄像头客户端，不复用 `pad:downlink` |
| 全屏开关 | 改完**立即下发**，不用点"保存并下发"（功能文档第十节的待确认项，暂按此执行） |
| 权限 | 沿用现有的 `camera-monitor-profile` 权限，不新增（同上，暂按此执行） |

## 3. 两条贯穿全局的规则

### 3.1 状态和动作分开

- **状态**：写进云端配置，重启后也要恢复。包括摄像头列表和每路的设置、顺序、分屏数、每行几个、横向铺满、**是否全屏**、大屏标题、模式、维护密码。客户端开机先按本地缓存恢复，再跟云端同步。
- **动作**：只执行一次。包括搜索、截图、全部重连、重启软件。动作通过 WebSocket 下发，设备离线时直接告诉后台"设备离线"，**不排队**，等设备上线也不会补发。

这样即使 WebSocket 全部断掉，影响的也只是"动作暂时用不了"，现场画面不受影响。

### 3.2 配置只有一方能写

| 模式 | 谁能写配置 | 另一方 |
|---|---|---|
| `full` | 客户端（和 v0.8 一样） | 后台只能看 |
| `managed` | 后台 | 客户端不上传配置；服务端收到客户端的 `PUT /config` 一律回 409 `MANAGED_PROFILE` |

任何时候只有一方在写，所以现有的乐观锁（`version`）不需要改。后台改配置和客户端改配置走同一个 `CameraMonitorConfigService::apply`。

## 4. 客户端

### 4.1 启动流程

```
启动
 ├─ 钥匙串里有登录会话 → 静默登录（现有逻辑）→ 读缓存的配置快照 → 按 mode 进入对应界面
 ├─ 选过「单机使用」（QSettings cloud/standalone=true）→ 完整界面
 ├─ 本机运行过旧版本（QSettings `DeviceNames` 或 `Cloud` 里有任何键）→ 写入 standalone=true，进完整界面（老用户升级不出欢迎页）
 └─ 以上都不是 → 欢迎页
```

- **欢迎页**（`welcome.py`）：一个大号设备码输入框、"开始使用"按钮，下面一行"不用云端，单机使用 →"。登录过程中和登录失败的提示都在本页显示，错误文案沿用 `cloud_sync` 现有的中文提示。
- 登录成功后看快照里的 `mode`：`full` 进完整界面（和 v0.8 一样）；`managed` 进傻瓜模式。
- 第一次同步的方向（`first_sync_direction`）只在 `full` 模式下判断。`managed` 模式下一律以云端为准。

### 4.2 傻瓜模式的界面

- 复用全屏演示模式（`set_presentation(True)`）那套隐藏逻辑：侧边栏、设置页、按钮全部不显示。
- 在这之上再锁住所有交互。`MultiView` / `CameraTile` / `EmptySlot` 增加一个 `locked` 属性，为真时：
  - 鼠标点击、双击、拖拽、放下都直接忽略；
  - 右键菜单不弹出；
  - `Window` 的 F11、Esc 等快捷键不起作用。
- 是否全屏由 `layout.fullscreen` 决定：
  - `true`：`showFullScreen()`。系统层面退出全屏（`changeEvent`）会被拉回全屏，而且**不弹密码框**。现有的 `native_exit_requested` 在 managed 模式下改走这条路。
  - `false`：最大化的普通窗口，里面同样只有监控墙。
- **没有摄像头时**：墙上居中显示"已连接云端 · 正在等待管理员配置摄像头"，下面显示本机名称和设备码末 4 位。
- **断线提示**：WebSocket 连续断开超过 10 秒，右上角显示"云端连接中断，画面正常播放中 · 正在自动重连"；重新连上后消失。

### 4.3 密码逃生口

- 组合键 `Ctrl+Shift+Alt+Q`（mac 上是 `⌃⇧⌥Q`，即物理 Control 键；`⌘⇧⌥Q` 是系统「立即注销」，不能用）弹出"维护人员入口"对话框。对话框里有密码框和两个按钮："退出软件"、"解除绑定（换设备码）"。
- 密码校验复用 `ScreenLock`。存储格式本来就是 `pbkdf2_sha256$240000$<salt>$<digest>`。云端快照里的 `escape_password_hash` 就是同样格式的一条记录，客户端收到后原样写进 `ScreenLock.KEY`（新增 `ScreenLock.set_record(record)`，先校验格式再写）。所以**断网时也能验证**。
- 云端还没设置维护密码（字段为 null）时，保留本地现有的记录，初始是 `000000`。
- **解除绑定**依次做这几件事：
  1. `CloudSync.logout()`，清掉钥匙串里的登录会话；
  2. 删除开机启动项；
  3. 清掉 `cloud/version`、`cloud/snapshot`、`cloud/standalone`；
  4. 停掉所有画面；
  5. 回到欢迎页。
- 本机的摄像头配置（名称、顺序、钥匙串里的摄像头密码）**保留**。用户换一个设备码、走到 `full` 首次同步时，`first_sync_direction` 会照旧处理这些配置。

### 4.4 开机启动（`autostart.py`）

对外接口：`enable()`、`disable()`、`is_enabled()`。按平台分两个实现：

- **Windows**：写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，值名 `CameraMonitor`，值为 `"<sys.executable>" --autostart`。用标准库 `winreg`。
- **macOS**：写 `~/Library/LaunchAgents/com.cameramonitor.desktop.plist`，`RunAtLoad=true`，`ProgramArguments` 指向 `.app` 里的可执行文件，再加参数 `--autostart`。只写文件，不调用 `launchctl`，下次登录时生效。
- **源码运行时**（`sys.frozen` 为假）`enable()` 什么也不做，只记一条日志。这样开发和测试时不会往真实系统里写启动项。
- **调用时机**：
  - 进入 managed 模式时（登录后，以及每次启动时）检查一下，没开就开。这样即使用户手动删掉了启动项，下次进入也会补回来。
  - 离开 managed 模式时（解绑、切回 `full`、收到 `revoked`）删除。
- `--autostart` 参数目前只用来区分启动来源、记日志，行为和正常启动完全一样。

### 4.5 新增模块

| 模块 | 职责 | 依赖 |
|---|---|---|
| `welcome.py` | 欢迎页组件，发出 `login_requested(code)` 和 `standalone_chosen` 信号 | 无 |
| `managed_mode.py` | `ManagedController`：进入和退出 managed 模式、锁定界面、逃生口、执行动作、回报状态 | 依赖 `Window` 的程序化入口（见 4.6） |
| `cloud_channel.py` | `CloudChannel(QObject)`：基于 `QWebSocket` 的连接、`hello` 握手、心跳、断线退避重连，收到的消息转成 Qt 信号 | `PySide6.QtWebSockets`（PySide6 6.8.3 自带，不需要新增依赖） |
| `autostart.py` | 开机启动项（见 4.4） | `winreg` / `plistlib` |
| `snapshots.py` | 截单路画面或整个窗口，压成 JPEG，在后台线程上传 | `QImage`、`requests`（沿用 `CloudClient` 的会话设置） |

`CloudClient` 新增 `upload_snapshot(ip, kind, jpeg_bytes)`。`CloudSync` 新增两项：

- 属性 `mode`；
- 信号 `mode_changed(str)`。快照落地后，如果 `mode` 和之前不同就发出这个信号，由 `Window` 决定进入还是退出 managed 模式。

### 4.6 程序化入口改造

现在有几个操作在全屏演示时会被拦掉，或者会弹出模态框，远程调用时没法用。改造方式是把"判断界面状态"和"真正执行操作"拆开：

| 现有方法 | 问题 | 改造 |
|---|---|---|
| `Window.start_scan()` | 处于 `presentation` 时直接 return | 拆出 `run_scan(target_ip=None, on_finished=None)`，不检查界面状态；`start_scan()` 只做界面判断，再调它。扫描完不再自动打开设置面板，这一步留在界面路径里 |
| `MultiView.set_grid_columns()` | 演示模式下拒绝执行 | 加参数 `force=False`，云端路径传 `True` |
| `MultiView.set_fill_width()` | 演示模式下会回退到旧值 | 同上；把 `apply_cloud_config` 里直接写 `_fill_width` 的写法收归到这里 |
| `Window.exit_fullscreen()` | 弹模态密码框 | managed 模式改用 `set_fullscreen(bool)`：直接切换，不需要密码 |
| `finish_scan()` | 结束后打开设置面板 | managed 模式下不打开，改为上报 `scan_result` |

`apply_cloud_config` 的 managed 分支在现有逻辑之后再做三件事：

1. 按 `layout.fullscreen` 调 `set_fullscreen`；
2. 写入 `escape_password_hash`；
3. 摄像头没填账号密码时，使用 `default_credentials`（见 6.1），写进 `session_credentials`，不写钥匙串。

### 4.7 动作的执行

| `command.name` | 参数 | 执行 | 结果 |
|---|---|---|---|
| `scan` | `target_ip?` | `run_scan(target_ip)`。扫描期间再收到 `scan` 时，回 `ack ok=false error=BUSY` | `scan_result {id, devices}` |
| `refresh_snapshots` | `ips?`（不传表示全部） | 对在墙上并且正在播放的摄像头，取最后一帧上传；不在墙上的，用 `thumbnails.py` 免密截一张试试 | 每张图单独上传；完成后回 `command_done {id}` |
| `capture_screen` | 无 | `QWidget.grab()` 截整个窗口，缩到宽 1280，JPEG 不超过 400 KB，以 `kind=screen` 上传 | `command_done {id}` |
| `reconnect_all` | 无 | `wall.reconnect_all()` | `command_done {id}` |
| `restart_app` | 无 | 先回 `ack`，再 `QProcess.startDetached(sys.executable, argv)`，然后 `QApplication.quit()` | 重启后重新 `hello` |

- 每个动作收到后**先回 `ack`**；不认识的 `name` 回 `ack ok=false error=UNKNOWN_COMMAND`。
- 只有在 managed 模式下才执行动作。`full` 模式下一律回 `ack ok=false error=NOT_MANAGED`。

### 4.8 状态回报

`status` 消息的内容：

```json
{"type":"status","config_version":13,"fullscreen":true,"app_version":"0.9.0",
 "cameras":[{"ip":"192.168.2.216","state":"playing","width":1280,"height":720},
            {"ip":"192.168.2.219","state":"auth_failed"}]}
```

- **`state` 取值**：`playing`（在播）、`connecting`（连接中）、`auth_failed`（账号密码错）、`unreachable`（连不上）、`stopped`（已停止）。由 `PlayerWindow` 现有的状态文字和信号整理成枚举，新增 `PlayerWindow.state_changed(str)` 信号。
- **发送时机**：
  - 握手成功后立刻发一次；
  - 之后每当有变化就发，但 1 秒内的多次变化合并成一条；
  - 另外每 60 秒兜底发一次。
- **自动截图**：某一路**第一次**进入 `playing` 时，自动上传一张这路的截图。

## 5. 云端通道协议

### 5.1 部署

- 新增 artisan 命令 `camera-monitor:ws {run|start|stop|restart|status}`，写法参照 `PadDownlinkWebSocketCommand`。
- 单 worker，开两个监听：
  - `websocket://127.0.0.1:16505`：对外，由 nginx 转发；
  - `http://127.0.0.1:16506`：对内，只给本机的 PHP-FPM 调用。
- nginx 配置：`location /api/camera-monitor/ws { proxy_pass http://127.0.0.1:16505; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; proxy_read_timeout 120s; proxy_set_header X-Real-IP $remote_addr; }`
- 守护：新增 `supervisor/camera-monitor-ws.conf`，开启 `autorestart`。

### 5.2 握手与认证

1. 客户端连上 `wss://<host>/api/camera-monitor/ws`，URL 里**不带任何凭据**。
2. 客户端在 5 秒内发送 `{"type":"hello","token":"…","client_uid":"…","app_version":"0.9.0"}`；超过 5 秒没发，服务端断开连接。
3. 服务端调用 `CameraMonitorSessionVerifier::verify(token, client_uid)`。这个类是从 `CameraMonitorAuth` 中间件里抽出来的，HTTP 和 WebSocket 共用。它检查四项：token 签名、token 是否过期、设备码是否启用、`acceptsClient`。
4. 校验失败：回 `{"type":"denied","failure_code":"INVALID_TOKEN|PROFILE_DISABLED|PROFILE_IN_USE|…"}` 后关闭连接。客户端的处理和 HTTP 返回同样错误码时一样，例如 `INVALID_TOKEN` 时静默重新登录。
5. 校验成功：回 `{"type":"welcome","version":<当前配置版本>}`。如果这个版本比客户端本地的新，客户端立刻拉一次配置。
6. 同一个 `profile_id` 已经有连接时，先给旧连接发 `{"type":"replaced"}`，再关掉它。旧连接收到 `replaced` 后**不再重连**。

### 5.3 消息

**服务端发给客户端**

| type | 字段 | 含义 |
|---|---|---|
| `welcome` | `version` | 握手成功 |
| `denied` | `failure_code` | 握手失败 |
| `config_changed` | `version` | 配置有更新。客户端收到后用 `GET /config` 拉取，并按 `version` 去重 |
| `command` | `id`, `name`, `args` | 一次性动作，见 4.7。`id` 是服务端生成的 UUID |
| `revoked` | `failure_code` | 设备码被解绑、停用或重置。客户端的处理同"解除绑定"，并在欢迎页上提示原因 |
| `replaced` | 无 | 同一个设备码有了新连接，这条旧连接被替换 |
| `pong` | 无 | 心跳回应 |

**客户端发给服务端**

| type | 字段 | 含义 |
|---|---|---|
| `hello` | `token`, `client_uid`, `app_version` | 握手 |
| `ping` | 无 | 每 30 秒一次 |
| `ack` | `id`, `ok`, `error?` | 收到了动作 |
| `command_done` | `id`, `ok`, `error?` | 动作执行完了（适用于没有专门结果消息的动作） |
| `scan_result` | `id`, `devices[]` | 搜索结果。每台设备的字段：`ip`、`model`、`manufacturer`、`protocols`、`onvif_urls` |
| `status` | 见 4.8 | 运行状态 |

- 收到不认识的 `type`，双方都直接忽略，不断开连接。
- 单条消息超过 256 KB 时，服务端直接断开连接。

### 5.4 心跳与断线

- **服务端**：90 秒内没收到某个连接的任何消息，就断开它，并把这台设备标记为离线。
- **客户端**：
  - 断线后按 1、2、4、8、16、32、60 秒的间隔重连，之后一直保持 60 秒，每次加 ±20% 的随机抖动；
  - 握手成功后，退避间隔重置；
  - 收到 `denied` 且错误码属于"永久失败"（`PROFILE_DISABLED`、`INVALID_AUTH_CODE`）时，停止重连。
- **兜底**：现有的 60 秒 `/ping` 轮询**保留**。

### 5.5 对内的发消息接口

```
POST http://127.0.0.1:16506/send
{"profile_id": 3, "message": {...}}
→ {"delivered": true}  或  {"delivered": false, "reason": "offline"}
```

- 只监听 127.0.0.1。请求还要带一个 `X-Internal-Key`，值等于 `config('camera_monitor.ws_internal_key')`。
- PHP 端通过 `CameraMonitorChannel::send(profileId, message)` 调用，超时 2 秒。超时或连接失败都当作 `offline` 处理。

### 5.6 WebSocket 进程把收到的数据落到哪里

消息处理放在 `CameraMonitorChannelRouter`。它是一个普通类，不依赖 Workerman，接收"连接上下文 + 消息"，返回"要回的消息 + 要做的事"，方便单元测试。

| 消息 | 写入位置 |
|---|---|
| `status` | `camera_monitor_client.runtime_status`（JSON）、`runtime_status_at` |
| `scan_result` | 在一个事务里，删掉该 `profile_id` 在 `camera_monitor_discovered` 中的全部记录，再插入这次的结果 |
| `ack`、`command_done` | `camera_monitor_command`：更新 `acked_at`、`finished_at`、`ok`、`error` |
| 连接、断开 | `camera_monitor_client.channel_connected_at` / `channel_disconnected_at` |

## 6. HTTP 接口变化

### 6.1 配置快照

在现有字段的基础上新增：

```json
{"version": 13,
 "mode": "managed",
 "escape_password_hash": "pbkdf2_sha256$240000$…$…",
 "default_credentials": {"username": "admin", "password": "…"},
 "layout": {"capacity": 12, "columns": {"12": 4}, "fill_width": true, "organization": "阳光养老院", "fullscreen": true},
 "cameras": [ ... ]}
```

- `escape_password_hash` 和 `default_credentials` 没设置时，值为 `null`。
- `default_credentials.password` 和摄像头的 `password` 一样：存储时加密，下发时解密。客户端只放在内存里，**不写钥匙串**。
- `PUT /config` 在 `mode=managed` 时回 409，错误码 `MANAGED_PROFILE`。客户端收到后停止上传，并立即拉一次配置，借此发现模式已经变了。
- 0.9.0 之前的老客户端不认识这些新字段，会直接忽略（已核对 `cloud_state.py` 只读取它认识的键）。

### 6.2 截图上传

```
POST /api/camera-monitor/snapshots      (auth.camera_monitor)
multipart: kind=camera|screen, ip=<kind=camera 时必填>, image=<jpeg>
```

**校验**

- `image` 必须是真正的 JPEG：检查文件头魔数，并且 `getimagesize` 能读出来；
- 大小上限：`camera` 300 KB，`screen` 500 KB；
- `kind=camera` 时，`ip` 必须属于本设备码：要么在 `camera_monitor_camera` 里，要么在 `camera_monitor_discovered` 里。

**存储与限流**

- 存放位置：`storage/app/camera-monitor/{profile_id}/camera-{ip}.jpg` 或 `screen.jpg`，直接覆盖旧文件，同时更新 `camera_monitor_snapshot` 表里的时间。
- 限流：每个设备码每分钟最多 120 次。

### 6.3 客户端版本门槛

- `camera_monitor_client.app_version` 已经有了。
- 切换为 managed 时，检查绑定的那台客户端的 `app_version` 是否 ≥ `0.9.0`。不满足就拒绝切换，并提示"请先把现场软件升级到 0.9.0 或以上"。
- 还没绑定任何电脑的设备码允许直接切换，因为第一次登录的必然是新版本客户端。

## 7. 数据库变更（yunqi）

新建迁移，命名和写法沿用 `2026_09_22_*`：单数表名、每列写 `comment`、用 `hasTable` / `hasColumn` 做幂等保护、命名索引。线上按 `--path` 逐个执行。

| 表 | 变更 |
|---|---|
| `camera_monitor_profile` | 加列：`mode` `string(16)` 默认 `full`；`escape_password_hash` `string(160)` 可空；`default_username` `string(64)` 可空；`default_password_encrypted` `text` 可空 |
| `camera_monitor_client` | 加列：`runtime_status` `text` 可空；`runtime_status_at`；`channel_connected_at`；`channel_disconnected_at` |
| `camera_monitor_discovered`（新表） | `profile_id`、`ip`、`model`、`manufacturer`、`protocols`（JSON）、`onvif_urls`（JSON）、`discovered_at`；唯一键 `(profile_id, ip)` |
| `camera_monitor_snapshot`（新表） | `profile_id`、`kind`、`ip`（可空）、`path`、`captured_at`；唯一键 `(profile_id, kind, ip)` |
| `camera_monitor_command`（新表） | `uuid`、`profile_id`、`admin_user_id`、`name`、`args`（JSON）、`delivered`、`acked_at`、`finished_at`、`ok`、`error`、`created_at`；索引 `(profile_id, created_at)` |
| `camera_monitor_action_log`（新表） | `profile_id`、`admin_user_id`、`action`（如 `save_config`、`set_fullscreen`、`switch_mode`、`set_escape_password`、`set_default_credentials`、`command:scan`）、`summary`（JSON，**不含任何密码**）、`created_at` |

`layout.fullscreen` 放在现有的 `layout` JSON 里，不新增列。`CameraMonitorPayload` 的白名单里加上它，类型为 bool，默认 false。

**为什么用 `camera_monitor_command` 存命令**：后台页面需要显示"已送达 / 执行中 / 完成 / 失败"，而 WebSocket 进程和 PHP-FPM 是两个不同的进程，需要一个双方都能读写的地方。这张表**不是**用来做离线排队的（见 §3.1）：发送时如果设备离线，这条记录直接标记为未送达，不会再补发。

## 8. 后台页面（Dcat）

### 8.1 设备码列表

在现有列表的基础上：

- 新增"模式"列（标签：傻瓜模式 / 完整模式）；
- "现场电脑"列显示在线状态。判断规则：WebSocket 连接存在，或者 `last_seen_at` 在 2 分钟以内；
- "在播 / 摄像头"列，数据来自 `runtime_status`；
- 行操作：
  - managed 模式显示"远程控制"；
  - full 模式显示"切换为傻瓜模式"，点击后先过版本门槛，再弹确认框。
- 新建设备码的表单里加一项"模式"。

### 8.2 远程控制页

路由：`GET admin/camera-monitor-profiles/{id}/remote`。用 Blade 模板加原生 JS，写法参照 `resources/views/admin/pad/room-sort.blade.php`，拖拽用原生 HTML5 drag，不引入新的前端库。

页面上的接口都在 admin 路由组里，走 Dcat 的会话认证和 `camera-monitor-profile` 权限：

| 方法 / 路径（前缀 `admin/camera-monitor-profiles/{id}/remote`） | 作用 |
|---|---|
| `GET /state` | 页面每 2 秒轮询一次。返回：当前配置（不含密码，只有"是否已设置"标记）、`version`、在线状态、`runtime_status`、`config_version`（现场实际在用的版本）、发现的摄像头列表、截图时间、最近 10 条命令的状态 |
| `POST /config` | "保存并下发"。请求体为 `{version, layout, cameras}`，摄像头密码字段的规则和 v0.8 一样（不传表示不改，空串表示清空）。走 `ConfigService::apply`，成功后发 `config_changed`；版本冲突时回 409 并附上最新配置 |
| `POST /fullscreen` | `{value: bool}`，立即生效：直接改 `layout.fullscreen`，版本号 +1，然后发 `config_changed` |
| `POST /command` | `{name, args}`，写入 `camera_monitor_command` 并调用 `Channel::send`。返回 `{id, delivered}` |
| `POST /settings` | 默认账号密码、维护密码。维护密码在服务端用 `hash_pbkdf2('sha256', pw, salt, 240000, 32, true)` 算出和 `ScreenLock` 相同格式的记录；之后版本号 +1，发 `config_changed` |
| `POST /mode` | 切换 `full` 或 `managed`（受版本门槛约束），版本号 +1，发 `config_changed` |
| `GET /snapshot/{kind}/{ip?}` | 读截图文件，带权限校验，响应头 `Cache-Control: no-store` |

**页面上的交互**（界面细节以 Word 功能文档里的示意图为准）

- **顶部状态栏**：显示在线情况、`config_version` 和 `version` 是否一致（一致显示"已是最新配置"，不一致显示"正在更新"）、三个远程按钮。设备离线时按钮置灰。
- **进入页面**时，如果设备在线，自动发一个 `capture_screen`。
- **监控墙**：在页面里暂存改动，显示"有 N 处改动还没下发到现场"，点"保存并下发"后统一提交。
  - 拖到另一个画面上：两者互换；拖到空格子：移动过去；从"发现的摄像头"拖进来：加入；
  - 点击一个画面，打开右侧的设置抽屉。
- **全屏开关**：一改就调 `POST /fullscreen`，不进暂存区。
- **版本基线**：`/fullscreen`、`/settings`、`/mode` 都会让版本号 +1，接口返回新的 `version`。
  - 如果返回值正好等于页面当前的基线 +1，说明这期间没有别人改过，页面把基线更新为新版本，暂存的改动保留，之后照常可以保存。
  - 否则说明别人也改过，页面提示"配置已被他人修改"，并让用户重新加载。
- **"加入监控墙"**：放进第一个空格子，摄像头字段从 `camera_monitor_discovered` 带过来。
- **操作日志**：以上所有写操作和命令，都写进 `camera_monitor_action_log`。

### 8.3 对 v0.8 "后台只读"决策的修订

v0.8 的控制器注释里写着"配置以客户端为准，后台不做表单"。这条规则继续适用于 `full` 模式。`managed` 模式下写配置的一方换成了后台，见 §3.2。实现时把该注释改成两种模式分开说明。

## 9. 上线顺序

| 步骤 | 内容 | 对现有用户的影响 |
|---|---|---|
| ① 后端基础 | 第 7 节的迁移；快照新增字段；`MANAGED_PROFILE`；截图上传接口；抽出 `SessionVerifier` | 无（老客户端忽略新字段） |
| ② WebSocket 进程 | `camera-monitor:ws`、nginx、supervisor、`Channel::send`、在线状态 | 无 |
| ③ 客户端 0.9.0 | 第 4 节全部内容 | `full` 模式行为不变；新装的电脑会先看到欢迎页 |
| ④ 后台页面 | 第 8 节 | 功能对运营人员开放 |

- ③ 和 ①② 可以同时开发：客户端对接本地的假服务器。
- ④ 依赖 ①②。
- 正式启用 managed 模式的前提：③ 已经发布，并且现场客户端都升到了 0.9.0（由 §6.3 的版本门槛保证）。

## 10. 测试

### 10.1 客户端（pytest）

- 所有构造 `Window`、`ScreenLock`、`CloudSync` 的测试都**显式注入** QSettings。原因：在 macOS 上没法用 HOME 隔离 QSettings，不注入就会写进用户真实的配置。
- 钥匙串一律用假的 `CredentialStore` / `CloudSessionStore`。
- 用例：

| 用例 | 要点 |
|---|---|
| 启动分流 | 有会话 / 选过单机 / 老用户 / 全新电脑，四种情况分别进入正确的界面 |
| ManagedController | 用假 `Window` 记录调用：每个 `command` 调到正确的方法；锁定后鼠标、键盘事件被忽略；`fullscreen` 状态切换 |
| 逃生口 | 密码正确 / 错误；云端下发的记录写进 `ScreenLock` 后能验证；解除绑定后会话、启动项、缓存都被清掉，摄像头配置保留 |
| autostart | Windows 上把 `winreg` 换成假对象；macOS 上把 plist 写到临时目录；源码运行时什么都不做 |
| cloud_channel | 本地起一个 `websockets` 测试服务器（只作为测试依赖）：握手成功、`denied`、`replaced` 后不重连、退避序列（用假时钟）、心跳超时 |
| 状态回报 | `state_changed` 被合并、首次 `playing` 时自动截图、`config_version` 正确 |
| 打包 | `--packaging-smoke-test` 里加一项检查：能 `import PySide6.QtWebSockets` |

### 10.2 yunqi（PHPUnit 纯契约测试，继承 `PHPUnit\Framework\TestCase`）

本机跑不起 Laravel 5.8，所以服务类的依赖都从构造函数注入，测试时不需要容器。

| 用例 | 要点 |
|---|---|
| Payload | 新字段的白名单、`fullscreen` 默认值、`mode` 只能取两个值 |
| SessionVerifier | 四种失败码和一种成功，与原中间件的行为一致 |
| ChannelRouter | `hello` 超时、认证、替换旧连接、各类消息落库（仓储用假对象替代）、未知 `type` 被忽略、超大消息断开 |
| 维护密码 | PHP 生成的记录交给 Python 的 `ScreenLock.verify` 能通过。做法：用固定的盐算出一条测试向量，两边的测试都用它 |
| 版本门槛 | `0.8.3` 拒绝；`0.9.0`、`0.10.0` 通过 |
| 截图校验 | 不是 JPEG、超过大小、IP 不属于本设备码，都被拒绝 |

### 10.3 线上验收清单

- **②**：`wscat -c wss://…/api/camera-monitor/ws` 发出 `hello` 后能收到 `welcome`；进程被 kill 后 supervisor 会自动拉起。
- **③**：在 Windows 真机上验证开机启动；GitHub Actions 打出来的 exe 能完成一次完整的 managed 流程。
- **④**：按功能文档第六章的四个典型场景逐条走一遍。另外验证：断网、断电重启、逃生口、切回完整模式、后台解绑。

## 11. 本次不做

- 在后台看实时视频（只看截图）。
- 设备离线时把动作排队，等上线后执行。
- 一个设备码给多台电脑同时使用。
- 手机端远程控制。
- 批量把完整模式切换成傻瓜模式（功能文档里的待确认项，等确认需要后再做）。

## 12. 实现时的调整（客户端计划）

- **默认摄像头账号存进钥匙串**：原设计是"只放内存"，现在改为存到钥匙串账户 `__default__` 下；后台没有单独填账号的摄像头，也写入这组账号。这样断网重启后照样能播放。离线缓存只保留默认账号的用户名，不保留密码。
- **播放状态不新增信号**：没有给 `PlayerWindow` 加 `state_changed`，而是用 `player_state()` 解析现有的状态文字，`ManagedController` 每秒比对一次、有变化才上报。
- **不加 `force` 参数**：4.6 设想过给 `set_fill_width` / `set_grid_columns` 加 `force` 参数。实际上云端配置直接把值写进设置和 `_fill_width`，演示模式拦不到这条路，所以不需要。
- **`refresh_snapshots` 的 `command_done`**：表示截图请求都已经排进队列，不代表每张都已上传。后台以截图时间的变化为准。
- **启动分流**：`cloud/standalone` 是三态（没写过 / true / false），只在没写过时判断一次，判断完就写下结果。判断"本机用过旧版本"只看本程序自己的设置分组（`names` / `appearance` / `monitor`）和大屏密码 `fullscreen/password`，不看 `allKeys()` 是否为空，原因是 macOS 上 QSettings 会带出系统的全局键。大屏密码也算数：v0.6～v0.8 一启动就会写下初始密码，而这次判断只做一次、并且赶在本次进程的 `ScreenLock` 写入之前完成，所以能看到它就说明旧版本在这台电脑上运行过。
- **维护快捷键**：Windows 是 Ctrl+Shift+Alt+Q；macOS 是 Control+Shift+Option+Q。macOS 上不用 ⌘，因为 ⌘⇧⌥Q 是系统的「立即注销」。
- **下行通道不走系统代理**：和 HTTP 客户端保持一致；连接失败会写日志（同样的错误连续出现只写一次）。
- **被顶号不自动重连**：设备码被另一台电脑占用（`replaced`）后，本机不自动重连，静默重新登录也不会重新打开通道，要在本机重新手动登录。
- **HTTP 拒绝也会解绑**：托管电脑通过 HTTP 收到停用、失效、占用这三种拒绝时，和收到 `revoked` 一样，彻底解绑并回到欢迎页。
- **`revoked` 只在三种情况下发**：解绑电脑（包括删除占用者的监控屏记录）、停用档案、删除档案。重置授权码不发。
- **默认账号总是下发**：快照里总有 `default_credentials`，没设置时用户名和密码都是空串。
- **整屏截图的 `ip` 存空串**：不存 NULL，这样唯一键 `(profile_id, kind, ip)` 对整屏截图也能生效。
- **关机、注销时放行**：`main()` 里把 `commitDataRequest` 接到 `authorized_quit = True`。在 Qt 6.8.3 上实测过：Windows 的 `WM_QUERYENDSESSION`，以及 macOS 的 ⌘Q、程序坞「退出」、注销、关机（都走 `applicationShouldTerminate`），Qt 都会先发 `commitDataRequest`，再关所有窗口。所以 macOS 上分不清注销和 ⌘Q，一律放行；现场电脑都是 Windows，macOS 只是开发机。
- **远程搜索后的截图会试默认账号**：4.7 写的是不在墙上的摄像头"免密截一张"。实际做法是：这台摄像头本机没存过账号，就用点位的默认账号（钥匙串 `__default__`）去截图。原因是现场摄像头几乎都设了密码，免密截图基本拿不到画面，后台的人就没法靠预览认出是哪一台。风险：局域网里如果混进一台冒充摄像头的设备，它会收到默认摄像头密码。这个风险我们接受，因为不看预览就没法认摄像头；默认账号只用于摄像头，不是后台或电脑的密码。云端配置落下后，远程搜到但还没摆上墙的摄像头仍然记着，后台照样能要它们的截图。
- **完整模式收到 `revoked`**：只有托管电脑按 `failure_code` 彻底解绑。完整模式有侧边栏，所以只停掉下行通道，再走一次 HTTP 拉配置：如果真的被收回，服务端的原话会显示在侧边栏，后续处理和 HTTP 被拒时一样。
- **退出或解绑之前发出的请求作废**：`CloudSync` 维护一个会话代数，`logout()` 时加一。之前发出的请求（包括还没回来的静默重登）结果晚到时一律丢弃，免得把刚解绑的电脑又绑回去。退出后人手发起的登录用的是新代数，正常生效。
- **钥匙串暂时读不出会话**：启动时读会话遇到安全存储错误，不当作登录失效处理。照常铺开本机缓存，侧边栏按钮显示未连接；托管电脑保持锁定继续播放，不删开机自启，也不回欢迎页。
- **`restart_app` 先启动新进程**：先回 `ack`，同步调用 `QProcess.startDetached`（不阻塞）。启动成功才回 `command_done ok=true`，300 ms 后退出；启动失败就回 `command_done ok=false error=RESTART_FAILED`，当前进程继续运行。
- **日志文件**：`main()` 把日志写到 `QStandardPaths.AppDataLocation/logs/camera_monitor.log`，级别 INFO，每个文件 1 MB，保留 3 份旧文件。Windows 上是 `%APPDATA%\Camera Monitor\logs\`，macOS 上是 `~/Library/Application Support/Camera Monitor/logs/`。启动时记一行版本号，并注明是不是开机自启（`--autostart`）。目录或文件写不了时就不写文件，软件照常运行。
