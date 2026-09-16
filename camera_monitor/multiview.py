"""Independent camera players arranged in a 4/9 tile monitor wall."""
from PySide6.QtCore import Qt,QTimer,Signal,QEvent,QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QGridLayout,
    QLabel,QPushButton,QFrame,QScrollArea,QApplication,QMenu,QLineEdit,QCheckBox)
from .choices import ChoiceButton
from .playback import PlayerWindow
from .credentials import CredentialStore
from .device_names import DeviceNames
from .wall_layout import wall_rectangles, DEFAULT_COLUMNS


class CameraTile(QFrame):
    remove_requested=Signal(object)
    settings_requested=Signal(object)
    HEADER=40

    def __init__(self,number,device,credential_store,parent=None):
        super().__init__(parent)
        self.setObjectName('cameraTile')
        self.setStyleSheet('QFrame#cameraTile {background:#101a28; border:0px solid #dce4ed; border-radius:0px;}')
        self.monitor=parent
        self.setAcceptDrops(True)
        self.drag_start=None
        self.player=PlayerWindow(device,self,credential_store=credential_store,device_names=parent.device_names)
        self.player.hosted=True
        self.player.credentials_required.connect(self.show_settings)
        self.player.preview_width=1280
        self.player.setWindowTitle(f'连接设置 · {device.ip}')
        self.player.layout().removeWidget(self.player.surface)
        self.player.surface.setParent(self)
        self.player.surface.setMinimumSize(1,1)
        self.player.surface.set_stretch(True)
        self.player.surface.installEventFilter(self)
        self.player.surface.aspect_changed.connect(lambda _:self.monitor.schedule_layout())
        self.player.surface.activated.connect(lambda:self.monitor.toggle_focus(self))
        self.title=QLabel(f'{parent.device_names.display(device)} · {device.ip}',self)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        parent.device_names.changed.connect(self.refresh_name)
        self.title.setStyleSheet('background:transparent; font-weight:600; font-size:13px; padding-left:12px;')
        self.title.setToolTip(self.title.text())
        self.name_overlay=QLabel(self.player.surface)
        self.name_overlay.setTextFormat(Qt.TextFormat.PlainText)
        self.name_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.name_overlay.hide()
        parent.device_names.appearance_changed.connect(self.refresh_overlay)
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
        self.refresh_overlay()

    def refresh_name(self, ip, name):
        if ip == self.player.device.ip:
            self.title.setText(f'{self.monitor.device_names.display(self.player.device)} · {ip}')
            self.title.setToolTip(self.title.text())
            self.refresh_overlay()

    def update_status(self,text):
        live=text.startswith('正在播放') or text.startswith('画面正在播放')
        summary='播放中' if live else ('重连中' if '重连' in text else ('待处理' if '失败' in text else '待连接'))
        self.status.setText(summary)
        self.status.setStyleSheet(f'background:transparent; color:{"#20b765" if live else "#c68b21"}; font-size:12px;')
        self.status.setToolTip(text)
        self.monitor.device_status.emit(self.player.device.ip,summary)

    def refresh_overlay(self, ip=None):
        if ip is not None and ip != self.player.device.ip:return
        color, self.name_corner = self.monitor.device_names.appearance(self.player.device.ip)
        self.name_overlay.setText(self.monitor.device_names.display(self.player.device))
        self.name_overlay.setStyleSheet(f'color:{color};background:rgba(0,0,0,95);font-size:16px;font-weight:600;padding:4px 8px;border-radius:3px;')
        self.layout_contents()

    def layout_contents(self):
        if not hasattr(self,'controls'):return
        fullscreen=self.monitor.presentation
        self.title.hide();self.status.hide()
        self.player.surface.setGeometry(self.rect())
        width=min(self.player.surface.width(),350)
        self.controls.setGeometry(self.player.surface.width()-width,max(0,self.player.surface.height()-38),width,38)
        self.name_overlay.setMaximumWidth(max(1,self.player.surface.width()-16))
        self.name_overlay.adjustSize()
        x=max(0,self.player.surface.width()-self.name_overlay.width()-8) if 'right' in self.name_corner else 8
        y=max(0,self.player.surface.height()-self.name_overlay.height()-8) if 'bottom' in self.name_corner else 8
        self.name_overlay.move(x,y);self.name_overlay.setVisible(True);self.name_overlay.raise_()
        if fullscreen:self.controls.hide()

    def resizeEvent(self,event):
        super().resizeEvent(event);self.layout_contents()

    def eventFilter(self,watched,event):
        if watched is self.player.surface:
            if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
                self.drag_start=event.position().toPoint()
            elif event.type()==QEvent.Type.MouseButtonDblClick:
                self.drag_start=None
                return True
            elif event.type()==QEvent.Type.MouseButtonRelease:
                clicked=self.drag_start is not None and event.button()==Qt.MouseButton.LeftButton
                self.drag_start=None
                if clicked:
                    self.monitor.toggle_focus(self)
                    return True
            elif event.type()==QEvent.Type.MouseMove and self.drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
                if (event.position().toPoint()-self.drag_start).manhattanLength() >= QApplication.startDragDistance():
                    self.drag_start=None
                    drag=QDrag(self);mime=QMimeData();mime.setData('application/x-camera-monitor-tile',b'move')
                    drag.setMimeData(mime);drag.exec(Qt.DropAction.MoveAction);return True
        if event.type()==QEvent.Type.FocusIn and not self.monitor.presentation:
            self.controls.show();self.controls.raise_()
        return super().eventFilter(watched,event)

    def dragEnterEvent(self,event):
        if event.source() in self.monitor.tiles and event.mimeData().hasFormat('application/x-camera-monitor-tile'):
            event.acceptProposedAction()
        else:event.ignore()

    def dropEvent(self,event):
        if self.monitor.swap_tiles(event.source(),self):event.acceptProposedAction()
        else:event.ignore()

    def enterEvent(self,event):
        if not self.monitor.presentation:self.controls.show();self.controls.raise_()
        super().enterEvent(event)

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


