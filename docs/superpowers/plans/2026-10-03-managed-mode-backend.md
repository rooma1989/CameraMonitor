# 傻瓜模式 · yunqi 后端与下行通道 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** yunqi 支持托管模式的数据与接口（快照里的新字段、托管时拒绝客户端写配置、截图上传），并新建 `camera-monitor:ws` 常驻进程，让后台的消息 1 秒内送到现场电脑。

**Architecture:**

- 所有规则写成不依赖框架的纯类（`CameraMonitorManaged`、`CameraMonitorSnapshotRules`、`CameraMonitorChannelRouter`），用 PHPUnit 纯契约测试覆盖。
- Eloquent、Storage、Workerman 只出现在很薄的一层接线代码里，这部分本机跑不起来，上线后按验收清单检查。
- WebSocket 进程只有一个 worker。它在同一进程内额外监听 `127.0.0.1:16506`，供 PHP-FPM 调用来发消息。

**Tech Stack:** Laravel 5.8、PHP 7.4（线上）、Workerman 4.1.15（已在 composer.json）、PHPUnit（`vendor/bin/phpunit`）。

**Spec:** CameraMonitor 仓库的 `docs/superpowers/specs/2026-10-03-managed-mode-design.md`，第 3、5、6、7、9、10.2、10.3 节。

**仓库：** `/Users/rooma/Projects/云栖/yunqi`。线上部署方式是"推 main 分支"，**本计划全程在独立分支上做，不碰 main**。

---

## 约定

- **本机限制**：PHP 8.3 跑不起 Laravel 5.8，`php artisan` 和继承 `Tests\TestCase` 的测试都会致命错误，连实例化模型也会失败。因此：
  - 测试一律 `extends PHPUnit\Framework\TestCase`，只测纯类；
  - 碰数据库、Storage、Workerman 的代码只做 `php -l` 语法检查，上线后验收。
- 跑测试：`php vendor/bin/phpunit tests/Unit/XxxTest.php`
- 跑全部 CameraMonitor 测试（**不能**用 `--filter` 跑整个目录：别的测试文件依赖 Laravel，本机一加载就致命错误）：
  ```bash
  for f in tests/Unit/CameraMonitor*Test.php; do out=$(php vendor/bin/phpunit "$f" 2>&1) || { echo "$out"; echo "FAILED: $f"; break; }; done && echo ALL-OK
  ```
  下文写"跑全部 CameraMonitor 测试"时，指的就是这条命令。
- 迁移命名和写法跟随 `database/migrations/2026_09_22_*`：单数表名、每列写 `comment`、用 `hasTable` / `hasColumn` 做幂等保护、命名索引。线上按 `--path` **逐个**执行，不能跑全量。
- 新表的模型**不继承** `App\Models\Base`。原因：Base 带了 `SoftDeletes`，新表没有 `deleted_at`，查询会直接报错。新模型继承 `Illuminate\Database\Eloquent\Model`。
- 提交信息用中文，结尾加：
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/Services/CameraMonitor/CameraMonitorManaged.php` | 新 | 模式、客户端版本门槛、维护密码记录、快照里的托管字段 |
| `app/Services/CameraMonitor/CameraMonitorPayload.php` | 改 | `layout.fullscreen`；公开 `ip()` |
| `app/Services/CameraMonitor/CameraMonitorSessionVerifier.php` | 新 | 令牌、设备码、绑定的校验。HTTP 中间件和 WebSocket 共用 |
| `app/Http/Middleware/CameraMonitorAuth.php` | 改 | 改为调用 SessionVerifier |
| `app/Services/CameraMonitor/CameraMonitorConfigService.php` | 改 | 快照带上托管字段 |
| `app/Http/Controllers/Api/CameraMonitor/CameraMonitorConfigController.php` | 改 | 托管点位拒绝 `PUT /config` |
| `app/Services/CameraMonitor/CameraMonitorSnapshotRules.php` | 新 | 截图校验与存储路径 |
| `app/Http/Controllers/Api/CameraMonitor/CameraMonitorSnapshotController.php` | 新 | `POST /snapshots` |
| `app/Services/CameraMonitor/Channel/*` | 新 | 连接状态、消息路由、存储接口及其 DB 实现、PHP-FPM 侧的发送器 |
| `app/Console/Commands/CameraMonitorWebSocketCommand.php` | 新 | Workerman 进程 |
| `app/Models/CameraMonitor/*` | 新/改 | 4 个新模型；Profile、Client 的 fillable |
| `database/migrations/2026_10_03_1000*_*.php` | 新 | 5 个迁移 |
| `config/camera_monitor.php`、`.env.example` | 改 | 通道端口和内部密钥 |
| 后台三个解绑或停用入口 | 改 | 推送 `revoked` |
| `supervisor/camera-monitor-ws.conf`、`docs/运维/camera-monitor-ws.md` | 新 | 部署 |

---

### Task 0: 建分支和工作目录

**Files:** 无

- [ ] **Step 1: 从最新的 origin/main 开一个 worktree**

本机的 yunqi 停在 main，落后远端很多提交，还有未提交的安装包删除，**不要动它**。

```bash
cd /Users/rooma/Projects/云栖/yunqi
git fetch origin
git worktree add ../yunqi-managed-mode -b feat/camera-monitor-managed-mode origin/main
cd ../yunqi-managed-mode
```

- [ ] **Step 2: 复制 vendor（不能用软链）**

composer 的 autoload 会按 vendor 的真实路径推算项目根目录。用软链的话，测试加载到的会是旧目录里的 `App\` 类。

```bash
cp -Rc ../yunqi/vendor ./vendor
php vendor/bin/phpunit tests/Unit/CameraMonitorBindingTest.php
```

Expected: `OK (5 tests, 9 assertions)`

以下所有步骤都在 `/Users/rooma/Projects/云栖/yunqi-managed-mode` 下执行。

---

### Task 1: 托管规则（纯类）

**Files:**
- Create: `app/Services/CameraMonitor/CameraMonitorManaged.php`
- Test: `tests/Unit/CameraMonitorManagedTest.php`

- [ ] **Step 1: 写失败的测试**

```php
<?php

namespace Tests\Unit;

use App\Services\CameraMonitor\CameraMonitorManaged;
use InvalidArgumentException;
use PHPUnit\Framework\TestCase;

class CameraMonitorManagedTest extends TestCase
{
    /** 与客户端 tests/test_managed_lock.py 里的 VECTOR 完全相同：盐 00..0f，密码 246810 */
    private const VECTOR = 'pbkdf2_sha256$240000$000102030405060708090a0b0c0d0e0f$'
        . '6779a48d726cc20519aa5f663a475da180d9ddcf213ec794eef28f7ed9f9bc4e';

    private function salt(): string
    {
        return implode('', array_map('chr', range(0, 15)));
    }

    public function testTheEscapeRecordMatchesThePythonClientByteForByte()
    {
        $this->assertSame(self::VECTOR, CameraMonitorManaged::escapeRecord('246810', $this->salt()));
    }

    public function testEveryRecordGetsAFreshSalt()
    {
        $this->assertNotSame(
            CameraMonitorManaged::escapeRecord('246810'),
            CameraMonitorManaged::escapeRecord('246810')
        );
    }

    public function testAnEmptyEscapePasswordIsRefused()
    {
        $this->expectException(InvalidArgumentException::class);
        CameraMonitorManaged::escapeRecord('   ');
    }

    public function testRecordValidation()
    {
        $this->assertTrue(CameraMonitorManaged::validEscapeRecord(self::VECTOR));
        $this->assertFalse(CameraMonitorManaged::validEscapeRecord(null));
        $this->assertFalse(CameraMonitorManaged::validEscapeRecord('pbkdf2_sha256$1000$00$11'));
        $this->assertFalse(CameraMonitorManaged::validEscapeRecord(substr(self::VECTOR, 0, -2)));
    }

    public function testModesFallBackToFull()
    {
        $this->assertSame('managed', CameraMonitorManaged::normalizeMode('managed'));
        $this->assertSame('full', CameraMonitorManaged::normalizeMode('kiosk'));
        $this->assertSame('full', CameraMonitorManaged::normalizeMode(null));
        $this->assertTrue(CameraMonitorManaged::rejectsClientWrites('managed'));
        $this->assertFalse(CameraMonitorManaged::rejectsClientWrites('full'));
    }

    public function testClientVersionGate()
    {
        $this->assertFalse(CameraMonitorManaged::clientSupportsManaged('0.8.3'));
        $this->assertTrue(CameraMonitorManaged::clientSupportsManaged('0.9.0'));
        $this->assertTrue(CameraMonitorManaged::clientSupportsManaged('0.10.0'), '按数字比，不按字符串');
        $this->assertTrue(CameraMonitorManaged::clientSupportsManaged('1.0'));
        $this->assertFalse(CameraMonitorManaged::clientSupportsManaged(''));
        $this->assertFalse(CameraMonitorManaged::clientSupportsManaged('dev'));
    }

    public function testSwitchingToManagedNeedsANewEnoughClient()
    {
        $this->assertNull(CameraMonitorManaged::switchBlocker('managed', null, null), '还没绑电脑的可以直接切');
        $this->assertNull(CameraMonitorManaged::switchBlocker('managed', 'uid-a', '0.9.0'));
        $this->assertNull(CameraMonitorManaged::switchBlocker('full', 'uid-a', '0.8.3'), '切回完整模式不设门槛');
        $this->assertSame(
            '现场软件版本过低（0.8.3），请先升级到 0.9.0 或以上',
            CameraMonitorManaged::switchBlocker('managed', 'uid-a', '0.8.3')
        );
    }

    public function testSnapshotFields()
    {
        $fields = CameraMonitorManaged::snapshotFields('managed', self::VECTOR, 'admin', 'pw');
        $this->assertSame([
            'mode' => 'managed',
            'escape_password_hash' => self::VECTOR,
            'default_credentials' => ['username' => 'admin', 'password' => 'pw'],
        ], $fields);

        $empty = CameraMonitorManaged::snapshotFields(null, 'garbage', null, null);
        $this->assertSame('full', $empty['mode']);
        $this->assertNull($empty['escape_password_hash']);
        $this->assertSame(['username' => '', 'password' => ''], $empty['default_credentials'],
            '清空默认账号也要下发空值，客户端才会把钥匙串里那份删掉');
    }
}
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorManagedTest.php`
Expected: ERROR `Class "App\Services\CameraMonitor\CameraMonitorManaged" not found`

- [ ] **Step 3: 实现**

```php
<?php

namespace App\Services\CameraMonitor;

use InvalidArgumentException;

/**
 * 傻瓜模式（云端托管）的规则。
 *
 * 纯静态、不碰 Eloquent：本机 PHP 8.3 跑不起 Laravel 5.8，只有脱离框架的逻辑才测得了。
 */
class CameraMonitorManaged
{
    public const MODE_FULL = 'full';
    public const MODE_MANAGED = 'managed';
    public const MODES = [self::MODE_FULL, self::MODE_MANAGED];

    /** 低于这个版本的客户端不认识托管：切过去后它的上传一直被拒，现场却看不出异常 */
    public const MIN_CLIENT_VERSION = '0.9.0';

    /** 与客户端 screen_lock.ScreenLock 一致，两边才能互相验证 */
    public const ESCAPE_ALGORITHM = 'pbkdf2_sha256';
    public const ESCAPE_ITERATIONS = 240000;

    public static function normalizeMode($value): string
    {
        $mode = (string) $value;

        return in_array($mode, self::MODES, true) ? $mode : self::MODE_FULL;
    }

    public static function rejectsClientWrites($mode): bool
    {
        return self::normalizeMode($mode) === self::MODE_MANAGED;
    }

    public static function clientSupportsManaged(?string $appVersion): bool
    {
        $version = trim((string) $appVersion);
        if (preg_match('/^\d+(\.\d+){0,3}$/', $version) !== 1) {
            return false;
        }

        return version_compare($version, self::MIN_CLIENT_VERSION, '>=');
    }

