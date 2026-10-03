# 傻瓜模式 · yunqi 后台远程控制页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 运营人员在后台把设备码切成傻瓜模式，并在"远程控制"页完成以下操作：远程搜索摄像头、加入监控墙、拖动排序、填账号密码、开关全屏、截屏、重连、重启、修改维护密码。

**Architecture:**
- **纯类**（`CameraMonitorRemoteState`、`CameraMonitorRemoteCommands`）：负责把数据整理成页面要的样子、校验命令，用 PHPUnit 测。
- **`CameraMonitorRemoteService`**：负责读写数据库、递增版本号、推送消息、写操作日志。
- **控制器**：只做权限判断和 JSON 收发。
- **页面**：一个 Blade 模板加原生 JS，每 2 秒轮询 `/state`。拖拽用原生 HTML5 drag，写法参照 `resources/views/admin/pad/room-sort.blade.php`。

**Tech Stack:** Laravel 5.8、Dcat Admin、原生 JS（不引入新的前端库）。

**Spec:** CameraMonitor 仓库的 `docs/superpowers/specs/2026-10-03-managed-mode-design.md` §8；界面示意见 Word 功能文档第五章。

**前置：** 计划 A（`2026-10-03-managed-mode-backend.md`）已经在同一个分支 `feat/camera-monitor-managed-mode` 上完成。本计划接着在 `/Users/rooma/Projects/云栖/yunqi-managed-mode` 里做。

---

## 约定

与计划 A 相同：
- 只有纯类写 PHPUnit 测试；
- 其余代码用 `php -l` 做语法检查，上线后按清单验收；
- "跑全部 CameraMonitor 测试"指的是：

  ```bash
  for f in tests/Unit/CameraMonitor*Test.php; do out=$(php vendor/bin/phpunit "$f" 2>&1) || { echo "$out"; echo "FAILED: $f"; break; }; done && echo ALL-OK
  ```

提交信息结尾加：
```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/Services/CameraMonitor/CameraMonitorRemoteCommands.php` | 新 | 校验动作名和参数，提供中文名 |
| `app/Services/CameraMonitor/CameraMonitorRemoteState.php` | 新 | 判断在线、整理摄像头和命令的显示状态、判断是否正在搜索 |
| `app/Services/CameraMonitor/CameraMonitorRemoteService.php` | 新 | 读写、版本号、推送、操作日志 |
| `app/Admin/Controllers/CameraMonitor/ScopesInstitutions.php` | 新 | 机构权限判断（从 ProfileController 挪出来） |
| `app/Admin/Controllers/CameraMonitor/CameraMonitorRemoteController.php` | 新 | 页面与 JSON 接口 |
| `resources/views/admin/camera-monitor/remote.blade.php` | 新 | 远程控制页 |
| `app/Admin/RowActions/CameraMonitor/SwitchToManaged.php` | 新 | 列表行操作"切换为傻瓜模式" |
| `app/Admin/Controllers/CameraMonitor/CameraMonitorProfileController.php` | 改 | 模式列、行操作、表单、详情页入口、类注释 |
| `app/Admin/routes.php` | 改 | 远程控制的路由 |

---

### Task 1: 命令校验（纯类）

**Files:**
- Create: `app/Services/CameraMonitor/CameraMonitorRemoteCommands.php`
- Test: `tests/Unit/CameraMonitorRemoteCommandsTest.php`

- [ ] **Step 1: 写失败的测试**

```php
<?php

namespace Tests\Unit;

use App\Services\CameraMonitor\CameraMonitorRemoteCommands;
use InvalidArgumentException;
use PHPUnit\Framework\TestCase;

class CameraMonitorRemoteCommandsTest extends TestCase
{
    public function testPlainCommandsCarryNoArguments()
    {
        foreach (['capture_screen', 'reconnect_all', 'restart_app'] as $name) {
            $this->assertSame([$name, []], CameraMonitorRemoteCommands::normalize($name, ['junk' => 1]));
        }
    }

    public function testAScanMayTargetOneIp()
    {
        $this->assertSame(['scan', []], CameraMonitorRemoteCommands::normalize('scan', null));
        $this->assertSame(['scan', ['target_ip' => '192.168.2.216']],
            CameraMonitorRemoteCommands::normalize('scan', ['target_ip' => ' 192.168.2.216 ']));
    }

    public function testABadScanTargetIsRefused()
    {
        $this->expectException(InvalidArgumentException::class);
        $this->expectExceptionMessage('IP 地址格式不正确');
        CameraMonitorRemoteCommands::normalize('scan', ['target_ip' => '192.168.2.999']);
    }

    public function testSnapshotRefreshKeepsOnlyValidIps()
    {
        $this->assertSame(['refresh_snapshots', []], CameraMonitorRemoteCommands::normalize('refresh_snapshots', []));
        $this->assertSame(['refresh_snapshots', ['ips' => ['10.0.0.1', '10.0.0.2']]],
            CameraMonitorRemoteCommands::normalize('refresh_snapshots', ['ips' => ['10.0.0.1', 'x', '10.0.0.2', '10.0.0.1']]));
    }

    public function testUnknownCommandsAreRefused()
    {
        $this->expectException(InvalidArgumentException::class);
        CameraMonitorRemoteCommands::normalize('format_disk', []);
    }

    public function testLabels()
    {
        $this->assertSame('搜索摄像头', CameraMonitorRemoteCommands::label('scan'));
        $this->assertSame('截取大屏画面', CameraMonitorRemoteCommands::label('capture_screen'));
        $this->assertSame('未知操作', CameraMonitorRemoteCommands::label('???'));
    }
}
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorRemoteCommandsTest.php`
Expected: ERROR class not found

- [ ] **Step 3: 实现**

```php
<?php

namespace App\Services\CameraMonitor;

use InvalidArgumentException;

/**
 * 后台能下发给现场电脑的一次性动作。名称和参数在这里收口，客户端只认这几个。
 */
class CameraMonitorRemoteCommands
{
    public const LABELS = [
        'scan' => '搜索摄像头',
        'refresh_snapshots' => '刷新截图',
        'capture_screen' => '截取大屏画面',
        'reconnect_all' => '全部重连',
        'restart_app' => '重启软件',
    ];

    private const MAX_REFRESH_IPS = 64;

    /**
     * @return array [string $name, array $args]
     * @throws InvalidArgumentException
     */
    public static function normalize(string $name, $args): array
    {
        if (!array_key_exists($name, self::LABELS)) {
            throw new InvalidArgumentException('不支持的操作');
        }
        $args = is_array($args) ? $args : [];

        if ($name === 'scan') {
            $target = trim((string) ($args['target_ip'] ?? ''));
            if ($target === '') {
                return [$name, []];
            }
            $ip = CameraMonitorPayload::ip($target);
            if ($ip === null) {
                throw new InvalidArgumentException('IP 地址格式不正确');
            }

            return [$name, ['target_ip' => $ip]];
        }

        if ($name === 'refresh_snapshots') {
            if (!isset($args['ips'])) {
                return [$name, []];
            }
            $ips = [];
            foreach ((array) $args['ips'] as $value) {
                $ip = CameraMonitorPayload::ip($value);
                if ($ip !== null && !in_array($ip, $ips, true)) {
                    $ips[] = $ip;
                }
            }

            return [$name, ['ips' => array_slice($ips, 0, self::MAX_REFRESH_IPS)]];
        }

        return [$name, []];
    }

    public static function label(string $name): string
    {
        return self::LABELS[$name] ?? '未知操作';
    }
}
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorRemoteCommandsTest.php`
Expected: `OK (6 tests, ...)`

- [ ] **Step 5: 提交**

```bash
git add app/Services/CameraMonitor/CameraMonitorRemoteCommands.php tests/Unit/CameraMonitorRemoteCommandsTest.php
git commit -m "feat(camera-monitor): 远程动作的名称与参数校验

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 页面状态整理（纯类）

**Files:**
- Create: `app/Services/CameraMonitor/CameraMonitorRemoteState.php`
- Test: `tests/Unit/CameraMonitorRemoteStateTest.php`

- [ ] **Step 1: 写失败的测试**

```php
<?php

namespace Tests\Unit;

use App\Services\CameraMonitor\CameraMonitorRemoteState;
use PHPUnit\Framework\TestCase;

class CameraMonitorRemoteStateTest extends TestCase
{
    public function testOnlineFollowsTheChannelFirstThenRecentPolling()
    {
        $this->assertTrue(CameraMonitorRemoteState::online(900, null, null, 1000), '通道连着');
        $this->assertTrue(CameraMonitorRemoteState::online(950, 900, null, 1000), '断过又连上了');
        $this->assertFalse(CameraMonitorRemoteState::online(900, 950, 700, 1000), '通道断了，轮询也很久没来');
        $this->assertTrue(CameraMonitorRemoteState::online(900, 950, 950, 1000), '通道断了但 2 分钟内轮询过');
        $this->assertFalse(CameraMonitorRemoteState::online(null, null, null, 1000));
    }

    public function testCamerasNeverCarryTheirPassword()
    {
        $camera = CameraMonitorRemoteState::camera(
            ['ip' => '10.0.0.1', 'username' => 'admin', 'password' => 'secret'],
            ['10.0.0.1' => ['ip' => '10.0.0.1', 'state' => 'auth_failed']],
            ['camera:10.0.0.1' => '2026-10-03 10:00:00']
        );

        $this->assertArrayNotHasKey('password', $camera);
        $this->assertTrue($camera['password_set']);
        $this->assertSame('auth_failed', $camera['state']);
        $this->assertSame('2026-10-03 10:00:00', $camera['snapshot_at']);

        $bare = CameraMonitorRemoteState::camera(['ip' => '10.0.0.2', 'password' => ''], [], []);
        $this->assertFalse($bare['password_set']);
        $this->assertNull($bare['state']);
        $this->assertNull($bare['snapshot_at']);
    }

    public function testRuntimeIsIndexedByIp()
    {
        $this->assertSame(
            ['10.0.0.1' => ['ip' => '10.0.0.1', 'state' => 'playing']],
            CameraMonitorRemoteState::runtimeByIp(['cameras' => [['ip' => '10.0.0.1', 'state' => 'playing'], 'junk']])
        );
        $this->assertSame([], CameraMonitorRemoteState::runtimeByIp(null));
    }

    public function testCommandStatus()
    {
        $this->assertSame(['offline', '设备离线，未送达'], CameraMonitorRemoteState::commandStatus(false, null, null, null, null));
        $this->assertSame(['sent', '已送达'], CameraMonitorRemoteState::commandStatus(true, null, null, null, null));
        $this->assertSame(['running', '执行中'], CameraMonitorRemoteState::commandStatus(true, '10:00', null, null, null));
        $this->assertSame(['done', '完成'], CameraMonitorRemoteState::commandStatus(true, '10:00', '10:01', true, null));
        $this->assertSame(['failed', '失败：上一次搜索还没结束'],
            CameraMonitorRemoteState::commandStatus(true, '10:00', '10:00', false, 'BUSY'));
        $this->assertSame(['failed', '失败：XYZ'], CameraMonitorRemoteState::commandStatus(true, '10:00', '10:00', false, 'XYZ'));
    }