class EmptySlot(QLabel):
    def __init__(self,monitor):
        super().__init__(monitor.canvas)
        self.monitor=monitor
        self.slot_index=0
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet('background:#172333;color:#8293a8;border:1px solid #354255;')

    def dragEnterEvent(self,event):
        if event.source() in self.monitor.tiles and event.mimeData().hasFormat('application/x-camera-monitor-tile'):
            event.acceptProposedAction()
        else:event.ignore()

    def dropEvent(self,event):
        if self.monitor.move_tile(event.source(),self.slot_index):event.acceptProposedAction()
        else:event.ignore()


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

    def __init__(self,devices,parent=None,credential_store=None,embedded=False,device_names=None):
        super().__init__(parent)
        self.embedded=embedded
        self.device_names=device_names if device_names is not None else DeviceNames()
        if embedded:self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle('Camera Monitor · 多画面监控')
        self.resize(1200,850)
        self.setMinimumSize(620,350) if embedded else self.setMinimumSize(820,600)
        self.credential_store=credential_store or CredentialStore()
        self.capacity=4
        self.presentation=False
        self.layout_mode=4
        self.focused_tile=None
        self.layout_timer=QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.timeout.connect(self.relayout)
        self.tiles=[]
        self.slots=[None]*25
        self.saved_slots=self.device_names.slot_order()
        self.placeholders=[]
        self.closing=False
        layout=QVBoxLayout(self)
        heading=QLabel('多画面监控')
        heading.setStyleSheet('font-size:24px; font-weight:600;')
        if embedded:heading.setText('实时监控')
        self.toolbar_widget=QWidget()
        toolbar=QHBoxLayout(self.toolbar_widget)
        toolbar.setContentsMargins(0,0,0,0)
        toolbar.addWidget(heading)
        self.fill_width=QCheckBox('横向铺满（允许变形）')
        self._fill_width=str(self.device_names.settings.value('monitor/fill_width',False)).lower() in ('true','1')
        self.fill_width.setChecked(self._fill_width)
        self.fill_width.setToolTip('消除左右留白，画面横向拉宽；关闭后恢复 16:9。')
        self.fill_width.toggled.connect(self.set_fill_width)
        toolbar.addWidget(self.fill_width)
        toolbar.addStretch()
        self.auto=QPushButton('4 格默认')
        self.auto.setMinimumWidth(90)
        self.auto.clicked.connect(lambda:self.change_layout('auto'))
        self.auto.hide()
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
        self.featured=ChoiceButton()
        for count in (6,10,15):self.featured.addItem(f'一大多小 · {count} 格',count)
        self.featured.activated.connect(lambda _:self.change_layout('featured'))
        toolbar.addWidget(self.featured)
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
        layout.addWidget(self.toolbar_widget)
        self.layout_toolbar=QWidget()
        layout_choices=QHBoxLayout(self.layout_toolbar)
        layout_choices.setContentsMargins(0,0,0,0)
        layout_choices.addWidget(QLabel('分屏'))
        self.equal_buttons={4:self.four,9:self.nine}
        for count in (12,16,20,25):
            button=QPushButton(f'{count}画面')
            button.clicked.connect(lambda checked=False,n=count:self.change_layout(n))
            self.equal_buttons[count]=button
        for button in self.equal_buttons.values():
            toolbar.removeWidget(button);button.setMinimumWidth(62);layout_choices.addWidget(button)
        toolbar.removeWidget(self.featured);layout_choices.addWidget(self.featured)
        self.columns_choice=ChoiceButton()
        self.columns_choice.setToolTip('设置当前等分布局每行的画面数量；每格保持 16:9。')
        self.columns_choice.activated.connect(lambda _:self.set_grid_columns(self.columns_choice.currentData()))
        layout_choices.addWidget(self.columns_choice)
        self.sync_columns_choice()
        layout_choices.addStretch()
        layout.addWidget(self.layout_toolbar)

        self.organization_button=QPushButton('机构名称')
        toolbar.addWidget(self.organization_button)
        self.organization_panel=QWidget()
        organization_layout=QHBoxLayout(self.organization_panel)
        organization_layout.addWidget(QLabel('全屏顶部名称'))
        self.organization_input=QLineEdit(self.device_names.settings.value('monitor/organization',''))
        self.organization_input.setMaxLength(80)
        self.organization_input.setPlaceholderText('例如：阳光养老院（留空不显示）')
        organization_layout.addWidget(self.organization_input)
        save=QPushButton('保存');organization_layout.addWidget(save)
        save.clicked.connect(self.save_organization)
        self.organization_input.returnPressed.connect(self.save_organization)
        self.organization_button.clicked.connect(lambda:self.organization_panel.setVisible(self.organization_panel.isHidden()))
        layout.addWidget(self.organization_panel);self.organization_panel.hide()
        self.organization_header=QLabel(self.organization_input.text().strip())
        self.organization_header.setTextFormat(Qt.TextFormat.PlainText)
        self.organization_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.organization_header.setFixedHeight(48)
        self.organization_header.setStyleSheet('font-size:24px;font-weight:600;color:white;background:#152235;')
        layout.addWidget(self.organization_header);self.organization_header.hide()
        self.message=QLabel('每格独立播放；需要密码时打开该格的“连接设置”。')
        self.message.setWordWrap(True)
        self.message.hide()
        scroll=self.scroll=QScrollArea()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        self.canvas=WatchCanvas()
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        self.canvas.resized.connect(self.schedule_layout)
        self.empty=QLabel('双击左侧设备，开始实时监控',self.canvas)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet('color:#8191a5; font-size:16px;')
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll,1)
        self.hint=QLabel('拖动交换画面 · 单击放大 / 返回 · 全屏按 Esc 返回')
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

    def save_organization(self):
        from PySide6.QtCore import QSettings
        name=self.organization_input.text().strip()
        settings=self.device_names.settings
        previous=settings.value('monitor/organization','')
        settings.setValue('monitor/organization',name);settings.sync()
        if settings.status()!=QSettings.Status.NoError:
            settings.setValue('monitor/organization',previous)
            self.hint.setText('机构名称保存失败，请检查本机存储权限。');return
        self.organization_header.setText(name)
        self.organization_header.setVisible(self.presentation and bool(name))
        self.organization_panel.hide()
        self.schedule_layout()

    def update_display_mode(self):
        for tile in self.tiles:tile.player.surface.set_stretch(True)

    def update_devices(self,devices):
        self.choice.clear()
        for device in devices:self.choice.addItem(f'{device.ip} · {self.device_names.display(device)}',device)
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
            self.hint.setText('当前布局已满，请选择格子更多的布局。')
            return False
        tile=CameraTile(len(self.tiles)+1,device,self.credential_store,self)
        tile.setParent(self.canvas)
        tile.remove_requested.connect(self.remove_tile)
        tile.settings_requested.connect(self.settings_requested)
        tile.player.playing.connect(self.playing)
        tile.player.surface.set_stretch(True)
        self.tiles.append(tile)
        preferred=self.saved_slots.index(device.ip) if device.ip in self.saved_slots else -1
        index=preferred if 0<=preferred<self.capacity and self.slots[preferred] is None else self.slots.index(None)
        self.slots[index]=tile
        self.tiles=[t for t in self.slots if t is not None]
        self.relayout()
        self.message.setText(f'已添加 {len(self.tiles)} 台摄像头。点击“全部连接”，或在每格单独连接。')
        return True

    def schedule_layout(self):
        if not self.closing:self.layout_timer.start(0)

    def relayout(self):
        if self.closing:return
        self.empty.setGeometry(self.canvas.rect())
        self.empty.hide()
        visible=[self.focused_tile] if self.focused_tile in self.tiles else list(self.tiles)
        width=max(1,self.canvas.width());height=max(1,self.canvas.height())
        focused=self.focused_tile in self.tiles
        count=1 if focused else self.capacity
        rects=wall_rectangles(count,width,height,not focused and self.capacity in (6,10,15),
                              header=0 if self.presentation else CameraTile.HEADER,
                              gap=1 if self.presentation else 4,
                              columns=None if focused else self.grid_columns(),fill_width=self._fill_width)
        missing=count-len(visible)
        while len(self.placeholders)>missing:
            placeholder=self.placeholders.pop();placeholder.hide();placeholder.deleteLater()
        while len(self.placeholders)<missing:
            placeholder=EmptySlot(self)
            self.placeholders.append(placeholder)
        empty_indices=[] if focused else [i for i in range(self.capacity) if self.slots[i] is None]
        for placeholder,index in zip(self.placeholders,empty_indices):
            placeholder.slot_index=index
            placeholder.setText(f'画面 {index+1:02d}\n拖动摄像头到此处')
            placeholder.setGeometry(*rects[index]);placeholder.show()
        for tile in visible:
            index=0 if focused else self.slots.index(tile)
            tile.setGeometry(*rects[index])
            tile.layout_contents();tile.show()
            tile.expand.setText('返回' if self.focused_tile is tile else '放大')
        for tile in self.tiles:
            if tile not in visible:tile.hide()
        for mode,button in self.equal_buttons.items():
            button.setStyleSheet('background:#2463eb;color:white;border:1px solid #2463eb;' if self.layout_mode==mode else '')

    def set_presentation(self,enabled):
        self.presentation=enabled
        self.canvas.setStyleSheet('background:#101a28;' if enabled else '')
        self.toolbar_widget.setVisible(not enabled);self.layout_toolbar.setVisible(not enabled);self.hint.setVisible(not enabled)
        if enabled:self.organization_panel.hide()
        self.organization_header.setVisible(enabled and bool(self.organization_header.text()))
        self.layout().setContentsMargins(*((0,0,0,0) if enabled else (9,9,9,9)))
        self.layout().setSpacing(0 if enabled else 6)
        self.relayout();self.schedule_layout()

    def persist_slots(self):
        self.saved_slots=[t.player.device.ip if t else '' for t in self.slots]
        try:self.device_names.save_slot_order(self.saved_slots)
        except OSError:self.hint.setText('位置已调整，但未能保存到本机。')

    def move_tile(self,source,index):
        if self.closing or source not in self.tiles or not 0<=index<self.capacity:return False
        old=self.slots.index(source)
        if old==index:return False
        self.slots[old],self.slots[index]=self.slots[index],self.slots[old]
        self.tiles=[t for t in self.slots if t is not None]
        self.persist_slots();self.relayout();return True

    def swap_tiles(self,source,target):
        if target not in self.tiles:return False
        return self.move_tile(source,self.slots.index(target))

    def toggle_focus(self,tile):
        self.focused_tile=None if self.focused_tile is tile else tile
        self.relayout()

    def set_fill_width(self,enabled):
        settings=self.device_names.settings
        if not self.presentation:
            settings.setValue('monitor/fill_width',enabled)
            settings.sync()
            if settings.status()==settings.Status.NoError:
                self._fill_width=enabled
            else:
                settings.setValue('monitor/fill_width',self._fill_width)
                self.hint.setText('横向铺满设置未保存，请检查本机设置存储权限。')
        self.fill_width.blockSignals(True)
        self.fill_width.setChecked(self._fill_width)
        self.fill_width.blockSignals(False)
        self.relayout()

    def grid_columns(self):
        default=DEFAULT_COLUMNS.get(self.capacity)
        if default is None:return None
        value=self.device_names.settings.value(f'monitor/columns/{self.capacity}',default)
        try:value=int(value)
        except (ValueError,TypeError):return default
        return value if 1<=value<=self.capacity else default

    def sync_columns_choice(self):
        choice=self.columns_choice
        choice.blockSignals(True)
        choice.clear()
        default=DEFAULT_COLUMNS.get(self.capacity)
        choice.setEnabled(default is not None)
        if default is None:
            choice.setText('每行数量 · 环绕固定')
        else:
            choice.addItem(f'每行 {default} 个（默认）',None)
            for columns in range(1,self.capacity+1):
                choice.addItem(f'每行 {columns} 个',columns)
            current=self.grid_columns()
            choice.setCurrentIndex(0 if current==default else current)
        choice.blockSignals(False)

    def set_grid_columns(self,columns):
        if self.presentation or self.capacity not in DEFAULT_COLUMNS:return False
        if columns is not None and (not isinstance(columns,int) or not 1<=columns<=self.capacity):return False
        settings=self.device_names.settings
        key=f'monitor/columns/{self.capacity}'
        previous=settings.value(key,None)
        if columns is None:settings.remove(key)
        else:settings.setValue(key,columns)
        settings.sync()
        if settings.status()!=settings.Status.NoError:
            if previous is None:settings.remove(key)
            else:settings.setValue(key,previous)
            self.hint.setText('每行数量未保存，请检查本机设置存储权限。')
            self.sync_columns_choice()
            return False
        self.sync_columns_choice()
        self.relayout()
        return True

    def change_layout(self,capacity):
        if capacity=='featured':capacity=self.featured.currentData()
        if capacity=='auto':capacity=4
        if capacity not in (4,6,9,10,12,15,16,20,25):return False
        limit=capacity
        if len(self.tiles)>limit:
            self.hint.setText('已有画面数量超过所选布局，请先移除多余设备。');return False
        overflow=[t for t in self.slots[limit:] if t is not None]
        if overflow:
            self.slots[limit:]=[None]*(25-limit)
            for tile in overflow:self.slots[self.slots.index(None)]=tile
            self.tiles=[t for t in self.slots if t is not None]
            self.persist_slots()
        self.layout_mode=capacity;self.capacity=limit
        self.featured.blockSignals(True)
        if capacity in (6,10,15):self.featured.setCurrentIndex((6,10,15).index(capacity))
        else:self.featured.setText('一大多小')
        self.featured.blockSignals(False)
        self.sync_columns_choice()
        self.focused_tile=None;self.relayout();return True

    def remove_tile(self,tile):
        if self.closing:return
        if tile not in self.tiles:return
        if not tile.shutdown():
            QTimer.singleShot(150,lambda:self.remove_tile(tile))
            return
        self.tile_removing.emit(tile.player)
        self.slots[self.slots.index(tile)]=None
        self.tiles.remove(tile)
        self.persist_slots()
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