    /**
     * @return string|null 不能切换时返回给运营看的原因
     */
    public static function switchBlocker(string $target, ?string $boundClientUid, ?string $boundAppVersion): ?string
    {
        if (self::normalizeMode($target) !== self::MODE_MANAGED) {
            return null;
        }
        // 还没有电脑用过这个码：第一个来登录的必然是新版本
        if (trim((string) $boundClientUid) === '') {
            return null;
        }
        if (self::clientSupportsManaged($boundAppVersion)) {
            return null;
        }

        $current = trim((string) $boundAppVersion) ?: '未知';

        return '现场软件版本过低（' . $current . '），请先升级到 ' . self::MIN_CLIENT_VERSION . ' 或以上';
    }

    public static function escapeRecord(string $password, ?string $salt = null): string
    {
        if (trim($password) === '') {
            throw new InvalidArgumentException('维护密码不能为空');
        }
        if (mb_strlen($password) > 64) {
            throw new InvalidArgumentException('维护密码不能超过 64 个字符');
        }
        $salt = $salt ?? random_bytes(16);
        if (strlen($salt) !== 16) {
            throw new InvalidArgumentException('salt must be 16 bytes');
        }
        $digest = hash_pbkdf2('sha256', $password, $salt, self::ESCAPE_ITERATIONS, 32, true);

        return self::ESCAPE_ALGORITHM . '$' . self::ESCAPE_ITERATIONS . '$' . bin2hex($salt) . '$' . bin2hex($digest);
    }

    public static function validEscapeRecord($record): bool
    {
        $parts = explode('$', (string) $record);
        if (count($parts) !== 4) {
            return false;
        }
        [$algorithm, $rounds, $salt, $digest] = $parts;

        return $algorithm === self::ESCAPE_ALGORITHM
            && $rounds === (string) self::ESCAPE_ITERATIONS
            && preg_match('/^[0-9a-f]{32}$/', $salt) === 1
            && preg_match('/^[0-9a-f]{64}$/', $digest) === 1;
    }

    /**
     * 快照里的托管字段。默认密码的明文只出现在这里，经已鉴权的接口下发。
     */
    public static function snapshotFields($mode, ?string $escapeHash, ?string $defaultUsername, ?string $defaultPassword): array
    {
        return [
            'mode' => self::normalizeMode($mode),
            'escape_password_hash' => self::validEscapeRecord($escapeHash) ? (string) $escapeHash : null,
            'default_credentials' => [
                'username' => trim((string) $defaultUsername),
                'password' => (string) $defaultPassword,
            ],
        ];
    }
}
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorManagedTest.php`
Expected: `OK (8 tests, ...)`

- [ ] **Step 5: 提交**

```bash
git add app/Services/CameraMonitor/CameraMonitorManaged.php tests/Unit/CameraMonitorManagedTest.php
git commit -m "feat(camera-monitor): 托管模式规则——模式、版本门槛、维护密码记录

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 布局里加上全屏开关；公开 IP 校验

**Files:**
- Modify: `app/Services/CameraMonitor/CameraMonitorPayload.php`
- Test: `tests/Unit/CameraMonitorLayoutFullscreenTest.php`

- [ ] **Step 1: 写失败的测试**

```php
<?php

namespace Tests\Unit;

use App\Services\CameraMonitor\CameraMonitorPayload;
use PHPUnit\Framework\TestCase;

class CameraMonitorLayoutFullscreenTest extends TestCase
{
    public function testFullscreenDefaultsToOff()
    {
        $this->assertFalse(CameraMonitorPayload::normalizeLayout([])['fullscreen']);
    }

    public function testFullscreenAcceptsTheUsualTruthyValues()
    {
        $this->assertTrue(CameraMonitorPayload::normalizeLayout(['fullscreen' => true])['fullscreen']);
        $this->assertTrue(CameraMonitorPayload::normalizeLayout(['fullscreen' => 'true'])['fullscreen']);
        $this->assertTrue(CameraMonitorPayload::normalizeLayout(['fullscreen' => 1])['fullscreen']);
        $this->assertFalse(CameraMonitorPayload::normalizeLayout(['fullscreen' => 'false'])['fullscreen']);
    }

    public function testIpValidationIsReusable()
    {
        $this->assertSame('192.168.2.216', CameraMonitorPayload::ip(' 192.168.2.216 '));
        $this->assertNull(CameraMonitorPayload::ip('239.255.255.250'), '组播地址不是摄像头');
        $this->assertNull(CameraMonitorPayload::ip('not-an-ip'));
    }
}
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorLayoutFullscreenTest.php`
Expected: FAIL `Undefined array key "fullscreen"`；`Call to undefined method ...::ip()`

- [ ] **Step 3: 实现**

在 `normalizeLayout` 的返回数组里，`'fill_width' => ...` 之后加一行：

```php
            'fullscreen' => self::toBool($raw['fullscreen'] ?? false),
```

在 `normalizeCameras` 方法之前加：

```php
    /** 给通道和截图接口复用的 IPv4 校验，规则与摄像头列表完全相同 */
    public static function ip($value): ?string
    {
        return self::normalizeIp($value);
    }
```

- [ ] **Step 4: 跑测试，确认通过**

Run: 跑全部 CameraMonitor 测试（见"约定"里的命令）
Expected: 全部 OK。现有的布局测试是逐个字段断言的，不受新字段影响。

- [ ] **Step 5: 提交**

```bash
git add app/Services/CameraMonitor/CameraMonitorPayload.php tests/Unit/CameraMonitorLayoutFullscreenTest.php
git commit -m "feat(camera-monitor): 布局加入全屏开关，IP 校验可复用

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 迁移与模型

**Files:**
- Create: `database/migrations/2026_10_03_100000_add_camera_monitor_managed_mode.php`
- Create: `database/migrations/2026_10_03_100100_create_camera_monitor_discovered.php`
- Create: `database/migrations/2026_10_03_100200_create_camera_monitor_snapshot.php`
- Create: `database/migrations/2026_10_03_100300_create_camera_monitor_command.php`
- Create: `database/migrations/2026_10_03_100400_create_camera_monitor_action_log.php`
- Create: `app/Models/CameraMonitor/CameraMonitorDiscovered.php`、`CameraMonitorSnapshot.php`、`CameraMonitorCommand.php`、`CameraMonitorActionLog.php`
- Modify: `app/Models/CameraMonitor/CameraMonitorProfile.php`、`CameraMonitorClient.php`

- [ ] **Step 1: 第一个迁移——给 profile 和 client 加列**

`2026_10_03_100000_add_camera_monitor_managed_mode.php`：

```php
<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * 傻瓜模式：设备码可以交给后台托管，现场只输一次码。
 *
 * mode=full 时配置以客户端为准（原行为）；mode=managed 时只有后台能写配置。
 */
class AddCameraMonitorManagedMode extends Migration
{
    public function up()
    {
        if (Schema::hasTable('camera_monitor_profile')) {
            Schema::table('camera_monitor_profile', function (Blueprint $table) {
                if (!Schema::hasColumn('camera_monitor_profile', 'mode')) {
                    $table->string('mode', 16)->default('full')->after('status')
                        ->comment('full=现场自己配置；managed=傻瓜模式，后台托管');
                }
                if (!Schema::hasColumn('camera_monitor_profile', 'escape_password_hash')) {
                    $table->string('escape_password_hash', 160)->nullable()->after('mode')
                        ->comment('维护密码记录，格式与客户端 ScreenLock 相同；空表示沿用客户端本机密码');
                }
                if (!Schema::hasColumn('camera_monitor_profile', 'default_username')) {
                    $table->string('default_username', 64)->nullable()->after('escape_password_hash')
                        ->comment('该点位摄像头的默认账号');
                }
                if (!Schema::hasColumn('camera_monitor_profile', 'default_password_encrypted')) {
                    $table->text('default_password_encrypted')->nullable()->after('default_username')
                        ->comment('该点位摄像头的默认密码密文');
                }
            });
        }

        if (Schema::hasTable('camera_monitor_client')) {
            Schema::table('camera_monitor_client', function (Blueprint $table) {
                if (!Schema::hasColumn('camera_monitor_client', 'runtime_status')) {
                    $table->text('runtime_status')->nullable()->after('last_ip')
                        ->comment('客户端最近一次回报的运行状态 JSON');
                }
                if (!Schema::hasColumn('camera_monitor_client', 'runtime_status_at')) {
                    $table->timestamp('runtime_status_at')->nullable()->after('runtime_status')
                        ->comment('运行状态回报时间');
                }
                if (!Schema::hasColumn('camera_monitor_client', 'channel_connected_at')) {
                    $table->timestamp('channel_connected_at')->nullable()->after('runtime_status_at')
                        ->comment('下行通道最近一次连上的时间');
                }
                if (!Schema::hasColumn('camera_monitor_client', 'channel_disconnected_at')) {
                    $table->timestamp('channel_disconnected_at')->nullable()->after('channel_connected_at')
                        ->comment('下行通道最近一次断开的时间');
                }
            });
        }
    }

    public function down()
    {
        foreach ([
            'camera_monitor_profile' => ['mode', 'escape_password_hash', 'default_username', 'default_password_encrypted'],
            'camera_monitor_client' => ['runtime_status', 'runtime_status_at', 'channel_connected_at', 'channel_disconnected_at'],
        ] as $tableName => $columns) {
            if (!Schema::hasTable($tableName)) {
                continue;
            }
            Schema::table($tableName, function (Blueprint $table) use ($tableName, $columns) {
                foreach ($columns as $column) {
                    if (Schema::hasColumn($tableName, $column)) {
                        $table->dropColumn($column);
                    }
                }
            });
        }
    }
}
```

- [ ] **Step 2: 第二个迁移——搜索结果表**

`2026_10_03_100100_create_camera_monitor_discovered.php`：

```php
<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * 现场电脑最近一次远程搜索到的摄像头。每次搜索结果整体覆盖上一次。
 */
class CreateCameraMonitorDiscovered extends Migration
{
    public function up()
    {
        if (Schema::hasTable('camera_monitor_discovered')) {
            return;
        }

        Schema::create('camera_monitor_discovered', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->unsignedBigInteger('profile_id')->comment('所属监控档案ID');
            $table->string('ip', 45)->comment('摄像头 IP');
            $table->string('model', 120)->default('')->comment('型号');
            $table->string('manufacturer', 120)->default('')->comment('厂家');
            $table->text('protocols')->nullable()->comment('支持的协议 JSON');
            $table->text('onvif_urls')->nullable()->comment('ONVIF 地址 JSON');
            $table->timestamp('discovered_at')->nullable()->comment('搜索到的时间');
            $table->timestamps();

            $table->unique(['profile_id', 'ip'], 'cm_discovered_profile_ip_uniq');
        });
    }

    public function down()
    {
        Schema::dropIfExists('camera_monitor_discovered');
    }
}
```

- [ ] **Step 3: 第三个迁移——截图表**

`2026_10_03_100200_create_camera_monitor_snapshot.php`：

```php
<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * 客户端上传的截图。每路摄像头一张、整屏一张，新的直接覆盖旧的。
 * 整屏截图的 ip 存空串而不是 NULL：唯一键里 NULL 不参与比较，会留下多行。
 */
class CreateCameraMonitorSnapshot extends Migration
{
    public function up()
    {
        if (Schema::hasTable('camera_monitor_snapshot')) {
            return;
        }

        Schema::create('camera_monitor_snapshot', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->unsignedBigInteger('profile_id')->comment('所属监控档案ID');
            $table->string('kind', 16)->comment('camera=单路截图；screen=整屏截图');
            $table->string('ip', 45)->default('')->comment('摄像头 IP，整屏截图为空串');
            $table->string('path', 255)->comment('storage/app 下的相对路径');
            $table->timestamp('captured_at')->nullable()->comment('截取时间');
            $table->timestamps();

            $table->unique(['profile_id', 'kind', 'ip'], 'cm_snapshot_profile_kind_ip_uniq');
        });
    }

    public function down()
    {
        Schema::dropIfExists('camera_monitor_snapshot');
    }
}
```

- [ ] **Step 4: 第四个迁移——命令表**

`2026_10_03_100300_create_camera_monitor_command.php`：

```php
<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * 后台下发给现场电脑的一次性动作。
 *
 * 只用来显示「已送达 / 执行中 / 完成 / 失败」：WebSocket 进程和 PHP-FPM 是两个进程，
 * 需要一个双方都能读写的地方。设备离线时直接记为未送达，不排队补发。
 */
