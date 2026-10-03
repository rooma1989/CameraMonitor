"""第一次打开时的欢迎页：输一个设备码就行，剩下的交给后台。"""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

CENTER = Qt.AlignmentFlag.AlignCenter


class WelcomePage(QWidget):
    login_requested = Signal(str)
    standalone_chosen = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('welcomePage')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet('''
            QWidget#welcomePage { background:#f2f5fb; }
            QFrame#welcomeCard { background:white; border-radius:16px; }
            QFrame#welcomeCard QLabel { background:transparent; }
            QLabel#welcomeTitle { font-size:26px; font-weight:700; color:#111827; }
            QLabel#welcomeHint { color:#6b7280; font-size:14px; }
            QLabel#welcomeError { color:#c0392b; font-size:13px; }
            QLineEdit#welcomeCode { font-size:24px; padding:12px; border:2px solid #2463eb;
                                    border-radius:10px; background:white; }
            QPushButton#welcomeStart { background:#2463eb; color:white; font-size:18px;
                                       font-weight:600; padding:12px; border-radius:10px; border:none; }
            QPushButton#welcomeStart:disabled { background:#9db7f5; }
            QPushButton#welcomeAlone { background:transparent; border:none; color:#2463eb; font-size:13px; }
        ''')

        card = QFrame()
        card.setObjectName('welcomeCard')
        card.setFixedWidth(520)
        box = QVBoxLayout(card)
        box.setContentsMargins(48, 40, 48, 32)
        box.setSpacing(14)

        icon = QLabel()
        icon.setAlignment(CENTER)
        pixmap = QPixmap(str(Path(__file__).parent / 'assets' / 'app-icon.png'))
        if not pixmap.isNull():
            icon.setPixmap(pixmap.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        box.addWidget(icon)

        title = QLabel('内网监控中心')
        title.setObjectName('welcomeTitle')
        title.setAlignment(CENTER)
        box.addWidget(title)

        hint = QLabel('请输入管理员给您的设备码\n输入后无需任何设置，画面会自动出现')
        hint.setObjectName('welcomeHint')
        hint.setAlignment(CENTER)
        box.addWidget(hint)

        self.code = QLineEdit()
        self.code.setObjectName('welcomeCode')
        self.code.setAlignment(CENTER)
        self.code.setMaxLength(64)
        self.code.setPlaceholderText('例如 7K2M-9QXT-4B8N')
        self.code.returnPressed.connect(self.submit)
        box.addWidget(self.code)

        self.start = QPushButton('开始使用')
        self.start.setObjectName('welcomeStart')
        self.start.clicked.connect(self.submit)
        box.addWidget(self.start)

        self.error = QLabel('')
        self.error.setObjectName('welcomeError')
        self.error.setAlignment(CENTER)
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.hide()
        box.addWidget(self.error)

        tip = QLabel('不区分大小写，中间的横线可以不输')
        tip.setObjectName('welcomeHint')
        tip.setAlignment(CENTER)
        box.addWidget(tip)

        self.alone = QPushButton('没有设备码？不用云端，单机使用 →')
        self.alone.setObjectName('welcomeAlone')
        self.alone.clicked.connect(self.standalone_chosen)
        box.addWidget(self.alone)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

    def submit(self):
        if not self.start.isEnabled():
            return
        code = self.code.text().strip()
        if not code:
            self.show_error('请输入设备码。')
            return
        self.show_error('')
        self.login_requested.emit(code)

    def set_busy(self, busy):
        for widget in (self.start, self.code, self.alone):
            widget.setEnabled(not busy)
        self.start.setText('正在连接…' if busy else '开始使用')

    def show_error(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))
