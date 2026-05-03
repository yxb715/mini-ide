"""项目信息面板（只读）

历史上这里有 JVM 参数 / Spring Profile / 自定义参数 / 环境变量 编辑入口，
用户反馈这些字段不直观、本地用什么环境都在代码里配置，所以全部移除。
现在只展示一些只读元信息，方便用户确认 mini-ide 识别出的项目类型与路径。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from src.core.config import ProjectEntry
from src.core.project_detector import ProjectMeta
from src.ui.theme import FG_SECONDARY


class SettingsPanel(QWidget):
    """右侧可折叠的项目信息面板（只读）"""

    def __init__(self, meta: ProjectMeta, entry: ProjectEntry, parent=None):
        super().__init__(parent)
        self.meta = meta
        self.entry = entry
        self.setMinimumWidth(260)
        self.setMaximumWidth(360)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        info = QGroupBox("项目信息")
        fi = QFormLayout(info)
        fi.addRow("路径:", _readonly_label(meta.path))
        fi.addRow("类型:", _readonly_label(f"{meta.icon}  {meta.display_type}"))
        fi.addRow("包管理:", _readonly_label(meta.package_manager or "-"))
        if meta.main_class:
            fi.addRow("主类:", _readonly_label(meta.main_class))
        lay.addWidget(info)

        lay.addStretch(1)

        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)


def _readonly_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setStyleSheet(f"color: {FG_SECONDARY};")
    return lbl
