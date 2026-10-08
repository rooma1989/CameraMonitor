# 完整模式：开机自动启动、启动后自动全屏

日期：2026-10-07　　关联：[傻瓜模式设计](2026-10-03-managed-mode-design.md)

## 1. 目标

完整模式（现场自己配置的那一版）加两个开关：

- **开机自动启动**：电脑开机登录后自动打开监控软件。
- **启动后自动全屏**：软件一打开就进入全屏（和按 F11 一样），退出全屏照旧要输大屏密码。

两个开关在本机工具栏上能勾选，也能在后台设备码编辑页修改，两边通过云端配置同步。

不在本次范围内：傻瓜模式的行为不变（开机启动一直开着，全屏由远程页「全屏显示」实时控制）。

## 2. 名词与存储

| 含义 | 本机设置键（DeviceNames 的 QSettings） | 云端 `layout` 字段 |
|---|---|---|
| 开机自动启动 | `monitor/autostart`（bool，缺省 false） | `autostart`（bool） |
| 启动后自动全屏 | `monitor/start_fullscreen`（bool，缺省 false） | `start_fullscreen`（bool） |

- 两项都放进现有的 `layout` 对象，和 `fill_width`、`organization` 走同一条同步路径（上传 `PUT /config`、下发 `config_changed`）。
- 不复用傻瓜模式的 `layout.fullscreen`：那个表示「现在就全屏」，由后台实时控制；`start_fullscreen` 表示「每次打开时全屏」，两者语义不同，混用会让模式来回切换时出错。
- `monitor/autostart` 记的是「想要」，系统启动项（Windows 注册表 Run 键、macOS LaunchAgent）是「实际」。界面勾选框显示实际状态（`AutoStart.is_enabled()`）。

## 3. 本机界面

监控墙工具栏「大屏密码」按钮旁边加两个勾选框：「开机自动启动」「启动后自动全屏」。工具栏在傻瓜模式下本来就不可见。

### 3.1 开机自动启动

- 勾上：写 `monitor/autostart=true`，调用 `AutoStart.enable()`。返回 False（源码运行、macOS 从 DMG/下载目录直接打开、写注册表失败）时，勾选框退回未勾选，状态栏提示原因（「开机自动启动没有设置成功：请先把程序拖进「应用程序」文件夹再打开」之类，按失败原因给），设置也写回 false。
- 去掉勾：写 false，调用 `AutoStart.disable()`。
- 启动时：完整模式下按 `monitor/autostart` 对齐一次系统启动项（想要但不在就补上，不想要但在就删掉）。用户手动删了启动项，下次打开软件就补回来，和傻瓜模式的做法一致。
- 勾选框的初始状态 = `AutoStart.is_enabled()`。
- 改动后通知云端同步（`note_cloud_change`）。

### 3.2 启动后自动全屏

- 勾选只记 `monitor/start_fullscreen`，当场不进全屏。
- 软件打开时：完整模式、设置为 true、墙上至少有一路摄像头 → 进入全屏（走 `toggle_fullscreen` 同一条路，记下进全屏前是最大化）。
- 判断时机：在启动时铺完墙之后（云端缓存回放、或 §5 的本机恢复都做完），用一次性定时器触发，只触发一次。云端配置晚到不再触发。
- 墙上一路都没有：不进全屏，避免一打开就是一块黑屏。
- 欢迎页盖着时不进全屏（还没选用法）。
- 退出全屏照旧要大屏密码；退出后不会被拉回去。
- 改动后通知云端同步。

## 4. 云端同步

### 4.1 客户端

- `layout_entry` 增加 `autostart`、`start_fullscreen` 两个参数，`collect_cloud_payload` 从本机设置读。指纹包含 layout，所以改勾选会触发上传。
- `apply_snapshot` / `AppliedConfig` 增加 `autostart`、`start_fullscreen`。`_save_layout` 写进本机设置。下发里没有这两个键时（旧后台）不动本机设置。
- `apply_cloud_config`：完整模式下更新两个勾选框（不触发上传），并按 `autostart` 对齐系统启动项。`start_fullscreen` 只写设置，下次打开软件生效。傻瓜模式下不处理这两项。