    public function testScanningMeansARecentUnfinishedScan()
    {
        $scan = ['name' => 'scan', 'status' => 'running', 'created_ts' => 990];
        $this->assertTrue(CameraMonitorRemoteState::scanning([$scan], 1000));
        $this->assertFalse(CameraMonitorRemoteState::scanning([$scan], 1031), '超过 30 秒不再显示搜索中');
        $this->assertFalse(CameraMonitorRemoteState::scanning([array_merge($scan, ['status' => 'done'])], 1000));
        $this->assertFalse(CameraMonitorRemoteState::scanning([array_merge($scan, ['name' => 'reconnect_all'])], 1000));
    }
}
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorRemoteStateTest.php`
Expected: ERROR class not found

- [ ] **Step 3: 实现**

```php
<?php

namespace App\Services\CameraMonitor;

/**
 * 远程控制页要显示的东西，从数据库原始值整理出来。纯函数，便于测试。
 */
class CameraMonitorRemoteState
{
    /** 通道断开后，2 分钟内还有轮询请求就仍算在线 */
    public const ONLINE_GRACE_SECONDS = 120;

    /** 搜索大约 10 秒；超过 30 秒还没结果就不再显示「搜索中」 */
    public const SCAN_DISPLAY_SECONDS = 30;

    private const ERRORS = [
        'BUSY' => '上一次搜索还没结束',
        'NOT_MANAGED' => '现场还没进入傻瓜模式',
        'UNKNOWN_COMMAND' => '现场软件版本不支持这个操作',
        'BAD_TARGET' => 'IP 地址不正确',
        'CAPTURE_FAILED' => '截图失败',
    ];

    public static function online(?int $connectedAt, ?int $disconnectedAt, ?int $lastSeenAt, int $now): bool
    {
        if ($connectedAt !== null && ($disconnectedAt === null || $connectedAt > $disconnectedAt)) {
            return true;
        }

        return $lastSeenAt !== null && $now - $lastSeenAt <= self::ONLINE_GRACE_SECONDS;
    }

    public static function runtimeByIp($runtime): array
    {
        $byIp = [];
        foreach ((array) (is_array($runtime) ? ($runtime['cameras'] ?? []) : []) as $camera) {
            if (is_array($camera) && isset($camera['ip'])) {
                $byIp[(string) $camera['ip']] = $camera;
            }
        }

        return $byIp;
    }

    /** 页面上的摄像头：去掉密码，只留「是否已设置」 */
    public static function camera(array $camera, array $runtimeByIp, array $snapshotTimes): array
    {
        $passwordSet = ((string) ($camera['password'] ?? '')) !== '';
        unset($camera['password']);
        $ip = (string) ($camera['ip'] ?? '');
        $camera['password_set'] = $passwordSet;
        $camera['state'] = $runtimeByIp[$ip]['state'] ?? null;
        $camera['snapshot_at'] = $snapshotTimes['camera:' . $ip] ?? null;

        return $camera;
    }

    /**
     * @return array [string $status, string $label]
     */
    public static function commandStatus(bool $delivered, ?string $ackedAt, ?string $finishedAt, ?bool $ok, ?string $error): array
    {
        if (!$delivered) {
            return ['offline', '设备离线，未送达'];
        }
        if ($finishedAt !== null) {
            if ($ok) {
                return ['done', '完成'];
            }

            return ['failed', '失败：' . (self::ERRORS[(string) $error] ?? (string) $error)];
        }
        if ($ackedAt !== null) {
            return ['running', '执行中'];
        }

        return ['sent', '已送达'];
    }

    /**
     * @param array $commands 每项含 name、status、created_ts，按时间倒序
     */
    public static function scanning(array $commands, int $now): bool
    {
        foreach ($commands as $command) {
            if (($command['name'] ?? '') !== 'scan') {
                continue;
            }

            return in_array($command['status'] ?? '', ['sent', 'running'], true)
                && $now - (int) ($command['created_ts'] ?? 0) <= self::SCAN_DISPLAY_SECONDS;
        }

        return false;
    }
}
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorRemoteStateTest.php`
Expected: `OK (5 tests, ...)`

- [ ] **Step 5: 提交**

```bash
git add app/Services/CameraMonitor/CameraMonitorRemoteState.php tests/Unit/CameraMonitorRemoteStateTest.php
git commit -m "feat(camera-monitor): 远程控制页的状态整理

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 远程控制服务

**Files:**
- Create: `app/Services/CameraMonitor/CameraMonitorRemoteService.php`

全是数据库读写，本机测不了。它依赖的判断都已在 Task 1、Task 2 和计划 A 的纯类里测过。

- [ ] **Step 1: 先核对两个依赖的行为**

```bash
sed -n 31,60p app/Services/CameraMonitor/CameraMonitorSecretService.php
```

确认 `encrypt('')` 和 `encrypt(null)` 返回什么。下面的代码假设"空串表示清空、存 null"，**不依赖** `encrypt('')` 的返回值。

- [ ] **Step 2: 实现**