class CreateCameraMonitorCommand extends Migration
{
    public function up()
    {
        if (Schema::hasTable('camera_monitor_command')) {
            return;
        }

        Schema::create('camera_monitor_command', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->char('uuid', 36)->comment('下发给客户端的命令ID');
            $table->unsignedBigInteger('profile_id')->comment('所属监控档案ID');
            $table->unsignedBigInteger('admin_user_id')->nullable()->comment('操作人');
            $table->string('name', 32)->comment('动作名：scan / refresh_snapshots / capture_screen / reconnect_all / restart_app');
            $table->text('args')->nullable()->comment('动作参数 JSON');
            $table->boolean('delivered')->default(false)->comment('是否送达现场电脑');
            $table->timestamp('acked_at')->nullable()->comment('客户端确认收到的时间');
            $table->timestamp('finished_at')->nullable()->comment('客户端执行完的时间');
            $table->boolean('ok')->nullable()->comment('执行结果');
            $table->string('error', 64)->nullable()->comment('失败原因代码');
            $table->timestamps();

            $table->unique('uuid', 'cm_command_uuid_uniq');
            $table->index(['profile_id', 'created_at'], 'cm_command_profile_created_idx');
        });
    }

    public function down()
    {
        Schema::dropIfExists('camera_monitor_command');
    }
}
```

- [ ] **Step 5: 第五个迁移——操作日志表**

`2026_10_03_100400_create_camera_monitor_action_log.php`：

```php
<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * 后台对托管点位做的每一次写操作和远程动作。summary 里绝不记录任何密码。
 */
class CreateCameraMonitorActionLog extends Migration
{
    public function up()
    {
        if (Schema::hasTable('camera_monitor_action_log')) {
            return;
        }

        Schema::create('camera_monitor_action_log', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->unsignedBigInteger('profile_id')->comment('所属监控档案ID');
            $table->unsignedBigInteger('admin_user_id')->nullable()->comment('操作人');
            $table->string('action', 48)->comment('save_config / set_fullscreen / switch_mode / set_escape_password / set_default_credentials / command:<name> / revoke');
            $table->text('summary')->nullable()->comment('摘要 JSON，不含密码');
            $table->timestamp('created_at')->nullable();

            $table->index(['profile_id', 'created_at'], 'cm_action_log_profile_created_idx');
        });
    }

    public function down()
    {
        Schema::dropIfExists('camera_monitor_action_log');
    }
}
```

- [ ] **Step 6: 新模型**

`app/Models/CameraMonitor/CameraMonitorDiscovered.php`：

```php
<?php

namespace App\Models\CameraMonitor;

use Illuminate\Database\Eloquent\Model;

/** 不继承 Base：Base 带 SoftDeletes，这张表没有 deleted_at */
class CameraMonitorDiscovered extends Model
{
    protected $table = 'camera_monitor_discovered';

    protected $fillable = ['profile_id', 'ip', 'model', 'manufacturer', 'protocols', 'onvif_urls', 'discovered_at'];

    protected $casts = ['profile_id' => 'integer', 'protocols' => 'array', 'onvif_urls' => 'array'];

    protected $dates = ['discovered_at'];
}
```

`app/Models/CameraMonitor/CameraMonitorSnapshot.php`：

```php
<?php

namespace App\Models\CameraMonitor;

use Illuminate\Database\Eloquent\Model;

class CameraMonitorSnapshot extends Model
{
    protected $table = 'camera_monitor_snapshot';

    protected $fillable = ['profile_id', 'kind', 'ip', 'path', 'captured_at'];

    protected $casts = ['profile_id' => 'integer'];

    protected $dates = ['captured_at'];
}
```

`app/Models/CameraMonitor/CameraMonitorCommand.php`：

```php
<?php

namespace App\Models\CameraMonitor;

use Illuminate\Database\Eloquent\Model;

class CameraMonitorCommand extends Model
{
    protected $table = 'camera_monitor_command';

    protected $fillable = ['uuid', 'profile_id', 'admin_user_id', 'name', 'args', 'delivered',
        'acked_at', 'finished_at', 'ok', 'error'];

    protected $casts = ['profile_id' => 'integer', 'args' => 'array', 'delivered' => 'boolean', 'ok' => 'boolean'];

    protected $dates = ['acked_at', 'finished_at'];
}
```

`app/Models/CameraMonitor/CameraMonitorActionLog.php`：

```php
<?php

namespace App\Models\CameraMonitor;

use Illuminate\Database\Eloquent\Model;

class CameraMonitorActionLog extends Model
{
    public const UPDATED_AT = null;

    protected $table = 'camera_monitor_action_log';

    protected $fillable = ['profile_id', 'admin_user_id', 'action', 'summary'];

    protected $casts = ['profile_id' => 'integer', 'summary' => 'array'];
}
```

- [ ] **Step 7: 改已有的模型**

在 `CameraMonitorProfile` 的 `$fillable` 末尾加 `'mode'`、`'escape_password_hash'`、`'default_username'`、`'default_password_encrypted'`，并加一个方法：

```php
    public function isManaged(): bool
    {
        return \App\Services\CameraMonitor\CameraMonitorManaged::normalizeMode($this->mode)
            === \App\Services\CameraMonitor\CameraMonitorManaged::MODE_MANAGED;
    }
```

在 `CameraMonitorClient` 中：

- `$fillable` 末尾加 `'runtime_status'`、`'runtime_status_at'`、`'channel_connected_at'`、`'channel_disconnected_at'`；
- `$casts` 加 `'runtime_status' => 'array'`；
- `$dates` 改成 `['last_seen_at', 'runtime_status_at', 'channel_connected_at', 'channel_disconnected_at', 'deleted_at']`。

- [ ] **Step 8: 语法检查**

```bash
for f in database/migrations/2026_10_03_1000*.php app/Models/CameraMonitor/*.php; do php -l "$f" || break; done
```

Expected: 每个文件都输出 `No syntax errors detected`

- [ ] **Step 9: 提交**

```bash
git add database/migrations/2026_10_03_1000*.php app/Models/CameraMonitor/
git commit -m "feat(camera-monitor): 托管模式的表结构与模型

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 会话校验抽成服务

**Files:**
- Create: `app/Services/CameraMonitor/CameraMonitorSessionVerifier.php`
- Modify: `app/Http/Middleware/CameraMonitorAuth.php`
- Test: `tests/Unit/CameraMonitorSessionVerifierTest.php`

- [ ] **Step 1: 写失败的测试**

```php
<?php

namespace Tests\Unit;

use App\Services\CameraMonitor\CameraMonitorBinding;
use App\Services\CameraMonitor\CameraMonitorSessionVerifier;
use App\Services\CameraMonitor\CameraMonitorTokenService;
use PHPUnit\Framework\TestCase;

/** 只实现校验用到的那几个方法：本机实例化不了 Eloquent 模型 */
class FakeMonitorProfile
{
    public $id;
    public $version = 7;
    public $enabled = true;
    public $bound;

    public function __construct(int $id, ?string $bound = null)
    {
        $this->id = $id;
        $this->bound = $bound;
    }

    public function isEnabled(): bool
    {
        return $this->enabled;
    }

    public function acceptsClient(string $clientUid): bool
    {
        return CameraMonitorBinding::accepts($this->bound, $clientUid);
    }
}

class CameraMonitorSessionVerifierTest extends TestCase
{
    private $tokens;
    private $profiles = [];

    protected function setUp(): void
    {
        $this->tokens = new CameraMonitorTokenService('unit-test-signing-key', 3600);
        $this->profiles = [3 => new FakeMonitorProfile(3, 'uid-aaaa-1111')];
    }

    private function verifier(): CameraMonitorSessionVerifier
    {
        return new CameraMonitorSessionVerifier($this->tokens, function (int $id) {
            return $this->profiles[$id] ?? null;
        });
    }

    private function token(int $profileId = 3, string $uid = 'uid-aaaa-1111'): string
    {
        return $this->tokens->issue($profileId, $uid)['token'];
    }

    public function testAValidSessionPasses()
    {
        $result = $this->verifier()->verify($this->token());

        $this->assertTrue($result['ok']);
        $this->assertSame(3, $result['profile']->id);
        $this->assertSame('uid-aaaa-1111', $result['client_uid']);
    }

    public function testFailureCodesMatchTheOldMiddleware()
    {
        $this->assertSame('INVALID_TOKEN', $this->verifier()->verify('')['failure_code']);
        $this->assertSame('INVALID_TOKEN', $this->verifier()->verify('cm1.garbage.sig')['failure_code']);
        $this->assertSame('INVALID_TOKEN', $this->verifier()->verify($this->token(99))['failure_code'],
            '档案不存在');

        $this->profiles[3]->enabled = false;
        $disabled = $this->verifier()->verify($this->token());
        $this->assertSame('PROFILE_DISABLED', $disabled['failure_code']);
        $this->assertSame(403, $disabled['status']);

        $this->profiles[3]->enabled = true;
        $other = $this->verifier()->verify($this->token(3, 'uid-bbbb-2222'));
        $this->assertSame('PROFILE_IN_USE', $other['failure_code']);
        $this->assertSame(409, $other['status']);
    }

    public function testTheWebSocketHelloMustClaimTheSameComputer()
    {
        $this->assertTrue($this->verifier()->verify($this->token(), 'uid-aaaa-1111')['ok']);
        $this->assertSame('INVALID_TOKEN',
            $this->verifier()->verify($this->token(), 'uid-zzzz-9999')['failure_code']);
    }
}
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorSessionVerifierTest.php`
Expected: ERROR `Class "App\Services\CameraMonitor\CameraMonitorSessionVerifier" not found`

- [ ] **Step 3: 实现**

`app/Services/CameraMonitor/CameraMonitorSessionVerifier.php`：

```php
<?php

namespace App\Services\CameraMonitor;

use App\Models\CameraMonitor\CameraMonitorProfile;

/**
 * 令牌 → 档案 → 启用 → 绑定，逐条校验。HTTP 中间件和下行通道共用，失败码必须一致。
 *
 * 档案的查找可以注入：测试里用假对象，本机实例化不了 Eloquent 模型。
 */
class CameraMonitorSessionVerifier
{
    /** @var CameraMonitorTokenService */
    private $tokens;

    /** @var callable */
    private $findProfile;

    public function __construct(CameraMonitorTokenService $tokens, ?callable $findProfile = null)
    {
        $this->tokens = $tokens;
        $this->findProfile = $findProfile ?: function (int $id) {
            return CameraMonitorProfile::query()->whereKey($id)->first();
        };
    }

    /**
     * @param string|null $claimedClientUid 下行通道的 hello 里自报的电脑标识，必须与令牌一致
     * @return array{ok: bool, status: int, message: string, failure_code: string, profile: mixed, client_uid: string}
     */
    public function verify(string $token, ?string $claimedClientUid = null): array
    {
        $token = trim($token);
        if ($token === '') {
            return self::deny('请先使用授权码登录', 'INVALID_TOKEN');
        }

        $payload = $this->tokens->decode($token);
        if (!$payload) {
            return self::deny('登录已失效，请重新使用授权码登录', 'INVALID_TOKEN');
        }

        if ($claimedClientUid !== null && !hash_equals($payload['client_uid'], $claimedClientUid)) {
            return self::deny('登录已失效，请重新使用授权码登录', 'INVALID_TOKEN');
        }

        $profile = ($this->findProfile)($payload['profile_id']);
        if (!$profile) {
            return self::deny('监控档案不存在，请重新登录', 'INVALID_TOKEN');
        }

        if (!$profile->isEnabled()) {
            return self::deny('该监控档案已停用，请联系管理员', 'PROFILE_DISABLED', 403);
        }

        // 后台解绑或换绑之后，旧令牌必须立刻失效——否则被替换下来的那台电脑
        // 还能继续上传，把新电脑的配置覆盖掉。
        if (!$profile->acceptsClient($payload['client_uid'])) {
            return self::deny('该授权码已绑定到另一台电脑，请重新登录', 'PROFILE_IN_USE', 409);
        }

        return [
            'ok' => true,
            'status' => 200,
            'message' => '',
            'failure_code' => '',
            'profile' => $profile,
            'client_uid' => $payload['client_uid'],
        ];
    }

    private static function deny(string $message, string $failureCode, int $status = 401): array
    {
        return [
            'ok' => false,
            'status' => $status,
            'message' => $message,
            'failure_code' => $failureCode,
            'profile' => null,
            'client_uid' => '',
        ];
    }
}
```

`app/Http/Middleware/CameraMonitorAuth.php` 整个替换为：

```php
<?php

