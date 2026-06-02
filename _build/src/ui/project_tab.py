"""单项目 Tab 面板

把 项目元信息 / 工具栏按钮 / 日志 / 设置面板 / 状态条 / 进程运行器 串起来。
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton,
    QSplitter, QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from src.core.config import AppConfig, ProjectEntry
from src.core.file_index import FileIndexer
from src.core.git_worker import (
    GitCheckoutWorker, GitFetchWorker, GitMergePushWorker, GitStatusWorker,
)
from src.core.process_runner import ProcessRunner, RunContext
from src.core.project_detector import ProjectMeta, RunProfile
from src.ui.content_search import ContentSearchDialog
from src.ui.file_tree import FileTree
from src.ui.git_viewer import GitViewer
from src.ui.log_widget import LogWidget
from src.ui.quick_open import (
    PickerItem, show_command_palette, show_recent_files,
)
from src.ui.service_panel import (
    STATE_IDLE, STATE_RUNNING, STATE_RUNNING_EXTERNAL, STATE_STARTING,
    STATE_STOPPING, ServicePanel,
)
from src.ui.settings_panel import SettingsPanel
from src.ui.theme import (
    BG_L0, BG_L4, BORDER_SUBTLE, COLOR_SUCCESS, COLOR_WARN,
    DOT_IDLE, DOT_RUNNING, DOT_WARN, FG_DIM, FG_PRIMARY, FG_SECONDARY,
    FONT_PT_UI_SM, RADIUS_SM,
)
from src.util import git_info, notify
from src.util.editor import open_in_editor, open_folder, reveal_in_explorer


# 顶层调谐常量（提到顶部方便统一调，避免散在各方法里成 magic number）
LEFT_PANEL_WIDTH = 240
STATUS_REFRESH_MS = 3000
GIT_FETCH_INTERVAL_MS = 5 * 60 * 1000
GIT_FETCH_INITIAL_DELAY_MS = 8000
MODULE_STOP_TIMEOUT_MS = 10000
RESTART_GAP_MS = 1800
COMPILE_THEN_RUN_GAP_MS = 300

log = logging.getLogger("mini-ide")


# 状态栏分支/改动按钮的 base QSS（透明、hover 高亮 BG_L4）
def _status_btn_base_qss() -> str:
    return (
        f"QToolButton {{ background:transparent; border:none;"
        f" padding:2px 8px; font-size:{FONT_PT_UI_SM}pt; }}"
        f"QToolButton:hover {{ background:{BG_L4};"
        f" border-radius:{RADIUS_SM}px; }}"
        f"QToolButton::menu-indicator {{ image:none; width:0; }}"
    )


class _BranchMenu(QMenu):
    """分支下拉菜单：点击本地分支切换，右键复制分支名。"""

    switchRequested = Signal(str)
    mergeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_branch = ""

    def set_current_branch(self, name: str) -> None:
        self._current_branch = name or ""

    def contextMenuEvent(self, event):  # type: ignore[override]
        action = self.actionAt(event.pos())
        branch = action.data() if action is not None else None
        if not branch:
            super().contextMenuEvent(event)
            return
        ctx = QMenu(self)
        copy_act = ctx.addAction("📋 复制分支名")
        chosen = ctx.exec(event.globalPos())
        if chosen is copy_act:
            QApplication.clipboard().setText(branch)
        event.accept()


# 前端项目类型白名单：这些类型的项目如果 package.json 带 build script，就在工具栏多一个「打包」按钮
_FRONTEND_TYPES = {"vue", "react", "next", "nuxt", "svelte", "node"}

# 多模块 Spring Boot：从子模块启动日志里抓真实监听端口，覆盖 application.yml 里的默认值
# 兼容 Spring Boot 2.x/3.x 的 Tomcat / Netty / Undertow / Jetty 启动行：
#   Tomcat started on port(s): 8080 (http)
#   Tomcat started on port 8080 (http)
#   Netty started on port(s): 8080
_RE_BOOT_LISTEN_PORT = re.compile(
    r"(?:Tomcat|Netty|Undertow|Jetty)\s+started\s+on\s+port(?:\(s\))?[:\s]+(\d+)",
    re.IGNORECASE,
)


class ProjectTab(QWidget):

    statusChanged = Signal()   # 状态变化时通知主窗口刷新状态栏

    def __init__(self, meta: ProjectMeta, entry: ProjectEntry, config: AppConfig, parent=None):
        super().__init__(parent)
        self.project_meta = meta
        self.entry = entry
        self.config = config
        self.path = meta.path

        # 多模块（Spring Cloud 那种）：每个 @SpringBootApplication 子模块一个独立 runner。
        # 单模块项目这里就是 False，走 self.runner 的传统路径，行为 100% 不变。
        self._is_multi_module = len(meta.spring_boot_modules) >= 2

        # 是否 git 仓库：启动时判一次缓存住。非 git 项目不起染色/状态栏 worker，
        # 省得每 3s 白跑一堆注定失败的 git 子进程。
        from src.core.git_ops import is_git_repo
        self._is_git_repo = is_git_repo(meta.path)

        # 项目级 runner：单模块项目的启动用它；多模块项目用它跑编译/Clean 等全局 profile
        self.runner = ProcessRunner(self)
        self.runner.outputLine.connect(self._on_output)
        self.runner.stateChanged.connect(self._on_state)
        self.runner.finished.connect(self._on_finished)

        # 每个 Spring Boot 子模块独立的 runner + 日志 widget（仅多模块时有内容）
        self.module_runners: dict[str, ProcessRunner] = {}
        self.module_logs: dict[str, LogWidget] = {}
        self._module_current: dict[str, RunProfile] = {}
        # 模块启动日志里抓到的真实监听端口（覆盖 application.yml 默认值）
        self._module_ports: dict[str, int] = {}
        # 用户关 tab 触发 stop 的模块集合；finished 时自动清理 tab
        self._closing_modules: set[str] = set()
        # 「外部启动感知」：非 mini-ide 拉起、但端口快照里按 cmdline 匹配到的模块 → pid。
        # 这些模块面板显示「运行中(外部)」，停止按钮走 kill_pid 而非 runner.stop()。
        self._module_external_pids: dict[str, int] = {}

        # 文件树「▶ 运行脚本」启动的脚本：每个脚本绝对路径一个独立 runner + log tab。
        # 与 module_runners 完全解耦，不进 ServicePanel，不影响多模块聚合状态。
        # key 使用绝对路径（lower 后），同路径再次运行时会聚焦已有 tab 而不是再开一个
        self._script_runners: dict[str, ProcessRunner] = {}
        self._script_logs: dict[str, LogWidget] = {}
        self._closing_scripts: set[str] = set()

        self._current_profile: RunProfile | None = None
        self._pending_after_compile: RunProfile | None = None
        self._git_fetch_worker: GitFetchWorker | None = None
        self._git_checkout_worker: GitCheckoutWorker | None = None
        self._git_merge_worker: GitMergePushWorker | None = None
        self._git_status_worker: GitStatusWorker | None = None
        self._git_viewer = None
        self._merge_dialog = None
        # 手动「刷新远程分支」才给反馈；后台 5 分钟轮询保持静默
        self._fetch_manual = False
        self._fetch_behind_before = 0
        # 启动完成标记：日志里看到 Started / ready in 等 marker 后，只给状态栏贴一次"✓ 启动完成"
        self._startup_phase_marked = False
        self._recent_files: list[str] = []     # 最近在预览里打开的文件

        self.indexer = FileIndexer(self.project_meta.path, self)
        self.indexer.start_async_scan()

        self._build_ui()
        self._register_shortcuts()

        # 状态轮询（运行时长、占用端口、内存）
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(STATUS_REFRESH_MS)
        self._poll_timer.timeout.connect(self._refresh_status_row)
        self._poll_timer.start()

        # 远程分支轮询：每 5 分钟 git fetch 一次，发现新提交时高亮分支按钮
        self._fetch_timer = QTimer(self)
        self._fetch_timer.setInterval(GIT_FETCH_INTERVAL_MS)
        self._fetch_timer.timeout.connect(self._start_remote_fetch)
        self._fetch_timer.start()
        QTimer.singleShot(GIT_FETCH_INITIAL_DELAY_MS, self._start_remote_fetch)

    # ---- UI ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_status_row())

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(2)

        self.file_tree = FileTree(
            self.project_meta.path,
            indexer=self.indexer,
            project_type=self.project_meta.project_type,
        )
        self.file_tree.fileActivated.connect(self._on_file_activated)
        self.file_tree.fileCreated.connect(self._on_file_created)
        self.file_tree.scriptRunRequested.connect(self._run_script)

        # 多模块项目：左侧垂直 splitter，上是服务面板、下是文件树
        # 单模块项目：左侧只有文件树（行为不变）
        self.service_panel: ServicePanel | None = None
        if self._is_multi_module:
            left_wrap = QSplitter(Qt.Orientation.Vertical)
            left_wrap.setHandleWidth(2)
            self.service_panel = ServicePanel(
                [(name, port) for name, _path, port, _cls in self.project_meta.spring_boot_modules],
                parent=self,
            )
            self.service_panel.startRequested.connect(self._start_module)
            self.service_panel.stopRequested.connect(self._stop_module)
            self.service_panel.focusRequested.connect(self._focus_module_log)
            self.service_panel.startAllRequested.connect(self._start_all_modules)
            self.service_panel.stopAllRequested.connect(self._stop_all_modules)
            left_wrap.addWidget(self.service_panel)
            left_wrap.addWidget(self.file_tree)
            left_wrap.setSizes([220, 500])
            self.splitter.addWidget(left_wrap)
        else:
            self.splitter.addWidget(self.file_tree)

        self.log = LogWidget()
        self.log.set_max_blocks(self.config.max_log_blocks)
        self.log.fileJumpRequested.connect(self._jump_to_file)
        self.log.portDiagnosisRequested.connect(self.open_port_dialog)

        # 中心 Tab 容器：tab 0 固定是日志（不可关），其它 tab 是文件面板
        self.center_tabs = QTabWidget()
        self.center_tabs.setTabsClosable(True)
        self.center_tabs.setMovable(True)
        self.center_tabs.setDocumentMode(True)
        self.center_tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self.center_tabs.currentChanged.connect(self._on_center_tab_changed)
        self.center_tabs.addTab(self.log, "📋 日志")
        # 锁定日志 tab 的关闭按钮（两侧都清空，兼容不同平台默认位置）
        _bar = self.center_tabs.tabBar()
        _bar.setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
        _bar.setTabButton(0, QTabBar.ButtonPosition.LeftSide, None)
        # 多模块（微服务）项目：项目级日志平时是空的（各模块输出进各自 tab），
        # 默认隐藏这个常驻 tab；一旦有全局消息（整体编译输出 / 告警）写入就自动显示回来。
        # tab 始终留在 index 0，只切 visible，不动结构，避免别处「index 0 即日志」的假设错位。
        if self._is_multi_module:
            self.center_tabs.setTabVisible(0, False)
            self.log.contentAdded.connect(self._reveal_log_tab)
        # tab 右键菜单：关闭 / 关闭其它 / 关闭右侧 / 关闭左侧
        _bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        _bar.customContextMenuRequested.connect(self._on_tab_bar_context_menu)
        # 已打开文件去重：key = abs_path.lower() → FilePreviewPane
        self._file_panes: dict[str, object] = {}

        self.splitter.addWidget(self.center_tabs)
        self.splitter.setSizes([LEFT_PANEL_WIDTH, 1100])

        root.addWidget(self.splitter, 1)

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.Shape.NoFrame)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)

        type_lbl = QLabel(f"{self.project_meta.icon}  {self.project_meta.display_type}")
        type_lbl.setProperty("role", "title")
        lay.addWidget(type_lbl)

        name_lbl = QLabel(self.project_meta.name)
        name_lbl.setProperty("role", "subtitle")
        lay.addWidget(name_lbl)

        lay.addSpacing(20)

        # 工具栏极简：只露主按钮（启动 ↔ 停止）
        # 编译 / Clean / 重启 等次级命令统一进命令面板（Ctrl+Shift+P）
        # 多模块项目：启停走左侧服务面板，主按钮隐藏
        self._profile_buttons: dict[str, QPushButton] = {}
        primary_profiles = [p for p in self.project_meta.profiles if p.primary]
        self._primary_profile: RunProfile | None = primary_profiles[0] if primary_profiles else None

        self.btn_main = QPushButton()
        self.btn_main.setMinimumWidth(110)
        self.btn_main.clicked.connect(self._on_main_clicked)
        lay.addWidget(self.btn_main)
        if self._is_multi_module:
            self.btn_main.setVisible(False)
        else:
            self._update_main_button("idle")

        # 前端项目：package.json 里有 build script 时多一个「🔒 打包」按钮，
        # 走和主按钮同一套 _run_profile 逻辑（正在跑时会弹确认框先停再跑）
        self._build_profile: RunProfile | None = None
        if self.project_meta.project_type in _FRONTEND_TYPES:
            build_prof = next(
                (p for p in self.project_meta.profiles if p.name == "build"),
                None,
            )
            if build_prof:
                self._build_profile = build_prof
                self.btn_build = QPushButton(
                    f"{build_prof.icon}  {build_prof.label}" if build_prof.icon else build_prof.label
                )
                self.btn_build.setToolTip(
                    build_prof.description or " ".join(build_prof.command)
                )
                self.btn_build.clicked.connect(lambda: self._run_profile(build_prof))
                lay.addWidget(self.btn_build)

        lay.addStretch(1)

        # 工具栏极简：主按钮旁只留命令面板入口；其它常用动作全部进 Ctrl+Shift+P
        btn_cmd = QToolButton()
        btn_cmd.setText("⌘")
        btn_cmd.setToolTip("命令面板 (Ctrl+Shift+P)")
        btn_cmd.clicked.connect(self.open_command_palette)
        lay.addWidget(btn_cmd)

        return bar

    def _build_status_row(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(
            f"background:{BG_L0}; border-bottom:1px solid {BORDER_SUBTLE};"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(14)

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {DOT_IDLE};")
        self.lbl_state = QLabel("就绪")
        self.lbl_state.setProperty("role", "subtitle")
        lay.addWidget(self.dot)
        lay.addWidget(self.lbl_state)

        lay.addWidget(QLabel(""))
        self.lbl_elapsed = QLabel("")
        self.lbl_elapsed.setProperty("role", "subtitle")
        lay.addWidget(self.lbl_elapsed)

        self.lbl_phase = QLabel("")
        self.lbl_phase.setProperty("role", "subtitle")
        lay.addWidget(self.lbl_phase)

        # 分支按钮：点击弹出菜单复制分支名
        self.btn_branch = QToolButton()
        self.btn_branch.setText("")
        self.btn_branch.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_branch.setToolTip("点击复制分支名")
        # 默认色（无 git 信息时不显示，初始 QSS 用次级灰）
        self.btn_branch.setStyleSheet(
            _status_btn_base_qss() + f"QToolButton {{ color:{FG_SECONDARY}; }}"
        )
        self._branch_menu = _BranchMenu(self.btn_branch)
        self._branch_menu.aboutToShow.connect(self._populate_branch_menu)
        self.btn_branch.setMenu(self._branch_menu)
        self.btn_branch.setVisible(False)   # 没有 git 信息时隐藏
        lay.addWidget(self.btn_branch)

        # 改动按钮：紧贴分支按钮，显示本地改动文件数，点击打开 GitViewer
        self.btn_changes = QToolButton()
        self.btn_changes.setText("")
        self.btn_changes.setToolTip("查看当前分支改动文件")
        self.btn_changes.clicked.connect(self.open_git_viewer)
        self.btn_changes.setVisible(False)
        lay.addWidget(self.btn_changes)

        lay.addStretch(1)

        return bar

    # ---- 动作 ----

    def _run_profile(self, prof: RunProfile) -> None:
        if self.runner.is_running():
            ret = QMessageBox.question(
                self, "已有任务在运行",
                "当前已有任务在运行，需要先停止再执行吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
            self.runner.stop()
            QTimer.singleShot(RESTART_GAP_MS, lambda: self._run_profile(prof))
            return

        # 启动前强制编译
        if prof.pre_compile:
            compile_prof = self._find_compile_profile()
            if compile_prof and compile_prof.name != prof.name:
                self._pending_after_compile = prof
                self._start_profile(compile_prof)
                return
        self._start_profile(prof)

    def _find_compile_profile(self) -> RunProfile | None:
        for p in self.project_meta.profiles:
            if p.kind == "compile":
                return p
        return None

    def _start_profile(self, prof: RunProfile) -> None:
        self._current_profile = prof
        self.log.begin_run(f"{self.project_meta.name}-{prof.name}")
        ctx = RunContext(
            cwd=self.project_meta.path,
            env={},
            jvm_opts="-Xmx768m -Xms256m",
            spring_profile="",
            extra_args=[],
        )
        ok = self.runner.start(list(prof.command), ctx)
        if not ok:
            self.log.append_line("stderr", f"[启动失败] 无法启动 {prof.label}")

    def _stop(self) -> None:
        self._pending_after_compile = None
        self.runner.stop()

    def _on_main_clicked(self) -> None:
        """主按钮点击：根据当前状态决定启动 or 停止"""
        if self.runner.is_running():
            self._stop()
        elif self._primary_profile:
            self._run_profile(self._primary_profile)

    def _update_main_button(self, state: str) -> None:
        btn = self.btn_main
        if state == "running":
            btn.setText("⏹  停止")
            btn.setProperty("role", "danger")
            btn.setToolTip("停止当前运行的进程（递归杀进程树）")
            btn.setEnabled(True)
        elif state == "stopping":
            btn.setText("⏳  停止中...")
            btn.setToolTip("正在停止...")
            btn.setEnabled(False)
        else:   # idle
            p = self._primary_profile
            if p:
                btn.setText(f"{p.icon}  {p.label}" if p.icon else p.label)
                btn.setToolTip(p.description or " ".join(p.command))
            else:
                btn.setText("▶  启动")
                btn.setToolTip("(未识别出主启动命令)")
                btn.setEnabled(False)
                return
            btn.setProperty("role", "primary")
            btn.setEnabled(True)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _restart(self) -> None:
        primary = next((p for p in self.project_meta.profiles if p.primary), None)
        if not primary:
            return
        if self.runner.is_running():
            self._pending_after_stop = primary
            self.runner.stop()
            QTimer.singleShot(RESTART_GAP_MS, lambda: self._run_profile(primary))
        else:
            self._run_profile(primary)

    # ---- 多模块（Spring Cloud）专用动作 ----

    def _find_module_run_profile(self, module: str) -> RunProfile | None:
        """模块名 → 对应的 bootRun RunProfile（project_detector 已生成）"""
        # 子模块：profile.name == f"bootRun:{module}"
        for p in self.project_meta.profiles:
            if p.name == f"bootRun:{module}":
                return p
        # 主模块：primary=True 且 command 里含 :{module}:bootRun
        marker = f":{module}:bootRun"
        for p in self.project_meta.profiles:
            if p.primary and p.kind == "run":
                if any(marker in arg for arg in p.command):
                    return p
        return None

    def _ensure_module_runner(self, module: str) -> ProcessRunner:
        r = self.module_runners.get(module)
        if r is not None:
            return r
        r = ProcessRunner(self)
        r.outputLine.connect(lambda s, l, m=module: self._on_module_output(m, s, l))
        r.stateChanged.connect(lambda st, m=module: self._on_module_state(m, st))
        r.finished.connect(lambda c, m=module: self._on_module_finished(m, c))
        self.module_runners[module] = r
        return r

    def _ensure_module_log_tab(self, module: str) -> LogWidget:
        """第一次启动该模块时创建独立 LogWidget 并挂到中心 Tab 容器"""
        lw = self.module_logs.get(module)
        if lw is not None:
            return lw
        lw = LogWidget()
        lw.set_max_blocks(self.config.max_log_blocks)
        lw.fileJumpRequested.connect(self._jump_to_file)
        lw.portDiagnosisRequested.connect(self.open_port_dialog)
        self.module_logs[module] = lw
        idx = self.center_tabs.addTab(lw, f"📋 {module}")
        self.center_tabs.setCurrentIndex(idx)
        return lw

    def _start_module(self, module: str, silent: bool = False) -> None:
        """启动单个模块。

        silent=True 时用于 CLI：不弹任何确认框。若该模块已被一个 mini-ide
        没在管的外部进程占着（典型：跨重启后靠端口感知到的旧进程），先按 PID
        把它停掉、等端口释放，再用 mini-ide 自己启动——这样起来的就是 mini-ide
        亲手管理的进程，界面正常显示「运行中」，不会再留外部标记。
        """
        prof = self._find_module_run_profile(module)
        if not prof:
            if not silent:
                QMessageBox.warning(self, "启动失败", f"未找到模块「{module}」的启动命令")
            return
        runner = self._ensure_module_runner(module)
        if runner.is_running():
            return
        # 已有外部进程占着该模块
        if module in self._module_external_pids:
            pid = self._module_external_pids[module]
            if silent:
                # CLI：静默接管——杀掉旧的外部进程并等端口释放，避免新进程撞端口秒退
                self._takeover_external(module)
            else:
                ret = QMessageBox.question(
                    self, "模块已在外部运行",
                    f"模块「{module}」似乎已在 mini-ide 之外运行（PID {pid}）。\n"
                    f"仍要再启动一个实例吗？（可能端口冲突）",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    return
                self._module_external_pids.pop(module, None)
        log_w = self._ensure_module_log_tab(module)
        log_w.begin_run(f"{self.project_meta.name}-{module}")
        ctx = RunContext(
            cwd=self.project_meta.path,
            env={}, jvm_opts="-Xmx768m -Xms256m", spring_profile="", extra_args=[],
        )
        # 上次运行抓到的端口失效，等本次启动日志里重新抓
        self._module_ports.pop(module, None)
        if self.service_panel:
            self.service_panel.update_state(module, STATE_STARTING)
        ok = runner.start(list(prof.command), ctx)
        if not ok:
            log_w.append_line("stderr", f"[启动失败] 无法启动 {module}")

    def _takeover_external(self, module: str) -> bool:
        """杀掉占着该模块的外部进程并等端口真正释放。返回端口是否已空出。

        给 CLI 静默启动/重启用：旧进程不是 mini-ide 亲手起的（只靠扫端口感知到），
        重启时若不先停掉它，新进程会撞端口/gradle 冲突，瞬间以错误码退出，结果旧的
        没停、新的没起，界面继续挂着外部标记。
        """
        from src.core.process_runner import kill_pid, is_port_listening
        port = self._module_ports.get(module)
        pid = self._module_external_pids.pop(module, None)
        if pid:
            kill_pid(pid)
        # 等端口真正释放（最多 ~8s）。用实时直查而非后台快照：kill 后快照会被清空，
        # 依赖它会被「清空 = 已释放」骗到。期间 processEvents 让 UI 不至于完全冻结。
        if port:
            import time
            from PySide6.QtCore import QCoreApplication, QEventLoop
            deadline = time.time() + 8.0
            while time.time() < deadline:
                if not is_port_listening(port):
                    return True
                QCoreApplication.processEvents(
                    QEventLoop.ProcessEventsFlag.AllEvents, 100
                )
            return not is_port_listening(port)
        return True


    def _stop_module(self, module: str) -> None:
        runner = self.module_runners.get(module)
        if runner and runner.is_running():
            runner.stop()
            return
        # 外部进程（非 mini-ide 拉起）：按感知到的 pid 结束。需用户确认，避免误杀。
        pid = self._module_external_pids.get(module)
        if pid is not None:
            ret = QMessageBox.question(
                self, "停止外部进程",
                f"模块「{module}」是在 mini-ide 之外启动的（PID {pid}）。\n"
                f"确定要结束该进程及其子进程吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
            from src.core.process_runner import kill_pid
            if self.service_panel:
                self.service_panel.update_state(module, STATE_STOPPING)
            ok = kill_pid(pid)
            if ok:
                self._module_external_pids.pop(module, None)
                if self.service_panel:
                    self.service_panel.update_state(module, STATE_IDLE)
            else:
                self.log.append_line("stderr", f"[停止失败] 无法结束 {module} (PID {pid})")
                # 刷新会重新感知真实状态
                self._refresh_status_row()

    def _start_all_modules(self, silent: bool = False) -> None:
        """并行启动所有未运行的模块（无依赖编排——用户砍掉 Workspace 时已认可）

        silent=True（CLI 全部启动）：对靠端口感知到的外部进程也接管——先杀旧的
        再用 mini-ide 启动，使其纳入本实例管理。界面操作时仍跳过外部进程不重复拉起。
        """
        # 先刷新一次外部感知，避免对已在外部运行的模块重复拉起
        self._detect_and_apply_external()
        for mod_name, _path, _port, _cls in self.project_meta.spring_boot_modules:
            r = self.module_runners.get(mod_name)
            if r and r.is_running():
                continue
            if mod_name in self._module_external_pids and not silent:
                # 已在外部运行，跳过（不弹确认框）
                continue
            self._start_module(mod_name, silent=silent)

    def _stop_all_modules(self) -> None:
        """并行停止所有运行中的模块"""
        for mod_name, runner in list(self.module_runners.items()):
            if runner.is_running():
                runner.stop()

    def _focus_module_log(self, module: str) -> None:
        lw = self.module_logs.get(module)
        if lw is not None:
            idx = self.center_tabs.indexOf(lw)
            if idx >= 0:
                self.center_tabs.setCurrentIndex(idx)
                return
        # 模块还没启动过：提示用户先启动
        self.log.append_line(
            "meta",
            f"[提示] {module} 还未启动；点击其右侧的 ▶ 按钮或"
            f" Ctrl+Shift+P →「▶ 启动 {module}」",
        )

    def _on_module_output(self, module: str, stream: str, line: str) -> None:
        lw = self.module_logs.get(module)
        if lw is None:
            return
        lw.append_line(stream, line)
        # 抓真实监听端口；只抓第一次命中（HTTP 端口通常先于 management 端口出现）
        if module not in self._module_ports:
            m = _RE_BOOT_LISTEN_PORT.search(line)
            if m:
                self._module_ports[module] = int(m.group(1))
                r = self.module_runners.get(module)
                # 必须确认 runner 仍在跑——_on_finished 里 flush 残留日志会
                # 在切 idle 前 emit 这行，没这层守卫会把 panel 刷回 RUNNING
                if r and r.is_running() and self.service_panel:
                    self.service_panel.update_state(
                        module, STATE_RUNNING,
                        port=self._module_ports[module],
                        elapsed_seconds=r.elapsed_seconds(),
                    )

    def _on_module_state(self, module: str, state: str) -> None:
        if self.service_panel:
            mapping = {
                "idle":     STATE_IDLE,
                "running":  STATE_RUNNING,
                "stopping": STATE_STOPPING,
            }
            panel_state = mapping.get(state, STATE_IDLE)
            r = self.module_runners.get(module)
            elapsed = r.elapsed_seconds() if r else 0.0
            self.service_panel.update_state(
                module, panel_state,
                port=self._module_ports.get(module),
                elapsed_seconds=elapsed,
            )
        self._refresh_aggregate_state()
        self.statusChanged.emit()

    def _on_module_finished(self, module: str, exit_code: int) -> None:
        lw = self.module_logs.get(module)
        if lw is not None:
            lw.end_run()
        self._module_current.pop(module, None)
        if exit_code not in (0, -1):
            notify.notify_error(
                f"✗ {self.project_meta.name} / {module} 退出",
                f"退出码 {exit_code}",
            )
        # 如果是"关 tab 触发的停止"，在这里把 tab 真正移除
        if module in self._closing_modules:
            self._closing_modules.discard(module)
            self._remove_module_tab(module)

    def _force_close_module_if_stuck(self, module: str) -> None:
        """关 tab 触发 stop 10 秒后的兜底：若还在 _closing_modules 说明进程没正常退
        （SIGTERM 被忽略 / QProcess.finished 丢失），强制清理 tab。"""
        if module not in self._closing_modules:
            return
        self._closing_modules.discard(module)
        self.log.append_line(
            "meta",
            f"[警告] 模块 {module} 停止超时（10s）；关闭 tab 但进程可能残留，"
            f"请用「🔌 端口占用查询」或任务管理器确认",
        )
        self._remove_module_tab(module)

    def _remove_module_tab(self, module: str) -> None:
        """把某模块的 tab + runner + log 全部清理掉"""
        lw = self.module_logs.pop(module, None)
        if lw is not None:
            idx = self.center_tabs.indexOf(lw)
            if idx >= 0:
                self.center_tabs.removeTab(idx)
            lw.deleteLater()
        runner = self.module_runners.pop(module, None)
        if runner is not None:
            runner.deleteLater()
        self._module_current.pop(module, None)
        self._module_ports.pop(module, None)
        if self.service_panel:
            self.service_panel.update_state(module, STATE_IDLE)

    def _module_of_tab(self, widget) -> str | None:
        for m, lw in self.module_logs.items():
            if lw is widget:
                return m
        return None

    def _any_module_running(self) -> bool:
        return any(r.is_running() for r in self.module_runners.values())

    # ---- 文件树「▶ 运行脚本」 ----

    def _script_key(self, path: str) -> str:
        """脚本 tab 的去重 key：用 resolve 后的小写绝对路径，跟 _file_panes 一致"""
        try:
            return str(Path(path).resolve()).lower()
        except OSError:
            return path.lower()

    def _script_of_tab(self, widget) -> str | None:
        for k, lw in self._script_logs.items():
            if lw is widget:
                return k
        return None

    def _run_script(self, path: str) -> None:
        """文件树右键「▶ 运行 xxx.bat」：在中心 Tab 容器里开一个独立 LogWidget 跑脚本，
        stdout/stderr 都灌进 tab。CREATE_NO_WINDOW 已在 process_runner 里贴上，不会弹黑窗。
        关 tab 等同于停进程（与模块日志 tab 同套机制）。
        """
        p = Path(path)
        if not p.is_file():
            QMessageBox.warning(self, "运行脚本", f"文件不存在：\n{path}")
            return

        key = self._script_key(path)
        existing = self._script_runners.get(key)
        if existing and existing.is_running():
            # 同一脚本已有运行中 tab：聚焦它，不重复启动
            lw = self._script_logs.get(key)
            if lw is not None:
                idx = self.center_tabs.indexOf(lw)
                if idx >= 0:
                    self.center_tabs.setCurrentIndex(idx)
            return

        # 同路径上次运行已结束但 tab 还在：复用 LogWidget，新跑一轮
        lw = self._script_logs.get(key)
        if lw is None:
            lw = LogWidget()
            lw.set_max_blocks(self.config.max_log_blocks)
            lw.fileJumpRequested.connect(self._jump_to_file)
            lw.portDiagnosisRequested.connect(self.open_port_dialog)
            self._script_logs[key] = lw
            idx = self.center_tabs.addTab(lw, f"▶ {p.name}")
        else:
            # 复用已有 LogWidget：保留上次输出，begin_run 会贴新 banner 区分两轮
            idx = self.center_tabs.indexOf(lw)
        self.center_tabs.setCurrentIndex(idx)

        runner = self._script_runners.get(key)
        if runner is None:
            runner = ProcessRunner(self)
            runner.outputLine.connect(lambda s, l, k=key: self._on_script_output(k, s, l))
            runner.finished.connect(lambda c, k=key: self._on_script_finished(k, c))
            self._script_runners[key] = runner

        # 命令构造：按脚本类型选解释器。
        # .ps1 → PowerShell
        # .bat/.cmd/.exe/其他 → 直接交给 process_runner._split_program 处理
        ext = p.suffix.lower()
        if ext == ".ps1":
            command = ["powershell", "-NoLogo", "-NoProfile",
                       "-ExecutionPolicy", "Bypass", "-File", str(p)]
        else:
            # .bat / .cmd / .exe 都直接交给 _split_program；它会按扩展名走 cmd /c 或直跑
            command = [str(p)]

        lw.begin_run(p.name)
        ctx = RunContext(
            cwd=str(p.parent),     # 用脚本所在目录作为 cwd——nginx 这类相对路径配置才能找到
            env={}, jvm_opts="", spring_profile="", extra_args=[],
        )
        ok = runner.start(command, ctx)
        if not ok:
            lw.append_line("stderr", f"[启动失败] 无法启动 {p.name}")

    def _on_script_output(self, key: str, stream: str, line: str) -> None:
        lw = self._script_logs.get(key)
        if lw is not None:
            lw.append_line(stream, line)

    def _on_script_finished(self, key: str, exit_code: int) -> None:
        lw = self._script_logs.get(key)
        if lw is not None:
            lw.end_run()
        # 关 tab 触发的停止：进程退出后真正清理 tab
        if key in self._closing_scripts:
            self._closing_scripts.discard(key)
            self._remove_script_tab(key)

    def _remove_script_tab(self, key: str) -> None:
        lw = self._script_logs.pop(key, None)
        if lw is not None:
            idx = self.center_tabs.indexOf(lw)
            if idx >= 0:
                self.center_tabs.removeTab(idx)
            lw.deleteLater()
        runner = self._script_runners.pop(key, None)
        if runner is not None:
            runner.deleteLater()

    def _force_close_script_if_stuck(self, key: str) -> None:
        if key not in self._closing_scripts:
            return
        self._closing_scripts.discard(key)
        self.log.append_line(
            "meta",
            f"[警告] 脚本 {key} 停止超时（10s）；关闭 tab 但进程可能残留，"
            f"请用「🔌 端口占用查询」或任务管理器确认",
        )
        self._remove_script_tab(key)

    def _any_script_running(self) -> bool:
        return any(r.is_running() for r in self._script_runners.values())

    def _refresh_aggregate_state(self) -> None:
        """多模块项目：根据所有 runner + 外部感知汇总状态更新状态栏 dot / lbl_state"""
        if not self._is_multi_module:
            return
        total = len(self.project_meta.spring_boot_modules)
        running = sum(1 for r in self.module_runners.values() if r.is_running())
        # 外部感知到、且不是自管理 runner 在跑的模块也计入
        running += sum(
            1 for m in self._module_external_pids
            if not (self.module_runners.get(m) and self.module_runners[m].is_running())
        )
        stopping = any(r.state() == "stopping" for r in self.module_runners.values())
        if running == 0 and not stopping:
            self.dot.setStyleSheet(f"color: {DOT_IDLE};")
            self.lbl_state.setText("就绪")
        elif stopping:
            self.dot.setStyleSheet(f"color: {DOT_WARN};")
            self.lbl_state.setText(f"停止中 ({running}/{total} 运行中)")
        else:
            self.dot.setStyleSheet(f"color: {DOT_RUNNING};")
            self.lbl_state.setText(f"{running}/{total} 运行中")

    # ---- 事件回调 ----

    def _on_output(self, stream: str, line: str) -> None:
        self.log.append_line(stream, line)
        # 日志里识别到启动完成 marker 后，状态栏贴一次"✓ 启动完成"（仅状态栏，不弹系统通知）
        if not self._startup_phase_marked and self._current_profile and self._current_profile.kind == "run":
            markers = ("Started ", "ready in ", "compiled successfully", "Netty started on port", "Tomcat started on port")
            if any(m in line for m in markers):
                self._startup_phase_marked = True
                self.lbl_phase.setText("✓ 启动完成")
                self.lbl_phase.setStyleSheet(f"color: {COLOR_SUCCESS};")

    def _on_state(self, state: str) -> None:
        mapping = {
            "idle":     ("●", DOT_IDLE, "就绪"),
            "running":  ("●", DOT_RUNNING, "运行中"),
            "stopping": ("●", DOT_WARN, "正在停止..."),
        }
        dot, color, text = mapping.get(state, ("●", FG_SECONDARY, state))
        self.dot.setText(dot)
        self.dot.setStyleSheet(f"color: {color};")

        if state == "running" and self._current_profile:
            text = f"{self._current_profile.label} 中"
        self.lbl_state.setText(text)

        self._update_main_button(state)

        # 多模块项目：self.runner 只用来跑编译/Clean 等项目级 profile。
        # 跑完 idle 时要把状态栏恢复成"N/M 运行中"的聚合显示。
        if self._is_multi_module and state == "idle":
            self._refresh_aggregate_state()

        self.statusChanged.emit()

    def _on_finished(self, exit_code: int) -> None:
        prof = self._current_profile
        self.log.end_run()
        self._current_profile = None

        # 启动前编译完成，接着执行主任务
        if prof and prof.kind == "compile" and self._pending_after_compile:
            if exit_code == 0:
                nxt = self._pending_after_compile
                self._pending_after_compile = None
                QTimer.singleShot(COMPILE_THEN_RUN_GAP_MS, lambda: self._start_profile(nxt))
            else:
                self._pending_after_compile = None
                self.log.append_line("stderr", "[已取消] 编译失败，跳过启动")
                notify.notify_error(
                    f"✗ {self.project_meta.name} 编译失败",
                    "点击日志查看错误详情；已取消后续启动",
                )
                return

        # 运行任务异常退出
        if prof and prof.kind == "run" and exit_code not in (0, -1):
            notify.notify_error(
                f"✗ {self.project_meta.name} 退出",
                f"退出码 {exit_code}，查看日志定位原因",
            )
        self._startup_phase_marked = False

    def _detect_external_modules(self) -> dict[str, tuple[int, int]]:
        """从后台端口快照里按命令行匹配各模块，返回 {module: (pid, port)}。

        感知非 mini-ide 启动的进程（AI / 终端 / IDE 起的）。匹配信号：
        监听进程的 cmdline 含模块绝对路径（normalize 后），或含 /模块名/ 这类
        明确的模块标识。匹配不到端口的模块（如 timing-service）也能被认出来。

        只读后台快照，不阻塞主线程。
        """
        from src.core.process_runner import port_snapshot

        snap = port_snapshot()
        if not snap:
            return {}

        # 项目根路径（归一化）——用于排除与本项目无关的同名进程
        proj = (self.project_meta.path or "").replace("\\", "/").lower().rstrip("/")

        # 候选：(module, 归一化匹配键列表)
        result: dict[str, tuple[int, int]] = {}
        used_pids: set[int] = set()
        for mod_name, mod_path, _port, main_class in self.project_meta.spring_boot_modules:
            keys = self._module_match_keys(mod_name, mod_path, main_class)
            norm_cls = main_class.replace("\\", "/").lower() if main_class else ""
            best: tuple[int, int] | None = None
            for port, holders in snap.items():
                for h in holders:
                    pid = h.get("pid")
                    if pid is None or pid in used_pids:
                        continue
                    cmd = (h.get("cmdline") or "").replace("\\", "/").lower()
                    if not cmd:
                        continue
                    # 闸门：必须确认是「本项目」的进程，否则别的项目同名模块、
                    # 甚至 Chrome (...\Application\chrome.exe) 会误撞模块名。
                    # 两个可靠信号满足其一即可：
                    #   1. 命令行含本项目根路径（IDE/终端直接 java -jar、展开 classpath 的场景）
                    #   2. 命令行含本模块主类全限定名（gradle bootRun 把 classpath 塞进
                    #      Temp jar，命令行里没有项目路径，只能靠主类认）
                    in_project = bool(proj) and proj in cmd
                    in_main_class = bool(norm_cls) and norm_cls in cmd
                    if not (in_project or in_main_class):
                        continue
                    if any(k in cmd for k in keys):
                        best = (pid, port)
                        break
                if best:
                    break
            if best:
                result[mod_name] = best
                used_pids.add(best[0])
        return result

    def _module_match_keys(self, mod_name: str, mod_path: str, main_class: str = "") -> list[str]:
        """生成用于匹配进程命令行的关键字（已归一化为正斜杠小写）。"""
        keys: list[str] = []
        norm_path = mod_path.replace("\\", "/").lower().rstrip("/")
        if norm_path:
            keys.append(norm_path)
        # gradle 模块名 a:b → 路径片段 a/b；普通模块名直接作为路径片段
        seg = mod_name.replace(":", "/").lower().strip("/")
        if seg:
            keys.append(f"/{seg}/")
            keys.append(f"/{seg}.jar")
            keys.append(f"/{seg}-")  # 带版本号的 jar：timing-service-1.0.jar
        # 主类全限定名：gradle bootRun 把 classpath 塞进 Temp jar 后，进程命令行里
        # 没有项目路径，只剩主类名。这是认出本项目服务进程的关键信号，且带包名前缀
        # （com.shwhaty.xxx）不会误撞 Chrome 等无关进程。
        if main_class:
            keys.append(main_class.lower())
        return keys

    def _detect_and_apply_external(self) -> None:
        """刷新 _module_external_pids（不碰面板，仅供批量启动前去重用）。

        同时把感知到的外部端口记入 _module_ports，供 _takeover_external 精确等待
        端口释放。
        """
        external = self._detect_external_modules()
        for mod_name, _path, _port, _cls in self.project_meta.spring_boot_modules:
            r = self.module_runners.get(mod_name)
            if r and r.is_running():
                self._module_external_pids.pop(mod_name, None)
            elif mod_name in external:
                pid, port = external[mod_name]
                self._module_external_pids[mod_name] = pid
                if port:
                    self._module_ports[mod_name] = port
            else:
                self._module_external_pids.pop(mod_name, None)

    def _refresh_status_row(self) -> None:
        if self._is_multi_module:
            # 多模块：顶部只显示 "N/M 运行中"；每行运行时长更新到 ServicePanel
            external = self._detect_external_modules()
            running = 0
            for mod_name, _path, _port, _cls in self.project_meta.spring_boot_modules:
                r = self.module_runners.get(mod_name)
                if r and r.is_running():
                    # mini-ide 亲手拉起的进程优先，覆盖外部感知
                    running += 1
                    self._module_external_pids.pop(mod_name, None)
                    if self.service_panel:
                        self.service_panel.update_state(
                            mod_name, STATE_RUNNING,
                            port=self._module_ports.get(mod_name),
                            elapsed_seconds=r.elapsed_seconds(),
                        )
                    continue
                # 非自管理：runner 处于 stopping 时不抢状态，交给状态机回调
                if r and r.state() == "stopping":
                    continue
                if mod_name in external:
                    # 外部进程（AI 或终端启动）——靠端口快照按命令行匹配到
                    running += 1
                    pid, port = external[mod_name]
                    self._module_external_pids[mod_name] = pid
                    if self.service_panel:
                        self.service_panel.update_state(
                            mod_name, STATE_RUNNING_EXTERNAL, port=port,
                        )
                    continue
                # 既非自管理也无外部进程：清掉外部标记，刷成 IDLE
                self._module_external_pids.pop(mod_name, None)
                if self.service_panel:
                    row = self.service_panel._rows.get(mod_name)
                    if row and row.current_state() in (
                        STATE_RUNNING, STATE_RUNNING_EXTERNAL, STATE_STARTING,
                    ):
                        # 自管理 runner 已停时一并清掉抓到的端口
                        if not r or r.state() == "idle":
                            self._module_ports.pop(mod_name, None)
                        self.service_panel.update_state(mod_name, STATE_IDLE)
            total = len(self.project_meta.spring_boot_modules)
            self.lbl_elapsed.setText(f"已运行 {running}/{total}" if running else "")
        elif self.runner.is_running():
            elapsed = self.runner.elapsed_seconds()
            m, s = divmod(int(elapsed), 60)
            h, m = divmod(m, 60)
            self.lbl_elapsed.setText(f"已运行 {h}:{m:02d}:{s:02d}" if h else f"已运行 {m}:{s:02d}")
        else:
            self.lbl_elapsed.setText("")

        # git 分支 / 改动数 / 文件树染色：全部交给后台 worker 查，
        # 主线程不再同步跑 git 命令（否则大仓库每 3s 冻 UI 几百 ms）。
        # worker 回来后在 _on_git_status_done 里一次性渲染状态栏 + 刷染色。
        self._update_file_tree_git_colors()

    def _render_git_status_row(self, info) -> None:
        """用后台查到的 GitInfo 渲染分支按钮 + 改动数按钮（在主线程回调里调）。"""
        text = git_info.format_status(info)
        if text:
            self.btn_branch.setText(text + "  ▾")
            base = _status_btn_base_qss()
            # behind > 0 → 橙色高亮提示远程有新提交，点开菜单复制分支名
            if info and info.behind > 0:
                self.btn_branch.setStyleSheet(base + f"QToolButton {{ color:{COLOR_WARN}; }}")
                self.btn_branch.setToolTip(
                    f"远程有 {info.behind} 个新提交；点击菜单可复制分支名给 AI"
                )
            else:
                self.btn_branch.setStyleSheet(base + f"QToolButton {{ color:{FG_SECONDARY}; }}")
                self.btn_branch.setToolTip("点击选择要复制的分支名")
            self.btn_branch.setVisible(True)
        else:
            self.btn_branch.setVisible(False)

        # 改动按钮：dirty 时高亮 + 显示数字；干净时灰色「无改动」
        if info and info.branch:
            n = info.changed
            base = _status_btn_base_qss()
            if n > 0:
                self.btn_changes.setText(f"📝 {n} 处改动")
                self.btn_changes.setStyleSheet(base + f"QToolButton {{ color:{COLOR_WARN}; }}")
            else:
                self.btn_changes.setText("📝 无改动")
                self.btn_changes.setStyleSheet(base + f"QToolButton {{ color:{FG_DIM}; }}")
            self.btn_changes.setVisible(True)
        else:
            self.btn_changes.setVisible(False)

    def _update_file_tree_git_colors(self) -> None:
        # 非 git 项目：状态栏隐藏分支/改动按钮，不起 worker
        if not self._is_git_repo:
            self.btn_branch.setVisible(False)
            self.btn_changes.setVisible(False)
            return
        # 防抖：上一轮 worker 还在跑就跳过这一轮（避免堆积）。
        # 回收策略：finished→deleteLater 负责销毁 C++ 对象，done 回调里只把
        # self._git_status_worker 置 None。下一轮看到 None 起新 worker，永远不再
        # 通过该引用访问已被 deleteLater 的旧对象，因此既不泄漏也不会空壳崩溃。
        # （旧实现为怕空壳崩溃而完全不回收，结果每 3s 漏一个 QThread，长开会耗尽句柄。）
        w = self._git_status_worker
        if w is not None:
            try:
                if w.isRunning():
                    return
            except RuntimeError:
                # 极端情况下对象已被回收，直接当作空闲，重新起一轮
                self._git_status_worker = None
        worker = GitStatusWorker(self.project_meta.path, parent=self)
        worker.done.connect(self._on_git_status_done)
        worker.finished.connect(worker.deleteLater)
        self._git_status_worker = worker
        worker.start()

    def _on_git_status_done(
        self, statuses: dict, ignored: set, deleted_by_parent: dict, info,
    ) -> None:
        log.debug(
            f"[git_colors] statuses={len(statuses)}, "
            f"ignored={len(ignored)}, deleted_groups={len(deleted_by_parent)}"
        )
        self._render_git_status_row(info)
        self.file_tree.update_git_status(statuses, ignored, deleted_by_parent)
        # 本轮结束：只在仍是当前 worker 时清引用（避免极端时序下把刚起的新 worker 误置空）。
        # 对象本身由 finished→deleteLater 回收，这里不碰 C++ 生命周期。
        if self.sender() is self._git_status_worker:
            self._git_status_worker = None

    # ---- 文件跳转 ----

    def _jump_to_file(self, path: str, line: int, col: int) -> None:
        # file_open_mode=preview 总是用内置预览；auto 则先试外部编辑器
        if self.config.file_open_mode == "preview":
            self._show_preview(path, line, col)
            return
        ok = open_in_editor(
            path, line=line, column=col,
            editor_cmd=self.config.editor_cmd,
            project_root=self.project_meta.path,
        )
        if not ok:
            # 外部编辑器都不可用，回落到内置预览
            self._show_preview(path, line, col)

    def _show_preview(self, path: str, line: int, col: int) -> None:
        """在中心 tab 区打开文件：同文件已开则聚焦，否则新建 tab（默认编辑模式）"""
        from pathlib import Path
        p = Path(path)
        if not p.is_absolute():
            from src.util.editor import search_in_project
            found = search_in_project(Path(self.project_meta.path), p.name)
            if found:
                path = str(found)
        self._remember_recent(path)

        key = self._tab_key(path)
        existing = self._file_panes.get(key)
        if existing is not None:
            idx = self.center_tabs.indexOf(existing)
            if idx >= 0:
                self.center_tabs.setCurrentIndex(idx)
                if line > 0:
                    existing.goto_line(line, col)
                existing.setFocus()
                self.file_tree.reveal_path(path)
                return
            # 索引丢失（异常），从字典里清掉走新建分支
            self._file_panes.pop(key, None)

        from src.ui.file_preview import FilePreviewPane
        pane = FilePreviewPane(
            path=path, line=line, column=col,
            project_root=self.project_meta.path,
            config=self.config, parent=self.center_tabs,
        )
        pane.dirtyChanged.connect(
            lambda dirty, pn=pane: self._on_pane_dirty_changed(pn, dirty)
        )
        label = Path(path).name
        try:
            rel = str(Path(path).relative_to(self.project_meta.path))
        except ValueError:
            rel = path
        idx = self.center_tabs.addTab(pane, label)
        self.center_tabs.setTabToolTip(idx, rel)
        self._file_panes[key] = pane
        self.center_tabs.setCurrentIndex(idx)
        pane.setFocus()
        self.file_tree.reveal_path(path)

    def _on_file_created(self, path: str) -> None:
        """文件树新建文件后：直接在 tab 区打开（本来就是默认编辑模式）"""
        self._show_preview(path, 0, 0)

    def _tab_key(self, path: str) -> str:
        from pathlib import Path
        try:
            return str(Path(path).resolve()).lower()
        except OSError:
            return path.lower()

    def _on_pane_dirty_changed(self, pane, dirty: bool) -> None:
        from pathlib import Path
        idx = self.center_tabs.indexOf(pane)
        if idx <= 0:
            return
        name = Path(pane.get_path()).name
        self.center_tabs.setTabText(idx, f"{name} *" if dirty else name)

    def _on_center_tab_changed(self, index: int) -> None:
        if index <= 0:
            return
        widget = self.center_tabs.widget(index)
        if widget and hasattr(widget, "get_path"):
            path = widget.get_path()
            if path:
                self.file_tree.reveal_path(path)

    def _on_tab_close_requested(self, index: int) -> None:
        """× 按钮 / 中键 / Ctrl+W 触发。项目级日志 tab（index==0）不关。

        - 文件预览 tab：flush_save 后直接 removeTab
        - 模块日志 tab：如果进程在跑，弹确认 → 停进程；真正的 tab 移除推迟到 finished 回调里
        """
        if index == 0:
            return
        widget = self.center_tabs.widget(index)

        # 模块日志 tab
        module = self._module_of_tab(widget)
        if module is not None:
            runner = self.module_runners.get(module)
            if runner and runner.is_running():
                ret = QMessageBox.question(
                    self, "关闭模块",
                    f"「{module}」正在运行。关闭此 tab 会停止该模块的进程，继续吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    return
                self._closing_modules.add(module)
                runner.stop()
                # 不立即 removeTab，等 _on_module_finished 触发清理。
                # 兜底：进程 10 秒还没退出就强制清理 tab（子进程可能卡在 SIGTERM，
                # 或 QProcess.finished 信号丢失；UI 不能永远留个关不掉的 tab）。
                QTimer.singleShot(MODULE_STOP_TIMEOUT_MS, lambda m=module: self._force_close_module_if_stuck(m))
            else:
                self._remove_module_tab(module)
            return

        # 脚本运行 tab（文件树「▶ 运行」启动的 .bat/.cmd/.ps1/.exe）
        script_key = self._script_of_tab(widget)
        if script_key is not None:
            runner = self._script_runners.get(script_key)
            if runner and runner.is_running():
                ret = QMessageBox.question(
                    self, "关闭脚本",
                    f"脚本正在运行。关闭此 tab 会停止该进程，继续吗？\n\n{script_key}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    return
                self._closing_scripts.add(script_key)
                runner.stop()
                QTimer.singleShot(MODULE_STOP_TIMEOUT_MS, lambda k=script_key: self._force_close_script_if_stuck(k))
            else:
                self._remove_script_tab(script_key)
            return

        # 文件预览 tab
        from src.ui.file_preview import FilePreviewPane
        if isinstance(widget, FilePreviewPane):
            if not widget.flush_save():
                QMessageBox.warning(
                    self,
                    "保存失败",
                    "文件仍有未保存改动，已保留此 tab。\n\n"
                    f"{widget.get_path()}",
                )
                return
            key = self._tab_key(widget.get_path())
            self._file_panes.pop(key, None)
        self.center_tabs.removeTab(index)
        widget.deleteLater()

    def _close_current_file_tab(self) -> None:
        """Ctrl+W：关闭当前 file tab（日志 tab 被忽略）"""
        idx = self.center_tabs.currentIndex()
        if idx <= 0:
            return
        self._on_tab_close_requested(idx)

    def _reveal_log_tab(self) -> None:
        """多模块项目里日志 tab 默认隐藏；有全局消息写入时显示回来（不抢焦点）。"""
        if not self.center_tabs.isTabVisible(0):
            self.center_tabs.setTabVisible(0, True)

    def _on_tab_bar_context_menu(self, pos) -> None:
        """tabBar 右键菜单：关闭 / 关闭其它 / 关闭右侧 / 关闭左侧。

        日志 tab（index 0）永不可关，所以在筛选目标时统一过滤掉 0；
        模块 / 脚本 tab 的关闭走 _on_tab_close_requested（已弹确认 + 异步停进程）。
        """
        bar = self.center_tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            return
        total = self.center_tabs.count()

        others = [i for i in range(total) if i != index and i != 0]
        rights = [i for i in range(total) if i > index and i != 0]
        lefts = [i for i in range(total) if i < index and i != 0]

        menu = QMenu(self)
        act_close = menu.addAction("关闭")
        act_close.setEnabled(index != 0)
        menu.addSeparator()
        act_others = menu.addAction("关闭其它")
        act_others.setEnabled(bool(others))
        act_right = menu.addAction("关闭右侧")
        act_right.setEnabled(bool(rights))
        act_left = menu.addAction("关闭左侧")
        act_left.setEnabled(bool(lefts))

        chosen = menu.exec(bar.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_close:
            self._on_tab_close_requested(index)
        elif chosen is act_others:
            self._close_tab_indices(others)
        elif chosen is act_right:
            self._close_tab_indices(rights)
        elif chosen is act_left:
            self._close_tab_indices(lefts)

    def _close_tab_indices(self, indices: list[int]) -> None:
        """批量关闭一组 tab。倒序关，避免中途 index 错位。

        模块 / 脚本 tab 走异步删除（finished 回调里 removeTab），
        但倒序处理保证我们处理某个 index 时它前面（更小 index）的元素位置不变；
        异步删除发生时被删的就是当前 index 自身，对剩余待处理的更小 index 没影响。
        """
        for i in sorted(indices, reverse=True):
            if i <= 0 or i >= self.center_tabs.count():
                continue
            self._on_tab_close_requested(i)

    def _remember_recent(self, path: str) -> None:
        if not path:
            return
        if path in self._recent_files:
            self._recent_files.remove(path)
        self._recent_files.insert(0, path)
        self._recent_files = self._recent_files[:30]

    # ---- 分支操作 ----

    def _populate_branch_menu(self) -> None:
        """每次菜单弹出前重建：本地分支点击切换，右键复制。"""
        from src.core.git_ops import is_git_repo, list_branches
        menu = self._branch_menu
        menu.clear()
        if not is_git_repo(self.project_meta.path):
            act = menu.addAction("（不是 git 仓库）")
            act.setEnabled(False)
            return

        data = list_branches(self.project_meta.path)
        cur = data["current"]
        local = data["local"]
        remote = data["remote"]
        menu.set_current_branch(cur)

        menu.addAction("🔄 刷新远程分支", lambda: self._start_remote_fetch(manual=True))
        menu.addAction("🔀 合并远程分支到当前", self._show_merge_dialog)
        menu.addSeparator()

        if cur:
            act = menu.addAction(f"📋 复制当前分支：{cur}")
            act.setData(cur)
            act.triggered.connect(lambda _=False, br=cur: QApplication.clipboard().setText(br))
            menu.addSeparator()
        if local:
            head = menu.addAction("本地分支（点击切换 / 右键复制）")
            head.setEnabled(False)
            for b in local:
                label = ("● " if b == cur else "    ") + b
                act = menu.addAction(label)
                act.setData(b)
                if b == cur:
                    act.setEnabled(False)
                else:
                    act.triggered.connect(lambda _=False, br=b: self._do_checkout(br))
        if remote:
            menu.addSeparator()
            head = menu.addAction("远程分支（右键复制）")
            head.setEnabled(False)
            for b in remote:
                act = menu.addAction("    " + b)
                act.setData(b)
                act.triggered.connect(lambda _=False, br=b: QApplication.clipboard().setText(br))

    def _start_remote_fetch(self, manual: bool = False) -> None:
        """后台 git fetch。后台轮询时静默；手动点菜单（manual=True）给托盘反馈。"""
        if self._git_fetch_worker and self._git_fetch_worker.isRunning():
            if manual:
                notify.notify_info("刷新远程分支", "正在刷新，请稍候…")
            return
        from src.core.git_ops import has_upstream, is_git_repo
        path = self.project_meta.path
        if not is_git_repo(path):
            if manual:
                notify.notify_warn("刷新远程分支", "当前项目不是 git 仓库。")
            return
        if not has_upstream(path):
            if manual:
                notify.notify_warn("刷新远程分支", "当前分支没有配置远程上游，无法刷新。")
            return
        self._fetch_manual = manual
        info_before = git_info.get_info(path)
        self._fetch_behind_before = info_before.behind if info_before else 0
        if manual:
            notify.notify_info("刷新远程分支", "正在拉取远程更新…")
        self._git_fetch_worker = GitFetchWorker(path, parent=self)
        self._git_fetch_worker.done.connect(self._on_remote_fetch_done)
        self._git_fetch_worker.finished.connect(self._git_fetch_worker.deleteLater)
        self._git_fetch_worker.start()

    def _on_remote_fetch_done(self, ok: bool) -> None:
        if self.sender() is self._git_fetch_worker:
            self._git_fetch_worker = None
        manual = self._fetch_manual
        self._fetch_manual = False
        if not ok:
            if manual:
                notify.notify_error("刷新远程分支", "拉取失败，请检查网络或远程仓库权限。")
            return
        git_info.invalidate(self.project_meta.path)
        self._refresh_status_row()
        if not manual:
            return
        info = git_info.get_info(self.project_meta.path)
        behind = info.behind if info else 0
        new_commits = behind - self._fetch_behind_before
        if behind > 0:
            extra = f"，其中本次新增 {new_commits} 个" if new_commits > 0 else ""
            notify.notify_success(
                "刷新远程分支",
                f"远程领先当前分支 {behind} 个提交{extra}，可用「合并远程分支」拉取。",
            )
        else:
            notify.notify_success("刷新远程分支", "已是最新，没有需要合并的远程提交。")

    def _do_checkout(self, branch: str) -> None:
        from src.core.git_ops import is_dirty
        path = self.project_meta.path
        if is_dirty(path):
            ret = QMessageBox.question(
                self, "切换分支",
                f"本地有未提交的改动，仍要切换到 {branch} 吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        if self._git_checkout_worker and self._git_checkout_worker.isRunning():
            return
        self._git_checkout_worker = GitCheckoutWorker(path, branch, parent=self)
        self._git_checkout_worker.done.connect(self._on_checkout_done)
        self._git_checkout_worker.finished.connect(self._git_checkout_worker.deleteLater)
        self._git_checkout_worker.start()

    def _on_checkout_done(self, ok: bool, msg: str) -> None:
        if self.sender() is self._git_checkout_worker:
            self._git_checkout_worker = None
        git_info.invalidate(self.project_meta.path)
        self._refresh_status_row()
        if ok:
            info = git_info.get_info(self.project_meta.path)
            branch = info.branch if info else ""
            notify.notify_success("分支已切换", f"当前分支：{branch}")
        else:
            QMessageBox.warning(self, "切换失败", msg)

    def _show_merge_dialog(self) -> None:
        from src.core.git_ops import current_branch, list_remote_branches
        path = self.project_meta.path
        remotes = list_remote_branches(path)
        if not remotes:
            QMessageBox.information(self, "合并", "没有找到远程分支。")
            return
        cur = current_branch(path)
        items = [PickerItem(title=b, subtitle="", data=b) for b in remotes]
        from src.ui.quick_open import PickerDialog
        dlg = PickerDialog(f"合并远程分支到当前（{cur}）", self)
        dlg.set_static_items(items)
        dlg.picked.connect(self._do_merge_push)
        # 持有引用，避免局部变量回收；菜单关闭瞬间会抢焦点导致浮窗"失焦自动关闭"，
        # 用 singleShot 等菜单彻底关掉后再弹，浮窗才能拿到焦点不被立即关闭。
        self._merge_dialog = dlg
        QTimer.singleShot(0, dlg.show)

    def _do_merge_push(self, remote_branch: str) -> None:
        if self._git_merge_worker and self._git_merge_worker.isRunning():
            return
        path = self.project_meta.path
        notify.notify_info("合并远程分支", f"正在合并 {remote_branch} 并推送…")
        self._git_merge_worker = GitMergePushWorker(path, remote_branch, parent=self)
        self._git_merge_worker.done.connect(self._on_merge_push_done)
        self._git_merge_worker.finished.connect(self._git_merge_worker.deleteLater)
        self._git_merge_worker.start()

    def _on_merge_push_done(self, ok: bool, msg: str) -> None:
        if self.sender() is self._git_merge_worker:
            self._git_merge_worker = None
        git_info.invalidate(self.project_meta.path)
        self._refresh_status_row()
        if ok:
            QMessageBox.information(self, "合并推送完成", msg)
        else:
            QMessageBox.warning(self, "合并失败", msg)

    # ---- 其他 ----

    def _on_file_activated(self, path: str) -> None:
        if self.config.file_open_mode == "preview":
            self._show_preview(path, 0, 0)
            return
        ok = open_in_editor(path, editor_cmd=self.config.editor_cmd,
                            project_root=self.project_meta.path)
        if not ok:
            self._show_preview(path, 0, 0)

    def request_close(self) -> bool:
        any_running = (self.runner.is_running()
                       or self._any_module_running()
                       or self._any_script_running())
        if any_running:
            ret = QMessageBox.question(
                self, "项目正在运行",
                f"{self.project_meta.name} 正在运行，关闭会停止所有进程。确认关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return False
        # 关闭前强制把所有 pending 自动保存刷盘。保存失败时保留项目，
        # 避免 tab 被关掉后用户误以为改动已经落盘。
        failed_panes = []
        for pane in list(self._file_panes.values()):
            try:
                if not pane.flush_save():
                    failed_panes.append(pane)
            except Exception:
                failed_panes.append(pane)
        if failed_panes:
            first = failed_panes[0]
            idx = self.center_tabs.indexOf(first)
            if idx >= 0:
                self.center_tabs.setCurrentIndex(idx)
            QMessageBox.warning(
                self,
                "保存失败",
                f"有 {len(failed_panes)} 个文件未能保存，已取消关闭项目。",
            )
            return False
        if any_running:
            if self.runner.is_running():
                self.runner.stop()
            for r in self.module_runners.values():
                if r.is_running():
                    r.stop()
            for r in self._script_runners.values():
                if r.is_running():
                    r.stop()
        self._poll_timer.stop()
        self._fetch_timer.stop()
        self.indexer.stop()
        return True

    # ---- 快捷键与导航 ----

    def _register_shortcuts(self) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut
        sc_file = QShortcut(QKeySequence("Ctrl+Shift+N"), self)
        sc_file.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_file.activated.connect(self.open_file_picker)

        sc_find = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        sc_find.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_find.activated.connect(self.open_content_search)

        sc_recent = QShortcut(QKeySequence("Ctrl+E"), self)
        sc_recent.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_recent.activated.connect(self.open_recent_files)

        sc_cmd = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        sc_cmd.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_cmd.activated.connect(self.open_command_palette)

        sc_restart = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        sc_restart.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_restart.activated.connect(self._restart)

        sc_close_tab = QShortcut(QKeySequence("Ctrl+W"), self)
        sc_close_tab.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_close_tab.activated.connect(self._close_current_file_tab)

    def open_file_picker(self) -> None:
        """Ctrl+Shift+N：聚焦左侧文件树的过滤框"""
        if not self.file_tree.isVisible():
            self.file_tree.setVisible(True)
        self.file_tree.focus_filter()

    def open_content_search(self) -> None:
        dlg = ContentSearchDialog(self.project_meta.path, parent=self)
        dlg.open_requested.connect(lambda p, l, c: self._show_preview(p, l, c))
        dlg.show()

    def open_recent_files(self) -> None:
        if not self._recent_files:
            QMessageBox.information(self, "最近打开", "还没有打开过文件")
            return
        dlg = show_recent_files(
            self._recent_files, self.project_meta.path,
            on_pick=lambda p: self._show_preview(p, 0, 0),
            parent=self,
        )
        dlg.show()

    def open_command_palette(self) -> None:
        commands: list[tuple[str, str, callable]] = []

        if self._is_multi_module:
            # 多模块：模块启停走 _start_module / _stop_module；编译 / Clean 仍走 self.runner
            commands.append((
                "▶  全部启动",
                f"并行启动所有 {len(self.project_meta.spring_boot_modules)} 个模块",
                self._start_all_modules,
            ))
            for mod_name, _path, _port, _cls in self.project_meta.spring_boot_modules:
                r = self.module_runners.get(mod_name)
                if r and r.is_running():
                    commands.append((
                        f"⏹  停止 {mod_name}",
                        "停止该模块进程",
                        lambda m=mod_name: self._stop_module(m),
                    ))
                else:
                    commands.append((
                        f"▶  启动 {mod_name}",
                        "",
                        lambda m=mod_name: self._start_module(m),
                    ))
            for prof in self.project_meta.profiles:
                if prof.kind in ("compile", "clean"):
                    commands.append((
                        f"{prof.icon}  {prof.label}",
                        f"{' '.join(prof.command[:4])}  ...",
                        lambda p=prof: self._run_profile(p),
                    ))
        else:
            for prof in self.project_meta.profiles:
                commands.append((
                    f"{prof.icon}  {prof.label}",
                    f"{' '.join(prof.command[:4])}  ...",
                    lambda p=prof: self._run_profile(p),
                ))
            if self.runner.is_running():
                commands.append(("⏹  停止运行", "kill 当前进程树", self._stop))
            commands.append(("↻  重启项目", "Ctrl+Shift+R", self._restart))

        commands.append(("🔍  搜索文件名", "Ctrl+Shift+N", self.open_file_picker))
        commands.append(("🔎  搜索文件内容", "Ctrl+Shift+F", self.open_content_search))
        commands.append(("⏱  最近打开的文件", "Ctrl+E", self.open_recent_files))
        commands.append(("📁  打开项目目录", "用资源管理器", lambda: open_folder(self.project_meta.path)))
        commands.append(("🧹  清空日志", "", self.log.clear))
        commands.append(("🌿  Git 改动", "实时查看当前分支改动文件", self.open_git_viewer))
        commands.append(("🔌  端口占用查询", "查看指定端口被哪个进程占用 / kill", lambda: self.open_port_dialog(0)))
        commands.append(("📋  环境/配置文件", ".env / application*.yml 等集中查看", self.open_env_panel))
        commands.append(("ℹ️  项目信息", "路径 / 类型 / 包管理 / 主类（只读）", self.open_project_info))

        dlg = show_command_palette(commands, parent=self)
        dlg.show()

    def open_git_viewer(self) -> None:
        from src.core.git_ops import is_git_repo
        if not is_git_repo(self.project_meta.path):
            QMessageBox.information(self, "Git", "当前项目不是 git 仓库")
            return
        if self._git_viewer is not None:
            self._git_viewer.show()
            self._git_viewer.raise_()
            self._git_viewer.activateWindow()
            return
        dlg = GitViewer(self.project_meta.path, parent=self)
        self._git_viewer = dlg
        dlg.destroyed.connect(lambda: setattr(self, "_git_viewer", None))
        dlg.openFileRequested.connect(lambda p: self._show_preview(p, 0, 0))
        dlg.show()

    def open_port_dialog(self, default_port: int = 0) -> None:
        """端口占用查询对话框。default_port>0 时自动预填并查询。"""
        from src.ui.port_dialog import PortDialog
        dlg = PortDialog(default_port=default_port, parent=self)
        dlg.show()

    def open_env_panel(self) -> None:
        """环境/配置文件集中面板（只读查看入口）"""
        from src.ui.env_panel import EnvPanel
        dlg = EnvPanel(self.project_meta.path, parent=self)
        dlg.openFileRequested.connect(lambda p: self._show_preview(p, 0, 0))
        dlg.show()

    def open_project_info(self) -> None:
        """项目信息弹窗（路径/类型/包管理/主类，只读）"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        dlg = QDialog(self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.setWindowTitle(f"ℹ️ 项目信息 — {self.project_meta.name}")
        dlg.resize(440, 280)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(SettingsPanel(self.project_meta, self.entry, parent=dlg))
        dlg.show()
