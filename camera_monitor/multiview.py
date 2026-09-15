"""Independent camera players arranged in a 4/9 tile monitor wall."""
from PySide6.QtCore import Qt,QTimer,Signal,QEvent
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QGridLayout,
    QLabel,QPushButton,QFrame,QScrollArea,QApplication,QMenu)
from .choices import ChoiceButton
from .playback import PlayerWindow
from .credentials import CredentialStore


class CameraTile(QFrame):
    remove_requested=Signal(object)
    settings_requested=Signal(object)
    HEADER=40

    def __init__(self,number,device,credential_store,parent=None):
        super().__init__(parent)
        self.setObjectName('cameraTile')
        self.setStyleSheet('QFrame#cameraTile {background:white; border:1px solid #dce4ed; border-radius:6px;}')
        self.monitor=parent
        self.player=PlayerWindow(device,self,credential_store=credential_store)
        self.player.hosted=True
        self.player.credentials_required.connect(self.show_settings)
        self.player.preview_width=1280
        self.player.setWindowTitle(f'连接设置 · {device.ip}')
        self.player.layout().removeWidget(self.player.surface)
        self.player.surface.setParent(self)
        self.player.surface.setMinimumSize(1,1)
        self.player.surface.set_fill(False)
        self.player.surface.aspect_changed.connect(lambda _:self.monitor.schedule_layout())
        self.player.surface.activated.connect(lambda:self.monitor.toggle_focus(self))
        self.title=QLabel(f'{device.name or "摄像头"} · {device.ip}',self)
        self.title.setStyleSheet('background:transparent; font-weight:600; font-size:13px; padding-left:12px;')
        self.title.setToolTip(self.title.text())
        self.status=QLabel('待连接',self)
        self.status.setStyleSheet('background:transparent; color:#8191a5; font-size:12px;')
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        self.player.status_changed.connect(self.update_status)
        self.controls=QWidget(self.player.surface)
        self.controls.setObjectName('videoControls')
        self.controls.setStyleSheet('QWidget#videoControls {background:rgba(25,34,45,210); border-radius:4px;} QPushButton {color:white; background:transparent; border:none; padding:6px;} QPushButton:hover {background:#44566c;} QPushButton:disabled {color:#8995a3;}')
        actions=QHBoxLayout(self.controls);actions.setContentsMargins(5,2,5,2);actions.setSpacing(2)
        self.start=QPushButton('连接');self.start.clicked.connect(self.player.connect_camera)
        self.stop=QPushButton('停止');self.stop.clicked.connect(self.player.stop_playback)
        self.settings=QPushButton('设置');self.settings.clicked.connect(self.show_settings)
        self.expand=QPushButton('放大');self.expand.clicked.connect(lambda:self.monitor.toggle_focus(self))
        self.remove=QPushButton('移除');self.remove.clicked.connect(lambda:self.remove_requested.emit(self))
        for button in (self.start,self.stop,self.settings,self.expand,self.remove):
            actions.addWidget(button)
            button.installEventFilter(self)
        self.more=QPushButton('操作')
        menu=QMenu(self.more)
        menu.addAction('连接',self.player.connect_camera)
        menu.addAction('停止',self.player.stop_playback)
        menu.addAction('连接设置',self.show_settings)
        menu.addAction('放大 / 返回',lambda:self.monitor.toggle_focus(self))
        menu.addAction('移除',lambda:self.remove_requested.emit(self))
        self.more.setMenu(menu);actions.addWidget(self.more);self.more.installEventFilter(self)
        self.controls.hide()
        self.control_timer=QTimer(self);self.control_timer.setInterval(200)
        self.control_timer.timeout.connect(self.sync_controls);self.control_timer.start()
        self.sync_controls()

    def update_status(self,text):
        live=text.startswith('正在播放') or text.startswith('画面正在播放')
        summary='播放中' if live else ('重连中' if '重连' in text else ('待处理' if '失败' in text else '待连接'))
        self.status.setText(summary)
        self.status.setStyleSheet(f'background:transparent; color:{"#20b765" if live else "#c68b21"}; font-size:12px;')
        self.status.setToolTip(text)
        self.monitor.device_status.emit(self.player.device.ip,summary)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.title.setGeometry(0,0,max(1,self.width()-75),self.HEADER)
        self.status.setGeometry(max(0,self.width()-75),0,65,self.HEADER)
        self.player.surface.setGeometry(1,self.HEADER,max(1,self.width()-2),max(1,self.height()-self.HEADER-1))
        width=min(self.player.surface.width(),350)
        self.controls.setGeometry(self.player.surface.width()-width,max(0,self.player.surface.height()-38),width,38)

    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.FocusIn:
            self.controls.show();self.controls.raise_()
        return super().eventFilter(watched,event)

    def enterEvent(self,event):
        self.controls.show();self.controls.raise_();super().enterEvent(event)

    def leaveEvent(self,event):
        if not self.controls.isAncestorOf(QApplication.focusWidget()):self.controls.hide()
        super().leaveEvent(event)

    def show_settings(self):
        if self.monitor.embedded:
            self.settings_requested.emit(self.player);return
        self.player.show();self.player.raise_();self.player.activateWindow()

    def sync_controls(self):
        busy=self.player.busy()
        narrow=self.player.surface.width()<280
        self.more.setVisible(narrow)
        for button in (self.settings,self.expand,self.remove):button.setVisible(not narrow)
        self.start.setVisible(not busy and not narrow);self.stop.setVisible(busy and not narrow)
        self.start.setEnabled(not busy);self.stop.setEnabled(busy and not self.player.stopping)

    def shutdown(self):
        self.player.stop_playback();self.setEnabled(False);self.player.hide()
        if self.player.busy():return False
        self.control_timer.stop();self.player.hosted=False;self.player.close()
        return True