```php
<?php

namespace App\Services\CameraMonitor;

use App\Models\CameraMonitor\CameraMonitorActionLog;
use App\Models\CameraMonitor\CameraMonitorClient;
use App\Models\CameraMonitor\CameraMonitorCommand;
use App\Models\CameraMonitor\CameraMonitorDiscovered;
use App\Models\CameraMonitor\CameraMonitorProfile;
use App\Models\CameraMonitor\CameraMonitorSnapshot;
use App\Services\CameraMonitor\Channel\CameraMonitorChannel;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;
use InvalidArgumentException;
use RuntimeException;

/**
 * 后台远程控制托管点位。所有写操作都会：递增版本号 → 通知现场 → 写操作日志。
 *
 * 推送失败不报错：现场电脑离线时，配置照样会被它下次的轮询带走。
 */
class CameraMonitorRemoteService
{
    /** @var CameraMonitorConfigService */
    private $config;

    /** @var CameraMonitorSecretService */
    private $secrets;

    /** @var CameraMonitorChannel */
    private $channel;

    public function __construct(CameraMonitorConfigService $config, CameraMonitorSecretService $secrets, CameraMonitorChannel $channel)
    {
        $this->config = $config;
        $this->secrets = $secrets;
        $this->channel = $channel;
    }

    // ==================== 读 ====================

    public function state(CameraMonitorProfile $profile): array
    {
        $profile = $profile->fresh() ?: $profile;
        $snapshot = $this->config->snapshot($profile);
        $client = $this->boundClient($profile);
        $runtime = $client && is_array($client->runtime_status) ? $client->runtime_status : [];
        $byIp = CameraMonitorRemoteState::runtimeByIp($runtime);
        $now = time();

        $times = [];
        foreach (CameraMonitorSnapshot::query()->where('profile_id', $profile->id)->get() as $row) {
            $times[$row->kind . ':' . $row->ip] = $row->captured_at ? $row->captured_at->toDateTimeString() : null;
        }

        $cameras = array_map(function (array $camera) use ($byIp, $times) {
            return CameraMonitorRemoteState::camera($camera, $byIp, $times);
        }, $snapshot['cameras']);
        $slots = [];
        foreach ($cameras as $camera) {
            $slots[$camera['ip']] = (int) $camera['slot_index'];
        }

        $discovered = CameraMonitorDiscovered::query()
            ->where('profile_id', $profile->id)
            ->orderByRaw('INET_ATON(ip)')
            ->get()
            ->map(function (CameraMonitorDiscovered $row) use ($slots, $times) {
                return [
                    'ip' => (string) $row->ip,
                    'model' => (string) $row->model,
                    'manufacturer' => (string) $row->manufacturer,
                    'protocols' => (array) $row->protocols,
                    'onvif_urls' => (array) $row->onvif_urls,
                    'slot_index' => $slots[$row->ip] ?? null,
                    'snapshot_at' => $times['camera:' . $row->ip] ?? null,
                ];
            })->values()->all();

        $commands = CameraMonitorCommand::query()
            ->where('profile_id', $profile->id)
            ->orderByDesc('id')
            ->limit(10)
            ->get()
            ->map(function (CameraMonitorCommand $row) {
                [$status, $label] = CameraMonitorRemoteState::commandStatus(
                    (bool) $row->delivered,
                    $row->acked_at ? $row->acked_at->toDateTimeString() : null,
                    $row->finished_at ? $row->finished_at->toDateTimeString() : null,
                    $row->ok === null ? null : (bool) $row->ok,
                    $row->error
                );

                return [
                    'id' => (string) $row->uuid,
                    'name' => (string) $row->name,
                    'label' => CameraMonitorRemoteCommands::label((string) $row->name),
                    'status' => $status,
                    'status_label' => $label,
                    'created_at' => $row->created_at ? $row->created_at->toDateTimeString() : '',
                    'created_ts' => $row->created_at ? $row->created_at->getTimestamp() : 0,
                ];
            })->values()->all();

        return [
            'version' => (int) $snapshot['version'],
            'mode' => $snapshot['mode'],
            'online' => $client ? CameraMonitorRemoteState::online(
                self::ts($client->channel_connected_at),
                self::ts($client->channel_disconnected_at),
                self::ts($client->last_seen_at),
                $now
            ) : false,
            'device_name' => (string) $profile->bound_device_name,
            'app_version' => $client ? (string) $client->app_version : '',
            'last_seen' => $client && $client->last_seen_at ? $client->last_seen_at->toDateTimeString() : null,
            'applied_version' => isset($runtime['config_version']) ? (int) $runtime['config_version'] : null,
            'layout' => $snapshot['layout'],
            'cameras' => $cameras,
            'discovered' => $discovered,
            'screen_at' => $times['screen:'] ?? null,
            'default_username' => (string) $profile->default_username,
            'default_password_set' => trim((string) $profile->default_password_encrypted) !== '',
            'escape_password_set' => CameraMonitorManaged::validEscapeRecord($profile->escape_password_hash),
            'commands' => $commands,
            'scanning' => CameraMonitorRemoteState::scanning($commands, $now),
        ];
    }

    // ==================== 写 ====================

    /**
     * 「保存并下发」。全屏开关不在这里改：沿用库里的当前值，免得一次保存把它改回旧状态。
     *
     * @return array ConfigService::apply 的返回值（status 为 ok 或 conflict）
     */
    public function saveConfig(CameraMonitorProfile $profile, int $version, $layout, $cameras, ?int $adminId): array
    {
        $layout = is_array($layout) ? $layout : [];
        $current = CameraMonitorPayload::normalizeLayout(optional($profile->fresh())->layout);
        $layout['fullscreen'] = $current['fullscreen'];

        $result = $this->config->apply($profile, $version, $layout, $cameras);
        if ($result['status'] === 'ok') {
            $this->channel->configChanged((int) $profile->id, (int) $result['version']);
            $this->log($profile, $adminId, 'save_config', [
                'version' => (int) $result['version'],
                'cameras' => count($result['snapshot']['cameras']),
            ]);
        }

        return $result;
    }

    public function setFullscreen(CameraMonitorProfile $profile, bool $value, ?int $adminId): int
    {
        $version = $this->bump($profile, function (CameraMonitorProfile $locked) use ($value) {
            $layout = CameraMonitorPayload::normalizeLayout($locked->layout);
            if ($layout['fullscreen'] === $value) {
                return false;
            }
            $layout['fullscreen'] = $value;
            $locked->layout = $layout;

            return true;
        });
        $this->log($profile, $adminId, 'set_fullscreen', ['value' => $value, 'version' => $version]);

        return $version;
    }

    /**
     * @param array $input default_username；default_password（不传或 null 表示不改，空串表示清空）；
     *                     escape_password（非空才改）
     * @throws InvalidArgumentException 维护密码不合规
     */
    public function saveSettings(CameraMonitorProfile $profile, array $input, ?int $adminId): int
    {
        $username = array_key_exists('default_username', $input)
            ? mb_substr(trim((string) $input['default_username']), 0, 64)
            : (string) $profile->default_username;
        $password = array_key_exists('default_password', $input) && $input['default_password'] !== null
            ? (string) $input['default_password']
            : null;
        $escape = trim((string) ($input['escape_password'] ?? ''));
        $record = $escape !== '' ? CameraMonitorManaged::escapeRecord($escape) : null;
        $encrypted = ($password !== null && $password !== '') ? $this->secrets->encrypt($password) : null;

        $version = $this->bump($profile, function (CameraMonitorProfile $locked) use ($username, $password, $encrypted, $record) {
            $locked->default_username = $username;
            if ($password !== null) {
                $locked->default_password_encrypted = $encrypted;
            }
            if ($record !== null) {
                $locked->escape_password_hash = $record;
            }

            return true;
        });

        if ($password !== null || array_key_exists('default_username', $input)) {
            $this->log($profile, $adminId, 'set_default_credentials', [
                'default_username' => $username,
                'password_changed' => $password !== null,
            ]);
        }
        if ($record !== null) {
            $this->log($profile, $adminId, 'set_escape_password', []);
        }

        return $version;
    }

    /**
     * @throws InvalidArgumentException 现场软件版本过低
     */
    public function switchMode(CameraMonitorProfile $profile, string $mode, ?int $adminId): int
    {
        $mode = CameraMonitorManaged::normalizeMode($mode);
        $client = $this->boundClient($profile);
        $blocker = CameraMonitorManaged::switchBlocker($mode, $profile->bound_client_uid, $client ? $client->app_version : null);
        if ($blocker !== null) {
            throw new InvalidArgumentException($blocker);
        }

        $version = $this->bump($profile, function (CameraMonitorProfile $locked) use ($mode) {
            if (CameraMonitorManaged::normalizeMode($locked->mode) === $mode) {
                return false;
            }
            $locked->mode = $mode;

            return true;
        });
        $this->log($profile, $adminId, 'switch_mode', ['mode' => $mode, 'version' => $version]);

        return $version;
    }

    /**
     * @return array{id: string, delivered: bool}
     * @throws InvalidArgumentException 动作或参数不合法
     */
    public function command(CameraMonitorProfile $profile, string $name, $args, ?int $adminId): array
    {
        [$name, $args] = CameraMonitorRemoteCommands::normalize($name, $args);
        $uuid = (string) Str::uuid();

        $row = CameraMonitorCommand::query()->create([
            'uuid' => $uuid,
            'profile_id' => (int) $profile->id,
            'admin_user_id' => $adminId,
            'name' => $name,
            'args' => $args,
            'delivered' => false,
        ]);

        // args 必须是 JSON 对象：客户端按 dict 取值，空数组编码成 [] 会让它拿不到
        $delivered = $this->channel->send((int) $profile->id, [
            'type' => 'command', 'id' => $uuid, 'name' => $name, 'args' => (object) $args,
        ]);
        if ($delivered) {
            $row->delivered = true;
            $row->save();
        }
        $this->log($profile, $adminId, 'command:' . $name, $args + ['delivered' => $delivered]);

        return ['id' => $uuid, 'delivered' => $delivered];
    }

    // ==================== 内部 ====================

    /**
     * 锁档案行改动；改了就把版本号加一并通知现场。$mutate 返回 false 表示没变。
     */
    private function bump(CameraMonitorProfile $profile, callable $mutate): int
    {
        [$version, $changed] = DB::transaction(function () use ($profile, $mutate) {
            /** @var CameraMonitorProfile|null $locked */
            $locked = CameraMonitorProfile::query()->whereKey($profile->id)->lockForUpdate()->first();
            if (!$locked) {
                throw new RuntimeException('监控档案不存在');
            }
            if ($mutate($locked) === false) {
                return [(int) $locked->version, false];
            }
            $locked->version = (int) $locked->version + 1;
            $locked->save();

            return [(int) $locked->version, true];
        });

        if ($changed) {
            $this->channel->configChanged((int) $profile->id, $version);
        }

        return $version;
    }

    private function boundClient(CameraMonitorProfile $profile): ?CameraMonitorClient
    {
        $uid = trim((string) $profile->bound_client_uid);
        if ($uid === '') {
            return null;
        }

        return CameraMonitorClient::query()->where('profile_id', $profile->id)->where('client_uid', $uid)->first();
    }

    private function log(CameraMonitorProfile $profile, ?int $adminId, string $action, array $summary): void
    {
        CameraMonitorActionLog::query()->create([
            'profile_id' => (int) $profile->id,
            'admin_user_id' => $adminId,
            'action' => $action,
            'summary' => $summary,
        ]);
    }

    private static function ts($value): ?int
    {
        return $value instanceof Carbon || $value instanceof \DateTimeInterface ? $value->getTimestamp() : null;
    }
}
```

- [ ] **Step 3: 语法检查**

Run: `php -l app/Services/CameraMonitor/CameraMonitorRemoteService.php`
Expected: 无语法错误

- [ ] **Step 4: 提交**

```bash
git add app/Services/CameraMonitor/CameraMonitorRemoteService.php
git commit -m "feat(camera-monitor): 远程控制服务——保存、全屏、设置、模式、远程动作

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 机构权限抽成 trait

**Files:**
- Create: `app/Admin/Controllers/CameraMonitor/ScopesInstitutions.php`
- Modify: `app/Admin/Controllers/CameraMonitor/CameraMonitorProfileController.php`

- [ ] **Step 1: 新建 trait，把三个方法原样挪过去**

`ScopesInstitutions.php`：

```php
<?php

namespace App\Admin\Controllers\CameraMonitor;

use App\Models\InstitutionDetail;
use Dcat\Admin\Admin;

/**
 * 复用 InstitutionDetail 自带的机构权限作用域：机构管理员只看得到、动得了自己机构的档案。
 * 档案列表和远程控制页共用。
 */
trait ScopesInstitutions
{
    /**
     * 超级管理员身上没有作用域，返回 null 表示不加限制。
     */
    private function visibleInstitutionIds(): ?array
    {
        if ($this->isUnrestrictedAdmin()) {
            return null;
        }

        return InstitutionDetail::pluck('id')->all();
    }

    /**
     * 判断条件与 InstitutionDetail::boot() 里决定是否挂作用域的条件保持一致。
     */
    private function isUnrestrictedAdmin(): bool
    {
        $user = Admin::user();
        if (!$user) {
            return true;
        }

        if ((int) $user->id === 1) {
            return true;
        }

        return empty($user->org_id) && empty($user->institution_id) && empty($user->company_id);
    }

    private function canReach(int $institutionId): bool
    {
        $visible = $this->visibleInstitutionIds();

        return $visible === null || in_array($institutionId, array_map('intval', $visible), true);
    }
}
```

- [ ] **Step 2: ProfileController 改用 trait**

在 `CameraMonitorProfileController` 类体的第一行加 `use ScopesInstitutions;`，然后删掉类里的 `visibleInstitutionIds()`、`isUnrestrictedAdmin()`、`canReach()` 三个私有方法，以及它们上方的注释块。

- [ ] **Step 3: 语法检查，并确认没有重复定义**

```bash
php -l app/Admin/Controllers/CameraMonitor/ScopesInstitutions.php
php -l app/Admin/Controllers/CameraMonitor/CameraMonitorProfileController.php
grep -n "function visibleInstitutionIds\|function isUnrestrictedAdmin\|function canReach" app/Admin/Controllers/CameraMonitor/*.php
```

Expected: 无语法错误；三个方法都只在 `ScopesInstitutions.php` 里出现。

- [ ] **Step 4: 提交**

```bash
git add app/Admin/Controllers/CameraMonitor/ScopesInstitutions.php app/Admin/Controllers/CameraMonitor/CameraMonitorProfileController.php
git commit -m "refactor(camera-monitor): 机构权限判断抽成 trait，供远程控制页复用

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 远程控制控制器与路由

**Files:**
- Create: `app/Admin/Controllers/CameraMonitor/CameraMonitorRemoteController.php`
- Modify: `app/Admin/routes.php`

- [ ] **Step 1: 控制器**

```php
<?php

namespace App\Admin\Controllers\CameraMonitor;

use App\Models\CameraMonitor\CameraMonitorProfile;
use App\Services\CameraMonitor\CameraMonitorAuthCodeService;
use App\Services\CameraMonitor\CameraMonitorManaged;
use App\Services\CameraMonitor\CameraMonitorPayload;
use App\Services\CameraMonitor\CameraMonitorRemoteService;
use App\Services\CameraMonitor\CameraMonitorSecretService;
use App\Services\CameraMonitor\CameraMonitorSnapshotRules;
use Dcat\Admin\Admin;
use Dcat\Admin\Http\Controllers\AdminController;
use Dcat\Admin\Layout\Content;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Storage;
use InvalidArgumentException;
use Throwable;

/**
 * 傻瓜模式点位的远程控制页。页面每 2 秒拉一次 state；写操作都走 CameraMonitorRemoteService。
 */
class CameraMonitorRemoteController extends AdminController
{
    use ScopesInstitutions;

    /** 与客户端 wall_layout.DEFAULT_COLUMNS 一致 */
    private const DEFAULT_COLUMNS = [4 => 2, 9 => 3, 12 => 3, 16 => 4, 20 => 4, 25 => 5];

