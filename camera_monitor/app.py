from __future__ import annotations
from .choices import ChoiceButton as QComboBox

import platform
import sys
import threading
from PySide6.QtCore import QThread, Signal, Qt, QTimer, QEvent
from PySide6.QtGui import QShortcut, QKeySequence, QIcon
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QBoxLayout, QInputDialog,
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
from .cloud_state import camera_entry, layout_entry, stream_mode
from .cloud_sync import CloudSync


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
    def __init__(self, device_names=None, cloud_settings=None):
        super().__init__()
        from .device_names import DeviceNames
        self.device_names = device_names if device_names is not None else DeviceNames()
        self.device_names.changed.connect(self.refresh_device_name)
        self.closing=False
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
        self.wall=MultiView([],self,embedded=True,device_names=self.device_names)
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
            connection_options=ConnectionOptions(),live_image=self.live_thumbnail,
            connection=self.thumbnail_connection,parent=self)
        self.thumbnails.updated.connect(self.update_thumbnail)
        self.table.thumbnailClicked.connect(self.show_thumbnail)
        self.thumbnail_timer=QTimer(self);self.thumbnail_timer.setInterval(2500)
        self.thumbnail_timer.timeout.connect(self.refresh_live_thumbnails);self.thumbnail_timer.start()
        self.password_settings=QPushButton('大屏密码')
        self.password_settings.clicked.connect(self.show_password_settings)
        self.wall.toolbar_widget.layout().addWidget(self.password_settings)
        self.applying_cloud=False
        self.cloud=CloudSync(self.device_names,ConnectionOptions(),self.wall.credential_store,
            self.collect_cloud_payload,parent=self,settings=cloud_settings)
        self.cloud.status.connect(self.cloud_panel.set_status)
        self.cloud.applied.connect(self.apply_cloud_config)
        self.cloud.session_changed.connect(self.cloud_session_changed)
        self.cloud.login_result.connect(self.cloud_login_result)
        self.device_names.changed.connect(lambda *_:self.note_cloud_change())
        self.device_names.appearance_changed.connect(lambda *_:self.note_cloud_change())
        self.wall.layout_changed.connect(self.note_cloud_change)
        self.cloud_panel.set_connected(self.cloud.enabled(),self.cloud.profile_name())
        self.refresh_networks()
        QTimer.singleShot(0,self.cloud.start)

    def show_settings(self, player):
        if self.presentation:return
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
        if self.presentation:self.exit_fullscreen();return
        self.was_maximized=self.isMaximized()
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

    def show_password_settings(self):
        if self.presentation:return
        dialog=PasswordSettingsDialog(self.screen_lock,self)
        try:dialog.exec()
        finally:dialog.deleteLater()

    def exit_fullscreen(self):
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

    def native_exit_requested(self):
        if not self.presentation or self.authorized_exit:return
        self.showFullScreen()
        self.exit_fullscreen()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.WindowStateChange and hasattr(self,'wall') and self.wall:
            if self.isFullScreen() and not self.presentation:self.set_presentation(True)
            elif not self.isFullScreen() and self.presentation and not self.authorized_exit:
                # Restore presentation before prompting so Cancel cannot expose settings.
                QTimer.singleShot(0,self.native_exit_requested)

    def show_batch_settings(self):
        if self.presentation:return
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
        if self.worker and self.worker.isRunning():
            return
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


    # ---------- 云端同步 ----------

    def cloud_login(self,auth_code):
        self.cloud.login(auth_code,device_name=platform.node() or '监控屏',app_version=__version__)

    def cloud_logout(self):
        self.cloud.logout()

    def cloud_session_changed(self,connected):
        self.cloud_panel.set_connected(connected,self.cloud.profile_name())

    def cloud_login_result(self,ok,message):
        self.cloud_panel.set_status(message,error=not ok)
        self.cloud_panel.set_connected(self.cloud.enabled(),self.cloud.profile_name())

    def note_cloud_change(self):
        """本机配置有改动就排一次上传；应用云端配置的过程中不回传，避免来回打架。"""
        if not self.applying_cloud:self.cloud.schedule_push()

    def collect_cloud_payload(self):
        if self.wall is None:return None
        cameras=[]
        for index,tile in enumerate(self.wall.slots):
            if tile is None:continue
            player=tile.player
            # 以设备表为准：重新搜索会刷新型号与 ONVIF 地址，画面里那份可能是旧的
            device=self.devices.get(player.device.ip,player.device)
            try:saved=self.wall.credential_store.load(device.ip)
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
                    self.wall._fill_width,settings.value('monitor/organization','') or ''),
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
        if self.presentation and not self.exit_fullscreen():
            event.ignore();return
        self.closing=True
        if self.worker and self.worker.isRunning():self.worker.cancel.set()
        self.cloud.stop()
        self.thumbnail_timer.stop()
        self.thumbnails.cancel_all()
        if self.thumbnails.busy() or self.cloud.busy():
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


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Camera Monitor')
    app.setWindowIcon(QIcon(str(Path(__file__).parent/'assets'/'app-icon.png')))
    app.setStyle('Fusion')
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
