from __future__ import annotations
from .choices import ChoiceButton as QComboBox

import sys
import threading
from PySide6.QtCore import QThread, Signal, Qt, QTimer, QEvent
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QBoxLayout,
    QAbstractItemView, QPlainTextEdit, QProgressBar, QSplitter, QFrame, QScrollArea, QStackedWidget, QSizePolicy)
from .discovery import Device, interfaces, scan
from .playback import PlayerWindow
from .device_list import DeviceList
from .multiview import MultiView


class SearchWorker(QThread):
    device = Signal(object)
    message = Signal(str)

    def __init__(self, networks, parent=None):
        super().__init__(parent)
        self.networks = networks
        self.cancel = threading.Event()

    def run(self):
        try:
            scan(self.networks, self.device.emit, self.message.emit, self.cancel)
        except Exception as exc:
            self.message.emit(f'搜索发生错误：{exc}')


class Window(QMainWindow):
    def __init__(self, device_names=None):
        super().__init__()
        from .device_names import DeviceNames
        self.device_names = device_names if device_names is not None else DeviceNames()
        self.device_names.changed.connect(self.refresh_device_name)
        self.worker = None
        self.devices = {}
        self.networks = []
        self.players = []
        self.wall = None
        self.current_settings = None
        self.batch_panel = None
        self.session_credentials = {}
        self.presentation = False
        self.setWindowTitle('Camera Monitor · 内网监控中心')
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
        self.refresh=QPushButton('刷新网络');self.refresh.clicked.connect(self.refresh_networks)

        self.status=QLabel('准备就绪 · 点击搜索设备');self.status.setWordWrap(True)
        self.status.setObjectName('muted');side.addWidget(self.status)
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
        self.refresh_networks()

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

    def exit_fullscreen(self):
        if not self.presentation:return
        self.set_presentation(False)
        self.showMaximized() if getattr(self,'was_maximized',False) else self.showNormal()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.WindowStateChange and hasattr(self,'wall') and self.wall:
            if self.isFullScreen() and not self.presentation:self.set_presentation(True)
            elif not self.isFullScreen() and self.presentation:self.set_presentation(False)

    def show_batch_settings(self):
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

    def start_scan(self):
        if self.worker and self.worker.isRunning():
            return
        net = self.network.currentData()
        networks = [net] if net else self.networks
        self.devices.clear()
        self.table.setRowCount(0)
        self.details.clear()
        self.log.clear()
        self.copy.setEnabled(False)
        self.play.setEnabled(False)
        self.search.setEnabled(False)
        self.network.setEnabled(False)
        self.refresh.setEnabled(False)
        self.stop.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText('正在搜索… 约 8 秒，发现设备后会立即显示。')
        self.log.appendPlainText('搜索网络：' + '、'.join(f'{n.name} ({n.ip})' for n in networks))
        if self.worker:
            self.worker.deleteLater()
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
        if row == self.table.rowCount():
            self.table.insertRow(row)
        for column, text in enumerate((device.ip, self.device_names.display(device), device.model or '未提供', ' + '.join(device.protocols), '已发现 · 未验证视频')):
            self.table.setCellText(row, column, text)
        self.status.setText(f'已发现 {len(self.devices)} 台设备')
        self.wall.update_devices(list(self.devices.values()))
        self.filter_devices(self.device_filter.text())
        if self.table.currentRow() == row:
            self.show_details()

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
        self.open_player(force_settings=True)

    def open_player(self,force_settings=False):
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
        cancelled = self.worker and self.worker.cancel.is_set()
        self.search.setEnabled(bool(self.networks))
        self.network.setEnabled(True)
        self.refresh.setEnabled(True)
        self.stop.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if cancelled:
            self.status.setText(f'搜索已停止 · 已发现 {len(self.devices)} 台设备')
        elif self.devices:
            self.status.setText(f'搜索完成 · 发现 {len(self.devices)} 台设备，请选中设备查看 IP 和详情。')
        else:
            self.status.setText('搜索完成 · 未收到设备回复。这不代表没有摄像头，请检查下方提示后重新搜索。')

    def closeEvent(self, event):
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
    app.setStyle('Fusion')
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