namespace App\Http\Middleware;

use App\Services\CameraMonitor\CameraMonitorSessionVerifier;
use Closure;

/**
 * 监控屏令牌校验。取值方式与 PadAuth 一致：优先 token 头，其次 Bearer。
 * 校验规则在 CameraMonitorSessionVerifier，下行通道也用同一份。
 */
class CameraMonitorAuth
{
    /** @var CameraMonitorSessionVerifier */
    private $verifier;

    public function __construct(CameraMonitorSessionVerifier $verifier)
    {
        $this->verifier = $verifier;
    }

    public function handle($request, Closure $next)
    {
        $result = $this->verifier->verify($this->extractToken($request));
        if (!$result['ok']) {
            return response()->json([
                'code' => 0,
                'message' => $result['message'],
                'data' => ['failure_code' => $result['failure_code']],
            ], $result['status']);
        }

        $request->camera_monitor_profile = $result['profile'];
        $request->camera_monitor_client_uid = $result['client_uid'];

        return $next($request);
    }

    private function extractToken($request): string
    {
        $token = trim((string) $request->header('token', ''));
        if ($token !== '') {
            return $token;
        }

        $authorization = trim((string) $request->header('Authorization', ''));
        if (stripos($authorization, 'Bearer ') === 0) {
            return trim(substr($authorization, 7));
        }

        return '';
    }
}
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorSessionVerifierTest.php && php -l app/Http/Middleware/CameraMonitorAuth.php`
Expected: `OK (3 tests, ...)`、`No syntax errors detected`

- [ ] **Step 5: 提交**

```bash
git add app/Services/CameraMonitor/CameraMonitorSessionVerifier.php app/Http/Middleware/CameraMonitorAuth.php \
        tests/Unit/CameraMonitorSessionVerifierTest.php
git commit -m "refactor(camera-monitor): 会话校验抽成服务，HTTP 与下行通道共用

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 快照带上托管字段；托管点位拒绝客户端写配置

**Files:**
- Modify: `app/Services/CameraMonitor/CameraMonitorConfigService.php`
- Modify: `app/Http/Controllers/Api/CameraMonitor/CameraMonitorConfigController.php`

判断逻辑已在 Task 1 的纯类里测过，这里只是接线。

- [ ] **Step 1: 快照**

`CameraMonitorConfigService::snapshot()` 把 `return [ ... ];` 改成先放进 `$snapshot` 变量，再合并托管字段：

```php
        $snapshot = [
            // ……原来 return 的数组内容，原样不动……
        ];

        return array_merge($snapshot, CameraMonitorManaged::snapshotFields(
            $profile->mode,
            $profile->escape_password_hash,
            $profile->default_username,
            $this->readDefaultPassword($profile)
        ));
```

在 `readPassword` 方法之后加：

```php
    private function readDefaultPassword(CameraMonitorProfile $profile): string
    {
        try {
            return $this->secrets->decrypt($profile->default_password_encrypted);
        } catch (Throwable $exception) {
            Log::warning('CAMERA_MONITOR_DEFAULT_PASSWORD_UNREADABLE', ['profile_id' => (int) $profile->id]);

            return '';
        }
    }
```

> 先确认 `CameraMonitorSecretService::decrypt(null)` 返回空串（读 `app/Services/CameraMonitor/CameraMonitorSecretService.php:51` 起的实现）。如果它对 null 抛异常，就在调用前加一句 `if ($profile->default_password_encrypted === null) return '';`。

`sameConfig` 只比较 `layout` 和 `cameras`，所以模式、维护密码、默认账号的变化**不会**触发版本号 +1。这是有意的：这三项由后台通过专门的接口修改，那些接口自己会把版本号 +1（见计划 C）。

- [ ] **Step 2: 托管点位拒绝 PUT**

`CameraMonitorConfigController::update()` 中，在 `$version = $request->input('version');` 之前插入：

```php
        // 托管点位只认后台：现场老版本软件、或者切换瞬间还没收到新模式的客户端，
        // 都会撞到这里。回 409 让它去拉最新配置，里面带着新模式。
        if (CameraMonitorManaged::rejectsClientWrites($profile->mode)) {
            return $this->jsonResponse(0, '该点位已由后台托管，配置请在后台修改', [
                'failure_code' => 'MANAGED_PROFILE',
            ], 409);
        }
```

两个文件的顶部都加上 `use App\Services\CameraMonitor\CameraMonitorManaged;`。

- [ ] **Step 3: 语法检查并跑全部 CameraMonitor 测试**

```bash
php -l app/Services/CameraMonitor/CameraMonitorConfigService.php
php -l app/Http/Controllers/Api/CameraMonitor/CameraMonitorConfigController.php
for f in tests/Unit/CameraMonitor*Test.php; do out=$(php vendor/bin/phpunit "$f" 2>&1) || { echo "$out"; echo "FAILED: $f"; break; }; done && echo ALL-OK
```

Expected: 无语法错误，测试全部 OK

- [ ] **Step 4: 提交**

```bash
git add app/Services/CameraMonitor/CameraMonitorConfigService.php \
        app/Http/Controllers/Api/CameraMonitor/CameraMonitorConfigController.php
git commit -m "feat(camera-monitor): 快照下发模式、维护密码与默认账号，托管点位拒绝客户端写配置

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 截图上传

**Files:**
- Create: `app/Services/CameraMonitor/CameraMonitorSnapshotRules.php`
- Create: `app/Http/Controllers/Api/CameraMonitor/CameraMonitorSnapshotController.php`
- Modify: `routes/api.php`（camera-monitor 路由组）
- Test: `tests/Unit/CameraMonitorSnapshotRulesTest.php`

- [ ] **Step 1: 写失败的测试**

```php
<?php

namespace Tests\Unit;

use App\Services\CameraMonitor\CameraMonitorSnapshotRules;
use PHPUnit\Framework\TestCase;

class CameraMonitorSnapshotRulesTest extends TestCase
{
    /** 2×2 的真 JPEG，632 字节 */
    private const JPEG = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERXRTc4UG1RV19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2P/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDGooorlPIP/9k=';

    private function jpeg(): string
    {
        return base64_decode(self::JPEG);
    }

    private function check(string $kind, ?string $ip, string $bytes, array $known = ['10.0.0.1']): ?string
    {
        return CameraMonitorSnapshotRules::check($kind, $ip, $bytes, function (string $ip) use ($known) {
            return in_array($ip, $known, true);
        });
    }

    public function testAKnownCameraSnapshotIsAccepted()
    {
        $this->assertNull($this->check('camera', '10.0.0.1', $this->jpeg()));
    }

    public function testAScreenCaptureNeedsNoIp()
    {
        $this->assertNull($this->check('screen', null, $this->jpeg()));
    }

    public function testRejections()
    {
        $this->assertSame('INVALID_SNAPSHOT_KIND', $this->check('video', '10.0.0.1', $this->jpeg()));
        $this->assertSame('INVALID_SNAPSHOT', $this->check('camera', '10.0.0.1', ''));
        $this->assertSame('INVALID_SNAPSHOT', $this->check('camera', '10.0.0.1', "\x89PNG\r\n\x1a\nxxxx"));
        $this->assertSame('INVALID_SNAPSHOT', $this->check('camera', '10.0.0.1', "\xFF\xD8\xFF" . 'not really'));
        $this->assertSame('UNKNOWN_CAMERA', $this->check('camera', '10.0.0.2', $this->jpeg()), '不属于本设备码的 IP');
        $this->assertSame('UNKNOWN_CAMERA', $this->check('camera', null, $this->jpeg()));
    }

    public function testSizeLimitsDependOnKind()
    {
        $big = $this->jpeg() . str_repeat("\0", 350 * 1024);
        $this->assertSame('SNAPSHOT_TOO_LARGE', $this->check('camera', '10.0.0.1', $big));
        $this->assertNull($this->check('screen', null, $big), '整屏截图上限 500 KB');
    }

    public function testStoragePaths()
    {
        $this->assertSame('camera-monitor/3/camera-10.0.0.1.jpg', CameraMonitorSnapshotRules::path(3, 'camera', '10.0.0.1'));
        $this->assertSame('camera-monitor/3/screen.jpg', CameraMonitorSnapshotRules::path(3, 'screen', null));
    }
}
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorSnapshotRulesTest.php`
Expected: ERROR class not found

- [ ] **Step 3: 实现规则类**

`app/Services/CameraMonitor/CameraMonitorSnapshotRules.php`：

```php
<?php

namespace App\Services\CameraMonitor;

/**
 * 截图的校验与存放位置。纯函数：判断 IP 是否属于本设备码由调用方传入。
 */
class CameraMonitorSnapshotRules
{
    /** 各类截图的字节上限。客户端压得更小，这里留了余量 */
    public const LIMITS = [
        'camera' => 300 * 1024,
        'screen' => 500 * 1024,
    ];

    /**
     * @param callable $ipBelongs fn(string $ip): bool
     * @return string|null 失败码；null 表示通过
     */
    public static function check(string $kind, ?string $ip, string $bytes, callable $ipBelongs): ?string
    {
        if (!array_key_exists($kind, self::LIMITS)) {
            return 'INVALID_SNAPSHOT_KIND';
        }
        if ($bytes === '' || strncmp($bytes, "\xFF\xD8\xFF", 3) !== 0) {
            return 'INVALID_SNAPSHOT';
        }
        if (strlen($bytes) > self::LIMITS[$kind]) {
            return 'SNAPSHOT_TOO_LARGE';
        }
        $info = @getimagesizefromstring($bytes);
        if ($info === false || ($info[2] ?? null) !== IMAGETYPE_JPEG) {
            return 'INVALID_SNAPSHOT';
        }
        if ($kind === 'camera') {
            $ip = CameraMonitorPayload::ip($ip);
            if ($ip === null || !$ipBelongs($ip)) {
                return 'UNKNOWN_CAMERA';
            }
        }

        return null;
    }

    public static function path(int $profileId, string $kind, ?string $ip): string
    {
        return 'camera-monitor/' . $profileId . '/'
            . ($kind === 'screen' ? 'screen.jpg' : 'camera-' . $ip . '.jpg');
    }
}
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorSnapshotRulesTest.php`
Expected: `OK (5 tests, ...)`

- [ ] **Step 5: 控制器和路由**

`app/Http/Controllers/Api/CameraMonitor/CameraMonitorSnapshotController.php`：

```php
<?php

namespace App\Http\Controllers\Api\CameraMonitor;

use App\Http\Controllers\Controller;
use App\Models\CameraMonitor\CameraMonitorCamera;
use App\Models\CameraMonitor\CameraMonitorDiscovered;
use App\Services\CameraMonitor\CameraMonitorPayload;
use App\Services\CameraMonitor\CameraMonitorSnapshotRules;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;

/**
 * 客户端上传的截图：后台靠它认出哪个 IP 是哪个摄像头、现场大屏此刻在放什么。
 */
class CameraMonitorSnapshotController extends Controller
{
    public function store(Request $request)
    {
        $profile = $request->camera_monitor_profile;
        $profileId = (int) $profile->id;
        $kind = (string) $request->input('kind', '');
        $file = $request->file('image');
        $bytes = ($file && $file->isValid()) ? (string) file_get_contents($file->getRealPath()) : '';
        $ip = $kind === 'camera' ? CameraMonitorPayload::ip($request->input('ip')) : null;

        $failure = CameraMonitorSnapshotRules::check($kind, $ip, $bytes, function (string $ip) use ($profileId) {
            return CameraMonitorCamera::query()->where('profile_id', $profileId)->where('ip', $ip)->exists()
                || CameraMonitorDiscovered::query()->where('profile_id', $profileId)->where('ip', $ip)->exists();
        });
        if ($failure !== null) {
            return response()->json([
                'code' => 0,
                'message' => '截图不符合要求',
                'data' => ['failure_code' => $failure],
            ], 422);
        }

        $path = CameraMonitorSnapshotRules::path($profileId, $kind, $ip);
        Storage::disk('local')->put($path, $bytes);

        $now = now();
        $key = ['profile_id' => $profileId, 'kind' => $kind, 'ip' => (string) $ip];
        $updated = DB::table('camera_monitor_snapshot')->where($key)
            ->update(['path' => $path, 'captured_at' => $now, 'updated_at' => $now]);
        if (!$updated) {
            DB::table('camera_monitor_snapshot')->insert($key + [
                'path' => $path, 'captured_at' => $now, 'created_at' => $now, 'updated_at' => $now,
            ]);
        }

        return response()->json([
            'code' => 1,
            'message' => '截图已保存',
            'data' => ['captured_at' => $now->toDateTimeString()],
        ]);
    }
}
```

`routes/api.php` 的 camera-monitor 路由组里，在 `Route::put('/config', ...)` 之后加：

```php
        // 截图：单路一张、整屏一张，新的覆盖旧的
        Route::post('/snapshots', [CameraMonitorSnapshotController::class, 'store'])
            ->middleware('throttle:120,1');
