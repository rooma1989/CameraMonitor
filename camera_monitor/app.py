from __future__ import annotations
from .choices import ChoiceButton as QComboBox

import json
import logging
import logging.handlers
import os
import platform
import sys
import threading
from PySide6.QtCore import QThread, Signal, Qt, QTimer, QEvent, QSettings, QStandardPaths
from PySide6.QtGui import QShortcut, QKeySequence, QIcon
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QBoxLayout, QInputDialog, QCheckBox,
    QAbstractItemView, QPlainTextEdit, QProgressBar, QSplitter, QFrame, QScrollArea, QStackedWidget, QSizePolicy)
from . import __version__
from .discovery import Device, interfaces, scan, validate_target_ip
from .playback import PlayerWindow
from .device_list import DeviceList
from .multiview import MultiView
from .screen_lock import ScreenLock,request_unlock,PasswordSettingsDialog
from .thumbnails import ThumbnailController,ThumbnailPreview
from .connection_options import ConnectionOptions
from .cloud_panel import CloudPanel
from .cloud_state import apply_snapshot, cacheable, camera_entry, flag, layout_entry, stream_mode
from .cloud_sync import CloudSync
from . import startup
from .welcome import WelcomePage
from .autostart import (AutoStart, FLAG as AUTOSTART_FLAG, WANTED_KEY as AUTOSTART_KEY,
    failure_message as autostart_failure, reconcile as reconcile_autostart)
from .cloud import DEFAULT_BASE_URL
from .cloud_channel import CloudChannel, channel_url
from .managed_mode import ManagedController
from .snapshots import SnapshotUploader

logger=logging.getLogger(__name__)
LOG_FILE='camera_monitor.log'
LOG_MAX_BYTES=1024*1024
LOG_BACKUPS=3
# 完整模式「启动后自动全屏」；它和 monitor/autostart 一样跟着 layout 同步到云端
START_FULLSCREEN_KEY='monitor/start_fullscreen'
# 不登录云端的电脑存的本机快照，格式同云端配置（剥掉密码）。local/ 不算启动分流里的「用过」
LOCAL_SNAPSHOT_KEY='local/snapshot'
LOCAL_SNAPSHOT_DELAY_MS=1000

# 后台永久拒绝时给现场看的话：服务端原话面向管理员，这里说清楚该找谁、该做什么
REVOKED_MESSAGES={
    'PROFILE_DISABLED':'该设备码已被管理员停用，请联系管理员。',
    'PROFILE_IN_USE':'该设备码已在另一台电脑上使用，请联系管理员解绑后重试。',
    'INVALID_AUTH_CODE':'设备码已失效，请重新输入。',
}


class SearchWorker(QThread):
    device = Signal(object)
    message = Signal(str)

    def __init__(self, networks, parent=None, target_ip=None):
        super().__init__(parent)
        self.networks = networks
        self.target_ip = target_ip
        self.cancel = threading.Event()

    def run(self):
        try:
            scan(self.networks, self.device.emit, self.message.emit, self.cancel, target_ip=self.target_ip)
        except Exception as exc:
            self.message.emit(f'搜索发生错误：{exc}')


