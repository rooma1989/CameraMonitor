"""Device rows without Qt's Cocoa table accessibility objects.

Qt's synthesized AX table rows/columns can outlive their cells on macOS 26.
Each device here is a standard accessible button containing plain labels.
"""
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QWidget, QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QScrollArea


class Cell:
    def __init__(self, label, row):
        self.label, self.row = label, row

    def text(self):
        return self.label.text()

    def setText(self, text):
        self.label.setText(text)
        self.label.setToolTip(text)
        self.row.setAccessibleName(' · '.join(cell.text() for cell in self.row.cells))


class DeviceRow(QPushButton):
    activated = Signal()
    thumbnailClicked = Signal()

    def __init__(self, compact=False):
        super().__init__()
        self.setCheckable(True)
        self.setMinimumHeight(170 if compact else 80)
        self.setAccessibleDescription('点击选择摄像头，双击或按回车播放视频')
        self.setStyleSheet('''QPushButton {background:transparent; border:none; border-bottom:1px solid #e9eef5; border-radius:0; padding:8px;}
            QPushButton:checked {background:#e5efff; border-left:3px solid #2463eb;}
            QLabel {background:transparent; border:none;}''')
        layout = QVBoxLayout(self) if compact else QHBoxLayout(self)
        layout.setContentsMargins(10,5,10,5)
        layout.setSpacing(4)
        self.thumbnail_image = None
        self.thumbnail = QPushButton('待获取画面')
        self.thumbnail.setFixedSize(128 if compact else 112, 72 if compact else 64)
        self.thumbnail.setIconSize(QSize(124 if compact else 108, 68 if compact else 60))
        self.thumbnail.setAccessibleName('设备缩略图，点击查看大图')
        self.thumbnail.setEnabled(False)
        self.thumbnail.clicked.connect(self.thumbnailClicked.emit)
        layout.addWidget(self.thumbnail)
        self.cells=[]
        for _ in range(5):
            label=QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(label,1)
            self.cells.append(Cell(label,self))
        if compact:
            self.cells[2].label.hide()
            self.cells[3].label.hide()
            self.cells[0].label.setStyleSheet("font-weight:700; font-size:14px;")
            self.cells[4].label.setStyleSheet("font-size:11px; color:#64748b;")

    def set_thumbnail(self, image, status):
        self.thumbnail_image = image.copy() if image is not None and not image.isNull() else None
        self.thumbnail.setToolTip(status)
        self.thumbnail.setAccessibleName('设备缩略图 · ' + status)
        self.thumbnail.setEnabled(self.thumbnail_image is not None)
        if self.thumbnail_image is not None:
            self.thumbnail.setText('')
            self.thumbnail.setIcon(QIcon(QPixmap.fromImage(self.thumbnail_image)))
        else:
            self.thumbnail.setIcon(QIcon())
            self.thumbnail.setText('需登录' if '密码' in status else ('获取中…' if '获取' in status else '暂无预览'))

    def mouseDoubleClickEvent(self,event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.click()
            self.activated.emit()
            event.accept()
        else: super().mouseDoubleClickEvent(event)

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            self.click()
            self.activated.emit()
        else: super().keyPressEvent(event)


class DeviceList(QWidget):
    itemSelectionChanged=Signal()
    cellDoubleClicked=Signal(int,int)
    thumbnailClicked=Signal(int)

    def __init__(self, compact=False):
        super().__init__()
        self.compact=compact
        self.rows=[]
        self.selected=-1
        layout=QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        header=QWidget()
        header.setStyleSheet('background:#eaf0f7; border-radius:6px; font-weight:600;')
        labels=QHBoxLayout(header)
        labels.setContentsMargins(10,10,10,10)
        thumb_header = QLabel('画面')
        thumb_header.setFixedWidth(112)
        labels.addWidget(thumb_header)
        for text in ('IP 地址','设备名称','型号','发现协议','状态'):
            labels.addWidget(QLabel(text),1)
        layout.addWidget(header)
        if compact:header.hide()
        self.scroll=QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content=QWidget()
        self.body=QVBoxLayout(self.content)
        self.body.setContentsMargins(0,0,0,0)
        self.body.setSpacing(0)
        self.body.addStretch()
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll)

    def rowCount(self): return len(self.rows)
    def currentRow(self): return self.selected

    def insertRow(self,index):
        row=DeviceRow(self.compact)
        self.rows.insert(index,row)
        self.body.insertWidget(index,row)
        row.clicked.connect(lambda checked: self.selectRow(self.rows.index(row)))
        row.thumbnailClicked.connect(lambda: self.thumbnailClicked.emit(self.rows.index(row)))
        row.activated.connect(lambda: self.cellDoubleClicked.emit(self.rows.index(row),0))

    def selectRow(self,index):
        if not 0 <= index < len(self.rows): return
        self.selected=index
        for i,row in enumerate(self.rows): row.setChecked(i==index)
        self.itemSelectionChanged.emit()

    def item(self,row,column):
        return self.rows[row].cells[column] if 0 <= row < len(self.rows) and 0 <= column < 5 else None

    def setCellText(self,row,column,text):
        self.item(row,column).setText(text)

    def setThumbnail(self, row, image, status):
        if 0 <= row < len(self.rows): self.rows[row].set_thumbnail(image, status)

    def setRowCount(self,count):
        if count != 0: raise ValueError('Only clearing the device list is supported')
        self.selected=-1
        for row in self.rows:
            self.body.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows=[]
        self.itemSelectionChanged.emit()
