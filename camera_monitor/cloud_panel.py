"""Sidebar entry for cloud sync. Off by default: without a code the app stays standalone."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)


class CloudLoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('连接云端配置')
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        intro = QLabel('输入管理员发给这个点位的授权码，本机就会自动获取摄像头列表、'
                       '画面顺序和账号密码。不填则保持纯单机使用。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.code = QLineEdit()
        self.code.setPlaceholderText('例如 7K2M-9QXT-4B8N')
        self.code.setMaxLength(64)
        layout.addWidget(self.code)
        hint = QLabel('不区分大小写，连字符可以省略。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('连接')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.code.returnPressed.connect(self.accept)
        self.code.setFocus()

    def auth_code(self):
        return self.code.text().strip()


class CloudPanel(QWidget):
    """One button plus one status line; stays quiet until someone opts in."""

    login_requested = Signal(str)
    logout_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.button = QPushButton('云端同步')
        self.button.clicked.connect(self.toggle)
        layout.addWidget(self.button)
        self.status = QLabel('')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        layout.addWidget(self.status)
        self.dialog = None
        self.connected = False

    def toggle(self):
        # exec() 会起一个嵌套事件循环，期间这个方法可能被再次触发（重复点击、
        # 辅助功能重发的按键等）。此前内层的 finally 会把 self.dialog 置空，
        # 外层再去读它就是 AttributeError，登录框直接崩掉。
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return

        if self.connected:
            self.logout_requested.emit()
            return

        dialog = self.dialog = CloudLoginDialog(self)
        try:
            # 全程只用局部变量，不依赖 self.dialog 在 exec() 之后还在
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            code = dialog.auth_code()
        finally:
            self.dialog = None
            dialog.deleteLater()

        if accepted:
            self.login_requested.emit(code)

    def set_connected(self, connected, profile_name=''):
        self.connected = connected
        self.button.setText(f'退出云端 · {profile_name}' if connected and profile_name
                            else ('退出云端同步' if connected else '云端同步'))

    def set_status(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet('color:#c0392b;font-size:12px;' if error else '')
        self.status.setVisible(bool(text))