```

并在 `routes/api.php` 顶部现有的 CameraMonitor 控制器 `use` 语句旁边加：

```php
use App\Http\Controllers\Api\CameraMonitor\CameraMonitorSnapshotController;
```

- [ ] **Step 6: 语法检查**

```bash
php -l app/Http/Controllers/Api/CameraMonitor/CameraMonitorSnapshotController.php
php -l routes/api.php
```

Expected: 无语法错误

- [ ] **Step 7: 提交**

```bash
git add app/Services/CameraMonitor/CameraMonitorSnapshotRules.php \
        app/Http/Controllers/Api/CameraMonitor/CameraMonitorSnapshotController.php \
        routes/api.php tests/Unit/CameraMonitorSnapshotRulesTest.php
git commit -m "feat(camera-monitor): 客户端截图上传接口

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 通道消息路由（纯类）

**Files:**
- Create: `app/Services/CameraMonitor/Channel/CameraMonitorChannelConnection.php`
- Create: `app/Services/CameraMonitor/Channel/CameraMonitorChannelStore.php`
- Create: `app/Services/CameraMonitor/Channel/CameraMonitorChannelRouter.php`
- Test: `tests/Unit/CameraMonitorChannelRouterTest.php`

- [ ] **Step 1: 写失败的测试**

```php
<?php

namespace Tests\Unit;

use App\Services\CameraMonitor\Channel\CameraMonitorChannelConnection;
use App\Services\CameraMonitor\Channel\CameraMonitorChannelRouter;
use App\Services\CameraMonitor\Channel\CameraMonitorChannelStore;
use App\Services\CameraMonitor\CameraMonitorSessionVerifier;
use App\Services\CameraMonitor\CameraMonitorTokenService;
use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/CameraMonitorSessionVerifierTest.php';

class MemoryChannelStore implements CameraMonitorChannelStore
{
    public $calls = [];

    public function markConnected(int $profileId, string $clientUid, string $appVersion, bool $connected): void
    {
        $this->calls[] = ['connected', $profileId, $clientUid, $appVersion, $connected];
    }

    public function saveStatus(int $profileId, string $clientUid, array $status): void
    {
        $this->calls[] = ['status', $profileId, $clientUid, $status];
    }

    public function replaceDiscovered(int $profileId, array $devices): void
    {
        $this->calls[] = ['discovered', $profileId, $devices];
    }

    public function ackCommand(string $uuid, int $profileId, bool $ok, string $error): void
    {
        $this->calls[] = ['ack', $uuid, $profileId, $ok, $error];
    }

    public function finishCommand(string $uuid, int $profileId, bool $ok, string $error): void
    {
        $this->calls[] = ['done', $uuid, $profileId, $ok, $error];
    }
}

class CameraMonitorChannelRouterTest extends TestCase
{
    private $tokens;
    private $store;
    private $now = 1000;
    private $profiles;

    protected function setUp(): void
    {
        $this->tokens = new CameraMonitorTokenService('unit-test-signing-key', 3600);
        $this->store = new MemoryChannelStore();
        $this->profiles = [3 => new FakeMonitorProfile(3, 'uid-aaaa-1111')];
    }

    private function router(): CameraMonitorChannelRouter
    {
        $verifier = new CameraMonitorSessionVerifier($this->tokens, function (int $id) {
            return $this->profiles[$id] ?? null;
        });

        return new CameraMonitorChannelRouter($verifier, $this->store, function () {
            return $this->now;
        });
    }

    private function connection(): CameraMonitorChannelConnection
    {
        return new CameraMonitorChannelConnection($this->now);
    }

    private function send(CameraMonitorChannelRouter $router, CameraMonitorChannelConnection $conn, array $message): array
    {
        return $router->onMessage($conn, json_encode($message));
    }

    private function hello(CameraMonitorChannelRouter $router, CameraMonitorChannelConnection $conn, string $uid = 'uid-aaaa-1111'): array
    {
        return $this->send($router, $conn, [
            'type' => 'hello',
            'token' => $this->tokens->issue(3, $uid)['token'],
            'client_uid' => $uid,
            'app_version' => '0.9.0',
        ]);
    }

    public function testHelloWelcomesAndBindsTheProfile()
    {
        $router = $this->router();
        $conn = $this->connection();

        $result = $this->hello($router, $conn);

        $this->assertSame([['type' => 'welcome', 'version' => 7]], $result['replies']);
        $this->assertFalse($result['close']);
        $this->assertTrue($result['authenticated']);
        $this->assertSame(3, $conn->profileId);
        $this->assertSame(['connected', 3, 'uid-aaaa-1111', '0.9.0', true], $this->store->calls[0]);
    }

    public function testABadHelloIsDeniedAndClosed()
    {
        $router = $this->router();
        $conn = $this->connection();

        $result = $this->hello($router, $conn, 'uid-bbbb-2222');

        $this->assertSame([['type' => 'denied', 'failure_code' => 'PROFILE_IN_USE']], $result['replies']);
        $this->assertTrue($result['close']);
        $this->assertFalse($conn->authenticated);
    }

    public function testNothingButHelloIsHeardBeforeAuthentication()
    {
        $router = $this->router();
        $conn = $this->connection();

        $result = $this->send($router, $conn, ['type' => 'status', 'cameras' => []]);

        $this->assertSame([], $result['replies']);
        $this->assertSame([], $this->store->calls);
    }

    public function testPingPong()
    {
        $router = $this->router();
        $conn = $this->connection();
        $this->hello($router, $conn);

        $this->assertSame([['type' => 'pong']], $this->send($router, $conn, ['type' => 'ping'])['replies']);
    }

    public function testStatusIsCleanedBeforeItIsStored()
    {
        $router = $this->router();
        $conn = $this->connection();
        $this->hello($router, $conn);

        $this->send($router, $conn, ['type' => 'status', 'config_version' => '13', 'fullscreen' => 1,
            'app_version' => '0.9.0', 'cameras' => [
                ['ip' => '192.168.2.216', 'state' => 'playing', 'width' => 1280, 'height' => 720],
                ['ip' => 'evil', 'state' => 'playing'],
                ['ip' => '192.168.2.219', 'state' => 'rm -rf'],
            ]]);

        $this->assertSame(['status', 3, 'uid-aaaa-1111', [
            'config_version' => 13,
            'fullscreen' => true,
            'app_version' => '0.9.0',
            'cameras' => [
                ['ip' => '192.168.2.216', 'state' => 'playing', 'width' => 1280, 'height' => 720],
                ['ip' => '192.168.2.219', 'state' => 'unreachable'],
            ],
        ]], end($this->store->calls));
    }

    public function testScanResultsReplaceTheDiscoveredListAndFinishTheCommand()
    {
        $router = $this->router();
        $conn = $this->connection();
        $this->hello($router, $conn);

        $this->send($router, $conn, ['type' => 'scan_result', 'id' => 'cmd-1', 'devices' => [
            ['ip' => '192.168.2.216', 'model' => 'IPC', 'manufacturer' => 'Dahua',
                'protocols' => ['ONVIF'], 'onvif_urls' => ['http://192.168.2.216/onvif/device_service', 'javascript:x']],
            ['ip' => '192.168.2.216'],
            ['ip' => '224.0.0.1'],
        ]]);

        $discovered = $this->store->calls[1];
        $this->assertSame('discovered', $discovered[0]);
        $this->assertSame([[
            'ip' => '192.168.2.216', 'model' => 'IPC', 'manufacturer' => 'Dahua',
            'protocols' => ['ONVIF'], 'onvif_urls' => ['http://192.168.2.216/onvif/device_service'],
        ]], $discovered[2]);
        $this->assertSame(['done', 'cmd-1', 3, true, ''], $this->store->calls[2]);
    }

    public function testAcksAndCompletions()
    {
        $router = $this->router();
        $conn = $this->connection();
        $this->hello($router, $conn);

        $this->send($router, $conn, ['type' => 'ack', 'id' => 'c2', 'ok' => false, 'error' => 'BUSY']);
        $this->send($router, $conn, ['type' => 'command_done', 'id' => 'c3', 'ok' => true]);

        $this->assertSame(['ack', 'c2', 3, false, 'BUSY'], $this->store->calls[1]);
        $this->assertSame(['done', 'c3', 3, true, ''], $this->store->calls[2]);
    }

    public function testOversizedOrGarbageMessages()
    {
        $router = $this->router();
        $conn = $this->connection();

        $this->assertTrue($router->onMessage($conn, str_repeat('x', 262145))['close']);
        $this->assertSame([], $router->onMessage($conn, '{not json')['replies']);
    }

    public function testExpiry()
    {
        $router = $this->router();
        $conn = $this->connection();

        $this->now = 1004;
        $this->assertFalse($router->expired($conn));
        $this->now = 1006;
        $this->assertTrue($router->expired($conn), '5 秒内不 hello 就断开');

        $this->now = 1000;
        $conn = $this->connection();
        $this->hello($router, $conn);
        $this->now = 1089;
        $this->assertFalse($router->expired($conn));
        $this->now = 1091;
        $this->assertTrue($router->expired($conn), '90 秒没有任何消息就断开');
    }

    public function testClosingAnAuthenticatedConnectionIsRecorded()
    {
        $router = $this->router();
        $conn = $this->connection();
        $this->hello($router, $conn);

        $router->onClose($conn);

        $this->assertSame(['connected', 3, 'uid-aaaa-1111', '0.9.0', false], end($this->store->calls));
    }

    public function testInternalRequestsNeedTheKeyAndAPushableMessage()
    {
        $body = json_encode(['profile_id' => 3, 'message' => ['type' => 'config_changed', 'version' => 9]]);

        $this->assertSame([3, ['type' => 'config_changed', 'version' => 9]],
            CameraMonitorChannelRouter::internalRequest('secret-key', 'secret-key', $body));
        $this->assertNull(CameraMonitorChannelRouter::internalRequest('wrong', 'secret-key', $body));
        $this->assertNull(CameraMonitorChannelRouter::internalRequest('', '', $body), '没配密钥一律拒绝');
        $this->assertNull(CameraMonitorChannelRouter::internalRequest('secret-key', 'secret-key',
            json_encode(['profile_id' => 3, 'message' => ['type' => 'welcome']])), '只能推这几类消息');
        $this->assertNull(CameraMonitorChannelRouter::internalRequest('secret-key', 'secret-key', '{bad'));
    }
}
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorChannelRouterTest.php`
Expected: ERROR `Interface "App\Services\CameraMonitor\Channel\CameraMonitorChannelStore" not found`

- [ ] **Step 3: 实现**

`app/Services/CameraMonitor/Channel/CameraMonitorChannelConnection.php`：

```php
<?php

namespace App\Services\CameraMonitor\Channel;

/** 一条下行连接的状态。和 Workerman 的连接对象一一对应，但不依赖它，便于测试 */
class CameraMonitorChannelConnection
{
    public $authenticated = false;
    public $profileId = 0;
    public $clientUid = '';
    public $appVersion = '';
    public $connectedAt;
    public $lastHeardAt;

    public function __construct(int $now)
    {
        $this->connectedAt = $now;
        $this->lastHeardAt = $now;
    }
}
```

`app/Services/CameraMonitor/Channel/CameraMonitorChannelStore.php`：