### 4.2 后台

- `CameraMonitorPayload::normalizeLayout` 增加两个 bool。
- 客户端上传（`ConfigController::update` → `ConfigService::apply`）：上传里带了这两个键就采用；没带（v0.8、v0.9.0 客户端）就沿用库里的值，做法同 `keepStoredFullscreen`。纯函数 `keepStoredStartup(array $layout, $storedLayout, array $rawUpload)`。
- 下发快照里总带这两个键。
- 后台设备码编辑页（`CameraMonitorProfileController::form`，仅编辑时）加两个开关「开机自动启动」「启动后自动全屏」，初始值取当前 layout。这两个不是数据库字段，`ignore` 掉。保存后在 `saved` 回调里调用 `CameraMonitorRemoteService::setStartup($profile, $autostart, $startFullscreen, $adminId)`：在行锁内改 layout，有变化才加版本号，再推 `config_changed`。注意 saving/saved 回调里 `$this` 不是控制器，只能用 `use` 捕获的变量。
- 编辑页帮助文字注明：傻瓜模式下开机启动始终开启，全屏在远程控制页设置，这两个开关不起作用。
- 详情页的布局卡片显示这两项的当前值。
- 操作日志记一条 `startup`（只在有变化时）。

## 5. 不登录云端的电脑：恢复上次的监控墙

问题：不登录云端（单机）的完整模式电脑，重启后墙是空的，要人手搜索、再一路路加回去。登录云端的电脑靠离线缓存恢复，没有这个问题。不解决的话，单机电脑开了这两个开关也只会得到一块空白全屏。

做法：复用云端那套配置格式，存一份本机快照。

- 单机模式下，墙布局或摄像头设置有变化时（同样挂在 `note_cloud_change` 的那几个信号上，节流 1 秒），用 `collect_cloud_payload()` 生成配置，`cacheable()` 剥掉密码，写进 DeviceNames 设置的 `local/snapshot`（JSON 串；`local/` 不在启动分流判断「用过」的分组里，不影响分流）。密码本来就在钥匙串里。
- 启动时：没登录云端（`cloud.enabled()` 为假）、欢迎页没盖着、且有 `local/snapshot` → 用 `apply_snapshot` + `apply_cloud_config` 回放，墙按上次的格子恢复并开始播放。然后才做 §3.2 的全屏判断。
- 回放时不改钥匙串（快照里没有 password 键，`apply_snapshot` 本来就不碰）。
- 登录云端后不再写本机快照，以云端为准；退出云端后从下一次墙变化开始重新写。
- 坏掉的 JSON 直接忽略并记日志。

## 6. 和傻瓜模式的关系

- 进入傻瓜模式：照旧 `AutoStart.enable()`，两个勾选框随工具栏隐藏。
- 离开傻瓜模式（后台切回完整模式、维护人员解绑、被收回）：不再一律 `disable()`，而是按 `monitor/autostart` 对齐。从没勾过的电脑结果和以前一样（删除启动项）。
- 傻瓜模式下收到的 `autostart` / `start_fullscreen` 只写进本机设置，切回完整模式后生效。

## 7. 出错处理

- 写启动项失败：勾选框退回、提示原因、设置写回 false、记日志。云端下发的 `autostart=true` 落不下去时同样记日志，并把本机设置写回 false，下一次同步把实际状态传上去，后台能看到没设上。
- 本机快照读写失败：忽略，记日志，不影响正常使用。

## 8. 测试