class Window(QMainWindow):
    scan_completed=Signal(bool)

    def __init__(self, device_names=None, cloud_settings=None, connection_options=None, autostart=None):
        super().__init__()
        from .device_names import DeviceNames
        self.device_names = device_names if device_names is not None else DeviceNames()
        self.device_names.changed.connect(self.refresh_device_name)
        cloud_settings=cloud_settings if cloud_settings is not None else QSettings('CameraMonitor','Cloud')
        # 必须赶在 ScreenLock 之前判断：它一构造就往 DeviceNames 的设置里写初始密码
        self.startup_route=startup.startup_route(cloud_settings,self.device_names.settings)
        self.closing=False
        self.autostart=autostart if autostart is not None else AutoStart()
        self.worker = None
        self.target_ip = None
        self.target_received = False
        self.devices = {}
        self.networks = []
        self.players = []
        self.wall = None
        self.current_settings = None
        self.batch_panel = None
        self.session_credentials = {}
        self.presentation = False
        # None：不是傻瓜模式；True / False：云端要求全屏 / 窗口
        self.managed_fullscreen=None
        # 退出傻瓜模式后等窗口状态落定。macOS 的全屏是 0.5~1 秒的动画，动画期间窗口
        # 状态还不是全屏；这时 showMaximized() 会被 Qt 当成"已经是这个状态"直接忽略，
        # 动画结束才报"全屏"，按普通逻辑会被当成用户自己进了演示模式，随后再被拉回
        # 最大化时弹密码框。落定前遇到全屏就再要一次最大化，等到最大化了才恢复正常判断。
        self.settling_state=None
        self.settle_timer=QTimer(self);self.settle_timer.setSingleShot(True);self.settle_timer.setInterval(3000)
        self.settle_timer.timeout.connect(self.stop_settling)
        self.authorized_quit=False
        self.screen_lock=ScreenLock(self.device_names.settings)
        self.unlock_prompt_active=False
        self.authorized_exit=False
        self.setWindowTitle('Camera Monitor · 内网监控中心')
        self.setWindowIcon(QIcon(str(Path(__file__).parent/'assets'/'app-icon.png')))
        self.resize(1586, 960)
        self.setMinimumSize(1100, 720)
        root = QWidget()
        self.setCentralWidget(root)
        layout = self.root_layout = QVBoxLayout(root)
        layout.setContentsMargins(20,20,20,20)
        layout.setSpacing(12)
        header=QHBoxLayout()
        title=QLabel('内网监控中心');title.setObjectName('title')
        header.addWidget(title)
        header.addStretch()
        badge=QLabel('局域网设备 · 实时预览');badge.setObjectName('muted')
        header.addWidget(badge)
        title.hide();badge.hide()
        layout.addLayout(header)
        split=self.split=QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split,1)
        sidebar=self.sidebar=QWidget();sidebar.setMinimumWidth(215);sidebar.setMaximumWidth(255)
        side=QVBoxLayout(sidebar);side.setContentsMargins(0,0,12,0);side.setSpacing(10)
        label=QLabel('设备');label.setStyleSheet('font-size:23px;font-weight:700;');side.addWidget(label)
        self.device_filter=QLineEdit();self.device_filter.setPlaceholderText('搜索设备名称或 IP')
        self.device_filter.textChanged.connect(self.filter_devices)
        side.addWidget(self.device_filter)
        self.network=QComboBox()
        self.network.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)

        actions=QHBoxLayout()
        self.search=QPushButton('搜索设备');self.search.setObjectName('primary')
        self.search.clicked.connect(self.start_scan);actions.addWidget(self.search)
        self.stop=QPushButton('停止');self.stop.setEnabled(False)
        self.stop.clicked.connect(self.cancel_scan);actions.addWidget(self.stop)
        side.addLayout(actions)
        self.add_ip=QPushButton('按 IP 添加')
        self.add_ip.clicked.connect(self.prompt_target_ip)
        side.addWidget(self.add_ip)
        self.refresh=QPushButton('刷新网络');self.refresh.clicked.connect(self.refresh_networks)

        self.status=QLabel('准备就绪 · 点击搜索设备');self.status.setWordWrap(True)
        self.status.setObjectName('muted')
        self.status.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Minimum)
        side.addWidget(self.status)
        self.progress=QProgressBar();self.progress.setRange(0,1);self.progress.setValue(0)
        self.progress.setTextVisible(False);self.progress.setFixedHeight(3);side.addWidget(self.progress)
        self.table=DeviceList(compact=True)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.table.cellDoubleClicked.connect(lambda row,column:self.open_player())
        side.addWidget(self.table,1)
        self.play=QPushButton('连接设置');self.play.setObjectName('primary')
        self.play.setEnabled(False);self.play.clicked.connect(self.open_player);side.addWidget(self.play)
        self.play.hide()
        self.copy=QPushButton('复制 IP');self.copy.setEnabled(False);self.copy.clicked.connect(self.copy_ip)

        self.batch_button=QPushButton('批量账号密码');self.batch_button.clicked.connect(self.show_batch_settings)
        side.addWidget(self.batch_button)
        self.cloud_panel=CloudPanel()
        self.cloud_panel.login_requested.connect(self.cloud_login)
        self.cloud_panel.logout_requested.connect(self.cloud_logout)
        side.addWidget(self.cloud_panel)
        self.detail_toggle=QPushButton('网络与设备详情');self.detail_toggle.setCheckable(True)
        side.addWidget(self.detail_toggle)
        self.diagnostics=QWidget();diagnostic_layout=QVBoxLayout(self.diagnostics)
        diagnostic_layout.setContentsMargins(0,0,0,0)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumHeight(100)
        self.details.setPlaceholderText('选择设备查看详情')
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumHeight(70)
        diagnostic_layout.addWidget(self.network);diagnostic_layout.addWidget(self.refresh);diagnostic_layout.addWidget(self.copy)
        diagnostic_layout.addWidget(self.details);diagnostic_layout.addWidget(self.log)
        self.diagnostics.hide();side.addWidget(self.diagnostics)
        self.detail_toggle.toggled.connect(self.diagnostics.setVisible)
        split.addWidget(sidebar)
        workspace=QWidget();work=self.work_layout=QVBoxLayout(workspace);work.setContentsMargins(0,0,0,0)
        self.connection_options=connection_options if connection_options is not None else ConnectionOptions()
        self.wall=MultiView([],self,embedded=True,device_names=self.device_names,
            connection_options=self.connection_options)
        self.wall.playing.connect(self.video_verified)
        self.wall.device_status.connect(self.update_device_status)
        self.wall.fullscreen_requested.connect(self.toggle_fullscreen)
        self.wall.finished.connect(self.release_multiview)
        self.wall.settings_requested.connect(self.show_settings)
        self.wall.tile_removing.connect(self.remove_settings)
        work.addWidget(self.wall,1)
        work.setContentsMargins(0,0,42,0)
        self.settings_panel=QFrame(root);self.settings_panel.setObjectName('settingsPanel')
        panel=QVBoxLayout(self.settings_panel);panel.setContentsMargins(10,6,10,6)
        panel_header=QHBoxLayout()
        panel_header.addWidget(QLabel('连接设置'),1)
        collapse=QPushButton('收起设置');collapse.clicked.connect(self.close_settings)
        panel_header.addWidget(collapse);panel.addLayout(panel_header)
        self.settings_scroll=QScrollArea();self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setMinimumHeight(200)
        self.settings_stack=QStackedWidget();self.settings_scroll.setWidget(self.settings_stack)
        panel.addWidget(self.settings_scroll)
        self.settings_panel.hide()
        self.settings_tab=QPushButton('连\n接\n设\n置',root)
        self.settings_tab.setStyleSheet('padding:4px; color:#405570;')
        self.settings_tab.clicked.connect(self.open_selected_settings)
        self.settings_tab.setToolTip('选择左侧设备后打开连接设置')
        split.addWidget(workspace);split.setStretchFactor(1,1);split.setSizes([225,1320])
        self.setStyleSheet('''
            QWidget { background:#f7f9fc; color:#203247; font-size:13px; }
            QLabel#title { font-size:24px; font-weight:700; }
            QLabel#muted { color:#64748b; font-size:12px; }
            QPushButton { background:white; border:1px solid #d7dfe9; border-radius:6px; padding:7px 10px; }
            QPushButton:hover { background:#eaf0fa; }
            QPushButton#primary { background:#2463eb; color:white; border:1px solid #2463eb; font-weight:600; }
            QPushButton:disabled { background:#e9edf2; color:#8693a4; border-color:#e0e5ec; }
            QLineEdit,QSpinBox,QPlainTextEdit { background:white; border:1px solid #d7dfe9; border-radius:5px; padding:5px; }
            QScrollArea { border:none; }
            QFrame#settingsPanel { background:white; border:1px solid #cbd8e8; border-radius:8px; }
            QProgressBar { border:none; background:#e5eaf2; }
            QProgressBar::chunk { background:#2463eb; }
            QSplitter::handle { background:#dce4ee; width:1px; }
        ''')
        self.escape_shortcut=QShortcut(QKeySequence('Escape'),self)
        self.escape_shortcut.activated.connect(self.exit_fullscreen)
        self.fullscreen_shortcut=QShortcut(QKeySequence('F11'),self)
        self.fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        self.thumbnails=ThumbnailController(store=self.wall.credential_store,
            connection_options=self.connection_options,live_image=self.live_thumbnail,
            connection=self.thumbnail_connection,parent=self)
        self.thumbnails.updated.connect(self.update_thumbnail)
        self.table.thumbnailClicked.connect(self.show_thumbnail)
        self.thumbnail_timer=QTimer(self);self.thumbnail_timer.setInterval(2500)
        self.thumbnail_timer.timeout.connect(self.refresh_live_thumbnails);self.thumbnail_timer.start()
        self.password_settings=QPushButton('大屏密码')
        self.password_settings.clicked.connect(self.show_password_settings)
        self.wall.toolbar_widget.layout().addWidget(self.password_settings)
        # 完整模式的两个开关。工具栏在傻瓜模式下整条隐藏，这两个也跟着看不见
        self.autostart_toggle=QCheckBox('开机自动启动')
        self.autostart_toggle.setToolTip('电脑开机登录后自动打开监控软件。')
        self.start_fullscreen_toggle=QCheckBox('启动后自动全屏')
        self.start_fullscreen_toggle.setToolTip('软件打开时直接进入全屏（墙上要有摄像头）；退出全屏仍要输入大屏密码。')
        self.refresh_startup_toggles()
        self.autostart_toggle.toggled.connect(self.set_autostart_wanted)
        self.start_fullscreen_toggle.toggled.connect(self.set_start_fullscreen)
        for box in (self.autostart_toggle,self.start_fullscreen_toggle):self.wall.toolbar_widget.layout().addWidget(box)
        self.applying_cloud=False
        # 正在回放本机快照：这期间本机存储发出的「变了」是回放自己写的，不能再存一遍
        self.replaying_local=False
        self.local_snapshot_timer=QTimer(self);self.local_snapshot_timer.setSingleShot(True)
        self.local_snapshot_timer.setInterval(LOCAL_SNAPSHOT_DELAY_MS)
        self.local_snapshot_timer.timeout.connect(self.save_local_snapshot)
        self.cloud=CloudSync(self.device_names,self.connection_options,self.wall.credential_store,
            self.collect_cloud_payload,parent=self,settings=cloud_settings)
        self.cloud.status.connect(self.cloud_panel.set_status)
        self.cloud.applied.connect(self.apply_cloud_config)
        self.cloud.session_changed.connect(self.cloud_session_changed)
        self.cloud.login_result.connect(self.cloud_login_result)
        self.cloud.mode_changed.connect(self.on_cloud_mode)
        self.cloud.revoked.connect(self.on_cloud_revoked)
        self.cloud.storage_unavailable.connect(self.cloud_storage_unavailable)
        self.cloud.startup_switches_changed.connect(self.cloud_startup_switches_changed)
        self.channel=CloudChannel(channel_url(getattr(self.cloud.client,'base_url',DEFAULT_BASE_URL)),
            self.channel_hello,parent=self)
        self.channel.message.connect(self.on_channel_message)
        self.channel.denied.connect(self.on_channel_denied)
        self.channel.replaced.connect(self.on_channel_replaced)
        # 被别处顶掉之后，只有人亲手重新登录成功才重连；静默重登不算（见 on_channel_replaced）
        self.channel_replaced=False
        self.manual_login=False
        self.snapshot_uploader=SnapshotUploader(self.upload_snapshot,self)
        self.managed=ManagedController(self,self.channel,self.snapshot_uploader,self.autostart)
        # 同样只用绑定方法：device_names 的生命周期可能比窗口长
        self.device_names.changed.connect(self.note_cloud_change)
        self.device_names.appearance_changed.connect(self.note_cloud_change)
        self.wall.layout_changed.connect(self.note_cloud_change)
        self.cloud_panel.set_connected(self.cloud.enabled(),self.cloud.profile_name())
        self.refresh_networks()
        self.welcome=WelcomePage(root)
        self.welcome.login_requested.connect(self.welcome_login)
        self.welcome.standalone_chosen.connect(self.use_standalone)
        self.set_welcome_visible(self.startup_route==startup.WELCOME)
        # 用窗口自己持有的定时器，而不是静态的 QTimer.singleShot：后者排进全局事件
        # 队列，窗口若在事件循环跑起来之前就被销毁，这个事件仍会触发并访问已释放的对象。
        self.cloud_start_timer=QTimer(self)
        self.cloud_start_timer.setSingleShot(True)
        self.cloud_start_timer.timeout.connect(self.start_cloud)
        self.cloud_start_timer.start(0)
        # 「启动后自动全屏」等启动时铺完墙再判断，只判断一次。同样用窗口自己的定时器
        self.auto_fullscreen_pending=True
        self.auto_fullscreen_timer=QTimer(self)
        self.auto_fullscreen_timer.setSingleShot(True)
        self.auto_fullscreen_timer.timeout.connect(self.auto_fullscreen)

    def set_welcome_visible(self,visible):
        # 欢迎页只是盖在上面的一层，键盘却会穿过去：Tab 走进看不见的侧边栏，F11 把
        # 窗口锁进演示模式。所以盖着的时候把下面整块禁用（split 含侧边栏和墙，欢迎页
        # 是 root 的子控件而不是 split 的，不会被连带禁掉），快捷键也一并关掉。
        self.split.setEnabled(not visible);self.settings_tab.setEnabled(not visible)
        self.escape_shortcut.setEnabled(not visible);self.fullscreen_shortcut.setEnabled(not visible)
        self.welcome.setVisible(visible)
        if visible:self.position_overlay()

    def show_settings(self, player):
        # 欢迎页盖着时不能把设置面板抬到它上面，否则绕过了对下层的禁用
        if self.presentation or not self.welcome.isHidden():return
        if self.settings_stack.indexOf(player)<0:
            player.setParent(self.settings_stack,Qt.WindowType.Widget)
            player.setMinimumSize(0,0)
            player.layout().setContentsMargins(8,4,8,4)
            player.layout().setSpacing(10)
            player.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
            player.note.hide()
            for i in range(player.layout().count()):
                child=player.layout().itemAt(i).layout()
                if isinstance(child,QHBoxLayout):
                    child.setDirection(QBoxLayout.Direction.TopToBottom)
                    child.setAlignment(Qt.AlignmentFlag.AlignTop)
                    for j in range(child.count()):child.setStretch(j,0)
            player.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
            player.settings_hidden.connect(lambda:self.settings_auto_hidden(player))
            self.settings_stack.addWidget(player)
        self.current_settings=player
        self.settings_stack.setCurrentWidget(player)
        self.position_overlay()
        self.settings_panel.show()
        self.settings_panel.raise_()
        player.show()

    def position_overlay(self):
        root=self.centralWidget()
        self.settings_panel.setGeometry(max(0,root.width()-390),20,370,max(200,root.height()-40))
        self.settings_tab.setGeometry(max(0,root.width()-48),26,36,108)
        if hasattr(self,'managed'):self.managed.refresh_overlays()
        if hasattr(self,'welcome'):
            self.welcome.setGeometry(root.rect())
            if not self.welcome.isHidden():self.welcome.raise_()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'settings_panel'):self.position_overlay()

    def set_presentation(self, enabled):
        self.presentation=enabled
        if enabled:self.close_settings()
        self.sidebar.setVisible(not enabled);self.settings_tab.setVisible(not enabled)
        self.root_layout.setContentsMargins(*((0,0,0,0) if enabled else (20,20,20,20)))
        self.root_layout.setSpacing(0 if enabled else 12)
        self.work_layout.setContentsMargins(*((0,0,0,0) if enabled else (0,0,42,0)))
        self.wall.set_presentation(enabled)

    def toggle_fullscreen(self):
        if self.managed_fullscreen is not None:return
        if self.presentation:self.exit_fullscreen();return
        self.was_maximized=self.isMaximized()
        # 用户亲手按的全屏不是动画收尾，别被落定逻辑拉回最大化
        self.stop_settling()
        self.set_presentation(True);self.showFullScreen()

    def live_thumbnail(self,ip):
        if self.wall:
            for tile in self.wall.tiles:
                if tile.player.device.ip==ip:return tile.player.surface._image
        return None

    def thumbnail_connection(self,device):
        if self.wall:
            for tile in self.wall.tiles:
                p=tile.player
                if p.device.ip==device.ip:
                    return {'mode':('onvif','dahua','manual')[p.mode.currentIndex()],
                            'channel':p.channel.value(),'url':p.manual.text(),
                            'transport':p.transport.currentData()}
        return {}

    def update_thumbnail(self,ip,image,status):
        for index,row in enumerate(self.table.rows):
            if self.table.item(index,0).text()==ip:
                self.table.setThumbnail(index,image,status);break

    def refresh_live_thumbnails(self):
        for device in self.devices.values():
            if self.live_thumbnail(device.ip) is not None:self.thumbnails.request(device)

    def show_thumbnail(self,row):
        if self.presentation or not 0<=row<len(self.table.rows):return
        image=self.table.rows[row].thumbnail_image
        if image is None or image.isNull():
            self.open_selected_settings();return
        ip=self.table.item(row,0).text()
        dialog=ThumbnailPreview(image,f'{self.device_names.display(self.devices[ip])} · {ip}',self)
        try:dialog.exec()
        finally:dialog.deleteLater()

    def refresh_startup_toggles(self):
        """开机启动显示系统启动项的实际状态，自动全屏显示设置。不触发上传。"""
        values=((self.autostart_toggle,self.autostart.is_enabled()),
            (self.start_fullscreen_toggle,flag(self.device_names.settings.value(START_FULLSCREEN_KEY,False))))
        for box,checked in values:
            box.blockSignals(True);box.setChecked(bool(checked));box.blockSignals(False)

    def sync_autostart(self):
        """按 monitor/autostart 对齐系统启动项。想要却设不上时提示原因，返回 False。"""
        ok=reconcile_autostart(self.autostart,self.device_names.settings)
        if not ok:self.status.setText(autostart_failure(getattr(self.autostart,'last_error','')))
        self.refresh_startup_toggles()
        return ok

    def set_autostart_wanted(self,checked):
        settings=self.device_names.settings
        settings.setValue(AUTOSTART_KEY,bool(checked));settings.sync()
        self.sync_autostart()
        self.note_cloud_change()

    def set_start_fullscreen(self,checked):
        # 只记下来，下次打开软件才生效：当场进全屏的话，人还没反应过来就要输大屏密码了
        settings=self.device_names.settings
        settings.setValue(START_FULLSCREEN_KEY,bool(checked));settings.sync()
        self.note_cloud_change()

    def show_password_settings(self):
        if self.presentation:return
        dialog=PasswordSettingsDialog(self.screen_lock,self)
        try:dialog.exec()
        finally:dialog.deleteLater()

    def exit_fullscreen(self):
        if self.managed_fullscreen is not None:return False
        if not self.presentation:return True
        if self.unlock_prompt_active:return False
        self.unlock_prompt_active=True
        try:allowed=request_unlock(self,self.screen_lock)
        finally:self.unlock_prompt_active=False
        if not allowed:return False
        self.authorized_exit=True
        try:
            self.set_presentation(False)
            self.showMaximized() if getattr(self,'was_maximized',False) else self.showNormal()
        finally:self.authorized_exit=False
        return True

    def set_managed_fullscreen(self,enabled):
        """傻瓜模式下全屏与否由云端决定：不弹密码，侧边栏始终收起。"""
        self.managed_fullscreen=bool(enabled)
        self.stop_settling()
        if not self.presentation:self.set_presentation(True)
        self.authorized_exit=True
        try:
            self.showFullScreen() if enabled else self.showMaximized()
        finally:self.authorized_exit=False

    def leave_managed_window(self):
        self.managed_fullscreen=None
        self.settling_state='maximized';self.settle_timer.start()
        self.authorized_exit=True
        try:
            self.set_presentation(False)
            self.showMaximized()
        finally:self.authorized_exit=False

    def stop_settling(self):
        self.settling_state=None;self.settle_timer.stop()

    def native_exit_requested(self):
        if self.managed_fullscreen is not None:return
        if not self.presentation or self.authorized_exit:return
        self.showFullScreen()
        self.exit_fullscreen()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.WindowStateChange and hasattr(self,'wall') and self.wall:
            if self.managed_fullscreen is not None:
                # 傻瓜模式：云端说全屏就一直全屏，被系统退出了就拉回来，不弹密码
                if self.managed_fullscreen and not self.isFullScreen() and not self.authorized_exit:
                    QTimer.singleShot(0,self.showFullScreen)
                return
            if self.settling_state:
                # 还在等退出傻瓜模式落定：迟到的全屏是动画收尾，不是用户按的，再要一次最大化
                if self.isFullScreen():QTimer.singleShot(0,self,self.showMaximized)
                elif self.isMaximized():self.stop_settling()
                return
            if self.isFullScreen() and not self.presentation:self.set_presentation(True)
            elif not self.isFullScreen() and self.presentation and not self.authorized_exit:
                # Restore presentation before prompting so Cancel cannot expose settings.
                QTimer.singleShot(0,self.native_exit_requested)

    def show_batch_settings(self):
        if self.presentation or not self.welcome.isHidden():return
        from .batch_settings import BatchSettings
        devices=dict(self.devices)
        devices.update({t.player.device.ip:t.player.device for t in self.wall.tiles})
        if not devices:self.status.setText('请先搜索或添加设备。');return
        if self.batch_panel is not None:
            self.settings_stack.removeWidget(self.batch_panel);self.batch_panel.deleteLater()
        self.close_settings()
        self.batch_panel=BatchSettings(list(devices.values()),self.device_names,self.apply_batch_credentials)
        self.settings_stack.addWidget(self.batch_panel);self.settings_stack.setCurrentWidget(self.batch_panel)
        self.position_overlay();self.settings_panel.show();self.settings_panel.raise_()

    def apply_batch_credentials(self, ips, username, password, remember):
        from .credentials import CredentialError
        count=0;failed=[]
        known=set(self.devices)|{t.player.device.ip for t in self.wall.tiles}
        for ip in dict.fromkeys(ips):
            if ip not in known:failed.append(ip);continue
            if remember:
                try:self.wall.credential_store.save(ip,username,password)
                except CredentialError:failed.append(ip);continue
            self.session_credentials[ip]=(username,password,remember)
            for tile in self.wall.tiles:
                if tile.player.device.ip==ip:
                    tile.player.set_connection_credentials(username,password,remember)
            count+=1
        return count,failed

    def filter_devices(self,text):
        if not hasattr(self,'table'):return
        for row in self.table.rows:
            row.setVisible(text.lower() in ' '.join(c.text() for c in row.cells).lower())

    def update_device_status(self,ip,status):
        for row in range(self.table.rowCount()):
            if self.table.item(row,0).text()==ip:
                self.table.item(row,4).setText(status)
                self.table.rows[row].cells[4].label.setStyleSheet('color:#20b765;' if status=='播放中' else 'color:#c68b21;')

    def settings_auto_hidden(self,player):
        if self.current_settings is player:self.close_settings()

    def remove_settings(self,player):
        if self.current_settings is player:self.close_settings()
        if self.settings_stack.indexOf(player)>=0:
            self.settings_stack.removeWidget(player)
            player.setParent(None,Qt.WindowType.Widget)
            player.deleteLater()

    def close_settings(self):
        if self.batch_panel:self.batch_panel.password.clear()
        self.settings_panel.hide()
        if self.current_settings:self.current_settings.hide()
        self.current_settings=None

    def refresh_networks(self):
        self.network.clear()
        try:
            self.networks = interfaces()
            self.network.addItem('自动搜索所有活动局域网', None)
            for net in self.networks:
                self.network.addItem(f'{net.name} · {net.ip}', net)
            self.search.setEnabled(bool(self.networks))
            if not self.networks:
                self.status.setText('未找到活动网络，请连接 Wi-Fi 或有线网络后刷新。')
        except Exception as exc:
            self.status.setText(f'无法读取网络：{exc}')
            self.search.setEnabled(False)

    def prompt_target_ip(self):
        if self.presentation or (self.worker and self.worker.isRunning()):return
        value, accepted = QInputDialog.getText(self, '按 IP 添加',
            '请输入摄像头 IPv4 地址（例如 192.168.2.216）：')
        if accepted:self.start_target_scan(value)

    def start_target_scan(self, value):
        if self.presentation or (self.worker and self.worker.isRunning()):return
        try:
            target = validate_target_ip(value)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.start_scan(target_ip=target)

    def start_scan(self, checked=False, *, target_ip=None):
        if self.presentation:return
        self.run_scan(target_ip)

    def run_scan(self, target_ip=None):
        """真正去搜。不看界面状态，云端远程搜索也走这里。返回是否真的开始了。"""
        if self.worker and self.worker.isRunning():
            return False
        # 傻瓜模式开机自启可能赶在 DHCP 之前，那时网卡列表是空的；远程搜索没人去点"刷新网络"
        if not self.networks:self.refresh_networks()
        net = self.network.currentData()
        networks = [net] if net else self.networks
        self.target_ip = target_ip
        self.target_received = False
        self.thumbnails.cancel_all()
        if target_ip is None:
            self.devices.clear()
            self.table.setRowCount(0)
            self.details.clear()
        self.log.clear()
        self.copy.setEnabled(False)
        self.play.setEnabled(False)
        self.search.setEnabled(False)
        self.add_ip.setEnabled(False)
        self.network.setEnabled(False)
        self.refresh.setEnabled(False)
        self.stop.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText('正在搜索… 约 8 秒，发现设备后会立即显示。')
        self.log.appendPlainText('搜索网络：' + '、'.join(f'{n.name} ({n.ip})' for n in networks))
        if self.worker:
            self.worker.deleteLater()
        if target_ip:
            self.status.setText(f'正在探测 {target_ip}… 约 8 秒。')
            self.log.appendPlainText(f'定向搜索：{target_ip}')
            self.worker = SearchWorker(networks, self, target_ip=target_ip)
        else:
            self.worker = SearchWorker(networks, self)
        self.worker.device.connect(self.add_device)
        self.worker.message.connect(self.log.appendPlainText)
        self.worker.finished.connect(self.finish_scan)
        self.worker.start()
        return True

    def refresh_device_name(self, ip, name):
        if self.wall is None:return
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).text() == ip:
                self.table.setCellText(row, 1, self.device_names.display(self.devices[ip]))
        self.wall.update_devices(list(self.devices.values()))
        self.filter_devices(self.device_filter.text())
        if self.table.currentRow() >= 0:self.show_details()

    def add_device(self, device: Device):
        row = list(self.devices).index(device.ip) if device.ip in self.devices else self.table.rowCount()
        self.devices[device.ip] = device
        self.refresh_tile_device(device)
        if device.ip == self.target_ip:self.target_received = True
        if row == self.table.rowCount():
            self.table.insertRow(row)
        for column, text in enumerate((device.ip, self.device_names.display(device), device.model or '未提供', ' + '.join(device.protocols), '已发现 · 未验证视频')):
            self.table.setCellText(row, column, text)
        self.status.setText(f'已发现 {len(self.devices)} 台设备')
        self.wall.update_devices(list(self.devices.values()))
        self.filter_devices(self.device_filter.text())
        if self.table.currentRow() == row:
            self.show_details()

    def refresh_tile_device(self, device):
        """摄像头被换掉或 ONVIF 地址变了时，画面里那份也要跟着更新，否则连不上。"""
        if self.wall is None:return
        for tile in self.wall.tiles:
            current=tile.player.device
            if current.ip!=device.ip or current is device:continue
            for field in ('name','model','manufacturer'):
                setattr(current,field,getattr(device,field) or getattr(current,field))
            if device.protocols:current.protocols=list(device.protocols)
            if device.urls:current.urls=list(device.urls)

    def show_details(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        self.copy.setEnabled(item is not None)
        self.play.setEnabled(item is not None)
        if item is None:
            return
        d = self.devices[item.text()]
        self.details.setPlainText(f'IP：{d.ip}    名称：{d.name or "未提供"}    型号：{d.model or "未提供"}\n'
                                  f'厂商（设备自报）：{d.manufacturer or "未知"}    接收网络：{d.interface or "未知"}\n'
                                  f'服务地址：{"  ".join(d.urls) or "未提供"}')

    def copy_ip(self):
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row, 0):
            QApplication.clipboard().setText(self.table.item(row, 0).text())

    def open_selected_settings(self):
        if self.presentation:return
        self.open_player(force_settings=True)

    def open_player(self,force_settings=False):
        if self.presentation:return
        row=self.table.currentRow()
        item=self.table.item(row,0) if row>=0 else None
        if item is None:return
        device=self.devices[item.text()]
        tile=next((t for t in self.wall.tiles if t.player.device.ip==device.ip),None)
        if tile is None:
            if not self.wall.add_device(device):return
            tile=next(t for t in self.wall.tiles if t.player.device.ip==device.ip)
            if device.ip in self.session_credentials:
                tile.player.set_connection_credentials(*self.session_credentials[device.ip])
        if not force_settings and not tile.player.busy():
            tile.player.connect_camera()
        else:self.show_settings(tile.player)

    def open_multiview(self):
        self.wall.update_devices(list(self.devices.values()))
        self.wall.show()

    def release_multiview(self,result):
        wall,self.wall=self.wall,None
        if wall:wall.deleteLater()

    def release_player(self, player):
        if player in self.players:
            self.players.remove(player)
        player.deleteLater()

    def video_verified(self, ip):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).text() == ip:
                self.update_device_status(ip,'播放中')

    def cancel_scan(self):
        if self.worker:
            self.worker.cancel.set()
            self.stop.setEnabled(False)
            self.status.setText('正在停止搜索…')

    def finish_scan(self):
        if self.closing:return
        for device in self.devices.values():
            self.thumbnails.request(device,self.session_credentials.get(device.ip))
        self.show_details()
        cancelled = self.worker and self.worker.cancel.is_set()
        self.search.setEnabled(bool(self.networks))
        self.add_ip.setEnabled(True)
        self.network.setEnabled(True)
        self.refresh.setEnabled(True)
        self.stop.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if cancelled:
            self.status.setText(f'搜索已停止 · 已发现 {len(self.devices)} 台设备')
        elif self.target_ip:
            target = self.target_ip
            if self.target_received and target in self.devices:
                self.device_filter.clear()
                self.table.selectRow(list(self.devices).index(target))
                self.status.setText(f'已添加 {target} · 请在连接设置中填写账号密码。')
                self.open_selected_settings()
            else:
                self.status.setText(f'未收到 {target} 的设备回复，请检查 IP、网络连接和 ONVIF 是否开启。')
        elif self.devices:
            self.status.setText(f'搜索完成 · 发现 {len(self.devices)} 台设备，请选中设备查看 IP 和详情。')
        else:
            self.status.setText('搜索完成 · 未收到设备回复。这不代表没有摄像头，请检查下方提示后重新搜索。')
        self.scan_completed.emit(bool(cancelled))


    # ---------- 云端同步 ----------

    def cloud_login(self,auth_code):
        # 人亲手输了设备码：哪怕之前被别处顶掉过，这次登录成功也要把通道接回来。
        # 这里只做记号，等真登上了再放行（见 cloud_session_changed）——输错码不能解除
        self.manual_login=True
        self.cloud.login(auth_code,device_name=platform.node() or '监控屏',app_version=__version__)

    def cloud_logout(self):
        self.cloud.logout()

    def welcome_login(self,code):
        self.welcome.set_busy(True)
        self.cloud_login(code)

    def use_standalone(self):
        startup.choose_standalone(self.cloud.settings)
        self.set_welcome_visible(False)
        # 欢迎页盖着时启动不回放本机快照（比如从欢迎页登录过、在侧边栏退出云端后重启）。
        # 选了单机就补上；墙上已经有画面（同一次运行里刚解绑回来）就不动它。
        # 自动全屏只在打开软件时判断，这里不进
        if not self.cloud.enabled() and self.wall is not None and not self.wall.tiles:
            self.restore_local_snapshot()

    def start_cloud(self):
        # 要在 cloud.start() 之前看：设置说登录了、钥匙串里却没有会话时它会把登录改成 false，
        # 这种电脑的本机快照是登录云端之前存的，早就过时了，不能拿来回放
        standalone=not self.cloud.enabled()
        # 先进傻瓜模式再铺缓存：锁好界面之后才把全屏之类的设置落下去
        if self.cloud.enabled() and self.cloud.mode()=='managed':self.managed.enter()
        self.cloud.start()
        if self.cloud.token:self.channel.start()
        if standalone and self.welcome.isHidden():self.restore_local_snapshot()
        # 完整模式按勾选对齐一次：现场手动删了启动项，下次打开软件就补回来（和傻瓜模式一样）
        if not self.managed.active:self.sync_autostart()
        self.auto_fullscreen_timer.start(0)

    def save_local_snapshot(self):
        """单机电脑把现在的墙存一份到本机设置，重启后据此恢复。密码本来就在钥匙串里。"""
        # 登录云端后以云端为准（它有自己的离线缓存）；退出云端时和之后每次墙变化再接着写
        if self.cloud.enabled():return
        payload=self.collect_cloud_payload(credentials=False)
        if payload is None:return
        settings=self.device_names.settings
        settings.setValue(LOCAL_SNAPSHOT_KEY,json.dumps(cacheable(payload),ensure_ascii=False))
        settings.sync()
        if settings.status()!=settings.Status.NoError:logger.warning('本机快照没有写成功，下次打开墙会是空的')

    def restore_local_snapshot(self):
        raw=self.device_names.settings.value(LOCAL_SNAPSHOT_KEY,'')
        if not raw:return
        try:
            snapshot=json.loads(str(raw))
            if not isinstance(snapshot,dict):raise ValueError('不是 JSON 对象')
        except ValueError as exc:
            logger.warning('本机快照读不出来，已忽略：%s',exc);return
        self.replaying_local=True
        try:
            # 解析也放在这里面：layout 被手改成了字符串、数组之类，同样只记日志，启动照常往下走
            layout=snapshot.get('layout') or {}
            if not isinstance(layout,dict):raise ValueError('layout 不是 JSON 对象')
            # 两个开关以本机设置为准：快照最多晚一秒，刚改完勾选就关软件的话，回放会把它改回去
            layout={k:v for k,v in layout.items() if k not in ('autostart','start_fullscreen')}
            snapshot=dict(snapshot,layout=layout,mode='full')
            # 快照里没有 password 键，apply_snapshot 不会碰钥匙串，已存的账号密码照样能用
            self.apply_cloud_config(apply_snapshot(snapshot,self.device_names,self.connection_options,
                self.wall.credential_store))
        except Exception:
            # 手改坏了的快照也不能让软件起不来，空着墙照常用
            logger.warning('本机快照回放失败，已忽略',exc_info=True)
        finally:self.replaying_local=False

    def auto_fullscreen(self):
        """完整模式勾了「启动后自动全屏」：打开时铺好了墙就进全屏，和按 F11 一样。"""
        # 只在启动时判断一次。云端配置晚到不再触发，退出全屏后也不会被拉回去
        if not self.auto_fullscreen_pending:return
        self.auto_fullscreen_pending=False
        if not flag(self.device_names.settings.value(START_FULLSCREEN_KEY,False)):return
        # 傻瓜模式全屏由远程页决定；欢迎页盖着说明还没选用法；墙是空的就别给一块黑屏
        if self.managed.active or self.presentation or not self.welcome.isHidden():return
        if self.wall is None or not self.wall.tiles:return
        self.toggle_fullscreen()
        # 启动时窗口还是默认大小，退出全屏后回到最大化，监控墙铺满屏幕
        self.was_maximized=True

    def show_welcome(self,message=''):
        self.welcome.set_busy(False)
        self.welcome.show_error(message)
        # 必须走 set_welcome_visible：它会同时禁用下面的界面和快捷键
        self.set_welcome_visible(True)

    def unbind_cloud(self,message=''):
        """维护人员解绑，或后台收回了设备码：回到输入设备码的那一页。本机摄像头配置保留。"""
        self.channel.stop()
        self.managed.leave()
        self.cloud.logout()
        startup.clear_standalone(self.cloud.settings)
        if self.wall is not None:self.wall.stop_everything()
        self.show_welcome(message)

    def on_cloud_mode(self,mode):
        if mode=='managed':self.managed.enter()
        else:self.managed.leave()

    def channel_hello(self):
        if not self.cloud.token:return None
        return {'token':self.cloud.token,'client_uid':self.cloud.client_uid(),'app_version':__version__}

    def on_channel_message(self,payload):
        kind=payload.get('type')
        if kind in ('welcome','config_changed'):self.cloud.note_remote_version(payload.get('version',0))
        elif kind=='command':self.managed.run_command(payload)
        elif kind=='revoked':
            if self.managed.active:
                self.on_cloud_revoked(str(payload.get('failure_code') or ''))
            else:
                # 完整模式有侧边栏，不能因为通道一句话就清掉登录、停掉画面、盖上欢迎页。
                # 停掉通道，让 HTTP 去问一次：真被收回了，服务端原话会显示在侧边栏，
                # 之后的处理和 HTTP 被拒完全一样
                self.channel.stop()
                self.cloud.refresh()

    def on_channel_denied(self,failure_code):
        if failure_code=='INVALID_TOKEN':self.cloud.relogin()
        elif failure_code in REVOKED_MESSAGES and self.managed.active:self.on_cloud_revoked(failure_code)
        else:self.cloud.refresh()

    def on_cloud_revoked(self,failure_code):
        # 托管电脑被后台永久拒绝：只退出托管不够，设置里的启用、模式、缓存都得清掉，
        # 否则下次启动又按云端路线锁回旧画面，再被拒一次。完整模式有侧边栏，照原样处理
        if not self.managed.active:return
        self.unbind_cloud(REVOKED_MESSAGES.get(failure_code,'该设备码已不可用，请联系管理员。'))

    def on_channel_replaced(self):
        # 同一个设备码在别处连上了。通道自己已经不再重连；这里还要挡住之后的静默重登
        # （它也会发 session_changed(True)）——否则这边一重连又把那边顶掉，两台来回抢。
        # 配置同步照常走 HTTP，画面不受影响；要接回来就重新输一次设备码，或者重启软件。
        self.channel_replaced=True
        self.channel.stop()
        # 托管电脑看不到侧边栏，墙上得如实说清楚，不能挂着「正在自动重连」
        if self.managed.active:self.managed.show_replaced()
        self.cloud_panel.set_status('该设备码已在另一台电脑上登录，本机不再接收云端指令。'
            '如需由本机接管，请重新登录。',error=True)

    def upload_snapshot(self,kind,jpeg,ip=''):
        token=self.cloud.token
        if not token:raise RuntimeError('尚未登录云端')
        return self.cloud.client.upload_snapshot(token,kind,jpeg,ip)

    def cloud_session_changed(self,connected):
        self.cloud_panel.set_connected(connected,self.cloud.profile_name())
        # 退出云端时离线缓存跟着清掉了，马上把屏幕上这面墙存成本机快照，重启后还在
        if not connected:self.note_local_change()
        if connected:
            # 人亲手登录成功才算接管回来；session_changed(True) 先于 login_result 发出
            if self.manual_login:self.channel_replaced=False
            if not self.channel_replaced:self.channel.start()
            return
        self.channel.stop()
        if self.managed.active:
            # 傻瓜模式没有侧边栏可以重新登录，只能回到欢迎页
            self.managed.leave()
            self.show_welcome('云端登录已失效，请重新输入设备码。')

    def cloud_storage_unavailable(self,message):
        # 只是钥匙串一时读不出来，不是登录失效：按钮如实显示未连接，但不退出托管、
        # 不删开机自启、不盖欢迎页。托管画面照缓存继续放，通道没开，断线提示自会出来
        self.cloud_panel.set_connected(False,self.cloud.profile_name())

    def cloud_startup_switches_changed(self):
        # 首次登录上传方向：云端开着的开关已写进本机设置。设不上时设置已改回 false，
        # 随后那次上传读的就是 false，后台看得到没设上
        if self.managed.active:return
        self.sync_autostart()

    def cloud_login_result(self,ok,message):
        self.manual_login=False
        self.cloud_panel.set_status(message,error=not ok)
        self.cloud_panel.set_connected(self.cloud.enabled(),self.cloud.profile_name())
        if not self.welcome.isHidden():
            self.welcome.set_busy(False)
            if ok:self.set_welcome_visible(False)
            else:self.welcome.show_error(message)

    def note_cloud_change(self, *args):
        """本机配置有改动就排一次上传；应用云端配置的过程中不回传，避免来回打架。"""
        if self.applying_cloud or self.replaying_local:return
        self.cloud.schedule_push()
        self.note_local_change()

    def note_local_change(self):
        # 不登录云端的电脑没有离线缓存，另存一份本机快照；节流一秒，拖动格子时不连写
        if not self.cloud.enabled() and not self.cloud.applying and not self.replaying_local:
            self.local_snapshot_timer.start()

    def collect_cloud_payload(self,credentials=True):
        """credentials=False 时不读钥匙串：本机快照用不着账号密码，它们本来就在钥匙串里。"""
        if self.wall is None:return None
        cameras=[]
        for index,tile in enumerate(self.wall.slots):
            if tile is None:continue
            player=tile.player
            # 以设备表为准：重新搜索会刷新型号与 ONVIF 地址，画面里那份可能是旧的
            device=self.devices.get(player.device.ip,player.device)
            try:saved=self.wall.credential_store.load(device.ip) if credentials else None
            except Exception:saved=None
            cameras.append(camera_entry(device,slot_index=index,
                display_name=self.device_names.get(device.ip),
                name_color=self.device_names.appearance(device.ip)[0],
                name_corner=self.device_names.appearance(device.ip)[1],
                transport=player.transport.currentData() or 'tcp',
                username=saved[0] if saved else '',
                password=saved[1] if saved else None,
                stream_mode=stream_mode(player.mode.currentIndex()),
                dahua_channel=player.channel.value(),
                manual_url=player.manual.text()))
        settings=self.device_names.settings
        columns={}
        for capacity in (4,9,12,16,20,25):
            value=settings.value(f'monitor/columns/{capacity}',None)
            if value not in (None,''):columns[capacity]=value
        return {'version':self.cloud.version(),
                'layout':layout_entry(self.wall.capacity,columns,
                    self.wall._fill_width,settings.value('monitor/organization','') or '',
                    settings.value(AUTOSTART_KEY,False),settings.value(START_FULLSCREEN_KEY,False)),
                'cameras':cameras}

    def apply_cloud_config(self,result):
        """把云端配置落到界面。已经在播的画面尽量不打断。"""
        if self.wall is None:return
        self.applying_cloud=True
        try:
            self.devices={device.ip:device for device in result.devices}
            self.table.setRowCount(0)
            for device in result.devices:
                row=self.table.rowCount()
                self.table.insertRow(row)
                for column,text in enumerate((device.ip,self.device_names.display(device),
                        device.model or '未提供',' + '.join(device.protocols),'已发现 · 未验证视频')):
                    self.table.setCellText(row,column,text)
            self.wall.update_devices(list(self.devices.values()))
            self.wall.saved_slots=self.device_names.slot_order()
            wanted=set(self.devices)
            for tile in list(self.wall.tiles):
                if tile.player.device.ip not in wanted:self.wall.remove_tile(tile)
            if result.capacity!=self.wall.capacity:self.wall.change_layout(result.capacity)
            for device in result.devices:
                tile=next((t for t in self.wall.tiles if t.player.device.ip==device.ip),None)
                if tile is None:
                    if not self.wall.add_device(device):continue
                    tile=next(t for t in self.wall.tiles if t.player.device.ip==device.ip)
                self.apply_cloud_stream(tile,result.streams.get(device.ip,{}))
                # 云端刚把这台的账号密码写进钥匙串，早就建好的那一格还拿着旧的
                tile.player.reload_credentials()
            self.wall.organization_input.setText(result.organization)
            self.wall.organization_header.setText(result.organization)
            self.wall.fill_width.blockSignals(True)
            self.wall._fill_width=result.fill_width
            self.wall.fill_width.setChecked(result.fill_width)
            self.wall.fill_width.blockSignals(False)
            self.wall.sync_columns_choice()
            self.wall.relayout()
            self.filter_devices(self.device_filter.text())
            for device in self.devices.values():self.thumbnails.request(device)
        finally:
            self.applying_cloud=False
        # 两个开关只在完整模式下落到界面和系统：傻瓜模式开机启动一直开着，
        # 下发的值已由 apply_snapshot 写进设置，切回完整模式时生效
        if result.mode!='managed':
            self.refresh_startup_toggles()
            # 云端要开机启动、本机却设不上：设置已改回 false，记下要把实际状态传上去
            if result.autostart is not None and not self.sync_autostart():self.cloud.push_later()
        # 配置回来了画面却是黑的，还得人一格一格去点连接。既然摄像头、通道和
        # 密码都齐了，就直接连上。已经在播的那几格不会被打断。
        self.wall.connect_all()
        if result.mode=='managed':self.managed.apply(result)

    def apply_cloud_stream(self,tile,stream):
        if not stream:return
        player=tile.player
        player.mode.blockSignals(True)
        player.mode.setCurrentIndex(int(stream.get('mode_index',0)))
        player.mode.blockSignals(False)
        player.update_mode()
        player.channel.setValue(int(stream.get('dahua_channel',1)))
        player.manual.setText(str(stream.get('manual_url','')))
        transport=str(stream.get('transport','tcp'))
        player.transport.blockSignals(True)
        player.transport.setCurrentIndex(1 if transport=='udp' else 0)
        player.transport.blockSignals(False)

    def closeEvent(self, event):
        if self.managed_fullscreen is not None and not self.authorized_quit:
            event.ignore();return
        if self.presentation and self.managed_fullscreen is None and not self.exit_fullscreen():
            event.ignore();return
        self.closing=True
        # 改完不到一秒就关了软件：排着的本机快照当场写掉，否则重启后少了刚才那一步
        if self.local_snapshot_timer.isActive():self.local_snapshot_timer.stop();self.save_local_snapshot()
        if self.worker and self.worker.isRunning():self.worker.cancel.set()
        # 先断下行通道再去等线程：cloud.stop() 可能要等好几秒，重启时新进程已经连上来，
        # 旧连接多挂这几秒就会和它互相顶
        self.channel.stop()
        self.managed.status_timer.stop()
        self.snapshot_uploader.stop()
        self.cloud.stop()
        self.thumbnail_timer.stop()
        self.thumbnails.cancel_all()
        if self.thumbnails.busy() or self.cloud.busy() or self.snapshot_uploader.busy():
            event.ignore();QTimer.singleShot(200,self.close);return
        if self.wall is not None:
            self.wall.close()
            if self.wall is not None:
                event.ignore()
                QTimer.singleShot(200,self.close)
                return
        for player in list(self.players):
            player.close()
        if any(player.busy() for player in self.players):
            event.ignore()
            QTimer.singleShot(200, self.close)
            return
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
            event.ignore()
            QTimer.singleShot(200, self.close)
            return
        self.session_credentials.clear()
        if self.batch_panel:self.batch_panel.password.clear()
        event.accept()