    public function index(Content $content, $id)
    {
        $profile = $this->reachable($id);
        if (!$profile) {
            abort(404);
        }
        if (!$profile->isManaged()) {
            return redirect(admin_url('camera_monitor_profile/' . $profile->id));
        }

        try {
            $codes = app(CameraMonitorAuthCodeService::class);
            $masked = $codes->mask(app(CameraMonitorSecretService::class)->decrypt($profile->auth_code_encrypted));
        } catch (Throwable $exception) {
            $masked = '无法读取';
        }

        return $content
            ->title('远程控制 · ' . $profile->name)
            ->description((string) optional($profile->institution)->institution_name)
            ->body(view('admin.camera-monitor.remote', ['payload' => [
                'csrf' => csrf_token(),
                'base' => admin_url('camera_monitor_profile/' . $profile->id . '/remote'),
                'list' => admin_url('camera_monitor_profile'),
                'name' => (string) $profile->name,
                'institution' => (string) optional($profile->institution)->institution_name,
                'code' => $masked,
                'capacities' => CameraMonitorPayload::CAPACITIES,
                'equal' => CameraMonitorPayload::EQUAL_CAPACITIES,
                'default_columns' => self::DEFAULT_COLUMNS,
                'colors' => CameraMonitorPayload::COLORS,
            ]]));
    }

    public function state(CameraMonitorRemoteService $remote, $id): JsonResponse
    {
        $profile = $this->reachable($id);

        return $profile ? $this->ok(['state' => $remote->state($profile)]) : $this->fail('监控档案不存在或无权访问', 404);
    }

    public function config(Request $request, CameraMonitorRemoteService $remote, $id): JsonResponse
    {
        $profile = $this->managed($id);
        if (!$profile) {
            return $this->fail('该点位不是傻瓜模式或无权访问', 404);
        }

        $version = $request->input('version');
        if (!is_numeric($version)) {
            return $this->fail('缺少版本号，请刷新页面');
        }

        $result = $remote->saveConfig($profile, (int) $version, $request->input('layout'), $request->input('cameras'), $this->adminId());
        if ($result['status'] === 'conflict') {
            return $this->fail('配置已被他人修改，请重新加载后再改', 409, ['state' => $remote->state($profile)]);
        }

        return $this->ok(['state' => $remote->state($profile)], '已保存并下发');
    }

    public function fullscreen(Request $request, CameraMonitorRemoteService $remote, $id): JsonResponse
    {
        $profile = $this->managed($id);
        if (!$profile) {
            return $this->fail('该点位不是傻瓜模式或无权访问', 404);
        }

        $version = $remote->setFullscreen($profile, filter_var($request->input('value'), FILTER_VALIDATE_BOOLEAN), $this->adminId());

        return $this->ok(['version' => $version]);
    }

    public function settings(Request $request, CameraMonitorRemoteService $remote, $id): JsonResponse
    {
        $profile = $this->managed($id);
        if (!$profile) {
            return $this->fail('该点位不是傻瓜模式或无权访问', 404);
        }

        try {
            $version = $remote->saveSettings($profile, $request->only(['default_username', 'default_password', 'escape_password']), $this->adminId());
        } catch (InvalidArgumentException $exception) {
            return $this->fail($exception->getMessage());
        } catch (Throwable $exception) {
            return $this->fail('保存失败，请检查 CAMERA_MONITOR_SECRET_KEY 是否已配置', 500);
        }

        return $this->ok(['version' => $version], '已保存');
    }

    public function mode(Request $request, CameraMonitorRemoteService $remote, $id): JsonResponse
    {
        $profile = $this->reachable($id);
        if (!$profile) {
            return $this->fail('监控档案不存在或无权访问', 404);
        }

        try {
            $version = $remote->switchMode($profile, (string) $request->input('mode'), $this->adminId());
        } catch (InvalidArgumentException $exception) {
            return $this->fail($exception->getMessage());
        }

        return $this->ok(['version' => $version], '模式已切换');
    }

    public function command(Request $request, CameraMonitorRemoteService $remote, $id): JsonResponse
    {
        $profile = $this->managed($id);
        if (!$profile) {
            return $this->fail('该点位不是傻瓜模式或无权访问', 404);
        }

        try {
            $result = $remote->command($profile, (string) $request->input('name'), $request->input('args'), $this->adminId());
        } catch (InvalidArgumentException $exception) {
            return $this->fail($exception->getMessage());
        }

        return $this->ok($result, $result['delivered'] ? '已发送到现场电脑' : '现场电脑离线，操作未送达');
    }

    public function snapshot(Request $request, $id)
    {
        $profile = $this->reachable($id);
        $kind = (string) $request->input('kind', 'camera');
        $ip = $kind === 'camera' ? CameraMonitorPayload::ip($request->input('ip')) : null;
        if (!$profile || !in_array($kind, ['camera', 'screen'], true) || ($kind === 'camera' && $ip === null)) {
            abort(404);
        }

        $path = CameraMonitorSnapshotRules::path((int) $profile->id, $kind, $ip);
        if (!Storage::disk('local')->exists($path)) {
            abort(404);
        }

        return response(Storage::disk('local')->get($path), 200, [
            'Content-Type' => 'image/jpeg',
            'Cache-Control' => 'no-store',
        ]);
    }

    // ==================== 内部 ====================

    private function reachable($id): ?CameraMonitorProfile
    {
        /** @var CameraMonitorProfile|null $profile */
        $profile = CameraMonitorProfile::query()->with('institution')->whereKey((int) $id)->first();

        return $profile && $this->canReach((int) $profile->institution_id) ? $profile : null;
    }

    private function managed($id): ?CameraMonitorProfile
    {
        $profile = $this->reachable($id);

        return $profile && CameraMonitorManaged::rejectsClientWrites($profile->mode) ? $profile : null;
    }

    private function adminId(): ?int
    {
        $user = Admin::user();

        return $user ? (int) $user->id : null;
    }

    private function ok(array $data = [], string $message = '操作成功'): JsonResponse
    {
        return response()->json(array_merge(['status' => true, 'message' => $message], $data));
    }

    private function fail(string $message, int $status = 422, array $data = []): JsonResponse
    {
        return response()->json(array_merge(['status' => false, 'message' => $message], $data), $status);
    }
}
```

- [ ] **Step 2: 路由**

在 `app/Admin/routes.php` 第一个路由组里，紧接现有的 `$router->post('camera_monitor_profile/clients/{client}/delete', ...)` 之前插入（**必须**放在 `$router->resource('camera_monitor_profile', ...)` 之前、同一个带中间件的 group 里，原因见该处已有的注释）：

```php
    // 傻瓜模式的远程控制页及其接口
    $router->get('camera_monitor_profile/{id}/remote', 'CameraMonitor\CameraMonitorRemoteController@index')->where('id', '[0-9]+');
    $router->get('camera_monitor_profile/{id}/remote/state', 'CameraMonitor\CameraMonitorRemoteController@state')->where('id', '[0-9]+');
    $router->get('camera_monitor_profile/{id}/remote/snapshot', 'CameraMonitor\CameraMonitorRemoteController@snapshot')->where('id', '[0-9]+');
    $router->post('camera_monitor_profile/{id}/remote/config', 'CameraMonitor\CameraMonitorRemoteController@config')->where('id', '[0-9]+');
    $router->post('camera_monitor_profile/{id}/remote/fullscreen', 'CameraMonitor\CameraMonitorRemoteController@fullscreen')->where('id', '[0-9]+');
    $router->post('camera_monitor_profile/{id}/remote/settings', 'CameraMonitor\CameraMonitorRemoteController@settings')->where('id', '[0-9]+');
    $router->post('camera_monitor_profile/{id}/remote/mode', 'CameraMonitor\CameraMonitorRemoteController@mode')->where('id', '[0-9]+');
    $router->post('camera_monitor_profile/{id}/remote/command', 'CameraMonitor\CameraMonitorRemoteController@command')->where('id', '[0-9]+');
