"""Device rows without Qt's Cocoa table accessibility objects.

Qt's synthesized AX table rows/columns can outlive their cells on macOS 26.
Each device here is a standard accessible button containing plain labels.
"""
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPalette
from PySide6.QtWidgets import QWidget, QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QScrollArea, QSizePolicy


class ElidedLabel(QLabel):
    """Keep the full cell value for selection/filtering; elide only its painting."""
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(18)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width())
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class Cell:
    def __init__(self, label, row):
        self.label, self.row = label, row

    def text(self):
        return self.label.text()

    def setText(self, text):
        self.label.setText(text)
        self.label.setToolTip(text)
        full_text = ' · '.join(cell.text() for cell in self.row.cells)
        self.row.setAccessibleName(full_text)
        self.row.setToolTip(full_text)


class DeviceRow(QPushButton):
    activated = Signal()
    thumbnailClicked = Signal()

    def __init__(self, compact=False):
        super().__init__()
        self.setCheckable(True)
        if compact: self.setFixedHeight(78)
        else: self.setMinimumHeight(80)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleDescription('点击选择摄像头，双击或按回车播放视频')
        self.setStyleSheet('''QPushButton {background:transparent; border:none; border-bottom:1px solid #e9eef5; border-radius:0; padding:8px;}
            QPushButton:checked {background:#e5efff; border-left:3px solid #2463eb;}
            QLabel {background:transparent; border:none;}''')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6 if compact else 10, 5, 6 if compact else 10, 5)
        layout.setSpacing(7 if compact else 4)
        self.thumbnail_image = None
        self.thumbnail = QPushButton('待获取画面')
        self.thumbnail.setFixedSize(80 if compact else 112, 45 if compact else 64)
        self.thumbnail.setIconSize(QSize(76 if compact else 108, 41 if compact else 60))
        self.thumbnail.setAccessibleName('设备缩略图，点击查看大图')
        self.thumbnail.setEnabled(False)
        self.thumbnail.clicked.connect(self.thumbnailClicked.emit)
        self.thumbnail.setStyleSheet('QPushButton {padding:0; border:0; border-radius:4px; background:#edf2f8; color:#64748b; font-size:11px;}')
        layout.addWidget(self.thumbnail, 0, Qt.AlignmentFlag.AlignVCenter)
        text_layout = QVBoxLayout() if compact else layout
        if compact:
            text_layout.setSpacing(0)
            text_layout.setContentsMargins(0,0,0,0)
            layout.addLayout(text_layout,1)
        self.cells=[]
        for _ in range(5):
            label=ElidedLabel() if compact else QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(not compact)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            if not compact: layout.addWidget(label,1)
            self.cells.append(Cell(label,self))
        if compact:
            for index in (1, 0, 4): text_layout.addWidget(self.cells[index].label)
            self.cells[2].label.hide()
            self.cells[3].label.hide()
            self.cells[1].label.setStyleSheet("font-weight:600; font-size:12px;")
            self.cells[0].label.setStyleSheet("font-size:11px; color:#475569;")
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
