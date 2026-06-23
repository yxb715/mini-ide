"""主窗口：Tab 容器 + 菜单栏 + 状态栏 + 空状态切换"""
from __future__ import annotations

import time
from pathlib import Path

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtGui import (
    QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QIcon,
    QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QStackedWidget, QTabWidget,
)

from src.core.config import AppConfig, ProjectEntry
from src.core.project_detector import detect_project
from src.core.service_state import STATE_RUNNING_EXTERNAL, STATE_RUNNING_MANAGED
from src.core.workspace_manager import (
    close_workspace_paths, ensure_workspace_candidates, mark_workspace_opened,
    save_workspace,
)
from src.ui.empty_state import EmptyState
from src.ui.project_tab import ProjectTab
from src.util import app_log
from src.util.editor import open_folder

log = app_log.get_logger("main_window")


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.setWindowTitle("mini-ide")
        self.resize(1400, 860)
        self.setMinimumSize(960, 600)
        self.setAcceptDrops(True)

        res_dir = Path(__file__).resolve().parents[2] / "src" / "resources"
        icon_path = res_dir / "icon.ico"
        if not icon_path.exists():
            icon_path = res_dir / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._restore_geometry()

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.empty = EmptyState(config)
        self.empty.projectRequested.connect(self.open_project)
        self.stack.addWidget(self.empty)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.tabBar().tabMoved.connect(lambda _from, _to: self._save_tab_session())
        self.tabs.currentChanged.connect(lambda _index: self._save_tab_session())
        self.stack.addWidget(self.tabs)

        self._build_menu()
        self._build_statusbar()
        self._register_global_shortcuts()

        self._mem_timer = QTimer(self)
        self._mem_timer.setInterval(2000)
        self._mem_timer.timeout.connect(self._refresh_status)
        self._mem_timer.start()

        self._switch_view()

        # 命令行传入的初始项目路径（由 main.py 或单实例 IPC 写入）
        self.pending_initial_project: str | None = None

        # 延迟到事件循环起来后统一收尾：先恢复会话，再处理 pending 初始项目
        QTimer.singleShot(100, self._startup_finalize)

    # ---- 菜单 ----

    def _register_global_shortcuts(self) -> None:
        """窗口级快捷键统一在这里注册一次，触发时路由到当前可见的项目 tab。

        放在主窗口（而非每个 ProjectTab）注册的原因：
        - WindowShortcut 作用域 → 焦点在工具栏/标签栏/任意子组件都能触发，
          不用先点一下内容区（这正是之前"得先聚焦才生效"的根因）
        - 若每个 tab 各自注册窗口级快捷键，多 tab 时同窗口内同一序列重复，
          Qt 会判定歧义导致全部失效；集中注册一份就没有这个问题
        """
        # (序列, 调用当前 tab 的哪个方法名)
        bindings = [
            ("Ctrl+Shift+N", "open_file_picker"),
            ("Ctrl+Shift+F", "open_content_search"),
            ("Ctrl+E", "open_recent_files"),
            ("Ctrl+Shift+P", "open_command_palette"),
            ("Ctrl+\\", "open_endpoint_picker"),
            ("Ctrl+Shift+R", "restart_project"),
            ("Ctrl+W", "close_current_file_tab"),
        ]
        for seq, method in bindings:
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(lambda m=method: self._dispatch_to_current_tab(m))

    def _dispatch_to_current_tab(self, method_name: str) -> None:
        """把快捷键动作转发给当前可见的项目 tab。没有打开项目时静默忽略。"""
        tab = self.tabs.currentWidget()
        if isinstance(tab, ProjectTab):
            fn = getattr(tab, method_name, None)
            if callable(fn):
                fn()

    def _build_menu(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("文件(&F)")
        open_act = QAction("打开项目...", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._open_dialog)
        file_menu.addAction(open_act)

        self.recent_menu = file_menu.addMenu("最近打开")
        self._rebuild_recent_menu()
        self.workspace_menu = file_menu.addMenu("工作区")
        self._rebuild_workspace_menu()

        file_menu.addSeparator()
        quit_act = QAction("退出", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        help_menu = mb.addMenu("帮助(&H)")
        open_log_act = QAction("打开 mini-ide 日志", self)
        open_log_act.triggered.connect(self._open_app_log)
        help_menu.addAction(open_log_act)
        open_log_dir_act = QAction("打开日志目录", self)
        open_log_dir_act.triggered.connect(self._open_log_dir)
        help_menu.addAction(open_log_dir_act)
        help_menu.addSeparator()
        about = QAction("关于", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.clear()
        for entry in self.config.recent_projects[:15]:
            act = QAction(f"{entry.name or Path(entry.path).name}  -  {entry.path}", self)
            act.triggered.connect(lambda _, p=entry.path: self.open_project(p))
            self.recent_menu.addAction(act)
        if not self.config.recent_projects:
            empty_act = QAction("(无)", self)
            empty_act.setEnabled(False)
            self.recent_menu.addAction(empty_act)

    def _rebuild_workspace_menu(self) -> None:
        self.workspace_menu.clear()
        save_act = QAction("保存当前 Tab 为工作区...", self)
        save_act.triggered.connect(self._save_current_tabs_as_workspace)
        self.workspace_menu.addAction(save_act)
        close_act = QAction("关闭当前工作区", self)
        close_act.triggered.connect(self._close_current_workspace)
        self.workspace_menu.addAction(close_act)
        self.workspace_menu.addSeparator()

        if ensure_workspace_candidates(self.config):
            self.config.save()
        if not self.config.workspaces:
            empty = QAction("(无工作区)", self)
            empty.setEnabled(False)
            self.workspace_menu.addAction(empty)
            return
        for ws in self.config.workspaces[:15]:
            act = QAction(f"{ws.name}  ({len(ws.paths)} 项目)", self)
            act.triggered.connect(lambda _, name=ws.name: self.open_workspace(name))
            self.workspace_menu.addAction(act)

    def _save_current_tabs_as_workspace(self) -> None:
        paths = self._current_project_paths()
        if not paths:
            QMessageBox.information(self, "工作区", "当前没有打开的项目。")
            return
        default_name = Path(Path(paths[0]).parent).name if paths else "workspace"
        name, ok = QInputDialog.getText(self, "保存工作区", "工作区名称：", text=default_name)
        name = name.strip()
        if not ok or not name:
            return
        save_workspace(self.config, name, paths)
        self.config.save()
        self._rebuild_workspace_menu()

    def _current_project_paths(self) -> list[str]:
        paths: list[str] = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, ProjectTab):
                paths.append(w.project_meta.path)
        return paths

    def open_workspace(self, name: str) -> None:
        ws = self.config.find_workspace(name)
        if not ws:
            QMessageBox.warning(self, "工作区", f"未找到工作区：{name}")
            return
        opened = 0
        for path in ws.paths:
            if Path(path).is_dir() and self.open_project(path):
                opened += 1
        mark_workspace_opened(self.config, ws)
        self.config.save()
        self._rebuild_workspace_menu()
        log.info("打开工作区: %s | %d/%d", name, opened, len(ws.paths))

    def _close_current_workspace(self) -> None:
        paths = close_workspace_paths(self.config, self._current_project_paths())
        for i in range(self.tabs.count() - 1, -1, -1):
            w = self.tabs.widget(i)
            if isinstance(w, ProjectTab) and w.project_meta.path in paths:
                self._close_tab(i)
        self.config.save()
        self._rebuild_workspace_menu()

    # ---- 状态栏 ----

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.lbl_project = QLabel("")
        self.lbl_mem = QLabel("")
        sb.addWidget(self.lbl_project, 1)
        sb.addPermanentWidget(self.lbl_mem)

    def _refresh_status(self) -> None:
        if self.config.show_memory_usage:
            try:
                rss_mb = psutil.Process().memory_info().rss / 1024 / 1024
                self.lbl_mem.setText(f"mini-ide: {rss_mb:.0f} MB")
            except psutil.Error:
                self.lbl_mem.setText("")

        tab = self.tabs.currentWidget()
        if tab and hasattr(tab, "project_meta"):
            meta = tab.project_meta
            self.lbl_project.setText(f"{meta.project_type}  |  {meta.path}")
        else:
            self.lbl_project.setText("")

    # ---- Tab 管理 ----

    def _switch_view(self) -> None:
        self.stack.setCurrentIndex(1 if self.tabs.count() > 0 else 0)

    def _open_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择项目目录", self._default_dialog_dir()
        )
        if path:
            self.open_project(path)

    def _default_dialog_dir(self) -> str:
        """对话框默认落脚点：最近项目的父目录 > 配置的 default_project_dir > 家目录"""
        if self.config.recent_projects:
            parent = str(Path(self.config.recent_projects[0].path).parent)
            if Path(parent).is_dir():
                return parent
        if self.config.default_project_dir and Path(self.config.default_project_dir).is_dir():
            return self.config.default_project_dir
        return str(Path.home())

    def open_project(self, path: str, quiet: bool = False) -> ProjectTab | None:
        p = Path(path)
        if not p.is_dir():
            log.warning("尝试打开无效路径: %s", path)
            if not quiet:
                QMessageBox.warning(self, "路径无效", f"{path} 不是有效目录")
            return None

        existing = self.find_tab_by_path(str(p))
        if existing:
            self.tabs.setCurrentWidget(existing)
            return existing

        try:
            meta = detect_project(str(p))
        except Exception:
            log.exception("项目识别失败: %s", p)
            if not quiet:
                QMessageBox.warning(self, "识别失败", f"无法识别项目类型\n{p}")
            return None
        log.info("打开项目: %s | 类型=%s | port=%s | profiles=%s",
                 p, meta.project_type, meta.default_port, [x.name for x in meta.profiles])

        entry = self.config.find_project(str(p)) or ProjectEntry(path=str(p))
        entry.name = meta.name
        entry.project_type = meta.project_type
        entry.last_opened_at = time.time()
        self.config.touch_project(entry)
        self.config.save()

        tab = ProjectTab(meta, entry, self.config)
        tab.path = str(p)
        idx = self.tabs.addTab(tab, f"{meta.icon}  {meta.name}")
        self.tabs.setTabToolTip(idx, str(p))
        self.tabs.setCurrentIndex(idx)
        self._rebuild_recent_menu()
        self._rebuild_workspace_menu()
        self._switch_view()
        self._save_tab_session()
        return tab

    def find_tab_by_path(self, path: str):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if getattr(w, "path", None) == path:
                return w
        return None

    def focus_tab(self, tab) -> None:
        idx = self.tabs.indexOf(tab)
        if idx >= 0:
            self.tabs.setCurrentIndex(idx)

    def _close_tab(self, index: int) -> None:
        w = self.tabs.widget(index)
        if w and hasattr(w, "request_close"):
            if not w.request_close():
                return
        self.tabs.removeTab(index)
        self._switch_view()
        self._save_tab_session()

    def close_project_tab(self, tab: ProjectTab, quiet: bool = False) -> bool:
        """CLI/GUI 共用的项目关闭入口；quiet=True 时不弹消息框。"""
        idx = self.tabs.indexOf(tab)
        if idx < 0:
            return False
        if not tab.request_close(confirm_running=not quiet, quiet=quiet):
            return False
        self.tabs.removeTab(idx)
        self._switch_view()
        self._save_tab_session()
        return True

    # ---- 其他 ----

    def _about(self) -> None:
        from src.ui.theme import FG_SECONDARY
        QMessageBox.about(
            self,
            "关于 mini-ide",
            "<h3>mini-ide</h3>"
            "<p>AI 时代的开发指挥台</p>"
            "<p>Spring Boot / Vue / Python 项目的轻量启动器</p>"
            f"<p style='color:{FG_SECONDARY};'>v0.1.0</p>",
        )

    def _open_app_log(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        path = app_log.log_path_today()
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.information(self, "日志", f"当前日志文件尚未生成:\n{path}")

    def _open_log_dir(self) -> None:
        from src.core.config import LOG_DIR
        open_folder(str(LOG_DIR))

    def _restore_geometry(self) -> None:
        g = self.config.window_geometry
        # 首次启动（无任何记录）：默认最大化，撑满屏幕
        if not g:
            self.showMaximized()
            return
        # 上次是最大化关闭：继续最大化
        if g.get("maximized"):
            self.showMaximized()
            return
        # 否则按上次的尺寸/位置恢复
        if g.get("w") and g.get("h"):
            self.resize(g["w"], g["h"])
            if "x" in g and "y" in g:
                self.move(g["x"], g["y"])

    def _persist_geometry(self) -> None:
        # 最大化状态下 geometry() 返回的是恢复后的尺寸，不存为常规尺寸
        if self.isMaximized():
            self.config.window_geometry = {"maximized": True}
            return
        g = self.geometry()
        self.config.window_geometry = {
            "x": g.x(), "y": g.y(), "w": g.width(), "h": g.height(),
            "maximized": False,
        }

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path and Path(path).is_dir():
                self.open_project(path)
                break

    def closeEvent(self, e: QCloseEvent) -> None:
        running = self._collect_running_services()
        stop_running = True
        if running:
            choice = self._confirm_close_with_services(running)
            if choice == "cancel":
                e.ignore()
                return
            stop_running = choice == "stop"

        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if w and hasattr(w, "request_close"):
                if not w.request_close(confirm_running=False, stop_running=stop_running):
                    e.ignore()
                    return
        self._persist_geometry()
        self._persist_tabs()
        self.config.save()
        if running:
            kept = [
                x for x in running
                if not stop_running and x.get("state") == STATE_RUNNING_EXTERNAL
            ]
            log.info(
                "退出前服务处理：running=%d stop=%s kept_external=%d",
                len(running), stop_running, len(kept),
            )
        super().closeEvent(e)
        # 强制退出进程：notify.py 创建的 QSystemTrayIcon 在一次系统通知后会
        # 永远持有，Qt 不会因为主窗口关闭而 quit，导致进程残留（pythonw.exe 一直在）。
        # 单实例机制看到残留进程又会把新启动转发给它，用户感觉"代码改了没生效"。
        QApplication.quit()

    def _collect_running_services(self) -> list[dict]:
        items: list[dict] = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, ProjectTab):
                try:
                    items.extend(w.running_service_items(refresh_external=True))
                except Exception:
                    log.exception("收集运行服务失败: %s", w.project_meta.name)
        return items

    def _confirm_close_with_services(self, items: list[dict]) -> str:
        """返回 stop / keep_external / cancel。"""
        managed = [x for x in items if x.get("state") == STATE_RUNNING_MANAGED]
        external = [x for x in items if x.get("state") == STATE_RUNNING_EXTERNAL]
        lines = []
        for item in items[:12]:
            port = f":{item.get('port')}" if item.get("port") else ""
            pid = f" PID {item.get('pid')}" if item.get("pid") else ""
            label = "外部" if item.get("state") == STATE_RUNNING_EXTERNAL else "托管"
            lines.append(
                f"- [{label}] {item.get('project')} / {item.get('module')}{port}{pid}"
            )
        if len(items) > 12:
            lines.append(f"... 还有 {len(items) - 12} 个")
        msg = (
            "当前仍有服务在运行：\n\n"
            + "\n".join(lines)
            + "\n\n选择「全部停止后退出」会先停止这些服务；"
        )
        if external and not managed:
            msg += "也可以选择「保留外部服务退出」。"
        elif external and managed:
            msg += "托管服务必须停止；外部服务如需保留，请先手动停止托管服务后再退出。"
        else:
            msg += "当前没有可保留的外部服务。"

        box = QMessageBox(self)
        box.setWindowTitle("退出前检查")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(msg)
        stop_btn = box.addButton("全部停止后退出", QMessageBox.ButtonRole.AcceptRole)
        keep_btn = None
        if external and not managed:
            keep_btn = box.addButton("保留外部服务退出", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(stop_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is stop_btn:
            return "stop"
        if keep_btn is not None and clicked is keep_btn:
            return "keep_external"
        if clicked is cancel_btn:
            return "cancel"
        return "cancel"

    # ---- Tab 会话持久化 ----

    def _persist_tabs(self) -> None:
        """把当前 Tab 列表记下来，下次启动恢复"""
        active: list[str] = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if hasattr(w, "project_meta"):
                active.append(f"project:{w.project_meta.path}")
        self.config.active_tabs = active
        self.config.active_tab_index = max(0, self.tabs.currentIndex())
        log.info("保存 Tab 会话: %d 个 (current=%d)", len(active), self.config.active_tab_index)

    def _save_tab_session(self) -> None:
        """Tab 增删、切换、拖动后立即保存，避免异常退出时恢复旧会话。"""
        self._persist_tabs()
        self.config.save()

    def _startup_finalize(self) -> None:
        """事件循环起来后的收尾：先恢复上次会话，再打开命令行传入的初始项目。"""
        if self.config.startup_restore_mode == "workspace" and self.config.active_workspace_name:
            self.open_workspace(self.config.active_workspace_name)
        elif (self.config.startup_restore_mode == "last_session"
              and self.config.restore_tabs_on_startup and self.config.active_tabs):
            self._restore_tabs()
        if self.pending_initial_project:
            self.open_project(self.pending_initial_project)
            self.pending_initial_project = None

    def activate_and_open(self, path: str | None) -> None:
        """单实例 IPC 入口：老实例收到新请求后的响应。"""
        if path:
            self.open_project(path)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _restore_tabs(self) -> None:
        entries = list(self.config.active_tabs)
        if not entries:
            return
        log.info("恢复 Tab 会话: %d 个", len(entries))
        target_index = self.config.active_tab_index
        opened = 0
        for token in entries:
            kind, _, key = token.partition(":")
            if not key or kind != "project":
                continue
            try:
                if Path(key).is_dir():
                    self.open_project(key)
                    opened += 1
                else:
                    log.warning("跳过不存在的项目: %s", key)
            except Exception:
                log.exception("恢复 Tab 失败: %s", token)

        if opened and target_index < self.tabs.count():
            self.tabs.setCurrentIndex(target_index)