```

权限：现有权限 `camera-monitor-profile` 的 `http_path` 是 `/camera_monitor_profile,/camera_monitor_profile/*`，已经覆盖这些新路径，不需要新增权限。

- [ ] **Step 3: 语法检查**

```bash
php -l app/Admin/Controllers/CameraMonitor/CameraMonitorRemoteController.php
php -l app/Admin/routes.php
```

- [ ] **Step 4: 提交**

```bash
git add app/Admin/Controllers/CameraMonitor/CameraMonitorRemoteController.php app/Admin/routes.php
git commit -m "feat(camera-monitor): 远程控制页的控制器与路由

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 远程控制页面

**Files:**
- Create: `resources/views/admin/camera-monitor/remote.blade.php`

界面以 Word 功能文档第五章的示意图为准。所有数据都来自 `/state`；页面上的改动先暂存在 `draft` 里，点"保存并下发"才提交。

- [ ] **Step 1: 写模板**

````blade
<div id="cmr-app" class="cmr">
<style>
.cmr{--ink:#1f2937;--muted:#6b7280;--line:#e6e9ef;--soft:#f6f8fb;--pri:#586cb1;--ok:#1a7f43;--bad:#b91c1c;--warn:#b45309;color:var(--ink);font-size:13px}
.cmr-card{background:#fff;border:1px solid var(--line);border-radius:8px;margin-bottom:16px}
.cmr-head{padding:14px 18px;border-bottom:1px solid #eef0f4;display:flex;align-items:center;justify-content:space-between;gap:10px;font-weight:600;font-size:15px}
.cmr-body{padding:16px 18px}
.cmr-muted{color:var(--muted);font-weight:400;font-size:13px}
.cmr-btn{display:inline-flex;align-items:center;gap:6px;border:1px solid #d6dbe6;background:#fff;border-radius:6px;padding:0 14px;height:32px;font-size:13px;color:#374151;cursor:pointer}
.cmr-btn:disabled{opacity:.45;cursor:not-allowed}
.cmr-btn.pri{background:var(--pri);border-color:var(--pri);color:#fff}
.cmr-btn.red{color:#dc2626;border-color:#fecaca}
.cmr-btn.link{border:none;background:none;color:var(--pri);padding:0 4px}
.cmr-tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:4px;border:1px solid}
.cmr-tag.blue{background:#eef1fa;color:var(--pri);border-color:#cfd6ef}
.cmr-tag.green{background:#ecfdf3;color:var(--ok);border-color:#bbf0cf}
.cmr-tag.gray{background:#f3f4f6;color:var(--muted);border-color:#e5e7eb}
.cmr-tag.red{background:#fef2f2;color:var(--bad);border-color:#fecaca}
.cmr-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.cmr-dot.ok{background:#22c55e}.cmr-dot.warn{background:#f59e0b}.cmr-dot.bad{background:#ef4444}.cmr-dot.off{background:#9ca3af}
.cmr-alert{border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:13px}
.cmr-alert.red{background:#fef2f2;border:1px solid #fecaca;color:var(--bad)}
.cmr-alert.amber{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412}
.cmr-status{display:flex;align-items:center;gap:22px;flex-wrap:wrap}
.cmr-grid2{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:16px}
@media (max-width:1200px){.cmr-grid2{grid-template-columns:1fr}}
.cmr-settings{display:flex;flex-wrap:wrap;gap:18px;align-items:center;margin-bottom:14px}
.cmr-seg{display:inline-flex;border:1px solid #d6dbe6;border-radius:6px;overflow:hidden;vertical-align:middle}
.cmr-seg button{border:none;border-right:1px solid #e5e7eb;background:#fff;padding:0 11px;height:30px;font-size:13px;cursor:pointer}
.cmr-seg button:last-child{border-right:none}
.cmr-seg button.on{background:var(--pri);color:#fff}
.cmr-input{height:32px;border:1px solid #d6dbe6;border-radius:6px;padding:0 10px;font-size:13px}
.cmr-wall{display:grid;gap:6px}
.cmr-tile{border:1px solid #e1e5ee;border-radius:6px;overflow:hidden;background:#fff;cursor:grab}
.cmr-tile.over,.cmr-empty.over{outline:2px solid var(--pri)}
.cmr-tile.dragging{opacity:.45}
.cmr-shot{aspect-ratio:16/9;background:#1b2436;display:flex;align-items:center;justify-content:center;color:#6b7a95;font-size:12px;overflow:hidden}
.cmr-shot img{width:100%;height:100%;object-fit:cover;display:block}
.cmr-foot{padding:6px 8px;font-size:12px;display:flex;justify-content:space-between;gap:6px;white-space:nowrap}
.cmr-foot b{overflow:hidden;text-overflow:ellipsis}
.cmr-empty{aspect-ratio:16/9;border:1.5px dashed #cbd2e0;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#9ca3af;font-size:12px;background:#fafbfd;margin-bottom:28px}
.cmr-table{width:100%;border-collapse:collapse}
.cmr-table th{background:#f8f9fc;color:var(--muted);font-weight:500;text-align:left;padding:10px 12px;border-bottom:1px solid #eef0f4}
.cmr-table td{padding:10px 12px;border-bottom:1px solid #f1f3f7;vertical-align:middle}
.cmr-thumb{width:128px;aspect-ratio:16/9;border-radius:4px;background:#f1f3f7;color:#9ca3af;font-size:11px;display:flex;align-items:center;justify-content:center;text-align:center;overflow:hidden}
.cmr-thumb img{width:100%;height:100%;object-fit:cover}
.cmr-row{display:grid;grid-template-columns:150px 1fr;gap:14px;align-items:center;padding:12px 0;border-bottom:1px solid #f1f3f7}
.cmr-row>div:first-child{color:var(--muted)}
.cmr-drawer-mask{position:fixed;inset:0;background:rgba(17,24,39,.3);z-index:2000;display:none}
.cmr-drawer{position:fixed;right:0;top:0;bottom:0;width:460px;max-width:100%;background:#fff;box-shadow:-10px 0 40px rgba(0,0,0,.18);z-index:2001;display:none;flex-direction:column}
.cmr-drawer.open,.cmr-drawer-mask.open{display:flex}
.cmr-drawer .cmr-head{font-size:16px}
.cmr-drawer .cmr-body{flex:1;overflow:auto}
.cmr-field{margin-bottom:14px}
.cmr-field label{display:block;color:var(--muted);margin-bottom:6px}
.cmr-field .cmr-input{width:100%}
.cmr-two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.cmr-swatch{display:inline-block;width:22px;height:22px;border-radius:50%;margin-right:8px;border:2px solid #fff;box-shadow:0 0 0 1px #d6dbe6;cursor:pointer}
.cmr-swatch.on{box-shadow:0 0 0 2px var(--pri)}
.cmr-screen img{width:100%;border-radius:6px;border:1px solid #e1e5ee;display:block}
</style>

<div data-role="alerts"></div>
<div class="cmr-card"><div class="cmr-body cmr-status" data-role="status"></div></div>

<div class="cmr-grid2">
    <div class="cmr-card" style="margin:0">
        <div class="cmr-head"><span>监控墙</span><span data-role="save-bar"></span></div>
        <div class="cmr-body">
            <div class="cmr-settings" data-role="wall-settings"></div>
            <div class="cmr-wall" data-role="wall"></div>
            <div class="cmr-muted" style="margin-top:10px">拖动画面到另一个格子可交换位置；拖到空位即移动；点击画面可修改名称、账号密码等。</div>
        </div>
    </div>
    <div class="cmr-card" style="margin:0">
        <div class="cmr-head"><span>现场大屏</span><button class="cmr-btn" data-act="capture">重新截取</button></div>
        <div class="cmr-body cmr-screen" data-role="screen"></div>
    </div>
</div>

<div class="cmr-card" style="margin-top:16px">
    <div class="cmr-head"><span>发现的摄像头 <span class="cmr-muted" data-role="found-meta"></span></span>
        <span><button class="cmr-btn" data-act="scan">搜索摄像头</button> <button class="cmr-btn" data-act="scan-ip">按 IP 地址搜索</button></span></div>
    <table class="cmr-table"><thead><tr><th style="width:150px">画面</th><th>IP 地址</th><th>品牌</th><th>型号</th><th>状态</th><th>操作</th></tr></thead>
        <tbody data-role="found"></tbody></table>
    <div class="cmr-body cmr-muted">也可以直接把某一行拖到上方监控墙的格子里。</div>
</div>

<div class="cmr-card">
    <div class="cmr-head"><span>设备设置</span></div>
    <div class="cmr-body" data-role="settings"></div>
</div>

<div class="cmr-card">
    <div class="cmr-head"><span>最近的远程操作</span></div>
    <table class="cmr-table"><thead><tr><th>时间</th><th>操作</th><th>状态</th></tr></thead><tbody data-role="commands"></tbody></table>
</div>

<div class="cmr-drawer-mask" data-role="mask"></div>
<div class="cmr-drawer" data-role="drawer"></div>

<script>
(function () {
    var P = @json($payload);
    var root = document.getElementById('cmr-app');
    var STATE = {playing: ['播放中', 'ok'], connecting: ['连接中', 'warn'], auth_failed: ['账号密码错误', 'bad'],
        unreachable: ['连不上', 'bad'], stopped: ['已停止', 'off']};
    var CORNERS = [['top-left', '左上'], ['top-right', '右上'], ['bottom-left', '左下'], ['bottom-right', '右下']];
    var server = null, draft = null, baseVersion = 0, dirty = 0, conflict = false, editing = null, autoCaptured = false;

    function $(role) { return root.querySelector('[data-role="' + role + '"]'); }
    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
        });
    }
    function clone(value) { return JSON.parse(JSON.stringify(value)); }
    function toast(type, message) {
        if (window.Dcat && Dcat[type]) { Dcat[type](message); } else { window.alert(message); }
    }
    function request(method, path, body) {
        return fetch(P.base + path, {
            method: method,
            credentials: 'same-origin',
            headers: {'Accept': 'application/json', 'Content-Type': 'application/json', 'X-CSRF-TOKEN': P.csrf},
            body: body ? JSON.stringify(body) : undefined
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                return {ok: response.ok, status: response.status, data: data};
            });
        });
    }
    function shot(kind, ip, at) {
        if (!at) { return ''; }
        return P.base + '/snapshot?kind=' + kind + (ip ? '&ip=' + encodeURIComponent(ip) : '') + '&t=' + encodeURIComponent(at);
    }
    function columnsFor(layout) {
        var cap = layout.capacity;
        return (layout.columns && layout.columns[cap]) || P.default_columns[cap] || 4;
    }
    function cameraAt(slot) {
        return draft.cameras.filter(function (c) { return c.slot_index === slot; })[0] || null;
    }
    function freeSlot() {
        for (var i = 0; i < draft.layout.capacity; i++) { if (!cameraAt(i)) { return i; } }
        return -1;
    }
    function touch() { dirty += 1; render(); }

    // ---------- 数据 ----------

    function load() {
        return request('GET', '/state').then(function (res) {
            if (res.ok && res.data.state) { adopt(res.data.state); }
        });
    }

    function adopt(state) {
        server = state;
        if (!dirty) {
            draft = {layout: clone(state.layout), cameras: clone(state.cameras)};
            baseVersion = state.version;
            conflict = false;
        } else {
            var byIp = {};
            state.cameras.forEach(function (c) { byIp[c.ip] = c; });
            draft.cameras.forEach(function (c) {
                if (byIp[c.ip]) { c.state = byIp[c.ip].state; c.snapshot_at = byIp[c.ip].snapshot_at; }
            });
            if (state.version !== baseVersion) { conflict = true; }
        }
        render();
        if (!autoCaptured && state.online) {
            autoCaptured = true;
            command('capture_screen', {}, true);
        }
    }

    function acceptVersion(version) {
        if (version === baseVersion || version === baseVersion + 1) {
            baseVersion = version;
            if (!dirty) { server.version = version; }
            return true;
        }
        conflict = true;
        return false;
    }

    // ---------- 操作 ----------

    function command(name, args, quiet) {
        return request('POST', '/command', {name: name, args: args || {}}).then(function (res) {
            if (!res.ok) { toast('error', res.data.message || '操作失败'); return; }
            if (!quiet || !res.data.delivered) { toast(res.data.delivered ? 'success' : 'warning', res.data.message); }
            load();
        });
    }

    function save() {
        var cameras = draft.cameras.map(function (c) {
            var out = {ip: c.ip, slot_index: c.slot_index, display_name: c.display_name || '', model: c.model || '',
                manufacturer: c.manufacturer || '', protocols: c.protocols || [], onvif_urls: c.onvif_urls || [],
                stream_mode: c.stream_mode || 'onvif', dahua_channel: c.dahua_channel || 1, manual_url: c.manual_url || '',
                transport: c.transport || 'tcp', username: c.username || '', name_color: c.name_color || '#ffffff',
                name_corner: c.name_corner || 'top-left'};
            if (typeof c.password === 'string') { out.password = c.password; }
            return out;
        });
        request('POST', '/config', {version: baseVersion, layout: draft.layout, cameras: cameras}).then(function (res) {
            if (res.status === 409) { conflict = true; render(); toast('warning', res.data.message); return; }
            if (!res.ok) { toast('error', res.data.message || '保存失败'); return; }
            dirty = 0;
            adopt(res.data.state);
            toast('success', res.data.message);
        });
    }

    function discard() { dirty = 0; adopt(server); }

    function setFullscreen(value) {
        request('POST', '/fullscreen', {value: value}).then(function (res) {
            if (!res.ok) { toast('error', res.data.message || '操作失败'); return; }
            if (acceptVersion(res.data.version)) {
                draft.layout.fullscreen = value;
                server.layout.fullscreen = value;
            }
            render();
        });
    }

    function saveSettings(body) {
        return request('POST', '/settings', body).then(function (res) {
            if (!res.ok) { toast('error', res.data.message || '保存失败'); return false; }
            acceptVersion(res.data.version);
            toast('success', res.data.message);
            load();
            return true;
        });
    }

    function switchToFull() {
        if (!window.confirm('切回完整模式后，现场恢复完整界面并可自行修改配置，后台只能查看。确定吗？')) { return; }
        request('POST', '/mode', {mode: 'full'}).then(function (res) {
            if (!res.ok) { toast('error', res.data.message || '切换失败'); return; }
            window.location.href = P.list;
        });
    }

    function setCapacity(capacity) {
        if (draft.cameras.length > capacity) { toast('warning', '画面数量超过所选分屏，请先移除多余画面'); return; }
        var taken = {};
        draft.cameras.forEach(function (c) { if (c.slot_index < capacity) { taken[c.slot_index] = true; } });
        draft.cameras.forEach(function (c) {
            if (c.slot_index >= capacity) {
                for (var i = 0; i < capacity; i++) { if (!taken[i]) { taken[i] = true; c.slot_index = i; break; } }
            }
        });
        draft.layout.capacity = capacity;
        touch();
    }

    function addFound(ip, slot) {
        var found = server.discovered.filter(function (d) { return d.ip === ip; })[0];
        if (!found) { return; }
        if (draft.cameras.some(function (c) { return c.ip === ip; })) { toast('warning', '这台已经在监控墙上了'); return; }
        if (slot === undefined) { slot = freeSlot(); }
        if (slot < 0) { toast('warning', '监控墙已满，请先调大分屏或移除画面'); return; }
        if (cameraAt(slot)) { toast('warning', '这个格子已有画面，请拖到空位'); return; }
        var dahuaOnly = (found.protocols || []).indexOf('ONVIF') < 0 && (found.protocols || []).indexOf('大华 DHIP') >= 0;
        draft.cameras.push({ip: found.ip, slot_index: slot, display_name: '', model: found.model, manufacturer: found.manufacturer,
            protocols: found.protocols, onvif_urls: found.onvif_urls, stream_mode: dahuaOnly ? 'dahua' : 'onvif', dahua_channel: 1,
            manual_url: '', transport: 'tcp', username: '', password_set: false, name_color: '#ffffff', name_corner: 'top-left',
            state: null, snapshot_at: found.snapshot_at});
        touch();
    }

    function moveTo(ip, slot) {
        var source = draft.cameras.filter(function (c) { return c.ip === ip; })[0];
        if (!source || source.slot_index === slot) { return; }
        var target = cameraAt(slot);
        if (target) { target.slot_index = source.slot_index; }
        source.slot_index = slot;
        touch();
    }

    // ---------- 渲染 ----------

    function render() {
        if (!server || !draft) { return; }
        renderAlerts(); renderStatus(); renderWallSettings(); renderWall(); renderScreen(); renderFound(); renderSettings(); renderCommands();
    }

    function renderAlerts() {
        var html = '';
        if (!server.escape_password_set) {
            html += '<div class="cmr-alert red">⚠ 还没有设置维护密码，现场仍是初始密码 000000，建议在下方「设备设置」中修改。</div>';
        }
        if (conflict) {
            html += '<div class="cmr-alert amber">配置已被他人修改。<button class="cmr-btn link" data-act="reload">重新加载</button>（会放弃你还没下发的改动）</div>';
        }
        $('alerts').innerHTML = html;
    }

    function renderStatus() {
        var applied;
        if (!server.online) { applied = '<span class="cmr-muted">现场离线</span>'; }
        else if (server.applied_version === server.version) { applied = '<span style="color:var(--ok)">✓ 已是最新配置</span>'; }
        else { applied = '<span style="color:var(--warn)">正在更新…</span>'; }
        $('status').innerHTML =
            '<div style="flex:1"><div style="font-size:19px;font-weight:600;margin-bottom:6px">' + esc(P.name) +
            ' <span class="cmr-tag blue">傻瓜模式</span></div><div class="cmr-muted">' + esc(P.institution) + ' · 设备码 ' + esc(P.code) +
            ' · ' + esc(server.device_name || '尚未绑定电脑') + (server.app_version ? ' · 软件 ' + esc(server.app_version) : '') + '</div></div>' +
            '<div style="text-align:right;line-height:1.8"><div>' + (server.online
                ? '<span class="cmr-dot ok"></span><b style="color:var(--ok)">在线</b>'
                : '<span class="cmr-dot off"></span><b class="cmr-muted">离线</b>' + (server.last_seen ? ' <span class="cmr-muted">· 最后在线 ' + esc(server.last_seen) + '</span>' : '')) +
            '</div><div class="cmr-muted">现场画面 ' + applied + '</div></div>' +
            '<div style="display:flex;gap:8px">' +
            '<button class="cmr-btn" data-act="capture"' + (server.online ? '' : ' disabled') + '>截取大屏画面</button>' +
            '<button class="cmr-btn" data-act="reconnect"' + (server.online ? '' : ' disabled') + '>全部重连</button>' +
            '<button class="cmr-btn" data-act="restart"' + (server.online ? '' : ' disabled') + '>重启软件</button></div>';
    }

    function renderWallSettings() {
        var layout = draft.layout, cap = layout.capacity, html = '';
        html += '<div>分屏 <span class="cmr-seg">' + P.capacities.map(function (c) {
            return '<button data-act="capacity" data-value="' + c + '" class="' + (c === cap ? 'on' : '') + '">' + c + '</button>';
        }).join('') + '</span></div>';
        if (P.equal.indexOf(cap) >= 0) {
            var current = (layout.columns && layout.columns[cap]) || 0, options = [0];
            for (var n = 1; n <= cap && n <= 8; n++) { options.push(n); }
            html += '<div>每行 <span class="cmr-seg">' + options.map(function (n) {
                return '<button data-act="columns" data-value="' + n + '" class="' + (n === current ? 'on' : '') + '">' + (n ? n : '自动') + '</button>';
            }).join('') + '</span></div>';
        } else {
            html += '<div class="cmr-muted">一大多小布局，第 1 格为大画面</div>';
        }
        html += '<label><input type="checkbox" data-act="fill"' + (layout.fill_width ? ' checked' : '') + '> 横向铺满</label>';
        html += '<label><input type="checkbox" data-act="fullscreen"' + (layout.fullscreen ? ' checked' : '') + '> 全屏显示 <span class="cmr-muted">（立即生效）</span></label>';
        html += '<label>大屏标题 <input class="cmr-input" data-act="organization" maxlength="80" value="' + esc(layout.organization) + '"></label>';
        $('wall-settings').innerHTML = html;
        $('save-bar').innerHTML = dirty
            ? '<span class="cmr-tag red" style="margin-right:8px">有 ' + dirty + ' 处改动还没下发到现场</span>' +
              '<button class="cmr-btn" data-act="discard">放弃改动</button> <button class="cmr-btn pri" data-act="save"' + (conflict ? ' disabled' : '') + '>保存并下发</button>'
            : '<span class="cmr-muted">已与现场一致</span>';
    }

    function renderWall() {
        var cap = draft.layout.capacity, html = '';
        $('wall').style.gridTemplateColumns = 'repeat(' + columnsFor(draft.layout) + ', minmax(0, 1fr))';
        for (var slot = 0; slot < cap; slot++) {
            var c = cameraAt(slot);
            if (!c) { html += '<div class="cmr-empty" data-slot="' + slot + '">空位 · 可从下方拖入</div>'; continue; }
            var st = STATE[c.state] || ['未上报', 'off'], src = shot('camera', c.ip, c.snapshot_at);
            html += '<div class="cmr-tile" draggable="true" data-slot="' + slot + '" data-ip="' + esc(c.ip) + '">' +
                '<div class="cmr-shot">' + (src ? '<img src="' + esc(src) + '" alt="">' : '暂无截图') + '</div>' +
                '<div class="cmr-foot"><b>' + (slot + 1) + '. ' + esc(c.display_name || c.ip) + '</b>' +
                '<span><span class="cmr-dot ' + st[1] + '"></span>' + st[0] + '</span></div></div>';
        }
        $('wall').innerHTML = html;
    }

    function renderScreen() {
        var src = shot('screen', '', server.screen_at);
        $('screen').innerHTML = src
            ? '<img src="' + esc(src) + '" alt=""><div class="cmr-muted" style="margin-top:8px">' + esc(server.screen_at) + ' 截取 · 这是现场电脑当时显示的画面</div>'
            : '<div class="cmr-muted">还没有截图。现场在线时点「重新截取」。</div>';
        root.querySelectorAll('[data-act="capture"]').forEach(function (b) { b.disabled = !server.online; });
    }

    function renderFound() {
        var rows = server.discovered;
        $('found-meta').textContent = server.scanning ? '· 正在搜索，约 10 秒…' : (rows.length ? '· 共 ' + rows.length + ' 台' : '· 还没有搜索过');
        root.querySelector('[data-act="scan"]').disabled = !server.online || server.scanning;
        root.querySelector('[data-act="scan-ip"]').disabled = !server.online || server.scanning;
        $('found').innerHTML = rows.map(function (d) {
            var placed = draft.cameras.filter(function (c) { return c.ip === d.ip; })[0], src = shot('camera', d.ip, d.snapshot_at);
            return '<tr draggable="' + (placed ? 'false' : 'true') + '" data-found="' + esc(d.ip) + '">' +
                '<td><div class="cmr-thumb">' + (src ? '<img src="' + esc(src) + '" alt="">' : '填写账号密码后<br>显示画面') + '</div></td>' +
                '<td style="font-family:Menlo,monospace">' + esc(d.ip) + '</td><td>' + esc(d.manufacturer || '未知') + '</td>' +
                '<td class="cmr-muted">' + esc(d.model || '—') + '</td>' +
                '<td>' + (placed ? '<span class="cmr-tag green">已在监控墙 · 第 ' + (placed.slot_index + 1) + ' 格</span>' : '<span class="cmr-tag gray">未使用</span>') + '</td>' +
                '<td>' + (placed ? '<button class="cmr-btn link" data-act="edit" data-ip="' + esc(d.ip) + '">设置</button>'
                    : '<button class="cmr-btn pri" data-act="add" data-ip="' + esc(d.ip) + '">加入监控墙</button>') + '</td></tr>';
        }).join('') || '<tr><td colspan="6" class="cmr-muted">现场在线时点「搜索摄像头」，现场电脑会在它所在的内网里搜索。</td></tr>';
    }

    function renderSettings() {
        if (document.activeElement && $('settings').contains(document.activeElement)) { return; }
        $('settings').innerHTML =
            '<div class="cmr-row"><div>默认摄像头账号</div><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
            '<input class="cmr-input" data-field="default_username" placeholder="账号" value="' + esc(server.default_username) + '">' +
            '<input class="cmr-input" data-field="default_password" type="password" placeholder="' + (server.default_password_set ? '已设置，不修改请留空' : '密码') + '">' +
            '<button class="cmr-btn" data-act="save-defaults">保存</button>' +
            '<span class="cmr-muted">新搜到的摄像头先用这组账号密码，单路另填的优先</span></div></div>' +
            '<div class="cmr-row"><div>维护密码</div><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
            (server.escape_password_set ? '<span class="cmr-tag green">已设置</span>' : '<span class="cmr-tag red">仍是初始密码 000000</span>') +
            '<input class="cmr-input" data-field="escape_password" type="password" placeholder="新的维护密码">' +
            '<button class="cmr-btn" data-act="save-escape">修改维护密码</button>' +
            '<span class="cmr-muted">现场按 Ctrl+Shift+Alt+Q 输入此密码，才能退出软件或解除绑定</span></div></div>' +
            '<div class="cmr-row"><div>开机自动运行</div><div><span class="cmr-tag green">已开启</span> <span class="cmr-muted">傻瓜模式下自动开启</span></div></div>' +
            '<div class="cmr-row" style="border:none"><div>模式</div><div><span class="cmr-tag blue">傻瓜模式</span> ' +
            '<button class="cmr-btn" data-act="to-full">切回完整模式</button></div></div>';
    }

    function renderCommands() {
        $('commands').innerHTML = server.commands.map(function (c) {
            var cls = {done: 'green', failed: 'red', offline: 'gray'}[c.status] || 'blue';
            return '<tr><td class="cmr-muted">' + esc(c.created_at) + '</td><td>' + esc(c.label) + '</td><td><span class="cmr-tag ' + cls + '">' + esc(c.status_label) + '</span></td></tr>';
        }).join('') || '<tr><td colspan="3" class="cmr-muted">还没有远程操作</td></tr>';
    }

    // ---------- 侧边抽屉 ----------

    function openDrawer(ip) {
        var c = draft.cameras.filter(function (x) { return x.ip === ip; })[0];
        if (!c) { return; }
        editing = ip;
        var src = shot('camera', c.ip, c.snapshot_at), drawer = $('drawer');
        drawer.innerHTML =
            '<div class="cmr-head"><span>设置摄像头 · ' + esc(c.display_name || c.ip) + '</span><button class="cmr-btn link" data-act="close">✕</button></div>' +
            '<div class="cmr-body">' +
            '<div class="cmr-shot" style="border-radius:6px;margin-bottom:6px">' + (src ? '<img src="' + esc(src) + '" alt="">' : '暂无截图') + '</div>' +
            '<div class="cmr-muted" style="margin-bottom:14px">' + (c.snapshot_at ? '现场截图 · ' + esc(c.snapshot_at) : '') + '</div>' +
            '<div class="cmr-field"><label>显示名称</label><input class="cmr-input" data-f="display_name" maxlength="80" value="' + esc(c.display_name) + '"></div>' +
            '<div class="cmr-two"><div class="cmr-field"><label>IP 地址</label><input class="cmr-input" disabled value="' + esc(c.ip) + '"></div>' +
            '<div class="cmr-field"><label>品牌型号</label><input class="cmr-input" disabled value="' + esc((c.manufacturer || '') + ' ' + (c.model || '')) + '"></div>' +
            '<div class="cmr-field"><label>账号</label><input class="cmr-input" data-f="username" placeholder="留空则使用默认账号" value="' + esc(c.username) + '"></div>' +
            '<div class="cmr-field"><label>密码</label><input class="cmr-input" data-f="password" type="password" placeholder="' + (c.password_set ? '已设置，不修改请留空' : '未设置') + '"></div></div>' +
            '<div class="cmr-field"><label>画面来源</label><span class="cmr-seg" data-seg="stream_mode">' +
            [['onvif', '自动识别（推荐）'], ['dahua', '按通道号'], ['manual', '手动填写地址']].map(function (o) {
                return '<button data-v="' + o[0] + '" class="' + (c.stream_mode === o[0] ? 'on' : '') + '">' + o[1] + '</button>';
            }).join('') + '</span></div>' +
            '<div class="cmr-two"><div class="cmr-field"><label>通道号（按通道号时）</label><input class="cmr-input" data-f="dahua_channel" type="number" min="1" max="256" value="' + esc(c.dahua_channel || 1) + '"></div>' +
            '<div class="cmr-field"><label>地址（手动时，须指向这台摄像头）</label><input class="cmr-input" data-f="manual_url" value="' + esc(c.manual_url) + '"></div></div>' +
            '<div class="cmr-field"><label>传输方式</label><span class="cmr-seg" data-seg="transport">' +
            [['tcp', '稳定优先（推荐）'], ['udp', '低延迟优先']].map(function (o) {
                return '<button data-v="' + o[0] + '" class="' + (c.transport === o[0] ? 'on' : '') + '">' + o[1] + '</button>';
            }).join('') + '</span></div>' +
            '<div class="cmr-two"><div class="cmr-field"><label>名称颜色</label><div data-seg="name_color">' +
            P.colors.map(function (col) {
                return '<span class="cmr-swatch' + (c.name_color === col ? ' on' : '') + '" data-v="' + col + '" style="background:' + col + '"></span>';
            }).join('') + '</div></div>' +
            '<div class="cmr-field"><label>名称位置</label><span class="cmr-seg" data-seg="name_corner">' +
            CORNERS.map(function (o) {
                return '<button data-v="' + o[0] + '" class="' + (c.name_corner === o[0] ? 'on' : '') + '">' + o[1] + '</button>';
            }).join('') + '</span></div></div></div>' +
            '<div class="cmr-head" style="border-top:1px solid #eef0f4;border-bottom:none"><button class="cmr-btn red" data-act="remove">从监控墙移除</button>' +
            '<span><button class="cmr-btn" data-act="close">取消</button> <button class="cmr-btn pri" data-act="apply">确定</button></span></div>';
        drawer.classList.add('open');
        $('mask').classList.add('open');
    }

    function closeDrawer() {
        editing = null;
        $('drawer').classList.remove('open');
        $('mask').classList.remove('open');
    }

    function applyDrawer() {
        var c = draft.cameras.filter(function (x) { return x.ip === editing; })[0], drawer = $('drawer');
        if (!c) { closeDrawer(); return; }
        function val(name) { return drawer.querySelector('[data-f="' + name + '"]').value; }
        function seg(name) { var on = drawer.querySelector('[data-seg="' + name + '"] .on'); return on ? on.getAttribute('data-v') : null; }
        c.display_name = val('display_name').trim();
        c.username = val('username').trim();
        if (val('password') !== '') { c.password = val('password'); c.password_set = true; }
        c.stream_mode = seg('stream_mode') || c.stream_mode;
        c.dahua_channel = Math.max(1, Math.min(256, parseInt(val('dahua_channel'), 10) || 1));
        c.manual_url = val('manual_url').trim();
        c.transport = seg('transport') || c.transport;
        c.name_color = seg('name_color') || c.name_color;
        c.name_corner = seg('name_corner') || c.name_corner;
        closeDrawer();
        touch();
    }

    // ---------- 事件 ----------

    root.addEventListener('click', function (e) {
        var el = e.target.closest('[data-act], [data-seg] [data-v], .cmr-tile');
        if (!el || !root.contains(el)) { return; }
        if (el.matches('[data-seg] [data-v]')) {
            el.parentNode.querySelectorAll('[data-v]').forEach(function (x) { x.classList.remove('on'); });
            el.classList.add('on');
            return;
        }
        if (el.classList.contains('cmr-tile')) { openDrawer(el.getAttribute('data-ip')); return; }
        var act = el.getAttribute('data-act'), ip = el.getAttribute('data-ip'), value = el.getAttribute('data-value');
        if (act === 'capture') { command('capture_screen'); }
        else if (act === 'reconnect') { command('reconnect_all'); }
        else if (act === 'restart') { if (window.confirm('现场软件会关闭再打开，画面中断约 10 秒。确定吗？')) { command('restart_app'); } }
        else if (act === 'scan') { command('scan'); }
        else if (act === 'scan-ip') { var target = window.prompt('请输入摄像头 IP 地址（例如 192.168.2.216）'); if (target) { command('scan', {target_ip: target.trim()}); } }
        else if (act === 'capacity') { setCapacity(parseInt(value, 10)); }
        else if (act === 'columns') {
            var n = parseInt(value, 10), cols = clone(draft.layout.columns || {});
            if (n) { cols[draft.layout.capacity] = n; } else { delete cols[draft.layout.capacity]; }
            draft.layout.columns = cols; touch();
        }
        else if (act === 'save') { save(); }
        else if (act === 'discard') { discard(); }
        else if (act === 'reload') { dirty = 0; load(); }
        else if (act === 'add') { addFound(ip); }
        else if (act === 'edit') { openDrawer(ip); }
        else if (act === 'close') { closeDrawer(); }
        else if (act === 'apply') { applyDrawer(); }
        else if (act === 'remove') {
            draft.cameras = draft.cameras.filter(function (c) { return c.ip !== editing; });
            closeDrawer(); touch();
        }
        else if (act === 'save-defaults') {
            var body = {default_username: root.querySelector('[data-field="default_username"]').value};
            var pw = root.querySelector('[data-field="default_password"]').value;
            if (pw !== '') { body.default_password = pw; }
            document.activeElement.blur();
            saveSettings(body);
        }
        else if (act === 'save-escape') {
            var escape = root.querySelector('[data-field="escape_password"]').value;
            if (!escape.trim()) { toast('warning', '请输入新的维护密码'); return; }
            document.activeElement.blur();
            saveSettings({escape_password: escape});
        }
        else if (act === 'to-full') { switchToFull(); }
    });

    $('mask').addEventListener('click', closeDrawer);

    root.addEventListener('change', function (e) {
        var act = e.target.getAttribute('data-act');
        if (act === 'fill') { draft.layout.fill_width = e.target.checked; touch(); }
        else if (act === 'fullscreen') { setFullscreen(e.target.checked); }
        else if (act === 'organization') { draft.layout.organization = e.target.value.trim(); touch(); }
    });

    root.addEventListener('dragstart', function (e) {
        var tile = e.target.closest('.cmr-tile'), row = e.target.closest('[data-found]');
        if (tile) { e.dataTransfer.setData('text/plain', 'tile:' + tile.getAttribute('data-ip')); tile.classList.add('dragging'); }
        else if (row) { e.dataTransfer.setData('text/plain', 'found:' + row.getAttribute('data-found')); }
    });
    root.addEventListener('dragend', function (e) {
        var tile = e.target.closest('.cmr-tile');
        if (tile) { tile.classList.remove('dragging'); }
    });
    root.addEventListener('dragover', function (e) {
        var target = e.target.closest('[data-slot]');
        if (target) { e.preventDefault(); target.classList.add('over'); }
    });
    root.addEventListener('dragleave', function (e) {
        var target = e.target.closest('[data-slot]');
        if (target) { target.classList.remove('over'); }
    });
    root.addEventListener('drop', function (e) {
        var target = e.target.closest('[data-slot]');
        if (!target) { return; }
        e.preventDefault();
        target.classList.remove('over');
        var data = e.dataTransfer.getData('text/plain'), slot = parseInt(target.getAttribute('data-slot'), 10);
        if (data.indexOf('tile:') === 0) { moveTo(data.slice(5), slot); }
        else if (data.indexOf('found:') === 0) { addFound(data.slice(6), slot); }
    });

    load();
    setInterval(function () { if (!document.hidden) { load(); } }, 2000);
})();
</script>
</div>
````

- [ ] **Step 2: 用 JS 语法检查器检查模板里的脚本**

Blade 模板本机渲染不了，单独把 `<script>` 抽出来，用 Node 检查语法：

```bash
python3 - <<'EOF'
import re, subprocess
src = open('resources/views/admin/camera-monitor/remote.blade.php').read()
js = re.search(r'<script>(.*)</script>', src, re.S).group(1).replace('@json($payload)', '{}')
open('/tmp/cmr-check.js', 'w').write(js)
print(subprocess.run(['node', '--check', '/tmp/cmr-check.js'], capture_output=True, text=True))
EOF
```

Expected: `returncode=0`，`stderr` 为空

- [ ] **Step 3: 提交**

```bash
git add resources/views/admin/camera-monitor/remote.blade.php
git commit -m "feat(camera-monitor): 远程控制页——监控墙编排、远程搜索、截图与设备设置

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 列表、表单、详情页的入口

**Files:**
- Create: `app/Admin/RowActions/CameraMonitor/SwitchToManaged.php`
- Modify: `app/Admin/Controllers/CameraMonitor/CameraMonitorProfileController.php`

- [ ] **Step 1: 行操作"切换为傻瓜模式"**

```php
<?php

namespace App\Admin\RowActions\CameraMonitor;

use App\Models\CameraMonitor\CameraMonitorProfile;
use App\Services\CameraMonitor\CameraMonitorRemoteService;
use Dcat\Admin\Admin;
use Dcat\Admin\Grid\RowAction;
use InvalidArgumentException;

/**
 * 把完整模式的设备码交给后台托管。现场软件低于 0.9.0 时拒绝，见 CameraMonitorManaged::switchBlocker。
 */
