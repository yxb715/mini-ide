"""环境/配置文件集中面板（查看入口）

扫项目里的 .env* / application*.yml / application*.properties / bootstrap*.yml / *.env 等，
以列表展示，双击或点按钮 → 通过 openFileRequested 信号转发给 ProjectTab，在中心 Tab 容器里
开一个 FilePreviewPane（默认编辑模式，自动保存）。

扫描放 QThread，避免 mono-repo 遍历卡主线程。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from src.core import env_scanner
from src.ui.theme import FG_SECONDARY


class _ScanWorker(QThread):
    done = Signal(list)   # list[str] 绝对路径

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.project_root = project_root

    def run(self) -> None:
        files = env_scanner.list_env_files(self.project_root)
        self.done.emit([str(p) for p in files])


class EnvPanel(QDialog):

    # 对外信号：用户选择了某个文件要打开。ProjectTab 负责在中心 Tab 容器里开 FilePreviewPane
    openFileRequested = Signal(str)

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self.setWindowTitle(f"📋 环境/配置文件 — {Path(project_root).name}")
        self.resize(620, 440)

        self._scan_worker: _ScanWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        hint = QLabel(
            "扫描项目根下的环境/配置文件（.env* / application*.yml/properties / bootstrap* / *.env）。"
            "双击或选中后点「打开」走内置预览，预览窗里可切换外部编辑器。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{FG_SECONDARY};")
        root.addWidget(hint)

        self.lbl_status = QLabel("扫描中...")
        self.lbl_status.setStyleSheet(f"color:{FG_SECONDARY};")
        root.addWidget(self.lbl_status)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._on_open)
        root.addWidget(self.list, 1)

        bottom = QHBoxLayout()
        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_refresh.clicked.connect(self._start_scan)
        bottom.addWidget(self.btn_refresh)
        bottom.addStretch(1)
        self.btn_open = QPushButton("打开")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._on_open)
        bottom.addWidget(self.btn_open)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        self.list.itemSelectionChanged.connect(
            lambda: self.btn_open.setEnabled(bool(self.list.currentItem()))
        )

        QShortcut(QKeySequence("Escape"), self, activated=self.close)

        self._start_scan()

    def _start_scan(self) -> None:
        if self._scan_worker and self._scan_worker.isRunning():
            return
        self.btn_refresh.setEnabled(False)
        self.lbl_status.setText("扫描中...")
        self.list.clear()
        worker = _ScanWorker(self.project_root, QApplication.instance())
        self._scan_worker = worker
        worker.done.connect(self._on_scan_done)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_scan_done(self, files: list[str]) -> None:
        if self.sender() is not self._scan_worker:
            return
        self._scan_worker = None
        self.btn_refresh.setEnabled(True)
        if not files:
            self.lbl_status.setText("未找到环境/配置文件")
            return
        self.lbl_status.setText(f"共找到 {len(files)} 个文件")
        root = Path(self.project_root)
        for abs_path in files:
            try:
                rel = str(Path(abs_path).relative_to(root))
            except ValueError:
                rel = abs_path
            item = QListWidgetItem(rel)
            item.setToolTip(abs_path)
            item.setData(Qt.ItemDataRole.UserRole, abs_path)
            self.list.addItem(item)
        self.list.setCurrentRow(0)

    def _on_open(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.openFileRequested.emit(path)