客户端（unittest，注入临时 QSettings 和假的 AutoStart，绝不碰真实启动项）：
- 勾选开机启动 → 设置为 true、调用 enable；enable 失败 → 勾选框退回、设置为 false。
- 启动对齐：设置 true 但没启动项 → 补上；设置 false 但有启动项 → 删掉。
- 自动全屏：设置 true 且墙上有摄像头 → 启动后进全屏；墙为空、欢迎页盖着、傻瓜模式下 → 不进；只触发一次。
- 云端下发两项 → 勾选框和设置更新、启动项对齐；不触发上传；傻瓜模式下不对齐启动项。
- 上传 payload 带上两项。
- 离开傻瓜模式按设置对齐启动项。
- 单机快照：墙变化后写入、不含密码；重启回放后格子和顺序一致；登录云端时不写；坏 JSON 忽略。

后台（纯类 PHPUnit，逐个文件跑，PHP 8.3 和 7.4）：
- `normalizeLayout` 两个新字段；`keepStoredStartup` 带键采用、不带键沿用。
- `setStartup` 的变化判断（如能抽成纯函数）。

## 9. 版本

客户端 0.9.0 尚未发布，直接并进 0.9.0。

## 10. 实现时的调整

### 客户端

- **失败原因**：`AutoStart.enable()` 失败时把原因记在 `last_error`，有四种：源码运行、程序在临时位置（DMG、下载目录里直接打开）、写入失败、系统不支持。提示文字由 `autostart.failure_message()` 生成，显示在侧边栏的状态文字里，因为窗口没有单独的状态栏。
- **对齐不先看 `is_enabled()`**：`autostart.reconcile()` 想要就直接 `enable()`，不想要就直接 `disable()`，两个都可以重复调用。原因是程序挪过位置后，旧启动项还指着原来的路径，`is_enabled()` 会说没有；如果先看它，这条旧启动项就一直删不掉。删除失败只记日志，不提示，否则从源码运行时每次启动都会弹一句。
- **任务管理器里「禁用」过**：Windows 任务管理器「启动应用」里点禁用，不删 Run 键里的值，而是在 `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run` 写一个同名 REG_BINARY（12 字节，第一个字节 0x02、0x06 是启用，0x03、0x07 是禁用，后 8 字节是改动时间）。`is_enabled()` 现在把第一个字节是奇数的当成没启用，勾选框如实显示没勾；`enable()` 写完 Run 键再删掉这条记录（键、值不在都不算错），勾上才真的开机启动，删不掉算写入失败。注意：设置里还是「想要」时，下次打开软件对齐会把任务管理器的禁用撤掉，和手动删了启动项会被补回来一样；要关就去掉勾。
- **启动时对齐的时机**：在 `start_cloud` 里做，顺序是云端缓存回放 → 本机快照回放 → 对齐启动项 → 排上自动全屏的定时器。欢迎页盖着时也照样对齐（这时设置一般是 false，就是删掉启动项）。
- **自动全屏退出后回到最大化**：启动时窗口是默认大小，`toggle_fullscreen` 记下的是「没最大化」。自动全屏后把 `was_maximized` 改成 True，输完大屏密码退出后监控墙仍铺满屏幕。
- **云端下发的 `autostart=true` 落不下去**：本机设置改回 false，然后调用 `CloudSync.push_later()` 记下待补传，并清掉 `last_pushed`。不能当场上传，因为这时正在落云端配置，`schedule_push` 会被挡掉；不清 `last_pushed` 的话，本机这份可能正好和上次传的一样，会被当成重复跳过。实际状态在下一次心跳时传上去。
- **落下云端配置后换去重基准**：`CloudSync` 用 `last_pushed` 判断「这次要传的和云端那份一样」就不传。以前只在上传成功时更新，落下云端配置（拉取、`config_changed`、首次登录的下发方向、版本冲突退回）之后还是旧的：后台把开机启动打开、下发到本机，现场再去掉勾，本机这份正好和上次传的一样，被当成重复跳过，后台一直显示开着。现在每次 `_apply` 之后都用 `collector()` 把本机重取一遍，记成新的基准。不直接用下发的原文算：它比本机上传时多了 `fullscreen` 之类的键，那样每收一次下发都会多传一次。落配置时调过 `push_later()`（本机和云端对不上）就把基准清空，保证补传不被跳过。
- **首次登录时两个开关「开着的一边说了算」**：新后台下发里总带着这两个键，缺省 false，那个 false 往往谁也没选过。按 §4.1 原样落下的话，本机勾好的开机启动在首次登录下发方向时会被删掉。现在每个键各自取「或」（`cloud_state.first_login_switches`）：
  - 下发方向：本机开着、云端关着的，照本机留着（设置和启动项都不动），落完配置调 `push_later()` 并排一次上传，把这一边传上去。
  - 上传方向：云端开着、本机关着的，先写进本机设置、对齐启动项（`startup_switches_changed` 信号），再传，传上去的就是两边取「或」。
  - 只在人手登录时合并。静默重登（令牌过期）时本机一直跟着云端，两边不一样只能是后台刚改过、本机还没拉到，以云端为准。托管点位也以云端为准，不合并。下发里没有这两个键（旧后台）时不合并。
