"""Bulk connection credentials editor embedded in the existing settings drawer."""
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QCheckBox,QPushButton,QScrollArea


class BatchSettings(QWidget):
    def __init__(self, devices, names, apply, parent=None):
        super().__init__(parent)
        self.apply=apply
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('批量连接账号密码'))
        note=QLabel('只设置本软件连接使用的凭据，不修改设备账号。正在播放的画面会在下次手动连接时使用新凭据。')
        note.setWordWrap(True);layout.addWidget(note)
        self.checks=[]
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setMaximumHeight(260)
        group=QWidget();items=QVBoxLayout(group)
        for d in devices:
            check=QCheckBox(f'{names.display(d)} · {d.ip}');check.setChecked(True)
            items.addWidget(check);self.checks.append((d.ip,check))
        scroll.setWidget(group);layout.addWidget(scroll)
        actions=QHBoxLayout()
        for title,value in (('全选',True),('全不选',False)):
            b=QPushButton(title);b.clicked.connect(lambda checked=False,v=value:self.select_all(v));actions.addWidget(b)
        layout.addLayout(actions)
        self.username=QLineEdit();self.username.setPlaceholderText('统一用户名')
        self.password=QLineEdit();self.password.setPlaceholderText('统一密码');self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.username);layout.addWidget(self.password)
        self.remember=QCheckBox('保存到系统安全存储');self.remember.setChecked(True);layout.addWidget(self.remember)
        note=QLabel('不勾选时仅在本次软件运行中应用，不改动已保存的凭据。');note.setWordWrap(True);layout.addWidget(note)
        self.submit=QPushButton('应用到所选设备');self.submit.clicked.connect(self.submit_credentials);layout.addWidget(self.submit)
        self.result=QLabel();self.result.setWordWrap(True);layout.addWidget(self.result);layout.addStretch()

    def select_all(self,checked):
        for _,box in self.checks:box.setChecked(checked)

    def submit_credentials(self):
        ips=[ip for ip,box in self.checks if box.isChecked()]
        if not ips:self.result.setText('请先勾选至少一台设备。');return
        self.submit.setEnabled(False)
        try:
            count,failed=self.apply(ips,self.username.text(),self.password.text(),self.remember.isChecked())
            self.result.setText(f'已应用到 {count} 台设备。' + (f' {len(failed)} 台保存失败，未修改：'+ '、'.join(failed) if failed else ''))
            if not failed:self.password.clear()
        finally:self.submit.setEnabled(True)
