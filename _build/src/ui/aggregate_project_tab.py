"""聚合目录工作台：一个顶层 Tab 管理自己的项目和需求工作区。"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QSplitter, QStackedWidget,
    QStyle, QTabWidget, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from src.core.aggregate_workspace import (
    AggregateProject, DevelopmentWorkspace, save_aggregate_project,
)
from src.core.aggregate_workspace_manager import build_workspace_dashboard_snapshot
from src.core.config import AppConfig, ProjectEntry
from src.core.development_workspace_service import (
    create_development_workspace, delete_development_workspace,
    inspect_workspace_delete, merge_development_workspace,
    sync_development_workspace,
)
from src.core.git_ops import current_branch
from src.core.path_utils import normalized_path_key
from src.core.project_detector import detect_project
from src.core.service_state import (
    STATE_RUNNING_EXTERNAL, STATE_STARTING, STATE_STOPPING, STATE_UNKNOWN,
)
from src.core.tool_launchers import (
    CREATE_NO_WINDOW, open_in_cc_args, open_in_codex_args,
)
from src.ui.aggregate_project_dialog import AggregateProjectDialog
from src.ui.development_workspace_dialog import DevelopmentWorkspaceDialog
from src.ui.project_tab import ProjectTab
from src.ui.theme import (
    COLOR_ERROR, COLOR_SUCCESS, COLOR_WARN, FG_DIM, GAP_LG, GAP_MD, GAP_NONE,
    GAP_SM, H_TABLE_ROW,
)

action_log = logging.getLogger("mini-ide.action")


class _PrepareProjectsWorker(QThread):
    done = Signal(object)

    def __init__(self, items: tuple[dict, ...], parent=None):
        super().__init__(parent)
        self.items = items

    def run(self) -> None:
        results = []
        for item in self.items:
            try:
                meta = detect_project(item["path"])
                branch = item.get("branch", "")
                if not branch and not item.get("shared"):
                    branch = current_branch(item["path"])
                results.append({**item, "meta": meta, "branch": branch, "error": ""})
            except Exception as exc:
                results.append({**item, "meta": None, "branch": "", "error": str(exc)})
        self.done.emit(results)


class _SaveProjectWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, project: AggregateProject, parent=None):
        super().__init__(parent)
        self.project = project

    def run(self) -> None:
        try:
            self.done.emit(True, str(save_aggregate_project(self.project)))
        except Exception as exc:
            self.done.emit(False, str(exc))


class _WorkspaceSnapshotWorker(QThread):
    done = Signal(object, str)

    def __init__(self, aggregate_path: str, parent=None):
        super().__init__(parent)
        self.aggregate_path = aggregate_path

    def run(self) -> None:
        try:
            snapshot = build_workspace_dashboard_snapshot((self.aggregate_path,))
            self.done.emit(snapshot.development_workspaces, "")
        except Exception as exc:
            self.done.emit((), str(exc))


class _CreateWorkspaceWorker(QThread):
    done = Signal(object)

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = plan

    def run(self) -> None:
        self.done.emit(create_development_workspace(self.plan))


class _MergeWorkspaceWorker(QThread):
    done = Signal(object, str)

    def __init__(self, workspace: DevelopmentWorkspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace

    def run(self) -> None:
        try:
            self.done.emit(merge_development_workspace(self.workspace), "")
        except Exception as exc:
            self.done.emit(None, str(exc))


class _SyncWorkspaceWorker(QThread):
    done = Signal(object, str)

    def __init__(
        self,
        workspace: DevelopmentWorkspace,
        *,
        fetch_remote: bool,
        keep_conflicts: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.workspace = workspace
        self.fetch_remote = fetch_remote
        self.keep_conflicts = keep_conflicts

    def run(self) -> None:
        try:
            self.done.emit(sync_development_workspace(
                self.workspace,
                fetch_remote=self.fetch_remote,
                keep_conflicts=self.keep_conflicts,
            ), "")
        except Exception as exc:
            self.done.emit(None, str(exc))


class _DeletePreflightWorker(QThread):
    done = Signal(object, str)

    def __init__(
        self, workspace: DevelopmentWorkspace, running_paths: tuple[str, ...], parent=None,
    ):
        super().__init__(parent)
        self.workspace = workspace
        self.running_paths = running_paths

    def run(self) -> None:
        try:
            self.done.emit(
                inspect_workspace_delete(self.workspace, self.running_paths), "",
            )
        except Exception as exc:
            self.done.emit(None, str(exc))


class _DeleteWorkspaceWorker(QThread):
    done = Signal(bool, object, str)

    def __init__(self, plan, confirm_unmerged: bool, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.confirm_unmerged = confirm_unmerged

    def run(self) -> None:
        ok, kept, error = delete_development_workspace(
            self.plan, confirm_unmerged_backed_up=self.confirm_unmerged,
        )
        self.done.emit(ok, kept, error)


class AggregateProjectTab(QWidget):
    """聚合目录、直属项目和该目录需求工作区的唯一顶层工作台。"""

    def __init__(
        self,
        project: AggregateProject,
        config: AppConfig,
        *,
        workspace: DevelopmentWorkspace | None = None,
        environment_guard=None,
        existing_tabs: dict[str, ProjectTab] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.aggregate_project = project
        self.workspace = workspace
        self.config = config
        self.environment_guard = environment_guard
        self.path = project.root_path
        self.stable_id = project.id
        self.aggregate_root_path = project.root_path
        self.project_meta = SimpleNamespace(
            path=project.root_path,
            name=project.name,
            project_type="aggregate",
            display_type="聚合目录",
            package_manager="",
            default_port=None,
            health_check_path="",
        )
        self._project_tabs: dict[str, ProjectTab] = {}
        self._project_rows: dict[str, int] = {}
        self._metadata: dict[str, dict] = {}
        self._existing_tabs = existing_tabs or {}
        self._workspace_summaries = ()
        self._pending_workspace_path = ""
        self._prepare_worker: _PrepareProjectsWorker | None = None
        self._save_worker: _SaveProjectWorker | None = None
        self._workspace_worker: _WorkspaceSnapshotWorker | None = None
        self._create_worker: _CreateWorkspaceWorker | None = None
        self._merge_worker: _MergeWorkspaceWorker | None = None
        self._sync_worker: _SyncWorkspaceWorker | None = None
        self._pending_sync_fetch = False
        self._delete_preflight_worker: _DeletePreflightWorker | None = None
        self._delete_worker: _DeleteWorkspaceWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(GAP_LG, GAP_LG, GAP_LG, GAP_LG)
        root.setSpacing(GAP_MD)

        header = QHBoxLayout()
        header.setSpacing(GAP_SM)
        self.title_label = QLabel(project.name)
        self.title_label.setProperty("role", "title")
        header.addWidget(self.title_label)
        self.context_label = QLabel()
        self.context_label.setProperty("role", "subtitle")
        header.addWidget(self.context_label)
        self.exit_workspace_button = QPushButton("退出工作区")
        self.exit_workspace_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack)
        )
        self.exit_workspace_button.setToolTip("退出当前需求工作区，返回聚合目录")
        self.exit_workspace_button.clicked.connect(
            lambda: self.activate_workspace(None)
        )
        header.addWidget(self.exit_workspace_button)
        header.addStretch(1)
        self.edit_button = QPushButton("编辑项目列表")
        self.edit_button.clicked.connect(self.edit_project_definition)
        header.addWidget(self.edit_button)
        root.addLayout(header)

        self.view_tabs = QTabWidget()
        self.projects_page = self._build_projects_page()
        self.workspaces_page = self._build_workspaces_page()
        self.view_tabs.addTab(self.projects_page, "项目")
        self.view_tabs.addTab(self.workspaces_page, "需求工作区")
        root.addWidget(self.view_tabs, 1)

        self._set_context_label()
        self._start_prepare()
        self.refresh_workspaces()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh_project_states)
        self._refresh_timer.start()

    def _build_projects_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(GAP_NONE, GAP_MD, GAP_NONE, GAP_NONE)
        layout.setSpacing(GAP_MD)
        self.project_progress = QLabel("正在识别项目...")
        self.project_progress.setProperty("role", "subtitle")
        progress_row = QHBoxLayout()
        progress_row.setSpacing(GAP_SM)
        progress_row.addWidget(self.project_progress, 1)
        self.project_codex_button = QPushButton("codex")
        self.project_codex_button.setToolTip("在 AgentDesk 中打开当前代码环境")
        self.project_codex_button.clicked.connect(self._open_current_in_codex)
        progress_row.addWidget(self.project_codex_button)
        self.project_cc_button = QPushButton("cc")
        self.project_cc_button.setToolTip("在 AgentDesk 中打开当前代码环境")
        self.project_cc_button.clicked.connect(self._open_current_in_cc)
        progress_row.addWidget(self.project_cc_button)
        layout.addLayout(progress_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        self.project_table = QTableWidget(0, 6)
        self.project_table.setHorizontalHeaderLabels(
            ["项目", "类型", "分支/来源", "状态", "端口", "操作"]
        )
        self.project_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.project_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.project_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.project_table.verticalHeader().setVisible(False)
        self.project_table.verticalHeader().setDefaultSectionSize(H_TABLE_ROW)
        header = self.project_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.project_table.itemSelectionChanged.connect(self._show_selected_project)
        splitter.addWidget(self.project_table)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(GAP_NONE, GAP_NONE, GAP_NONE, GAP_NONE)
        detail_layout.setSpacing(GAP_NONE)
        self.detail_stack = QStackedWidget()
        self.placeholder = QLabel(
            "项目加载完成后，在上方选择项目查看日志、文件和 Git。"
        )
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet(f"color: {FG_DIM};")
        self.detail_stack.addWidget(self.placeholder)
        detail_layout.addWidget(self.detail_stack)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        return page

    def _build_workspaces_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(GAP_NONE, GAP_MD, GAP_NONE, GAP_NONE)
        layout.setSpacing(GAP_MD)

        actions = QHBoxLayout()
        actions.setSpacing(GAP_SM)
        self.new_workspace_button = QPushButton("新建需求工作区")
        self.new_workspace_button.clicked.connect(self._create_workspace)
        actions.addWidget(self.new_workspace_button)
        self.codex_button = QPushButton("codex")
        self.codex_button.setToolTip("在 AgentDesk 中打开选中的需求工作区")
        self.codex_button.clicked.connect(self._open_selected_in_codex)
        actions.addWidget(self.codex_button)
        self.cc_button = QPushButton("cc")
        self.cc_button.setToolTip("在 AgentDesk 中打开选中的需求工作区")
        self.cc_button.clicked.connect(self._open_selected_in_cc)
        actions.addWidget(self.cc_button)
        self.sync_button = QPushButton("同步源分支")
        self.sync_button.setToolTip(
            "把源目录基准分支的新提交合并进当前任务分支，只改工作区，不动源目录"
        )
        self.sync_button.clicked.connect(self._sync_selected_workspace)
        actions.addWidget(self.sync_button)
        self.merge_button = QPushButton("合并代码")
        self.merge_button.clicked.connect(self._merge_selected_workspace)
        actions.addWidget(self.merge_button)
        self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self._delete_selected_workspace)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        self.refresh_workspace_button = QToolButton()
        self.refresh_workspace_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.refresh_workspace_button.setToolTip("刷新需求工作区")
        self.refresh_workspace_button.clicked.connect(self.refresh_workspaces)
        actions.addWidget(self.refresh_workspace_button)
        layout.addLayout(actions)

        self.workspace_status = QLabel("正在读取需求工作区...")
        self.workspace_status.setProperty("role", "subtitle")
        layout.addWidget(self.workspace_status)
        self.workspace_table = QTableWidget(0, 7)
        self.workspace_table.setHorizontalHeaderLabels(
            ["需求", "修改项目", "任务分支", "改动", "落后", "状态", "创建时间"]
        )
        self.workspace_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.workspace_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.workspace_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.workspace_table.verticalHeader().setVisible(False)
        self.workspace_table.verticalHeader().setDefaultSectionSize(H_TABLE_ROW)
        header = self.workspace_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.workspace_table.itemSelectionChanged.connect(self._update_workspace_buttons)
        self.workspace_table.itemDoubleClicked.connect(
            lambda _item: self._activate_selected_workspace()
        )
        layout.addWidget(self.workspace_table, 1)
        self._update_workspace_buttons()
        return page

    @property
    def component_tabs(self) -> dict[str, ProjectTab]:
        """兼容现有 CLI 的命名；界面统一称为“项目”。"""
        return dict(self._project_tabs)

    def _project_items(self) -> tuple[dict, ...]:
        if self.workspace is not None:
            workspace_map = {item.id.casefold(): item for item in self.workspace.components}
            items = []
            for definition in self.aggregate_project.components:
                workspace_item = workspace_map.get(definition.id.casefold())
                if workspace_item is None:
                    continue
                is_worktree = workspace_item.mode == "worktree"
                items.append({
                    "id": definition.id,
                    "name": definition.name or definition.id,
                    "path": (
                        workspace_item.worktree_path
                        or workspace_item.source_repository_path
                    ),
                    "shared": not is_worktree,
                    "branch": workspace_item.task_branch if is_worktree else "源目录",
                })
            return tuple(items)
        return tuple({
            "id": item.id,
            "name": item.name or item.id,
            "path": str(item.absolute_path(self.aggregate_project.root_path)),
            "shared": item.shared,
            "branch": "共享目录" if item.shared else "",
        } for item in self.aggregate_project.components)

    def _start_prepare(self) -> None:
        self.project_progress.setText("正在识别项目...")
        pending = []
        for item in self._project_items():
            existing = self._existing_tabs.get(normalized_path_key(item["path"]))
            if existing is not None:
                self._install_project({
                    **item,
                    "meta": existing.project_meta,
                    "branch": item.get("branch") or "Git 仓库",
                    "error": "",
                }, existing)
            else:
                pending.append(item)
        self._existing_tabs = {}
        if not pending:
            self._projects_prepared([])
            return
        worker = _PrepareProjectsWorker(tuple(pending), QApplication.instance())
        self._prepare_worker = worker
        worker.done.connect(self._projects_prepared)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _projects_prepared(self, results: list[dict]) -> None:
        if self._prepare_worker is not None and self.sender() is self._prepare_worker:
            self._prepare_worker = None
        for item in results:
            self._install_project(item)
        ready = len(self._project_tabs)
        errors = sum(1 for item in self._metadata.values() if item.get("error"))
        text = f"{ready} 个项目可用"
        if errors:
            text += f"，{errors} 个错误"
        self.project_progress.setText(text)
        if self.project_table.rowCount() and not self.project_table.selectionModel().hasSelection():
            self.project_table.selectRow(0)
        self.refresh_project_states()

    def _install_project(self, item: dict, existing: ProjectTab | None = None) -> None:
        project_id = item["id"]
        self._metadata[project_id] = item
        row = self.project_table.rowCount()
        self.project_table.insertRow(row)
        self._project_rows[project_id] = row
        meta = item.get("meta")
        values = [
            item.get("name") or project_id,
            meta.display_type if meta else "识别失败",
            item.get("branch") or ("共享目录" if item.get("shared") else "-"),
            "配置错误" if item.get("error") else "已停止",
            str(meta.default_port) if meta and meta.default_port else "-",
        ]
        for column, value in enumerate(values):
            table_item = QTableWidgetItem(value)
            if item.get("error"):
                table_item.setToolTip(item["error"])
            if column == 0:
                table_item.setData(Qt.ItemDataRole.UserRole, project_id)
            self.project_table.setItem(row, column, table_item)
        if meta is None:
            return
        tab = existing
        if tab is None:
            entry = self.config.find_project(item["path"]) or ProjectEntry(path=item["path"])
            entry.name = meta.name
            entry.project_type = meta.project_type
            tab = ProjectTab(meta, entry, self.config)
            tab.path = item["path"]
        tab.component_id = project_id
        self._project_tabs[project_id] = tab
        self.detail_stack.addWidget(tab)

        action_widget = QWidget()
        actions = QHBoxLayout(action_widget)
        actions.setContentsMargins(GAP_NONE, GAP_NONE, GAP_NONE, GAP_NONE)
        actions.setSpacing(GAP_SM)
        start = QToolButton()
        start.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        start.setToolTip(f"启动 {values[0]}")
        start.clicked.connect(lambda _checked=False, pid=project_id: self.start_component(pid))
        actions.addWidget(start)
        stop = QToolButton()
        stop.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        stop.setToolTip(f"停止 {values[0]}")
        stop.clicked.connect(lambda _checked=False, pid=project_id: self.stop_component(pid))
        actions.addWidget(stop)
        self.project_table.setCellWidget(row, 5, action_widget)

    def _show_selected_project(self) -> None:
        rows = self.project_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.project_table.item(rows[0].row(), 0)
        project_id = item.data(Qt.ItemDataRole.UserRole) if item else ""
        tab = self._project_tabs.get(project_id)
        if tab is not None:
            self.detail_stack.setCurrentWidget(tab)

    def show_projects(self) -> None:
        self.view_tabs.setCurrentWidget(self.projects_page)

    def show_workspaces(self) -> None:
        self.view_tabs.setCurrentWidget(self.workspaces_page)

    def _set_context_label(self) -> None:
        if self.workspace is None:
            self.context_label.setText(f"源目录：{self.aggregate_project.root_path}")
        else:
            self.context_label.setText(f"当前需求：{self.workspace.name}")
        self.exit_workspace_button.setVisible(self.workspace is not None)

    def activate_workspace(
        self, workspace: DevelopmentWorkspace | None, *, interactive: bool = True,
    ) -> bool:
        old_path = self.workspace.root_path if self.workspace else ""
        new_path = workspace.root_path if workspace else ""
        if old_path and new_path and normalized_path_key(old_path) == normalized_path_key(new_path):
            self.show_projects()
            return True
        if not old_path and not new_path:
            return True

        running = self.running_service_items(refresh_external=True)
        if running:
            if not interactive:
                return False
            answer = QMessageBox.question(
                self,
                "切换代码目录",
                "当前仍有项目在运行。切换需求工作区前需要停止这些项目，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        for tab in self._project_tabs.values():
            if not tab.request_close(
                confirm_running=False, stop_running=True, quiet=not interactive,
            ):
                return False
        if running and not self.wait_services_stopped(20000):
            QMessageBox.warning(self, "切换失败", "当前项目未能完全停止。")
            return False

        for tab in self._project_tabs.values():
            self.detail_stack.removeWidget(tab)
            tab.deleteLater()
        self._project_tabs.clear()
        self._project_rows.clear()
        self._metadata.clear()
        self.project_table.setRowCount(0)
        self.detail_stack.setCurrentWidget(self.placeholder)
        self.workspace = workspace
        self._set_context_label()
        self._start_prepare()
        self._update_workspace_buttons()
        self.show_projects()
        return True

    def _selected_workspace_summary(self):
        rows = self.workspace_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.workspace_table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _workspace_busy(self) -> bool:
        return bool(
            self._workspace_worker or self._create_worker or self._merge_worker
            or self._sync_worker or self._delete_preflight_worker
            or self._delete_worker
        )

    def _update_workspace_buttons(self) -> None:
        summary = self._selected_workspace_summary() if hasattr(self, "workspace_table") else None
        valid = bool(summary and summary.workspace is not None)
        busy = self._workspace_busy()
        for button in (
            self.codex_button, self.cc_button, self.sync_button,
            self.merge_button, self.delete_button,
        ):
            button.setEnabled(valid and not busy)
        self.new_workspace_button.setEnabled(not busy)
        self.refresh_workspace_button.setEnabled(not busy)
        self.exit_workspace_button.setEnabled(self.workspace is not None and not busy)

    def refresh_workspaces(self) -> None:
        if self._workspace_worker:
            return
        self.workspace_status.setText("正在读取需求工作区...")
        worker = _WorkspaceSnapshotWorker(
            self.aggregate_project.root_path, QApplication.instance(),
        )
        self._workspace_worker = worker
        self._update_workspace_buttons()
        worker.done.connect(self._workspaces_ready)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _workspaces_ready(self, summaries, error: str) -> None:
        if self.sender() is not self._workspace_worker:
            return
        self._workspace_worker = None
        self._workspace_summaries = tuple(summaries)
        self.workspace_table.clearContents()
        self.workspace_table.setRowCount(len(summaries))
        status_labels = {
            "created": "已创建",
            "active": "开发中",
            "reviewing": "评审中",
            "merged": "已合并",
            "invalid": "配置错误",
        }
        selected_row = -1
        for row, summary in enumerate(summaries):
            values = [
                summary.name,
                ", ".join(summary.component_ids) or "-",
                ", ".join(summary.task_branches) or "-",
                str(summary.git_change_count),
                str(summary.behind_count),
                status_labels.get(summary.status, summary.status),
                summary.created_at,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if summary.error:
                    item.setToolTip(summary.error)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, summary)
                if column == 3 and summary.git_change_count:
                    item.setForeground(QColor(COLOR_WARN))
                elif column == 4 and summary.behind_count:
                    item.setForeground(QColor(COLOR_WARN))
                    item.setToolTip(
                        f"任务分支落后源目录基准分支 {summary.behind_count} 个提交，"
                        "建议先同步源分支"
                    )
                elif column == 5 and summary.error:
                    item.setForeground(QColor(COLOR_ERROR))
                self.workspace_table.setItem(row, column, item)
            if self._pending_workspace_path and normalized_path_key(summary.root_path) == normalized_path_key(
                self._pending_workspace_path
            ):
                selected_row = row
        self._pending_workspace_path = ""
        if error:
            self.workspace_status.setText(f"读取失败：{error}")
        else:
            self.workspace_status.setText(f"{len(summaries)} 个需求工作区")
        if selected_row >= 0:
            self.workspace_table.selectRow(selected_row)
        elif summaries and not self.workspace_table.selectionModel().hasSelection():
            self.workspace_table.selectRow(0)
        self._update_workspace_buttons()

    def _activate_selected_workspace(self) -> None:
        summary = self._selected_workspace_summary()
        if summary and summary.workspace is not None:
            action_log.info("[GUI] 进入工作区 aggregate=%s workspace=%s",
                            self.aggregate_project.name, summary.name)
            self.activate_workspace(summary.workspace)

    def _create_workspace(self) -> None:
        if self._workspace_busy():
            return
        dialog = DevelopmentWorkspaceDialog(self.aggregate_project, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        plan = dialog.result_plan()
        if plan is None:
            return
        self.workspace_status.setText("正在创建所选项目的 Worktree...")
        worker = _CreateWorkspaceWorker(plan, QApplication.instance())
        self._create_worker = worker
        self._update_workspace_buttons()
        worker.done.connect(self._workspace_created)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _workspace_created(self, result) -> None:
        if self.sender() is not self._create_worker:
            return
        self._create_worker = None
        if not result.ok or result.workspace is None:
            action_log.warning("[GUI] 创建工作区失败 error=%s", result.error)
            details = [result.error]
            if result.created_components:
                details.append("已创建：" + ", ".join(result.created_components))
            if result.rolled_back_components:
                details.append("已安全回滚：" + ", ".join(result.rolled_back_components))
            if result.pending_components:
                details.append("未处理：" + ", ".join(result.pending_components))
            QMessageBox.warning(self, "创建需求工作区失败", "\n".join(details))
        else:
            action_log.info("[GUI] 创建工作区成功 workspace=%s", result.workspace.root_path)
            self._pending_workspace_path = result.workspace.root_path
            self.workspace_status.setText("需求工作区已创建。")
        self._update_workspace_buttons()
        self.refresh_workspaces()

    def _current_environment_root(self) -> Path:
        return Path(
            self.workspace.root_path
            if self.workspace is not None
            else self.aggregate_project.root_path
        )

    def _open_external_tool(self, target: Path, command: list[str]) -> None:
        if not command:
            QMessageBox.warning(
                self, "启动 AgentDesk 失败",
                "没有找到 AgentDesk，请先安装或配置 AGENTDESK_EXE。",
            )
            return
        try:
            subprocess.Popen(
                command, cwd=str(target), creationflags=CREATE_NO_WINDOW, close_fds=True,
            )
        except OSError as exc:
            QMessageBox.warning(self, "启动 AgentDesk 失败", str(exc))

    def _open_current_in_codex(self) -> None:
        target = self._current_environment_root()
        self._open_external_tool(target, open_in_codex_args(target))

    def _open_current_in_cc(self) -> None:
        target = self._current_environment_root()
        self._open_external_tool(target, open_in_cc_args(target))

    def _open_selected_in_codex(self) -> None:
        summary = self._selected_workspace_summary()
        if summary and summary.workspace is not None:
            target = Path(summary.workspace.root_path)
            self._open_external_tool(target, open_in_codex_args(target))

    def _open_selected_in_cc(self) -> None:
        summary = self._selected_workspace_summary()
        if summary and summary.workspace is not None:
            target = Path(summary.workspace.root_path)
            self._open_external_tool(target, open_in_cc_args(target))

    def _sync_selected_workspace(self) -> None:
        summary = self._selected_workspace_summary()
        if not summary or summary.workspace is None or self._workspace_busy():
            return
        editable = [
            item for item in summary.workspace.components if item.mode == "worktree"
        ]
        lines = ["将把源目录基准分支的新提交合并进任务分支："]
        lines.extend(
            f"- {item.id}: {item.base_branch} -> {item.task_branch}"
            for item in editable
        )
        lines.append(
            "\n只改工作区，不动源目录；工作区必须无未提交改动。确认继续？"
        )
        box = QMessageBox(self)
        box.setWindowTitle("确认同步源分支")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("\n".join(lines))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        fetch_box = QCheckBox("先从远端拉取源分支更新")
        fetch_box.setToolTip("对配置了上游的项目先 git fetch，再用较新的一端同步")
        box.setCheckBox(fetch_box)
        box.exec()
        if box.standardButton(box.clickedButton()) != QMessageBox.StandardButton.Yes:
            return
        self._start_workspace_sync(
            summary.workspace, fetch_remote=fetch_box.isChecked(), keep_conflicts=False,
        )

    def _start_workspace_sync(
        self,
        workspace: DevelopmentWorkspace,
        *,
        fetch_remote: bool,
        keep_conflicts: bool,
    ) -> None:
        action_log.info(
            "[GUI] 同步源分支 aggregate=%s workspace=%s fetch_remote=%s keep_conflicts=%s",
            self.aggregate_project.name, workspace.name, fetch_remote, keep_conflicts,
        )
        self.workspace_status.setText("正在把源分支同步到任务分支...")
        self._pending_sync_fetch = fetch_remote
        worker = _SyncWorkspaceWorker(
            workspace,
            fetch_remote=fetch_remote,
            keep_conflicts=keep_conflicts,
            parent=QApplication.instance(),
        )
        self._sync_worker = worker
        self._update_workspace_buttons()
        worker.done.connect(self._workspace_sync_ready)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    @staticmethod
    def _conflict_lines(items) -> list[str]:
        lines = []
        for item in items:
            files = "、".join(item.conflict_files[:5])
            if len(item.conflict_files) > 5:
                files += f" 等 {len(item.conflict_files)} 个文件"
            lines.append(f"- {item.id}: {files or '有冲突'}")
        return lines

    def _workspace_sync_ready(self, result, error: str) -> None:
        if self.sender() is not self._sync_worker:
            return
        self._sync_worker = None
        self._update_workspace_buttons()
        if error or result is None:
            message = error or "未知错误"
            action_log.warning("[GUI] 同步源分支失败 error=%s", message)
            QMessageBox.warning(self, "同步失败", message)
            self.refresh_workspaces()
            return

        synced = "、".join(result.synced_ids)
        others = [
            f"- {item.id}: {item.error}"
            for item in result.components
            if item.error and not item.conflicted
        ]
        if result.needs_conflict_confirmation:
            conflicted = [
                item for item in result.components
                if item.conflicted and item.rolled_back
            ]
            lines = ["以下项目同步时有冲突，已回滚到同步前状态："]
            lines.extend(self._conflict_lines(conflicted))
            if synced:
                lines.append(f"\n已成功同步：{synced}")
            if others:
                lines.append("\n其它未同步项目：")
                lines.extend(others)
            lines.append(
                "\n是否重新同步并把冲突保留在工作区，由你或 AI 手工解决？"
                "\n保留后工作区会停在合并中状态，解决冲突并提交后才能合并代码。"
            )
            answer = QMessageBox.question(
                self, "同步存在冲突", "\n".join(lines),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._start_workspace_sync(
                    result.workspace,
                    fetch_remote=self._pending_sync_fetch,
                    keep_conflicts=True,
                )
                return
            action_log.warning(
                "[GUI] 同步源分支存在冲突并已回滚 projects=%s",
                "、".join(item.id for item in conflicted),
            )
            self.workspace_status.setText("同步存在冲突，已回滚。")
            self.refresh_workspaces()
            return

        if not result.ok:
            action_log.warning("[GUI] 同步源分支失败 error=%s", result.error)
            QMessageBox.warning(self, "同步未完成", result.error)
            self.refresh_workspaces()
            return

        kept = [
            item for item in result.components
            if item.conflicted and not item.rolled_back
        ]
        current = [item.id for item in result.components if item.already_current]
        blocks = []
        if synced:
            blocks.append(f"已同步：{synced}")
        if kept:
            blocks.append(
                "冲突已保留在工作区，需手工解决后提交：\n"
                + "\n".join(self._conflict_lines(kept))
            )
        if current:
            blocks.append("已是最新：" + "、".join(current))
        action_log.info(
            "[GUI] 同步源分支成功 synced=%s kept_conflicts=%s",
            synced or "无", "、".join(item.id for item in kept) or "无",
        )
        QMessageBox.information(
            self, "同步完成", "\n\n".join(blocks) or "没有需要同步的项目。",
        )
        self.refresh_workspaces()

    def _merge_selected_workspace(self) -> None:
        summary = self._selected_workspace_summary()
        if not summary or summary.workspace is None or self._workspace_busy():
            return
        editable = [
            item for item in summary.workspace.components if item.mode == "worktree"
        ]
        lines = ["将把以下任务分支快进合并到基准分支："]
        lines.extend(
            f"- {item.id}: {item.task_branch} -> {item.base_branch}"
            for item in editable
        )
        lines.append("\n源目录和工作区必须无未提交改动，分支分叉时不会自动合并。确认继续？")
        answer = QMessageBox.question(
            self,
            "确认合并代码",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.workspace_status.setText("正在检查并合并代码...")
        worker = _MergeWorkspaceWorker(summary.workspace, QApplication.instance())
        self._merge_worker = worker
        self._update_workspace_buttons()
        worker.done.connect(self._workspace_merge_ready)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _workspace_merge_ready(self, result, error: str) -> None:
        if self.sender() is not self._merge_worker:
            return
        self._merge_worker = None
        self._update_workspace_buttons()
        if error or result is None or not result.ok:
            message = error or (result.error if result is not None else "未知错误")
            action_log.warning("[GUI] 合并工作区失败 error=%s", message)
            QMessageBox.warning(self, "合并失败", message)
        else:
            projects = "、".join(item.id for item in result.components if item.merged)
            action_log.info("[GUI] 合并工作区成功 projects=%s", projects)
            QMessageBox.information(self, "合并完成", f"已合并：{projects}")
        self.refresh_workspaces()

    def _running_project_paths(self) -> tuple[str, ...]:
        paths = []
        for tab in self._project_tabs.values():
            if tab.running_service_items(refresh_external=True):
                paths.append(tab.project_meta.path)
        return tuple(paths)

    def _delete_selected_workspace(self) -> None:
        summary = self._selected_workspace_summary()
        if not summary or summary.workspace is None or self._workspace_busy():
            return
        self.workspace_status.setText("正在执行删除预检...")
        worker = _DeletePreflightWorker(
            summary.workspace, self._running_project_paths(), QApplication.instance(),
        )
        self._delete_preflight_worker = worker
        self._update_workspace_buttons()
        worker.done.connect(self._delete_preflight_ready)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _delete_preflight_ready(self, plan, error: str) -> None:
        if self.sender() is not self._delete_preflight_worker:
            return
        self._delete_preflight_worker = None
        self._update_workspace_buttons()
        if error or plan is None:
            QMessageBox.warning(self, "删除预检失败", error or "未知错误")
            self.refresh_workspaces()
            return
        if plan.blockers:
            details = "\n".join(f"- {item}" for item in plan.blockers)
            if plan.unknown_paths:
                details += "\n\n未知路径：\n" + "\n".join(plan.unknown_paths)
            QMessageBox.warning(self, "当前不能删除", details)
            self.refresh_workspaces()
            return
        lines = ["将移除以下 Worktree："]
        for item in plan.workspace.components:
            if item.mode == "worktree":
                lines.append(f"- {item.id}: {item.worktree_path}")
        if plan.warnings:
            lines.append("\n任务分支将保留：")
            lines.extend(f"- {item}" for item in plan.warnings)
        lines.append("\n不会强删分支，也不会递归删除未知目录。确认继续？")
        answer = QMessageBox.question(
            self,
            "确认删除需求工作区",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.refresh_workspaces()
            return
        if self.workspace and normalized_path_key(self.workspace.root_path) == normalized_path_key(
            plan.workspace.root_path
        ):
            if not self.activate_workspace(None):
                QMessageBox.warning(self, "删除已取消", "当前工作区未能安全退出。")
                return
        worker = _DeleteWorkspaceWorker(
            plan, plan.needs_unmerged_confirmation, QApplication.instance(),
        )
        self._delete_worker = worker
        self._update_workspace_buttons()
        self.workspace_status.setText("正在移除 Worktree...")
        worker.done.connect(self._workspace_deleted)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _workspace_deleted(self, ok: bool, kept_branches, error: str) -> None:
        if self.sender() is not self._delete_worker:
            return
        self._delete_worker = None
        self._update_workspace_buttons()
        if not ok:
            action_log.warning("[GUI] 删除工作区失败 error=%s", error)
            QMessageBox.warning(self, "删除未完成", error)
        else:
            action_log.info("[GUI] 删除工作区成功 kept_branches=%s", kept_branches)
            message = "需求工作区已删除。"
            if kept_branches:
                message += "\n\n保留分支：" + ", ".join(kept_branches)
            QMessageBox.information(self, "删除完成", message)
        self.refresh_workspaces()

    def edit_project_definition(self) -> None:
        if self._save_worker or self._prepare_worker:
            return
        if self.workspace is not None and not self.activate_workspace(None):
            return
        dialog = AggregateProjectDialog(
            self.aggregate_project,
            title=f"编辑 {self.aggregate_project.name} 的项目列表",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        project = dialog.result_project()
        if project is None:
            return
        self.edit_button.setEnabled(False)
        worker = _SaveProjectWorker(project, QApplication.instance())
        self._save_worker = worker
        worker.done.connect(self._project_definition_saved)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _project_definition_saved(self, ok: bool, result: str) -> None:
        if self.sender() is not self._save_worker:
            return
        project = self._save_worker.project
        self._save_worker = None
        self.edit_button.setEnabled(True)
        if not ok:
            QMessageBox.warning(self, "保存项目列表失败", result)
            return
        self.aggregate_project = project
        self.stable_id = project.id
        self.project_meta.name = project.name
        self.title_label.setText(project.name)
        parent = self.parentWidget()
        if isinstance(parent, QTabWidget):
            index = parent.indexOf(self)
            if index >= 0:
                parent.setTabText(index, project.name)
        entry = self.config.find_project(project.root_path) or ProjectEntry(
            path=project.root_path,
        )
        entry.name = project.name
        entry.project_type = "aggregate"
        self.config.touch_project(entry)
        self.config.register_aggregate_project(project.root_path)
        self.config.save()
        self._rebuild_projects()
        self.refresh_workspaces()

    def _rebuild_projects(self) -> None:
        for tab in self._project_tabs.values():
            tab.request_close(confirm_running=False, stop_running=True, quiet=True)
            self.detail_stack.removeWidget(tab)
            tab.deleteLater()
        self._project_tabs.clear()
        self._project_rows.clear()
        self._metadata.clear()
        self.project_table.setRowCount(0)
        self.detail_stack.setCurrentWidget(self.placeholder)
        self._start_prepare()

    def start_component(self, component_id: str) -> dict:
        tab = self._project_tabs.get(component_id)
        if tab is None:
            return {"ok": False, "error": f"project not available: {component_id}"}
        states = [item for item in tab.service_states(refresh_external=True) if item.kind != "script"]
        if states and all(item.is_running for item in states):
            return {"ok": True, "already_running": True}
        result = tab.service_controller.start()
        self.refresh_project_states()
        return result

    def stop_component(self, component_id: str) -> dict:
        tab = self._project_tabs.get(component_id)
        if tab is None:
            return {"ok": False, "error": f"project not available: {component_id}"}
        if not tab.running_service_items(refresh_external=True):
            return {"ok": True, "already_stopped": True}
        result = tab.service_controller.stop()
        self.refresh_project_states()
        return result

    def refresh_project_states(self) -> None:
        for project_id, tab in self._project_tabs.items():
            row = self._project_rows.get(project_id)
            if row is None:
                continue
            try:
                states = [
                    item for item in tab.service_states(refresh_external=False)
                    if item.kind != "script"
                ]
            except Exception:
                states = []
            active = [item for item in states if item.is_active]
            unknown = [item for item in states if item.state == STATE_UNKNOWN]
            external = [item for item in states if item.state == STATE_RUNNING_EXTERNAL]
            changing = [
                item for item in states if item.state in (STATE_STARTING, STATE_STOPPING)
            ]
            if unknown:
                status, color = "状态不确定", COLOR_ERROR
            elif changing:
                status, color = "处理中", COLOR_WARN
            elif external:
                status, color = "外部运行", COLOR_WARN
            elif active:
                status, color = "运行中", COLOR_SUCCESS
            else:
                status, color = "已停止", FG_DIM
            status_item = self.project_table.item(row, 3)
            if status_item:
                status_item.setText(status)
                status_item.setForeground(QColor(color))
                status_item.setToolTip("\n".join(item.reason for item in states if item.reason))
            ports = sorted({
                item.port or item.expected_port
                for item in states if item.port or item.expected_port
            })
            port_item = self.project_table.item(row, 4)
            if port_item:
                port_item.setText(", ".join(str(item) for item in ports) or "-")

    def service_states(self, refresh_external: bool = True):
        states = []
        for tab in self._project_tabs.values():
            states.extend(tab.service_states(refresh_external=refresh_external))
        return states

    def running_service_items(self, refresh_external: bool = True) -> list[dict]:
        return [
            item.to_running_item()
            for item in self.service_states(refresh_external)
            if item.is_active
        ]

    def stop_all_services(self, include_external: bool = True, silent: bool = True) -> int:
        return sum(
            tab.stop_all_services(include_external=include_external, silent=silent)
            for tab in self._project_tabs.values()
        )

    def wait_services_stopped(self, timeout_ms: int = 12000) -> bool:
        deadline = time.time() + timeout_ms / 1000.0
        for tab in self._project_tabs.values():
            remaining = max(0, int((deadline - time.time()) * 1000))
            if remaining <= 0 or not tab.wait_services_stopped(remaining):
                return False
        return True

    def runtime_paths(self) -> tuple[str, ...]:
        return tuple(tab.project_meta.path for tab in self._project_tabs.values())

    def request_close(
        self, confirm_running: bool = True, stop_running: bool = True, quiet: bool = False,
    ) -> bool:
        running = self.running_service_items(refresh_external=True)
        if running and confirm_running:
            result = QMessageBox.question(
                self,
                "项目正在运行",
                "关闭会停止该目录内正在运行的项目，确认关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return False
        for tab in self._project_tabs.values():
            if not tab.request_close(
                confirm_running=False, stop_running=stop_running, quiet=quiet,
            ):
                return False
        self._refresh_timer.stop()
        for worker in (
            self._prepare_worker, self._save_worker, self._workspace_worker,
            self._create_worker, self._merge_worker, self._delete_preflight_worker,
            self._delete_worker,
        ):
            if worker and worker.isRunning():
                worker.wait(3000)
        return True

    def _current_project_tab(self) -> ProjectTab | None:
        tab = self.detail_stack.currentWidget()
        return tab if isinstance(tab, ProjectTab) else None

    def open_file_picker(self) -> None:
        tab = self._current_project_tab()
        if tab:
            tab.open_file_picker()

    def open_recent_files(self) -> None:
        tab = self._current_project_tab()
        if tab:
            tab.open_recent_files()

    def open_command_palette(self) -> None:
        tab = self._current_project_tab()
        if tab:
            tab.open_command_palette()

    def open_endpoint_picker(self) -> None:
        tab = self._current_project_tab()
        if tab:
            tab.open_endpoint_picker()

    def restart_project(self) -> None:
        tab = self._current_project_tab()
        if tab:
            tab.restart_project()

    def close_current_file_tab(self) -> None:
        tab = self._current_project_tab()
        if tab:
            tab.close_current_file_tab()