- **本机快照**：
  - 生成时不读钥匙串（`collect_cloud_payload(credentials=False)`），用户名也是空的。回放时快照里没有 password 键，`apply_snapshot` 不碰钥匙串。
  - 回放时不回放 `autostart`、`start_fullscreen`，以本机设置为准。快照最多晚一秒，刚改完勾选就关软件的话，回放旧值会把勾选改回去。
  - 关软件时如果还有一次没写，当场写掉。
  - 退出云端时马上写一次，不等下一次墙变化。原因是退出时云端离线缓存会被清掉，不写的话重启后墙是空的。
  - 「没登录云端」在 `cloud.start()` 之前判断。设置说登录了、钥匙串里却没有会话时，`cloud.start()` 会把登录改成 false；这种电脑的本机快照是登录之前存的，已经过时，不回放。
  - 欢迎页上选「单机使用」时也回放：从欢迎页登录过的电脑（`cloud/standalone=false`）在侧边栏退出云端后重启，会先看到欢迎页，启动时不回放；选了单机后墙是空的就回放本机快照，墙上已有画面（同一次运行里刚解绑回来）就不动。这里不做自动全屏判断，那只在打开软件时做。
  - 快照格式对、内容不对（手改坏了，包括 `layout` 不是对象），解析和回放时出的任何异常都只记日志，墙空着，软件照常用；启动项对齐和自动全屏的定时器照样往下走。
- **关机、注销时不弹大屏密码**：勾了「启动后自动全屏」的电脑一直全屏，Windows 关机或注销时系统要关掉所有窗口，`closeEvent` 以前会弹大屏密码框，屏幕前没人输，关机就卡住。现在和傻瓜模式一样认 `authorized_quit`（`allow_session_quit` 在 `commitDataRequest` 里设上）：放行时直接关，不弹密码。人手关窗口（Alt+F4、点关闭）照旧要密码。macOS 上 ⌘Q、程序坞「退出」也走 `commitDataRequest`，分不清是不是注销，同样不弹密码就退出；现场电脑都是 Windows，macOS 只是开发机，见[傻瓜模式设计](2026-10-03-managed-mode-design.md) §12。
- **打包冒烟测试隔离**：`packaging/launcher.py --packaging-smoke-test`（`build-windows.bat`、macOS 打包脚本在打好的程序上跑）以前直接构造真窗口，会按打包机上的设置删掉或补上开机启动项，读写真实设置。现在由 `smoke_window(folder)` 建窗口：设置全放进临时目录的 ini，启动项换成什么都不做的 `NoAutoStart`。原有的 TLS、WebSocket、JPEG、图标检查不变。
- **测试**：`tests/support.make_window` 默认注入 `FakeAutoStart`。直接构造 `Window` 的几个旧用例也改成传假的 AutoStart，因为窗口一打开就会用 `is_enabled()` 去读本机的启动项。