def allow_session_quit(app,window):
    # 关机、注销时系统要求所有程序退出；傻瓜模式平时拦着关闭，这时必须放行。
    # Qt 6.8 在 Windows 上收到 WM_QUERYENDSESSION，在 macOS 上走 applicationShouldTerminate
    # （⌘Q、程序坞「退出」、注销、关机都走这里），都是先发 commitDataRequest、再关所有窗口。
    # 所以 macOS 上分不清注销和 ⌘Q，一律放行：现场电脑都是 Windows，macOS 只是开发机
    app.commitDataRequest.connect(lambda manager:setattr(window,'authorized_quit',True))

def setup_logging(directory):
    """现场电脑出了问题只能靠日志：写到文件里，满 1 MB 轮换，留 3 份旧的。

    目录建不出来、文件打不开时不写文件，软件照常运行——日志不能反过来让监控起不来。
    返回装上的处理器，没装上返回 None。
    """
    try:
        os.makedirs(directory,exist_ok=True)
        handler=logging.handlers.RotatingFileHandler(os.path.join(directory,LOG_FILE),
            maxBytes=LOG_MAX_BYTES,backupCount=LOG_BACKUPS,encoding='utf-8')
    except OSError:
        return None
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    root=logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return handler

def log_startup(argv):
    # 记下是开机自启还是人手打开的：现场说「开机没起来」时先看这一行
    logger.info('Camera Monitor %s 启动%s',__version__,'（开机自启）' if AUTOSTART_FLAG in argv else '')

def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Camera Monitor')
    # 要在设好程序名之后取目录，否则会落到没有程序名的公共目录里。
    # 取不到目录时返回空串，拼出来就成了当前目录下的 logs，宁可不写
    data=QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if data:setup_logging(os.path.join(data,'logs'))
    log_startup(sys.argv)
    app.setWindowIcon(QIcon(str(Path(__file__).parent/'assets'/'app-icon.png')))
    app.setStyle('Fusion')
    window = Window()
    allow_session_quit(app,window)
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