```php
<?php

namespace App\Services\CameraMonitor\Channel;

/** 通道收到的东西落到哪里。线上是数据库，测试里是内存 */
interface CameraMonitorChannelStore
{
    public function markConnected(int $profileId, string $clientUid, string $appVersion, bool $connected): void;

    public function saveStatus(int $profileId, string $clientUid, array $status): void;

    public function replaceDiscovered(int $profileId, array $devices): void;

    public function ackCommand(string $uuid, int $profileId, bool $ok, string $error): void;

    public function finishCommand(string $uuid, int $profileId, bool $ok, string $error): void;
}
```

`app/Services/CameraMonitor/Channel/CameraMonitorChannelRouter.php`：

```php
<?php

namespace App\Services\CameraMonitor\Channel;

use App\Services\CameraMonitor\CameraMonitorPayload;

/**
 * 下行通道的消息处理：收一条消息，返回要回的消息、要不要断开。
 *
 * 不依赖 Workerman，也不直接碰数据库（交给 Store），所以能在本机测。
 * 连接表、替换旧连接这些只有进程里才有的事，由 CameraMonitorWebSocketCommand 处理。
 */
class CameraMonitorChannelRouter
{
    public const MAX_MESSAGE_BYTES = 262144;
    public const HELLO_TIMEOUT = 5;
    public const SILENCE_LIMIT = 90;
    public const MAX_DEVICES = 256;
    public const STATES = ['playing', 'connecting', 'auth_failed', 'unreachable', 'stopped'];
    /** PHP-FPM 能经内部接口推给客户端的消息类型 */
    public const PUSHABLE = ['config_changed', 'command', 'revoked'];

    /** @var object 有 verify(string $token, ?string $clientUid): array 方法 */
    private $verifier;

    /** @var CameraMonitorChannelStore */
    private $store;

    /** @var callable */
    private $clock;

    public function __construct($verifier, CameraMonitorChannelStore $store, ?callable $clock = null)
    {
        $this->verifier = $verifier;
        $this->store = $store;
        $this->clock = $clock ?: function () {
            return time();
        };
    }

    public function now(): int
    {
        return (int) ($this->clock)();
    }

    /**
     * @return array{replies: array, close: bool, authenticated: bool}
     *         authenticated 只在本条消息让连接通过认证时为 true
     */
    public function onMessage(CameraMonitorChannelConnection $conn, string $raw): array
    {
        $conn->lastHeardAt = $this->now();
        if (strlen($raw) > self::MAX_MESSAGE_BYTES) {
            return self::result([], true);
        }
        $message = json_decode($raw, true);
        if (!is_array($message)) {
            return self::result();
        }
        $type = (string) ($message['type'] ?? '');

        if (!$conn->authenticated) {
            return $type === 'hello' ? $this->hello($conn, $message) : self::result();
        }

        switch ($type) {
            case 'ping':
                return self::result([['type' => 'pong']]);
            case 'status':
                $this->store->saveStatus($conn->profileId, $conn->clientUid, self::cleanStatus($message));
                break;
            case 'scan_result':
                $this->store->replaceDiscovered($conn->profileId, self::cleanDevices($message['devices'] ?? []));
                $this->store->finishCommand(self::commandId($message), $conn->profileId, true, '');
                break;
            case 'ack':
                $this->store->ackCommand(self::commandId($message), $conn->profileId,
                    (bool) ($message['ok'] ?? false), self::errorCode($message));
                break;
            case 'command_done':
                $this->store->finishCommand(self::commandId($message), $conn->profileId,
                    (bool) ($message['ok'] ?? false), self::errorCode($message));
                break;
        }

        return self::result();
    }

    public function onClose(CameraMonitorChannelConnection $conn): void
    {
        if ($conn->authenticated) {
            $this->store->markConnected($conn->profileId, $conn->clientUid, $conn->appVersion, false);
        }
    }

    public function expired(CameraMonitorChannelConnection $conn): bool
    {
        $now = $this->now();
        if (!$conn->authenticated) {
            return $now - $conn->connectedAt > self::HELLO_TIMEOUT;
        }

        return $now - $conn->lastHeardAt > self::SILENCE_LIMIT;
    }

    /**
     * PHP-FPM 经 127.0.0.1 发来的「推一条消息给某台电脑」。
     *
     * @return array|null [profileId, message]；密钥不对、格式不对都返回 null
     */
    public static function internalRequest(string $providedKey, string $expectedKey, string $body): ?array
    {
        if ($expectedKey === '' || !hash_equals($expectedKey, $providedKey)) {
            return null;
        }
        $data = json_decode($body, true);
        if (!is_array($data)) {
            return null;
        }
        $profileId = (int) ($data['profile_id'] ?? 0);
        $message = $data['message'] ?? null;
        if ($profileId <= 0 || !is_array($message)
            || !in_array((string) ($message['type'] ?? ''), self::PUSHABLE, true)) {
            return null;
        }

        return [$profileId, $message];
    }

    private function hello(CameraMonitorChannelConnection $conn, array $message): array
    {
        $result = $this->verifier->verify((string) ($message['token'] ?? ''), (string) ($message['client_uid'] ?? ''));
        if (!$result['ok']) {
            return self::result([['type' => 'denied', 'failure_code' => $result['failure_code']]], true);
        }

        $conn->authenticated = true;
        $conn->profileId = (int) $result['profile']->id;
        $conn->clientUid = (string) $result['client_uid'];
        $conn->appVersion = mb_substr((string) ($message['app_version'] ?? ''), 0, 32);
        $this->store->markConnected($conn->profileId, $conn->clientUid, $conn->appVersion, true);

        return self::result([['type' => 'welcome', 'version' => (int) $result['profile']->version]], false, true);
    }

    public static function cleanStatus(array $message): array
    {
        $cameras = [];
        foreach ((array) ($message['cameras'] ?? []) as $camera) {
            if (!is_array($camera)) {
                continue;
            }
            $ip = CameraMonitorPayload::ip($camera['ip'] ?? '');
            if ($ip === null) {
                continue;
            }
            $state = (string) ($camera['state'] ?? '');
            $entry = ['ip' => $ip, 'state' => in_array($state, self::STATES, true) ? $state : 'unreachable'];
            if (isset($camera['width'], $camera['height'])) {
                $entry['width'] = max(0, min(10000, (int) $camera['width']));
                $entry['height'] = max(0, min(10000, (int) $camera['height']));
            }
            $cameras[] = $entry;
            if (count($cameras) >= CameraMonitorPayload::MAX_CAMERAS) {
                break;
            }
        }

        return [
            'config_version' => max(0, (int) ($message['config_version'] ?? 0)),
            'fullscreen' => (bool) ($message['fullscreen'] ?? false),
            'app_version' => mb_substr((string) ($message['app_version'] ?? ''), 0, 32),
            'cameras' => $cameras,
        ];
    }

    public static function cleanDevices($devices): array
    {
        $clean = [];
        $seen = [];
        foreach ((array) $devices as $device) {
            if (!is_array($device)) {
                continue;
            }
            $ip = CameraMonitorPayload::ip($device['ip'] ?? '');
            if ($ip === null || isset($seen[$ip])) {
                continue;
            }
            $seen[$ip] = true;
            $clean[] = [
                'ip' => $ip,
                'model' => mb_substr((string) ($device['model'] ?? ''), 0, 120),
                'manufacturer' => mb_substr((string) ($device['manufacturer'] ?? ''), 0, 120),
                'protocols' => self::strings($device['protocols'] ?? [], 8, 40),
                'onvif_urls' => array_values(array_filter(self::strings($device['onvif_urls'] ?? [], 8, 255),
                    function (string $url) {
                        return preg_match('#^https?://#i', $url) === 1;
                    })),
            ];
            if (count($clean) >= self::MAX_DEVICES) {
                break;
            }
        }

        return $clean;
    }

    private static function strings($values, int $maxItems, int $maxLength): array
    {
        $out = [];
        foreach ((array) $values as $value) {
            if (!is_string($value) || $value === '') {
                continue;
            }
            $out[] = mb_substr($value, 0, $maxLength);
            if (count($out) >= $maxItems) {
                break;
            }
        }

        return $out;
    }

    private static function commandId(array $message): string
    {
        return mb_substr((string) ($message['id'] ?? ''), 0, 36);
    }

    private static function errorCode(array $message): string
    {
        return mb_substr((string) ($message['error'] ?? ''), 0, 64);
    }

    private static function result(array $replies = [], bool $close = false, bool $authenticated = false): array
    {
        return ['replies' => $replies, 'close' => $close, 'authenticated' => $authenticated];
    }
}
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorChannelRouterTest.php`
Expected: `OK (11 tests, ...)`

再跑全部 CameraMonitor 测试（见"约定"里的命令）
Expected: 全部 OK

- [ ] **Step 5: 提交**

```bash
git add app/Services/CameraMonitor/Channel/ tests/Unit/CameraMonitorChannelRouterTest.php
git commit -m "feat(camera-monitor): 下行通道的消息路由

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 通道的数据库存储与 PHP-FPM 侧发送器

**Files:**
- Create: `app/Services/CameraMonitor/Channel/CameraMonitorChannelDbStore.php`
- Create: `app/Services/CameraMonitor/Channel/CameraMonitorChannel.php`
- Modify: `config/camera_monitor.php`、`.env.example`
- Test: `tests/Unit/CameraMonitorChannelSendTest.php`

- [ ] **Step 1: 配置**

`config/camera_monitor.php` 的数组末尾（`'auth_code' => [...]` 之后）加：

```php
    /*
    |--------------------------------------------------------------------------
    | 下行通道（camera-monitor:ws 进程）
    |--------------------------------------------------------------------------
    |
    | 对外只监听本机 16505，由 nginx 转发 wss://<域名>/api/camera-monitor/ws；
    | 16506 只给本机 PHP-FPM 推消息用，必须带 internal_key。
    |
    | 生成方式：php -r "echo bin2hex(random_bytes(24));"
    |
    */

    'ws' => [
        'port' => (int) env('CAMERA_MONITOR_WS_PORT', 16505),
        'internal_port' => (int) env('CAMERA_MONITOR_WS_INTERNAL_PORT', 16506),
        'internal_key' => env('CAMERA_MONITOR_WS_INTERNAL_KEY', ''),
    ],
```

`.env.example` 中，在 `CAMERA_MONITOR_SECRET_KEY` 那一行之后加（找不到这一行就加在文件末尾）：

```
CAMERA_MONITOR_WS_INTERNAL_KEY=
```

- [ ] **Step 2: 写失败的测试（只测发送器的纯逻辑部分）**

```php
<?php

namespace Tests\Unit;

use App\Services\CameraMonitor\Channel\CameraMonitorChannel;
use PHPUnit\Framework\TestCase;

class CameraMonitorChannelSendTest extends TestCase
{
    public function testDeliveredOnlyWhenTheProcessSaysSo()
    {
        $calls = [];
        $channel = new CameraMonitorChannel('http://127.0.0.1:16506', 'k', function ($url, $headers, $body) use (&$calls) {
            $calls[] = [$url, $headers, json_decode($body, true)];

            return '{"delivered":true}';
        });

        $this->assertTrue($channel->send(3, ['type' => 'config_changed', 'version' => 9]));
        $this->assertSame('http://127.0.0.1:16506/send', $calls[0][0]);
        $this->assertContains('X-Internal-Key: k', $calls[0][1]);
        $this->assertSame(['profile_id' => 3, 'message' => ['type' => 'config_changed', 'version' => 9]], $calls[0][2]);
    }

    public function testOfflineTimeoutsAndGarbageAllMeanNotDelivered()
    {
        foreach (['{"delivered":false,"reason":"offline"}', null, 'oops'] as $answer) {
            $channel = new CameraMonitorChannel('http://127.0.0.1:16506', 'k', function () use ($answer) {
                return $answer;
            });
            $this->assertFalse($channel->send(3, ['type' => 'config_changed', 'version' => 1]));
        }
    }

    public function testNoKeyMeansNoAttempt()
    {
        $called = false;
        $channel = new CameraMonitorChannel('http://127.0.0.1:16506', '', function () use (&$called) {
            $called = true;

            return '{"delivered":true}';
        });

        $this->assertFalse($channel->send(3, ['type' => 'config_changed', 'version' => 1]));
        $this->assertFalse($called);
    }
}
```

- [ ] **Step 3: 跑测试，确认失败**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorChannelSendTest.php`
Expected: ERROR class not found

- [ ] **Step 4: 实现发送器**

`app/Services/CameraMonitor/Channel/CameraMonitorChannel.php`：