class SwitchToManaged extends RowAction
{
    public function title()
    {
        return '切换为傻瓜模式';
    }

    public function confirm()
    {
        return [
            '把「' . $this->row->name . '」切换为傻瓜模式？',
            '现场电脑将只显示监控画面，不能再做任何设置；会按后台设置自动全屏并设为开机自动运行；'
            . '现有的摄像头、顺序和账号密码原样保留；之后所有配置都在「远程控制」页完成，随时可以切回完整模式。',
        ];
    }

    public function handle($input)
    {
        /** @var CameraMonitorProfile|null $profile */
        $profile = CameraMonitorProfile::query()->whereKey($this->getKey())->first();
        if (!$profile) {
            return $this->response()->error('监控档案不存在');
        }

        try {
            app(CameraMonitorRemoteService::class)->switchMode($profile, 'managed', Admin::user() ? (int) Admin::user()->id : null);
        } catch (InvalidArgumentException $exception) {
            return $this->response()->error($exception->getMessage());
        }

        return $this->response()
            ->success('已切换为傻瓜模式')
            ->redirect('camera_monitor_profile/' . $profile->id . '/remote');
    }

    public function parameters()
    {
        return [];
    }
}
```

- [ ] **Step 2: 列表**

在 `CameraMonitorProfileController::grid()` 中：

1. `$grid->column('name', ...)` 之后加一列：

```php
            $grid->column('mode', '模式')->display(function ($value) {
                return \App\Services\CameraMonitor\CameraMonitorManaged::normalizeMode($value) === 'managed'
                    ? '<span class="cm-pill cm-pill-on">傻瓜模式</span>'
                    : '<span class="cm-pill cm-pill-off">完整模式</span>';
            });
