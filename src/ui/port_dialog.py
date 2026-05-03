"""端口占用查询对话框

顶部：端口号输入 + 查询按钮
中部：结果表格（PID / 进程名 / 地址 / 状态 / 命令行）
底部：选中 → kill 按钮

psutil.net_connections() 在 Windows 上 200-800ms，必须放 QThread。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from src.core import port_scanner
from src.ui.theme import FG_SECONDARY


class _ScanWorker(QThread):
    done = Signal(int, list)   # (port, list[PortOwner])

    def __init__(self, port: int, parent=None):
        super().__init__(parent)
        self._port = port

    def run(self) -> None:
        owners = port_scanner.list_port_listeners(self._port)
        self.done.emit(self._port, owners)


class _KillWorker(QThread):
    done = Signal(int, bool, str)   # (pid, ok, msg)

    def __init__(self, pid: int, parent=None):
        super().__init__(parent)
        self._pid = pid

    def run(self) -> None:
        ok, msg = port_scanner.kill_process(self._pid)
        self.done.emit(self._pid, ok, msg)


class PortDialog(QDialog):

    def __init__(self, default_port: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔌 端口占用查询")
        self.resize(640, 380)
        self._scan_worker: _ScanWorker | None = None
        self._kill_worker: _KillWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # 输入行
        top = QHBoxLayout()
        top.addWidget(QLabel("端口号："))
        self.input_port = QLineEdit()
        self.input_port.setPlaceholderText("例如 8080")
        self.input_port.setMaximumWidth(120)
        self.input_port.returnPressed.connect(self._scan)
        top.addWidget(self.input_port)
        self.btn_scan = QPushButton("查询")
        self.btn_scan.clicked.connect(self._scan)
        top.addWidget(self.btn_scan)
        top.addStretch(1)
        root.addLayout(top)

        # 状态标签
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet(f"color:{FG_SECONDARY};")
        root.addWidget(self.lbl_status)

        # 结果表
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["PID", "进程名", "地址", "状态", "命令行"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_kill_enabled)
        root.addWidget(self.table, 1)

        # 底部按钮
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_kill = QPushButton("🗲 kill 选中进程")
        self.btn_kill.setEnabled(False)
        self.btn_kill.clicked.connect(self._kill_selected)
        bottom.addWidget(self.btn_kill)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        bottom.addWidget(self.btn_close)
        root.addLayout(bottom)

        # Esc 关闭
        QShortcut(QKeySequence("Escape"), self, activated=self.close)

        if default_port > 0:
            self.input_port.setText(str(default_port))
            # 自动触发一次查询
            self._scan()
        else:
            self.input_port.setFocus()

    # ---- 查询 ----

    def _scan(self) -> None:
        text = self.input_port.text().strip()
        try:
            port = int(text)
        except ValueError:
            QMessageBox.warning(self, "端口无效", f"请输入 1-65535 之间的端口号，收到：{text!r}")
            return
        if not (1 <= port <= 65535):
            QMessageBox.warning(self, "端口无效", "端口号需在 1-65535 范围内")
            return
        if self._scan_worker and self._scan_worker.isRunning():
            return
        self.btn_scan.setEnabled(False)
        self.lbl_status.setText(f"查询端口 {port} 占用情况...")
        self.table.setRowCount(0)
        self._scan_worker = _ScanWorker(port, self)
        self._scan_worker.done.connect(self._on_scan_done)
        self._scan_worker.start()

    def _on_scan_done(self, port: int, owners: list) -> None:
        self.btn_scan.setEnabled(True)
        if not owners:
            self.lbl_status.setText(f"✓ 端口 {port} 当前空闲（未找到监听或连接进程）")
            return
        self.lbl_status.setText(f"⚠ 端口 {port} 有 {len(owners)} 个进程占用：")
        self.table.setRowCount(len(owners))
        for row, o in enumerate(owners):
            self.table.setItem(row, 0, QTableWidgetItem(str(o.pid)))
            self.table.setItem(row, 1, QTableWidgetItem(o.name))
            self.table.setItem(row, 2, QTableWidgetItem(o.address))
            self.table.setItem(row, 3, QTableWidgetItem(o.status))
            cmd_item = QTableWidgetItem(o.cmdline)
            cmd_item.setToolTip(o.cmdline)
            self.table.setItem(row, 4, cmd_item)
        self.table.selectRow(0)

    # ---- kill ----

    def _update_kill_enabled(self) -> None:
        self.btn_kill.setEnabled(bool(self.table.selectedItems()))

    def _selected_pid(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        pid_item = self.table.item(rows[0].row(), 0)
        if not pid_item:
            return None
        try:
            return int(pid_item.text())
        except ValueError:
            return None

    def _kill_selected(self) -> None:
        pid = self._selected_pid()
        if pid is None:
            return
        row = self.table.selectionModel().selectedRows()[0].row()
        name = self.table.item(row, 1).text() if self.table.item(row, 1) else "?"
        ret = QMessageBox.question(
            self, "确认 kill",
            f"确认 kill 进程？\n\n  PID: {pid}\n  进程: {name}\n\n"
            "会先尝试 terminate，3 秒内未退出升级为 kill。操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        if self._kill_worker and self._kill_worker.isRunning():
            return
        self.btn_kill.setEnabled(False)
        self.lbl_status.setText(f"正在 kill PID {pid} ...")
        self._kill_worker = _KillWorker(pid, self)
        self._kill_worker.done.connect(self._on_kill_done)
        self._kill_worker.start()

    def _on_kill_done(self, pid: int, ok: bool, msg: str) -> None:
        if ok:
            self.lbl_status.setText(f"✓ PID {pid} 已结束：{msg}。点「查询」可刷新")
        else:
            self.lbl_status.setText(f"✗ kill PID {pid} 失败：{msg}")
            QMessageBox.warning(self, "kill 失败", f"PID {pid}\n\n{msg}")
        # 自动刷新一次
        self._scan()