```php
<?php

namespace App\Services\CameraMonitor\Channel;

/**
 * PHP-FPM 这边给现场电脑推一条消息。
 *
 * 推不到（进程没起、设备离线、超时）都只是返回 false：配置照样会被客户端 60 秒一次
 * 的轮询带走，调用方不需要也不应该因此报错。
 */
class CameraMonitorChannel
{
    private const TIMEOUT_SECONDS = 2;

    /** @var string */
    private $baseUrl;

    /** @var string */
    private $key;

    /** @var callable fn(string $url, array $headers, string $body): ?string */
    private $post;

    public function __construct(?string $baseUrl = null, ?string $key = null, ?callable $post = null)
    {
        $this->baseUrl = rtrim($baseUrl ?? ('http://127.0.0.1:' . (int) config('camera_monitor.ws.internal_port', 16506)), '/');
        $this->key = $key ?? (string) config('camera_monitor.ws.internal_key', '');
        $this->post = $post ?: [self::class, 'httpPost'];
    }

    public function send(int $profileId, array $message): bool
    {
        if ($this->key === '') {
            return false;
        }
        $answer = ($this->post)(
            $this->baseUrl . '/send',
            ['Content-Type: application/json', 'X-Internal-Key: ' . $this->key],
            json_encode(['profile_id' => $profileId, 'message' => $message], JSON_UNESCAPED_UNICODE)
        );
        $data = is_string($answer) ? json_decode($answer, true) : null;

        return is_array($data) && ($data['delivered'] ?? false) === true;
    }

    public function configChanged(int $profileId, int $version): bool
    {
        return $this->send($profileId, ['type' => 'config_changed', 'version' => $version]);
    }

    public function revoke(int $profileId, string $failureCode): bool
    {
        return $this->send($profileId, ['type' => 'revoked', 'failure_code' => $failureCode]);
    }

    public static function httpPost(string $url, array $headers, string $body): ?string
    {
        $context = stream_context_create(['http' => [
            'method' => 'POST',
            'header' => implode("\r\n", $headers),
            'content' => $body,
            'timeout' => self::TIMEOUT_SECONDS,
            'ignore_errors' => true,
        ]]);
        $answer = @file_get_contents($url, false, $context);

        return $answer === false ? null : $answer;
    }
}
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `php vendor/bin/phpunit tests/Unit/CameraMonitorChannelSendTest.php`
Expected: `OK (3 tests, ...)`

- [ ] **Step 6: 数据库存储（不能本机测，只做语法检查）**

`app/Services/CameraMonitor/Channel/CameraMonitorChannelDbStore.php`：

```php
<?php

namespace App\Services\CameraMonitor\Channel;

use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;
use Throwable;

/**
 * 通道落库。跑在常驻进程里：MySQL 会因为 wait_timeout 断掉空闲连接，
 * 所以每次写都包一层「失败就重连再试一次」。
 */
class CameraMonitorChannelDbStore implements CameraMonitorChannelStore
{
    public function markConnected(int $profileId, string $clientUid, string $appVersion, bool $connected): void
    {
        $this->write(function () use ($profileId, $clientUid, $appVersion, $connected) {
            $now = now();
            $values = $connected
                ? ['channel_connected_at' => $now, 'last_seen_at' => $now, 'updated_at' => $now]
                : ['channel_disconnected_at' => $now, 'updated_at' => $now];
            if ($connected && $appVersion !== '') {
                $values['app_version'] = $appVersion;
            }
            DB::table('camera_monitor_client')
                ->where('profile_id', $profileId)
                ->where('client_uid', $clientUid)
                ->update($values);
        });
    }

    public function saveStatus(int $profileId, string $clientUid, array $status): void
    {
        $this->write(function () use ($profileId, $clientUid, $status) {
            $now = now();
            DB::table('camera_monitor_client')
                ->where('profile_id', $profileId)
                ->where('client_uid', $clientUid)
                ->update([
                    'runtime_status' => json_encode($status, JSON_UNESCAPED_UNICODE),
                    'runtime_status_at' => $now,
                    'last_seen_at' => $now,
                    'updated_at' => $now,
                ]);
        });
    }

    public function replaceDiscovered(int $profileId, array $devices): void
    {
        $this->write(function () use ($profileId, $devices) {
            DB::transaction(function () use ($profileId, $devices) {
                DB::table('camera_monitor_discovered')->where('profile_id', $profileId)->delete();
                $now = now();
                $rows = array_map(function (array $device) use ($profileId, $now) {
                    return [
                        'profile_id' => $profileId,
                        'ip' => $device['ip'],
                        'model' => $device['model'],
                        'manufacturer' => $device['manufacturer'],
                        'protocols' => json_encode($device['protocols'], JSON_UNESCAPED_UNICODE),
                        'onvif_urls' => json_encode($device['onvif_urls'], JSON_UNESCAPED_UNICODE),
                        'discovered_at' => $now,
                        'created_at' => $now,
                        'updated_at' => $now,
                    ];
                }, $devices);
                foreach (array_chunk($rows, 100) as $chunk) {
                    DB::table('camera_monitor_discovered')->insert($chunk);
                }
            });
        });
    }

    public function ackCommand(string $uuid, int $profileId, bool $ok, string $error): void
    {
        $this->updateCommand($uuid, $profileId, $ok
            ? ['acked_at' => now()]
            : ['acked_at' => now(), 'finished_at' => now(), 'ok' => false, 'error' => $error]);
    }

    public function finishCommand(string $uuid, int $profileId, bool $ok, string $error): void
    {
        $this->updateCommand($uuid, $profileId, ['finished_at' => now(), 'ok' => $ok, 'error' => $error ?: null]);
    }

    private function updateCommand(string $uuid, int $profileId, array $values): void
    {
        if ($uuid === '') {
            return;
        }
        $this->write(function () use ($uuid, $profileId, $values) {
            DB::table('camera_monitor_command')
                ->where('uuid', $uuid)
                ->where('profile_id', $profileId)
                ->update($values + ['updated_at' => now()]);
        });
    }

    private function write(callable $work): void
    {
        try {
            $work();
        } catch (Throwable $first) {
            try {
                DB::reconnect();
                $work();
            } catch (Throwable $second) {
                Log::warning('CAMERA_MONITOR_CHANNEL_STORE_FAILED', ['error' => get_class($second)]);
            }
        }
    }
}
```

Run: `php -l app/Services/CameraMonitor/Channel/CameraMonitorChannelDbStore.php`
Expected: 无语法错误

- [ ] **Step 7: 提交**

```bash
git add app/Services/CameraMonitor/Channel/CameraMonitorChannel.php \
        app/Services/CameraMonitor/Channel/CameraMonitorChannelDbStore.php \
        config/camera_monitor.php .env.example tests/Unit/CameraMonitorChannelSendTest.php
git commit -m "feat(camera-monitor): 通道落库与 PHP-FPM 侧推送

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `camera-monitor:ws` 进程

**Files:**
- Create: `app/Console/Commands/CameraMonitorWebSocketCommand.php`

Workerman 的部分本机没法测，逻辑都在 Router 里。这一层只做连接表、替换旧连接、内部推送、定时清理。

- [ ] **Step 1: 实现**

```php
<?php

namespace App\Console\Commands;

use App\Services\CameraMonitor\CameraMonitorSessionVerifier;
use App\Services\CameraMonitor\Channel\CameraMonitorChannelConnection;
use App\Services\CameraMonitor\Channel\CameraMonitorChannelDbStore;
use App\Services\CameraMonitor\Channel\CameraMonitorChannelRouter;
use Illuminate\Console\Command;
use Workerman\Connection\TcpConnection;
use Workerman\Protocols\Http\Request;
use Workerman\Protocols\Http\Response;
use Workerman\Timer;
use Workerman\Worker;

/**
 * 监控屏下行通道：后台的操作 1 秒内送到现场电脑。
 *
 * 单 worker、单进程：连接表只在这个进程里。PHP-FPM 推消息走同一进程内额外监听的
 * 127.0.0.1 内部端口（不是文件队列：那样要按秒轮询，做不到 1 秒内）。
 */
class CameraMonitorWebSocketCommand extends Command
{
    protected $signature = 'camera-monitor:ws
        {action : run|start|stop|restart|status}
        {--daemon : Run Workerman in daemon mode}';

    protected $description = 'Camera monitor downlink WebSocket server.';

    /** @var CameraMonitorChannelRouter */
    private $router;

    /** @var array<int, array{0: TcpConnection, 1: CameraMonitorChannelConnection}> 连接 id → 连接 */
    private $connections = [];

    /** @var array<int, int> profile_id → 连接 id */
    private $byProfile = [];

    private $pidFile;

    public function handle(): void
    {
        $this->pidFile = storage_path('logs/camera_monitor_ws.pid');
        Worker::$pidFile = $this->pidFile;

        switch ($this->argument('action')) {
            case 'run':
                $this->start(true);
                break;
            case 'start':
                $this->start(false);
                break;
            case 'stop':
                $this->signal(SIGTERM, 'Camera monitor WebSocket server stopped');
                break;
            case 'restart':
                $this->signal(SIGTERM, 'Stopping…');
                sleep(2);
                $this->start(false);
                break;
            case 'status':
                $this->info($this->isRunning()
                    ? 'Camera monitor WebSocket server is running [PID: ' . trim(file_get_contents($this->pidFile)) . ']'
                    : 'Camera monitor WebSocket server is not running');
                break;
            default:
                $this->error('Usage: php artisan camera-monitor:ws {run|start|stop|restart|status}');
        }
    }

    private function start(bool $foreground): void
    {
        if ($this->isRunning()) {
            $this->error('Camera monitor WebSocket server is already running');
            return;
        }

        $port = (int) config('camera_monitor.ws.port', 16505);
        $internalPort = (int) config('camera_monitor.ws.internal_port', 16506);
        $internalKey = (string) config('camera_monitor.ws.internal_key', '');
        if ($internalKey === '') {
            $this->error('CAMERA_MONITOR_WS_INTERNAL_KEY 未配置，后台将无法推送消息');
            return;
        }

        $this->router = new CameraMonitorChannelRouter(
            app(CameraMonitorSessionVerifier::class),
            new CameraMonitorChannelDbStore()
        );

        Worker::$daemonize = $foreground ? false : (bool) $this->option('daemon');
        Worker::$stdoutFile = storage_path('logs/camera_monitor_ws.out.log');
        global $argv;
        $argv = [$argv[0] ?? 'artisan', 'start'];
        if (Worker::$daemonize) {
            $argv[] = '-d';
        }

        // 只听本机：对外由 nginx 终结 TLS 后转发进来
        $worker = new Worker("websocket://127.0.0.1:{$port}");
        $worker->name = 'camera_monitor_ws';
        $worker->count = 1;

        $worker->onConnect = function (TcpConnection $connection) {
            $this->connections[$connection->id] = [$connection, new CameraMonitorChannelConnection($this->router->now())];
        };

        $worker->onMessage = function (TcpConnection $connection, $data) {
            $this->onClientMessage($connection, (string) $data);
        };

        $worker->onClose = function (TcpConnection $connection) {
            $this->forget($connection);
        };

        $worker->onWorkerStart = function () use ($internalPort, $internalKey) {
            $inner = new Worker("http://127.0.0.1:{$internalPort}");
            $inner->onMessage = function (TcpConnection $connection, Request $request) use ($internalKey) {
                $this->onInternalRequest($connection, $request, $internalKey);
            };
            $inner->listen();

            Timer::add(5, function () {
                foreach ($this->connections as [$connection, $state]) {
                    if ($this->router->expired($state)) {
                        $connection->close();
                    }
                }
            });
        };

        $this->info("Camera monitor WebSocket listening on 127.0.0.1:{$port} (internal {$internalPort})");
        Worker::runAll();
    }

    private function onClientMessage(TcpConnection $connection, string $data): void
    {
        if (!isset($this->connections[$connection->id])) {
            return;
        }
        $state = $this->connections[$connection->id][1];
        $result = $this->router->onMessage($state, $data);

        if ($result['authenticated']) {
            $this->bind($connection, $state);
        }
        foreach ($result['replies'] as $reply) {
            $connection->send(json_encode($reply, JSON_UNESCAPED_UNICODE));
        }
        if ($result['close']) {
            $connection->close();
        }
    }

    /** 同一个设备码只留最新的连接：旧的先告诉它被替换了，再断开，它就不会再重连 */
    private function bind(TcpConnection $connection, CameraMonitorChannelConnection $state): void
    {
        $previousId = $this->byProfile[$state->profileId] ?? null;
        if ($previousId !== null && $previousId !== $connection->id && isset($this->connections[$previousId])) {
            $this->connections[$previousId][0]->close(json_encode(['type' => 'replaced']));
        }
        $this->byProfile[$state->profileId] = $connection->id;
    }

    private function forget(TcpConnection $connection): void
    {
        if (!isset($this->connections[$connection->id])) {
            return;
        }
        $state = $this->connections[$connection->id][1];
        unset($this->connections[$connection->id]);
        if ($state->authenticated && ($this->byProfile[$state->profileId] ?? null) === $connection->id) {
            unset($this->byProfile[$state->profileId]);
            $this->router->onClose($state);
        }
    }

    private function onInternalRequest(TcpConnection $connection, Request $request, string $internalKey): void
    {
        $parsed = $request->path() === '/send' && $request->method() === 'POST'
            ? CameraMonitorChannelRouter::internalRequest((string) $request->header('x-internal-key', ''), $internalKey, (string) $request->rawBody())
            : null;

        if ($parsed === null) {
            $connection->send(new Response(403, ['Content-Type' => 'application/json'], '{"delivered":false,"reason":"forbidden"}'));
            return;
        }

        [$profileId, $message] = $parsed;
        $connectionId = $this->byProfile[$profileId] ?? null;
        if ($connectionId === null || !isset($this->connections[$connectionId])) {
            $connection->send(new Response(200, ['Content-Type' => 'application/json'], '{"delivered":false,"reason":"offline"}'));
            return;
        }

        $client = $this->connections[$connectionId][0];
        $payload = json_encode($message, JSON_UNESCAPED_UNICODE);
        // 收回设备码：发完就断开，客户端会回到欢迎页
        if (($message['type'] ?? '') === 'revoked') {
            $client->close($payload);
        } else {
            $client->send($payload);
        }
        $connection->send(new Response(200, ['Content-Type' => 'application/json'], '{"delivered":true}'));
    }

    private function signal(int $signal, string $message): void
    {
        if (!$this->isRunning()) {
            $this->error('Camera monitor WebSocket server is not running');
            return;
        }
        posix_kill((int) trim(file_get_contents($this->pidFile)), $signal);
        $this->info($message);
    }

    private function isRunning(): bool
    {
        return file_exists($this->pidFile) && posix_kill((int) trim(file_get_contents($this->pidFile)), 0);
    }
}
```