```

2. `$grid->actions(...)` 替换为：

```php
            $grid->actions(function (Grid\Displayers\Actions $actions) {
                if (\App\Services\CameraMonitor\CameraMonitorManaged::normalizeMode($actions->row->mode) === 'managed') {
                    $actions->prepend('<a href="' . admin_url('camera_monitor_profile/' . $actions->getKey() . '/remote')
                        . '" style="font-weight:600">远程控制</a>&nbsp;&nbsp;');
                } else {
                    $actions->append(new SwitchToManaged());
                }
                $actions->append(new ReleaseBinding());
                $actions->append(new ResetAuthCode());
            });
```

3. 文件顶部加 `use App\Admin\RowActions\CameraMonitor\SwitchToManaged;`。

- [ ] **Step 3: 新建档案时可以直接选模式**

`form()` 中，`$form->switch('status', '启用')->default(1);` 之前加：

```php
            // 只在新建时可选：已有档案切换模式要过版本门槛，走列表的「切换为傻瓜模式」或远程控制页
            if ($form->isCreating()) {
                $form->radio('mode', '模式')
                    ->options(['full' => '完整模式（现场自己配置）', 'managed' => '傻瓜模式（后台远程配置）'])
                    ->default('managed')
                    ->help('傻瓜模式：现场只输入一次设备码，之后所有配置都在后台「远程控制」页完成。');
            } else {
                $form->display('mode', '模式')->with(function ($value) {
                    return \App\Services\CameraMonitor\CameraMonitorManaged::normalizeMode($value) === 'managed' ? '傻瓜模式' : '完整模式';
                });
            }
