"""Small choices rendered as menu actions, without Cocoa AX item-view tables."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QMenu


class ChoiceButton(QPushButton):
    currentIndexChanged = Signal(int)

    def __init__(self):
        super().__init__('请选择')
        self._items=[]
        self._index=-1
        self._menu=QMenu(self)
        self.setMenu(self._menu)

    def addItem(self,text,data=None):
        index=len(self._items)
        action=self._menu.addAction(text)
        action.setCheckable(True)
        action.triggered.connect(lambda checked=False,i=index:self.setCurrentIndex(i))
        self._items.append((text,data,action))
        if self._index < 0:self.setCurrentIndex(0)

    def addItems(self,texts):
        for text in texts:self.addItem(text)

    def clear(self):
        self._menu.clear()
        self._items=[]
        self._index=-1
        self.setText('请选择')

    def count(self):return len(self._items)
    def currentIndex(self):return self._index
    def currentData(self):return self._items[self._index][1] if self._index>=0 else None
    def currentText(self):return self._items[self._index][0] if self._index>=0 else ''

    def setCurrentIndex(self,index):
        if not 0<=index<len(self._items):return
        changed=index!=self._index
        self._index=index
        self.setText(self._items[index][0])
        for i,(_,_,action) in enumerate(self._items):action.setChecked(i==index)
        if changed:self.currentIndexChanged.emit(index)
