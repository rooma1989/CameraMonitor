import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from camera_monitor.cloud_state import DEFAULT_CREDENTIAL_ACCOUNT
from camera_monitor.discovery import Device
from camera_monitor.managed_mode import EscapeDialog, ManagedController, escape_keys
from support import make_window
from test_credentials import MemoryVault


class FakeChannel(QObject):
    online_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.online = True
        self.sent = []

    def send(self, payload):
        if not self.online:
            return False
        self.sent.append(payload)
        return True

    def of(self, kind):
        return [message for message in self.sent if message['type'] == kind]


class FakeUploader:
    def __init__(self):
        self.submitted = []

    def submit(self, kind, image, ip=''):
        self.submitted.append((kind, ip))
        return True


class FakeAutoStart:
    def __init__(self):
        self.enabled = False

    def enable(self):
        self.enabled = True
        return True

    def disable(self):
        self.enabled = False
        return True


class FakeThumbnailWorker(QObject):
    result = Signal(str, int, object, str)
    finished = Signal()

    def __init__(self, device, token, store, options, credentials, parent):
        super().__init__(parent)
        self.device, self.token, self.credentials = device, token, credentials
        self.cancel = threading.Event()

    def start(self):
        pass


def frame():
    image = QImage(64, 36, QImage.Format.Format_RGB32)
    image.fill(0)
    return image


class ManagedControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.vault = MemoryVault()
        patcher = patch('camera_monitor.credentials.CredentialStore.vault', lambda _self: self.vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = make_window(self)
        self.requests = []
        self.window.thumbnails.request = lambda device, credentials=None: self.requests.append(
            (device.ip, credentials))
        self.channel = FakeChannel()
        self.uploader = FakeUploader()
        self.autostart = FakeAutoStart()
        self.choice = None
        self.restarts = []
        self.managed = ManagedController(self.window, self.channel, self.uploader, self.autostart,
                                         escape=lambda window: self.choice,
                                         restart=lambda: self.restarts.append(1))

    def command(self, name, args=None, command_id='c1'):
        self.managed.run_command({'type': 'command', 'id': command_id, 'name': name, 'args': args or {}})

    def last(self, kind):
        return self.channel.of(kind)[-1]

    # ---------- 进出 ----------

    def test_entering_locks_everything_and_enables_autostart(self):
        self.managed.enter()

        self.assertTrue(self.window.wall.locked)
        self.assertTrue(self.window.presentation)
        self.assertTrue(self.autostart.enabled)
        self.assertTrue(self.managed.shortcut.isEnabled())

    def test_leaving_undoes_it(self):
        self.managed.enter()
        self.window.set_managed_fullscreen(True)
        # offscreen 平台会稍后重放窗口状态请求，进出之间要先让它落定
        QApplication.processEvents()

        self.managed.leave()

        self.assertFalse(self.window.wall.locked)
        self.assertFalse(self.window.presentation)
        self.assertIsNone(self.window.managed_fullscreen)
        self.assertFalse(self.autostart.enabled)

    def test_nothing_is_uploaded_or_reported_after_leaving(self):
        self.managed.enter()
        self.window.devices = {'10.0.0.7': Device('10.0.0.7')}
        self.command('refresh_snapshots', {'ips': ['10.0.0.7']})
        self.window.run_scan = lambda target_ip=None: True
        self.command('scan', command_id='s')
        sent = len(self.channel.sent)

        self.managed.leave()
        self.managed.on_thumbnail('10.0.0.7', frame(), '抓拍画面')
        self.window.scan_completed.emit(False)

        self.assertEqual([], self.uploader.submitted, '离开托管后不能再往云端传截图')
        self.assertEqual(sent, len(self.channel.sent))
        self.assertEqual((set(), set(), set(), None),
                         (self.managed.snapshot_wanted, self.managed.scan_dropped,
                          self.managed.seen_playing, self.managed.scan_command))

    def test_an_empty_wall_shows_the_waiting_notice(self):
        self.managed.enter()

        self.assertFalse(self.managed.waiting.isHidden())
        self.assertIn('等待管理员配置', self.managed.waiting.text())

        self.window.wall.add_device(Device('10.0.0.1'))
        self.managed.refresh_overlays()
        self.assertTrue(self.managed.waiting.isHidden())

    def test_apply_sets_fullscreen_and_the_escape_password(self):
        self.managed.enter()
        record = ('pbkdf2_sha256$240000$000102030405060708090a0b0c0d0e0f$'
                  '6779a48d726cc20519aa5f663a475da180d9ddcf213ec794eef28f7ed9f9bc4e')

        self.managed.apply(SimpleNamespace(fullscreen=True, escape_password_hash=record))

        self.assertTrue(self.window.managed_fullscreen)
        self.assertTrue(self.window.screen_lock.verify('246810'))

    # ---------- 动作 ----------

    def test_commands_are_refused_outside_managed_mode(self):
        self.command('scan')

        self.assertEqual({'type': 'ack', 'id': 'c1', 'ok': False, 'error': 'NOT_MANAGED'},
                         self.last('ack'))

    def test_unknown_commands_are_refused(self):
        self.managed.enter()
        self.command('format_disk')

        self.assertEqual('UNKNOWN_COMMAND', self.last('ack')['error'])

    def test_scan_reports_what_it_found_and_fetches_snapshots(self):
        self.managed.enter()
        scans = []
        self.window.run_scan = lambda target_ip=None: scans.append(target_ip) or True

        self.command('scan', {'target_ip': '192.168.2.216'})
        self.assertEqual(['192.168.2.216'], scans)
        self.assertTrue(self.last('ack')['ok'])

        self.window.devices = {'192.168.2.216': Device('192.168.2.216', model='IPC', manufacturer='Dahua',
                                                       protocols=['ONVIF'], urls=['http://x'])}
        self.window.scan_completed.emit(False)

        result = self.last('scan_result')
        self.assertEqual('c1', result['id'])
        self.assertEqual([{'ip': '192.168.2.216', 'model': 'IPC', 'manufacturer': 'Dahua',
                           'protocols': ['ONVIF'], 'onvif_urls': ['http://x']}], result['devices'])
        self.assertIn('192.168.2.216', self.managed.snapshot_wanted)
        self.assertEqual('192.168.2.216', self.requests[-1][0])

    def test_snapshots_dropped_by_a_scan_are_requested_again(self):
        self.managed.enter()
        self.window.wall.add_device(Device('10.0.0.7'))
        self.command('refresh_snapshots', {'ips': ['10.0.0.7']}, command_id='r')
        self.window.devices = {'10.0.0.9': Device('10.0.0.9')}
        self.command('refresh_snapshots', {'ips': ['10.0.0.9']}, command_id='r2')
        self.assertEqual(['10.0.0.7', '10.0.0.9'], [ip for ip, _ in self.requests])

        def run_scan(target_ip=None):
            # 真的 run_scan 一开始就会丢掉所有排队的抓图，全量搜索还会清空设备表
            self.window.thumbnails.cancel_all()
            self.window.devices = {}
            return True
        self.window.run_scan = run_scan
        self.command('scan', command_id='s')
        self.window.scan_completed.emit(False)

        self.assertEqual(['10.0.0.7', '10.0.0.9', '10.0.0.7'], [ip for ip, _ in self.requests],
                         '墙上那台还在，要重新排上；这次没搜到的那台不必再等')
        self.assertEqual({'10.0.0.7'}, self.managed.snapshot_wanted)

    def test_a_second_scan_while_busy_is_refused(self):
        self.managed.enter()
        self.window.run_scan = lambda target_ip=None: True

        self.command('scan', command_id='a')
        self.command('scan', command_id='b')

        self.assertEqual('BUSY', self.last('ack')['error'])

    def test_a_failing_command_is_closed_out_instead_of_hanging(self):
        self.managed.enter()

        def broken(target_ip=None):
            raise RuntimeError('boom')
        self.window.run_scan = broken
        with self.assertLogs('camera_monitor.managed_mode', 'ERROR'):
            self.command('scan', command_id='x')

        self.assertEqual({'type': 'command_done', 'id': 'x', 'ok': False, 'error': 'FAILED'},
                         self.last('command_done'))
        self.window.run_scan = lambda target_ip=None: True
        self.command('scan', command_id='y')
        self.assertTrue(self.last('ack')['ok'], '失败的那次不能把搜索一直占着')

    def test_a_handler_failing_before_its_ack_still_acks(self):
        self.managed.enter()

        def broken(command_id, args):
            raise RuntimeError('boom')
        self.managed.cmd_reconnect_all = broken
        with self.assertLogs('camera_monitor.managed_mode', 'ERROR'):
            self.command('reconnect_all', command_id='z')

        self.assertEqual([{'type': 'ack', 'id': 'z', 'ok': False, 'error': 'FAILED'}],
                         self.channel.of('ack'))
        self.assertEqual('FAILED', self.last('command_done')['error'])

    def test_a_handler_failing_after_its_ack_does_not_ack_twice(self):
        self.managed.enter()

        def broken():
            raise RuntimeError('boom')
        self.window.wall.reconnect_all = broken
        with self.assertLogs('camera_monitor.managed_mode', 'ERROR'):
            self.command('reconnect_all', command_id='z')

        self.assertEqual([{'type': 'ack', 'id': 'z', 'ok': True}], self.channel.of('ack'))
        self.assertEqual('FAILED', self.last('command_done')['error'])

    def test_remotely_scanned_cameras_get_the_default_login_with_the_real_queue(self):
        # 用真的 ThumbnailController：finish_scan 自己会先给每台排一次（不带账号），
        # 托管这边再带着默认账号要一次，不能因为「已经在抓了」就被吞掉
        del self.window.thumbnails.request
        created = []

        def factory(*args):
            worker = FakeThumbnailWorker(*args)
            created.append(worker)
            return worker
        self.window.thumbnails.worker_factory = factory
        self.managed.enter()
        self.window.wall.credential_store.save(DEFAULT_CREDENTIAL_ACCOUNT, 'admin', 'dflt')
        self.window.run_scan = lambda target_ip=None: True
        self.command('scan', command_id='s')
        self.window.devices = {ip: Device(ip) for ip in ('10.0.0.1', '10.0.0.2', '10.0.0.3')}

        self.window.finish_scan()

        self.assertEqual(['10.0.0.1', '10.0.0.2'], [w.device.ip for w in created])
        for worker in list(created):
            worker.result.emit(worker.device.ip, worker.token, None, '认证失败，请检查账号密码')
            worker.finished.emit()
        # 让排在最后的那台也跑起来
        next(w for w in created if w.device.ip == '10.0.0.3').finished.emit()
        started = {(w.device.ip, w.credentials) for w in created}
        for ip in ('10.0.0.1', '10.0.0.2', '10.0.0.3'):
            self.assertIn((ip, ('admin', 'dflt')), started)

        retried = next(w for w in created if w.device.ip == '10.0.0.1' and w.credentials)
        retried.result.emit('10.0.0.1', retried.token, frame(), '抓拍画面 · 点击放大')
        self.assertEqual([('camera', '10.0.0.1')], self.uploader.submitted)

    def test_a_scan_result_that_cannot_be_built_still_closes_the_command(self):
        self.managed.enter()
        self.window.run_scan = lambda target_ip=None: True
        self.command('scan', command_id='s')
        self.window.devices = {'10.0.0.7': Device('10.0.0.7', protocols=5)}

        with self.assertLogs('camera_monitor.managed_mode', 'ERROR'):
            self.window.scan_completed.emit(False)

        self.assertEqual({'type': 'command_done', 'id': 's', 'ok': False, 'error': 'FAILED'},
                         self.last('command_done'))
        self.command('scan', command_id='t')
        self.assertTrue(self.last('ack')['ok'])

    def test_a_bad_target_is_refused(self):
        self.managed.enter()

        self.command('scan', {'target_ip': '8.8.8.8; rm -rf'})

        self.assertEqual('BAD_TARGET', self.last('ack')['error'])

    def test_discovered_cameras_without_their_own_login_use_the_default(self):
        self.managed.enter()
        self.window.wall.credential_store.save(DEFAULT_CREDENTIAL_ACCOUNT, 'admin', 'dflt')
        self.window.devices = {'10.0.0.7': Device('10.0.0.7')}

        self.command('refresh_snapshots')

        self.assertEqual(('10.0.0.7', ('admin', 'dflt')), self.requests[-1])
        self.assertTrue(self.last('command_done')['ok'])

    def test_a_wanted_thumbnail_is_uploaded_once(self):
        self.managed.enter()
        self.window.devices = {'10.0.0.7': Device('10.0.0.7')}
        self.command('refresh_snapshots', {'ips': ['10.0.0.7']})

        self.managed.on_thumbnail('10.0.0.7', frame(), '抓拍画面')
        self.managed.on_thumbnail('10.0.0.7', frame(), '抓拍画面')
        self.managed.on_thumbnail('10.0.0.8', frame(), '抓拍画面')

        self.assertEqual([('camera', '10.0.0.7')], self.uploader.submitted)

    def test_capture_screen_uploads_the_window(self):
        self.managed.enter()

        self.command('capture_screen')

        self.assertEqual([('screen', '')], self.uploader.submitted)
        self.assertTrue(self.last('command_done')['ok'])

    def test_reconnect_all(self):
        self.managed.enter()
        calls = []
        self.window.wall.reconnect_all = lambda: calls.append(1)

        self.command('reconnect_all')

        self.assertEqual([1], calls)

    def test_restart_is_acknowledged_before_it_happens(self):
        self.managed.enter()

        self.command('restart_app')

        self.assertTrue(self.last('ack')['ok'])
        self.assertTrue(self.last('command_done')['ok'], '重启后没人回报这条命令，必须先报完成')
        acked = self.channel.sent.index(self.last('ack'))
        self.assertLess(acked, self.channel.sent.index(self.last('command_done')))
        self.assertTrue(self.window.authorized_quit)
        self.assertEqual([], self.restarts, '重启要等一会儿，让消息先发出去')

    # ---------- 状态 ----------

    def test_status_is_reported_on_change_only(self):
        self.managed.enter()
        self.window.wall.add_device(Device('10.0.0.1'))
        tile = self.window.wall.tiles[0]
        tile.player.status.setText('正在播放 · 1280 × 720 · H264')

        self.managed.report_status()
        self.managed.report_status()

        statuses = self.channel.of('status')
        self.assertEqual(1, len(statuses))
        self.assertEqual([{'ip': '10.0.0.1', 'state': 'playing'}], statuses[0]['cameras'])
        self.assertIn('config_version', statuses[0])

        tile.player.status.setText('视频认证失败，请检查摄像头用户名和密码。')
        self.managed.report_status()
        self.assertEqual('auth_failed', self.channel.of('status')[-1]['cameras'][0]['state'])

    def test_the_first_frame_triggers_one_snapshot(self):
        self.managed.enter()
        self.window.wall.add_device(Device('10.0.0.1'))
        tile = self.window.wall.tiles[0]
        tile.player.status.setText('正在播放 · 1280 × 720 · H264')

        self.managed.report_status()
        self.assertEqual([], self.requests, '还没画出第一帧，抓图会再开一路流')

        tile.player.surface._image = frame()
        self.managed.report_status()
        self.managed.report_status()
        self.assertEqual(['10.0.0.1'], [ip for ip, _ in self.requests], '第一次播起来自动补一张截图')
        self.assertIn('10.0.0.1', self.managed.snapshot_wanted)

    def test_a_failing_status_tick_is_logged_and_the_next_tick_still_reports(self):
        self.managed.enter()
        real = self.managed.status_payload
        failures = [RuntimeError('boom'), RuntimeError('boom again')]

        def flaky():
            if failures:
                raise failures.pop(0)
            return real()
        self.managed.status_payload = flaky

        with self.assertLogs('camera_monitor.managed_mode', 'ERROR') as logs:
            self.managed.report_status()
            self.managed.report_status()
        self.assertEqual(1, len(logs.records), '每秒一拍，同一种错一分钟内只记一次')

        self.managed.report_status()
        self.assertEqual(1, len(self.channel.of('status')))

    def test_status_waits_while_offline(self):
        self.managed.enter()
        self.channel.online = False

        self.managed.report_status()
        self.channel.online = True
        self.managed.report_status()

        self.assertEqual(1, len(self.channel.of('status')), '离线那一拍没发出去，联网后要补')

    # ---------- 断线提示、逃生口 ----------

    def test_the_offline_notice_waits_before_showing(self):
        self.managed.enter()
        self.channel.online = False

        self.managed.on_online_changed(False)
        self.assertTrue(self.managed.offline.isHidden())
        self.assertTrue(self.managed.offline_timer.isActive())

        self.managed.show_offline()
        self.assertFalse(self.managed.offline.isHidden())

        self.channel.online = True
        self.managed.on_online_changed(True)
        self.assertTrue(self.managed.offline.isHidden())

    def test_being_replaced_says_so_instead_of_reconnecting(self):
        # 被顶号后通道不会再重连，「正在自动重连」是在骗人
        self.managed.enter()
        self.channel.online = False
        self.managed.on_online_changed(False)

        self.managed.show_replaced()

        self.assertFalse(self.managed.offline_timer.isActive())
        self.assertFalse(self.managed.offline.isHidden())
        self.assertIn('另一台电脑', self.managed.offline.text())
        self.assertNotIn('重连', self.managed.offline.text())

    def test_the_offline_notice_comes_back_after_being_replaced(self):
        original = self.managed.offline.text()
        self.managed.enter()
        self.managed.show_replaced()

        self.channel.online = True
        self.managed.on_online_changed(True)
        self.assertEqual(original, self.managed.offline.text())

        self.managed.show_replaced()
        self.managed.leave()
        self.assertEqual(original, self.managed.offline.text())
        self.assertTrue(self.managed.offline.isHidden())

        self.managed.show_replaced()
        self.managed.enter()
        self.assertEqual(original, self.managed.offline.text())

    def test_escape_quit(self):
        self.managed.enter()
        closed = []
        self.window.close = lambda: closed.append(1)
        self.choice = EscapeDialog.QUIT

        self.managed.open_escape()

        self.assertTrue(self.window.authorized_quit)
        self.assertEqual([1], closed)

    def test_escape_unbind(self):
        self.managed.enter()
        unbinds = []
        self.window.unbind_cloud = lambda message='': unbinds.append(message)
        self.choice = EscapeDialog.UNBIND

        self.managed.open_escape()

        self.assertEqual([''], unbinds)

    def test_a_cancelled_escape_does_nothing(self):
        self.managed.enter()
        self.choice = None

        self.managed.open_escape()

        self.assertFalse(self.window.authorized_quit)


class EscapeKeysTests(unittest.TestCase):
    def test_macos_avoids_the_system_log_out_shortcut(self):
        # Qt 在 macOS 上把 Ctrl 映射成 ⌘，⌘⇧⌥Q 是系统「立即注销」；Meta 才是物理 Control 键
        self.assertEqual('Meta+Shift+Alt+Q', escape_keys('darwin'))

    def test_other_platforms_use_control(self):
        self.assertEqual('Ctrl+Shift+Alt+Q', escape_keys('win32'))
        self.assertEqual('Ctrl+Shift+Alt+Q', escape_keys('linux'))


class EscapeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_wrong_password_keeps_the_dialog_open(self):
        lock = SimpleNamespace(verify=lambda password: password == '246810')
        dialog = EscapeDialog(lock)
        self.addCleanup(dialog.deleteLater)

        dialog.password.setText('000000')
        dialog.quit_button.click()
        self.assertIsNone(dialog.choice)
        self.assertEqual('密码不正确。', dialog.error.text())

        dialog.password.setText('246810')
        dialog.unbind_button.click()
        self.assertEqual(EscapeDialog.UNBIND, dialog.choice)


if __name__ == '__main__':
    unittest.main()