class WatchCanvas(QWidget):
    resized=Signal()
    def resizeEvent(self,event):
        super().resizeEvent(event);self.resized.emit()


class MultiView(QDialog):
    playing=Signal(str)
    device_status=Signal(str,str)
    fullscreen_requested=Signal()
    tile_removing=Signal(object)
    settings_requested=Signal(object)

    def __init__(self,devices,parent=None,credential_store=None,embedded=False):
        super().__init__(parent)
        self.embedded=embedded
        if embedded:self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle('Camera Monitor · 多画面监控')
        self.resize(1200,850)
        self.setMinimumSize(620,350) if embedded else self.setMinimumSize(820,600)
        self.credential_store=credential_store or CredentialStore()
        self.capacity=9
        self.layout_mode="auto"
        self.focused_tile=None
        self.layout_timer=QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.timeout.connect(self.relayout)
        self.tiles=[]
        self.placeholders=[]
        self.closing=False
        layout=QVBoxLayout(self)
        heading=QLabel('多画面监控')
        heading.setStyleSheet('font-size:24px; font-weight:600;')
        if embedded:heading.setText('实时监控')
        toolbar=QHBoxLayout()
        toolbar.addWidget(heading)
        toolbar.addStretch()
        self.auto=QPushButton('自动')
        self.auto.setMinimumWidth(90)
        self.auto.clicked.connect(lambda:self.change_layout('auto'))
        toolbar.addWidget(self.auto)
        self.choice=ChoiceButton()
        toolbar.addWidget(self.choice,1)
        self.add=QPushButton('添加摄像头')
        self.add.clicked.connect(self.add_selected)
        toolbar.addWidget(self.add)
        self.four=QPushButton('4画面')
        self.four.setMinimumWidth(90)
        self.four.clicked.connect(lambda:self.change_layout(4))
        toolbar.addWidget(self.four)
        self.nine=QPushButton('9画面')
        self.nine.setMinimumWidth(90)
        self.nine.clicked.connect(lambda:self.change_layout(9))
        toolbar.addWidget(self.nine)
        self.display_mode=ChoiceButton()
        self.display_mode.addItems(['完整画面', '铺满（裁剪）'])
        self.display_mode.setToolTip('等比铺满会裁剪边缘；完整画面保留全部内容，可能留边。')
        self.display_mode.currentIndexChanged.connect(self.update_display_mode)
        toolbar.addWidget(self.display_mode)
        self.start_all=QPushButton('全部连接')
        self.start_all.clicked.connect(self.connect_all)
        toolbar.addWidget(self.start_all)
        self.stop_all=QPushButton('全部停止')
        self.stop_all.clicked.connect(self.stop_everything)
        toolbar.addWidget(self.stop_all)
        toolbar.addStretch()
        self.fullscreen=QPushButton('全屏')
        self.fullscreen.clicked.connect(self.fullscreen_requested)
        toolbar.addWidget(self.fullscreen)
        layout.addLayout(toolbar)
        self.message=QLabel('每格独立播放；需要密码时打开该格的“连接设置”。')
        self.message.setWordWrap(True)
        self.message.hide()
        scroll=QScrollArea()
        scroll.setWidgetResizable(True)
        self.canvas=WatchCanvas()
        self.canvas.resized.connect(self.schedule_layout)
        self.empty=QLabel('双击左侧设备，开始实时监控',self.canvas)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet('color:#8191a5; font-size:16px;')
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll,1)
        self.hint=QLabel('双击画面放大 · 保持原始比例')
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet('color:#8191a5; font-size:13px; padding:12px;')
        layout.addWidget(self.hint)
        if embedded:
            self.choice.hide()
            self.add.hide()
            self.start_all.hide()
            self.stop_all.hide()
            self.display_mode.hide()
        self.update_devices(devices)
        self.relayout()

    def update_display_mode(self):
        for tile in self.tiles:tile.player.surface.set_fill(self.display_mode.currentIndex()==1)

    def update_devices(self,devices):
        self.choice.clear()
        for device in devices:self.choice.addItem(f'{device.ip} · {device.name or device.model or "摄像头"}',device)
        self.add.setEnabled(self.choice.count()>0)

    def active_tiles(self):return list(self.tiles)

    def add_selected(self):
        device=self.choice.currentData()
        if device:self.add_device(device)

    def add_device(self,device):
        if self.closing:return False
        if any(tile.player.device.ip==device.ip for tile in self.tiles):
            self.hint.setText('此摄像头已在监控画面中。')
            return False
        if len(self.tiles)>=self.capacity:
            self.hint.setText('当前画面已满，请切换自动 / 9画面，或先移除一个画面。')
            return False
        tile=CameraTile(len(self.tiles)+1,device,self.credential_store,self)
        tile.setParent(self.canvas)
        tile.remove_requested.connect(self.remove_tile)
        tile.settings_requested.connect(self.settings_requested)
        tile.player.playing.connect(self.playing)
        tile.player.surface.set_fill(self.display_mode.currentIndex()==1)
        self.tiles.append(tile)
        self.relayout()
        self.message.setText(f'已添加 {len(self.tiles)} 台摄像头。点击“全部连接”，或在每格单独连接。')
        return True

    def schedule_layout(self):
        if not self.closing:self.layout_timer.start(0)

    def relayout(self):
        if self.closing:return
        self.empty.setGeometry(self.canvas.rect())
        self.empty.setVisible(not self.tiles)
        visible=[self.focused_tile] if self.focused_tile in self.tiles else list(self.tiles)
        columns=1 if self.focused_tile in self.tiles else (3 if self.layout_mode==9 or (self.layout_mode=='auto' and len(visible)>4) else 2)
        rows=[visible[i:i+columns] for i in range(0,len(visible),columns)]
        gap=16; width=max(1,self.canvas.width());height=max(1,self.canvas.height())
        y=0
        for row in rows:
            ratios=[t.player.surface.aspect_ratio for t in row]
            available=max(60,(height-gap*(len(rows)-1))/max(1,len(rows))-CameraTile.HEADER-1)
            video_height=max(1,int(min((width-gap*(len(row)-1)-2*len(row))/sum(ratios),available)))
            x=0
            for tile,ratio in zip(row,ratios):
                w=round(video_height*ratio)+2
                tile.setGeometry(x,y,w,video_height+CameraTile.HEADER+1)
                tile.show()
                tile.expand.setText('返回' if self.focused_tile is tile else '放大')
                x+=w+gap
            y+=video_height+CameraTile.HEADER+1+gap
        for tile in self.tiles:
            if tile not in visible:tile.hide()
        for button,mode in ((self.auto,'auto'),(self.four,4),(self.nine,9)):
            button.setStyleSheet('background:#2463eb;color:white;border:1px solid #2463eb;' if self.layout_mode==mode else '')

    def toggle_focus(self,tile):
        self.focused_tile=None if self.focused_tile is tile else tile
        self.relayout()

    def change_layout(self,capacity):
        if capacity not in ('auto',4,9):return False
        if capacity==4 and len(self.tiles)>4:
            self.hint.setText('已有超过4路画面，请使用自动或9画面，或移除多余设备。')
            return False
        self.layout_mode=capacity
        self.capacity=9 if capacity=='auto' else capacity
        self.focused_tile=None
        self.relayout()
        return True

    def remove_tile(self,tile):
        if self.closing:return
        if tile not in self.tiles:return
        if not tile.shutdown():
            QTimer.singleShot(150,lambda:self.remove_tile(tile))
            return
        self.tile_removing.emit(tile.player)
        self.tiles.remove(tile)
        if self.focused_tile is tile:self.focused_tile=None
        tile.hide();tile.deleteLater()
        self.relayout()

    def connect_all(self):
        for tile in self.tiles:
            if not tile.player.busy():tile.player.connect_camera()

    def stop_everything(self):
        for tile in self.tiles:tile.player.stop_playback()

    def reject(self):
        if not self.embedded:self.close()

    def closeEvent(self,event):
        self.closing=True
        self.layout_timer.stop()
        self.setEnabled(False)
        ready=True
        for tile in self.tiles:
            if not tile.shutdown():ready=False
        if not ready:
            event.ignore()
            QTimer.singleShot(150,self.close)
            return
        event.accept()
        self.done(QDialog.DialogCode.Rejected)
