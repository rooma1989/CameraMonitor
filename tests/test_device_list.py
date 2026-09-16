import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from PySide6.QtWidgets import QApplication, QAbstractItemView
from PySide6.QtGui import QAccessible
from camera_monitor.device_list import DeviceList

class DeviceListTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
 def test_rows_are_accessible_buttons_not_native_tables(self):
  widget=DeviceList()
  widget.insertRow(0)
  widget.setCellText(0,0,'192.168.1.108')
  widget.setCellText(0,1,'大华')
  iface=QAccessible.queryAccessibleInterface(widget.rows[0])
  self.assertIn(iface.role(),(QAccessible.Role.Button,QAccessible.Role.CheckBox))
  self.assertIn('192.168.1.108',iface.text(QAccessible.Text.Name))
  self.assertEqual(widget.findChildren(QAbstractItemView),[])
  widget.close()
 def test_selection_update_and_rescan(self):
  widget=DeviceList()
  selected=[]
  widget.itemSelectionChanged.connect(lambda:selected.append(widget.currentRow()))
  for row in range(2):
   widget.insertRow(row)
   widget.setCellText(row,0,f'192.168.1.{row+1}')
  widget.rows[1].click()
  self.assertEqual(widget.currentRow(),1)
  self.assertEqual(selected,[1])
  cell=widget.item(1,0)
  widget.setCellText(1,0,'192.168.1.108')
  self.assertIs(widget.item(1,0),cell)
  widget.setRowCount(0)
  self.assertEqual(widget.rowCount(),0)
  self.assertEqual(widget.currentRow(),-1)
  widget.close()

 def test_choice_selection_has_no_item_view(self):
  from camera_monitor.choices import ChoiceButton
  choice=ChoiceButton()
  choice.addItem('ONVIF','onvif')
  choice.addItem('大华','dahua')
  choice._menu.actions()[1].trigger()
  self.assertEqual(choice.currentData(),'dahua')
  self.assertEqual(choice.findChildren(QAbstractItemView),[])
  choice.clear()
  self.assertIsNone(choice.currentData())
  choice.close()

 def test_compact_rows_fit_narrow_sidebar_with_full_cell_values(self):
  widget=DeviceList(compact=True)
  widget.resize(191,400)
  full_name='仓库东侧非常长的摄像头名称不能丢失'
  for row in range(5):
   widget.insertRow(row)
   widget.setCellText(row,0,f'192.168.100.{row+100}')
   widget.setCellText(row,1,full_name)
   widget.setCellText(row,4,'已发现 · 未验证视频')
  widget.show();self.app.processEvents()
  first=widget.rows[0]
  self.assertEqual(first.height(),78)
  self.assertEqual(first.thumbnail.width(),80)
  self.assertEqual(first.thumbnail.height(),45)
  self.assertLessEqual(first.width(),widget.scroll.viewport().width())
  self.assertEqual(widget.item(0,1).text(),full_name)
  self.assertEqual(widget.item(0,1).label.toolTip(),full_name)
  for index in (0,1,4):
   label=first.cells[index].label
   self.assertGreaterEqual(label.x(),first.thumbnail.geometry().right()+1)
   self.assertLessEqual(label.geometry().right(),first.width())
   self.assertLessEqual(label.geometry().bottom(),first.height())
  self.assertEqual(widget.scroll.horizontalScrollBar().maximum(),0)
  widget.close()