- [ ] **Step 2: 语法检查，并核对 Workerman API**

```bash
php -l app/Console/Commands/CameraMonitorWebSocketCommand.php
grep -n "public function path\|public function method\|public function header\|public function rawBody" vendor/workerman/workerman/Protocols/Http/Request.php
grep -n "public function listen" vendor/workerman/workerman/Worker.php
```

Expected: 无语法错误；这四个方法和 `Worker::listen` 都能找到。如果签名不一样，以 vendor 里的源码为准修改。

- [ ] **Step 3: 提交**

```bash
git add app/Console/Commands/CameraMonitorWebSocketCommand.php
git commit -m "feat(camera-monitor): camera-monitor:ws 下行通道进程

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: 后台收回设备码时通知现场

**Files:**
- Modify: `app/Admin/RowActions/CameraMonitor/ReleaseBinding.php`
- Modify: `app/Admin/Controllers/CameraMonitor/CameraMonitorProfileController.php`（`destroyClient`、表单的 `saved` 回调、删除）

**为什么"重置授权码"不发：** 重置只换授权码，不让现有令牌失效，已绑定的那台电脑会继续同步（这是现有行为）。给它发 `revoked` 会让现场无端掉线，所以不发。spec §5.3 的 `revoked` 触发条件据此收窄（在 Task 12 回写到 spec）。

- [ ] **Step 1: 解绑电脑**

`ReleaseBinding::handle()` 中，`$profile->releaseBinding();` 之后加：

```php
        // 被解绑的那台电脑正连着下行通道的话，立刻让它回到欢迎页
        app(\App\Services\CameraMonitor\Channel\CameraMonitorChannel::class)
            ->revoke((int) $profile->id, 'PROFILE_IN_USE');
```

- [ ] **Step 2: 删除监控屏记录时顺带解绑**

`CameraMonitorProfileController::destroyClient()` 中，`$profile->releaseBinding(); $released = true;` 之后加同样的两行（`revoke((int) $profile->id, 'PROFILE_IN_USE')`）。

- [ ] **Step 3: 停用档案**

`form()` 里，在 `$form->saving(...)` 之后加：

```php
            // 停用的档案：在线的那台电脑立刻回到欢迎页，不用等它下一次轮询
            $form->saved(function (Form $form) {
                if ((int) $form->model()->status !== CameraMonitorProfile::STATUS_ENABLED) {
                    app(\App\Services\CameraMonitor\Channel\CameraMonitorChannel::class)
                        ->revoke((int) $form->model()->id, 'PROFILE_DISABLED');
                }
            });
```

> 注意：现有注释已经提醒过，Dcat 的回调会被重新绑定到模型，回调里的 `$this` 不是控制器。上面的写法没有用 `$this`。

- [ ] **Step 4: 删除档案**

`form()` 里再加：

```php
            $form->deleted(function (Form $form, $result) {
                foreach ((array) $form->getKey() as $id) {
                    app(\App\Services\CameraMonitor\Channel\CameraMonitorChannel::class)
                        ->revoke((int) $id, 'PROFILE_DISABLED');
                }
            });
```

先查一下 vendor 里的签名：`grep -n "function deleted" vendor/dcat/laravel-admin/src/Form/Concerns/HasEvents.php`。回调参数以源码为准；如果没有 `deleted` 事件，改用 `deleting`，在删除前发出。

- [ ] **Step 5: 语法检查**

```bash
php -l app/Admin/RowActions/CameraMonitor/ReleaseBinding.php
php -l app/Admin/Controllers/CameraMonitor/CameraMonitorProfileController.php
```

- [ ] **Step 6: 提交**

```bash
git add app/Admin/RowActions/CameraMonitor/ReleaseBinding.php \
        app/Admin/Controllers/CameraMonitor/CameraMonitorProfileController.php
git commit -m "feat(camera-monitor): 解绑、停用、删除时立即通知现场电脑

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: 部署材料

**Files:**
- Create: `supervisor/camera-monitor-ws.conf`
- Create: `docs/运维/camera-monitor-ws.md`

- [ ] **Step 1: supervisor**

`supervisor/camera-monitor-ws.conf`，写法参照同目录的 `xqq-face-detect.conf`。`directory` 和 `user` 以线上实际为准，部署时核对：

```ini
[program:camera-monitor-ws]
command=/www/server/php/74/bin/php artisan camera-monitor:ws run
directory=/www/wwwroot/yunqi
autostart=true
autorestart=true
startsecs=3
stopsignal=TERM
stopwaitsecs=10
user=www
redirect_stderr=true
stdout_logfile=/www/wwwroot/yunqi/storage/logs/camera_monitor_ws.supervisor.log
stdout_logfile_maxbytes=20MB
stdout_logfile_backups=5
```

> 用 `run`（前台）而不是 `start -d`：supervisor 要直接管住这个进程，进程挂了才会被拉起来。

- [ ] **Step 2: 运维文档**

`docs/运维/camera-monitor-ws.md`：

````markdown
# 监控屏下行通道（camera-monitor:ws）

监控屏「傻瓜模式」靠这个进程把后台的操作在 1 秒内送到现场电脑。它挂了不影响现场画面，
配置仍会被客户端 60 秒一次的轮询带走，只是「搜索 / 截图 / 重连 / 重启」这类远程动作暂时不可用。

## 首次部署

1. `.env` 增加 `CAMERA_MONITOR_WS_INTERNAL_KEY=<php -r "echo bin2hex(random_bytes(24));">`，
   然后 `php artisan config:cache`（如果线上缓存了配置）。
2. 逐个执行迁移（不要跑全量）：
   ```bash
   php artisan migrate --path=database/migrations/2026_10_03_100000_add_camera_monitor_managed_mode.php
   php artisan migrate --path=database/migrations/2026_10_03_100100_create_camera_monitor_discovered.php
   php artisan migrate --path=database/migrations/2026_10_03_100200_create_camera_monitor_snapshot.php
   php artisan migrate --path=database/migrations/2026_10_03_100300_create_camera_monitor_command.php
   php artisan migrate --path=database/migrations/2026_10_03_100400_create_camera_monitor_action_log.php
   ```
3. nginx：在站点的 `server { listen 443 ... }` 里加
   ```nginx
   location = /api/camera-monitor/ws {
       proxy_pass http://127.0.0.1:16505;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_set_header X-Real-IP $remote_addr;
       proxy_read_timeout 120s;
       proxy_send_timeout 120s;
   }
   ```
   `nginx -t && nginx -s reload`
4. supervisor：把 `supervisor/camera-monitor-ws.conf` 放进 supervisor 的配置目录，
   `supervisorctl reread && supervisorctl update && supervisorctl status camera-monitor-ws`

## 验收

- `supervisorctl status camera-monitor-ws` 显示 RUNNING。
- 本机内部端口：`curl -s -X POST http://127.0.0.1:16506/send -H 'X-Internal-Key: <密钥>' -d '{"profile_id":1,"message":{"type":"config_changed","version":1}}'`
  返回 `{"delivered":false,"reason":"offline"}`（没有设备在线时）。不带密钥返回 403。
- 外网握手：`npx wscat -c wss://<域名>/api/camera-monitor/ws`，连上后发
  `{"type":"hello","token":"x","client_uid":"abcdefgh"}`，收到 `{"type":"denied","failure_code":"INVALID_TOKEN"}` 并被断开。
- 用一台 0.9.0 客户端登录托管设备码，后台列表的「现场电脑」显示在线；`kill` 掉进程后 supervisor 3 秒内拉起，客户端自动重连。
- 16505、16506 都**不能**从外网直接访问（安全组 / 防火墙只放 443）。

## 日常

- 日志：`storage/logs/camera_monitor_ws.out.log`、`camera_monitor_ws.supervisor.log`、Laravel 日志里的 `CAMERA_MONITOR_CHANNEL_STORE_FAILED`
- 发版后要重启：`supervisorctl restart camera-monitor-ws`（常驻进程不会自动加载新代码）
````

- [ ] **Step 3: 提交**

```bash
git add supervisor/camera-monitor-ws.conf docs/运维/camera-monitor-ws.md
git commit -m "docs(camera-monitor): 下行通道的部署与验收说明

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: 全量检查与交接

- [ ] **Step 1: 全部测试和语法检查**

```bash
for f in tests/Unit/CameraMonitor*Test.php; do out=$(php vendor/bin/phpunit "$f" 2>&1) || { echo "$out"; echo "FAILED: $f"; break; }; done && echo ALL-OK
git diff --name-only origin/main...HEAD -- '*.php' | xargs -n1 php -l | grep -v "No syntax errors"
```

Expected: 测试全部 OK；第二条命令没有输出。

- [ ] **Step 2: 把实现层面的调整写回 spec**

在 CameraMonitor 仓库的 `docs/superpowers/specs/2026-10-03-managed-mode-design.md` 第 12 节（客户端计划已经建好这一节）末尾追加：

```markdown
- **`revoked` 只在三种情况下发**：后台解绑电脑（含删除占用者的监控屏记录）、停用档案、删除档案。重置授权码不发，因为它不会让现有令牌失效，已绑定的电脑本来就会继续同步。
- **默认账号总是下发**：快照里的 `default_credentials` 不为 null，没设置时是两个空串。这样后台清空默认账号后，客户端会把钥匙串里的那份删掉。
- **整屏截图的 `ip` 存空串**：唯一键里 NULL 不参与比较，用 NULL 会留下多行。
```

- [ ] **Step 3: 推送分支，交给用户决定合并时机**

```bash
git push -u origin feat/camera-monitor-managed-mode
```

**不要合并到 main**：推 main 就等于上线，而且上线还要按 `docs/运维/camera-monitor-ws.md` 配好 nginx、supervisor、.env 和迁移。何时合并、何时部署，由用户决定。