```

> 如果 Dcat 在编辑时也提交了 `mode` 字段，`display` 字段不会落库。保存后用"编辑一个完整模式档案、保存、看模式没变"的方式验收。

- [ ] **Step 4: 详情页入口**

`detailBody()` 中，`return '<div class="cm-detail">'` 之后、`. $this->heroCard($profile)` 之前插入：

```php
            . ($profile->isManaged()
                ? '<div class="cm-note"><a href="' . admin_url('camera_monitor_profile/' . $profile->id . '/remote')
                    . '" style="font-weight:600">此点位为傻瓜模式，摄像头和布局请在「远程控制」页修改 →</a></div>'
                : '')
```

- [ ] **Step 5: 更新类注释**

`CameraMonitorProfileController` 的类注释中，"摄像头参数本身不在后台编辑……"那一段替换为：

```php
 * 两种模式：
 * - 完整模式：配置以客户端为准，现场拖拽就是编辑方式，后台只读——再做一套表单会和客户端争抢版本号。
 * - 傻瓜模式：只有后台能写配置（客户端的 PUT 会被拒绝），编辑在 CameraMonitorRemoteController 的远程控制页。
 * 任何时候只有一方在写，所以版本号机制不需要改。
```

- [ ] **Step 6: 语法检查并跑全部 CameraMonitor 测试**

```bash
php -l app/Admin/RowActions/CameraMonitor/SwitchToManaged.php
php -l app/Admin/Controllers/CameraMonitor/CameraMonitorProfileController.php
for f in tests/Unit/CameraMonitor*Test.php; do out=$(php vendor/bin/phpunit "$f" 2>&1) || { echo "$out"; echo "FAILED: $f"; break; }; done && echo ALL-OK
```

- [ ] **Step 7: 提交**

```bash
git add app/Admin/RowActions/CameraMonitor/SwitchToManaged.php app/Admin/Controllers/CameraMonitor/CameraMonitorProfileController.php
git commit -m "feat(camera-monitor): 列表、新建表单和详情页加入傻瓜模式入口

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 上线验收清单

**Files:**
- Modify: `docs/运维/camera-monitor-ws.md`（计划 A 已建）

- [ ] **Step 1: 在运维文档末尾加一节**

```markdown
## 傻瓜模式上线验收（后台 + 客户端 0.9.0）

准备：一台 Windows 电脑（装 0.9.0），同一内网有 2 台摄像头，其中至少 1 台的账号密码已知。

1. 新建设备码，模式选「傻瓜模式」，列表里出现「远程控制」按钮。
2. 现场电脑输入设备码，看到「已连接云端 · 正在等待管理员配置摄像头」，尾号和后台一致；后台显示在线。
3. 远程控制页点「搜索摄像头」，约 10 秒后列表出现 2 台；免密能截到图的那台显示截图。
4. 「设备设置」填默认账号密码并保存；把两台都加入监控墙，「保存并下发」，现场 1 秒内出现画面；监控墙下方状态变为「播放中」，截图自动补上。
5. 拖动交换两路，再保存，现场顺序随之改变；顶部显示「✓ 已是最新配置」。
6. 关闭「全屏显示」，现场退出全屏但仍然只有监控墙；再打开，现场回到全屏。
7. 故意把一路密码改错并下发，状态变为「账号密码错误」。
8. 「截取大屏画面」能看到现场当前画面；「全部重连」「重启软件」生效，重启后自动回到原画面。
9. 修改维护密码，现场按 Ctrl+Shift+Alt+Q 输入新密码能退出；输入旧密码提示「密码不正确」。
10. 拔掉现场网线：后台约 90 秒后显示离线，远程按钮置灰；现场画面继续播放，10 秒后右上角出现断线提示。接回网线后自动恢复。
11. 断电重启现场电脑：自动打开、自动全屏、画面恢复。
12. 后台「解绑电脑」：现场立刻回到输入设备码的页面。
13. 用 0.8.3 客户端登录一个完整模式的码，再在列表里点「切换为傻瓜模式」：提示「现场软件版本过低（0.8.3）…」并拒绝。
14. 两个浏览器同时打开同一个远程控制页，A 保存后 B 再保存：B 提示「配置已被他人修改」。
15. 查 `camera_monitor_action_log`：以上操作都有记录，`summary` 里没有任何密码。
```

- [ ] **Step 2: 提交并推送**

```bash
git add docs/运维/camera-monitor-ws.md
git commit -m "docs(camera-monitor): 傻瓜模式上线验收清单

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```

**不要合并到 main**：何时合并、何时部署，由用户决定（推 main 就等于上线）。
