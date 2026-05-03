"""空状态引导页：第一次打开或没有任何 Tab 时展示"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.config import AppConfig
from src.ui.theme import (
    BG_L2, BORDER_STRONG, FG_PRIMARY, FG_SECONDARY,
    FONT_PT_UI, FONT_PT_UI_SM, RADIUS_MD,
)


class EmptyState(QWidget):
    projectRequested = Signal(str)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setAcceptDrops(True)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.setSpacing(18)

        title = QLabel("mini-ide")
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size: 22pt; font-weight: 300; color: {FG_PRIMARY};")
        root.addWidget(title)

        subtitle = QLabel("AI 时代的开发指挥台")
        subtitle.setProperty("role", "subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"font-size: {FONT_PT_UI}pt; color: {FG_SECONDARY};")
        root.addWidget(subtitle)

        drop_hint = QFrame()
        drop_hint.setObjectName("dropZone")
        drop_hint.setStyleSheet(
            f"QFrame#dropZone {{ border: 1px dashed {BORDER_STRONG};"
            f" border-radius: {RADIUS_MD}px; background: {BG_L2}; }}"
        )
        drop_layout = QVBoxLayout(drop_hint)
        drop_layout.setContentsMargins(40, 30, 40, 30)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        tip = QLabel("拖拽项目文件夹到此处，或")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setStyleSheet(f"color: {FG_SECONDARY}; font-size: {FONT_PT_UI}pt;")
        drop_layout.addWidget(tip)

        open_btn = QPushButton("选择项目目录")
        open_btn.setProperty("role", "primary")
        open_btn.setFixedWidth(180)
        open_btn.clicked.connect(self._choose_dir)
        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_row.addWidget(open_btn)
        drop_layout.addLayout(btn_row)

        root.addWidget(drop_hint)

        if self.config.recent_projects:
            recent_label = QLabel("最近打开")
            recent_label.setProperty("role", "subtitle")
            recent_label.setStyleSheet(
                f"color: {FG_SECONDARY}; font-size: {FONT_PT_UI}pt; margin-top: 12px;"
            )
            root.addWidget(recent_label)

            lst = QListWidget()
            lst.setFrameShape(QFrame.Shape.NoFrame)
            lst.setStyleSheet("QListWidget { background: transparent; } QListWidget::item { padding: 6px; }")
            lst.setFixedWidth(500)
            lst.setMaximumHeight(240)
            for entry in self.config.recent_projects[:8]:
                text = f"  {entry.name or Path(entry.path).name}    {entry.project_type}\n  {entry.path}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, entry.path)
                lst.addItem(item)
            lst.itemActivated.connect(self._on_recent_clicked)
            lst.itemClicked.connect(self._on_recent_clicked)
            row = QHBoxLayout()
            row.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(lst)
            root.addLayout(row)

    def _choose_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择项目目录", self._default_dir()
        )
        if path:
            self.projectRequested.emit(path)

    def _default_dir(self) -> str:
        if self.config.recent_projects:
            parent = str(Path(self.config.recent_projects[0].path).parent)
            if Path(parent).is_dir():
                return parent
        if self.config.default_project_dir and Path(self.config.default_project_dir).is_dir():
            return self.config.default_project_dir
        return str(Path.home())

    def _on_recent_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).exists():
            self.projectRequested.emit(path)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path and Path(path).is_dir():
                self.projectRequested.emit(path)
                break
