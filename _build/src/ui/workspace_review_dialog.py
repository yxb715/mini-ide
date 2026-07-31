"""开发工作区级 Git Review 汇总。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QPlainTextEdit, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from PySide6.QtCore import Qt

from src.ui.theme import GAP_LG, GAP_MD, H_DIALOG_TALL, H_TABLE_ROW, W_DIALOG_WIDE


class WorkspaceReviewDialog(QDialog):
    def __init__(self, workspace, components, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Code Review - {workspace.name}")
        self.resize(W_DIALOG_WIDE, H_DIALOG_TALL)
        root = QVBoxLayout(self)
        root.setContentsMargins(GAP_LG, GAP_LG, GAP_LG, GAP_LG)
        root.setSpacing(GAP_MD)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(len(components), 8)
        self.table.setHorizontalHeaderLabels([
            "项目", "基准分支", "任务分支", "改动", "提交", "已推送", "已合并", "状态",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(H_TABLE_ROW)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self._components = list(components)
        for row, item in enumerate(components):
            values = (
                item.id, item.base_branch, item.task_branch, str(item.changed_files),
                str(len(item.commits)), "是" if item.pushed else "否",
                "是" if item.merged else "否", item.error or "可评审",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        splitter.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        if components:
            self.table.selectRow(0)

    def _show_detail(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self._components[rows[0].row()]
        commits = "\n".join(item.commits) or "(无任务提交)"
        stat = item.diff_stat or "(无提交 diff 摘要)"
        self.detail.setPlainText(
            f"项目：{item.id}\n"
            f"基准：{item.base_branch} @ {item.base_commit}\n"
            f"任务分支：{item.task_branch}\n"
            f"已推送：{'是' if item.pushed else '否'}\n"
            f"已合并：{'是' if item.merged else '否'}\n"
            f"编译结果：{item.compile_result}\n"
            f"健康检查：{item.health_result}\n"
            f"测试结果：{item.test_result}\n\n"
            f"提交：\n{commits}\n\nDiff 摘要：\n{stat}"
        )
