"""主窗口：Tab 容器 + 菜单栏 + 状态栏 + 空状态切换"""
from __future__ import annotations

import time
from pathlib import Path

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QMainWindow, QMessageBox,
    QStackedWidget, QTabWidget,
)

from src.core.config import AppConfig, ProjectEntry
from src.core.project_detector import detect_project
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

        import sys as _sys
        res_dir = Path(__file__).resolve().parents[2] / "src" / "resources"
        _icon_name = "icon.icns" if _sys.platform == "darwin" else "icon.ico"
        icon_path = res_dir / _icon_name
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
        self.stack.addWidget(self.tabs)

        self._build_menu()
        self._build_statusbar()

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

    def _build_menu(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("文件(&F)")
        open_act = QAction("打开项目...", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._open_dialog)
        file_menu.addAction(open_act)

        self.recent_menu = file_menu.addMenu("最近打开")
        self._rebuild_recent_menu()

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

    def open_project(self, path: str) -> ProjectTab | None:
        p = Path(path)
        if not p.is_dir():
            log.warning("尝试打开无效路径: %s", path)
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
        self._switch_view()
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
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if w and hasattr(w, "request_close"):
                if not w.request_close():
                    e.ignore()
                    return
        self._persist_geometry()
        self._persist_tabs()
        self.config.save()
        super().closeEvent(e)
        # 强制退出进程：notify.py 创建的 QSystemTrayIcon 在一次系统通知后会
        # 永远持有，Qt 不会因为主窗口关闭而 quit，导致进程残留（pythonw.exe 一直在）。
        # 单实例机制看到残留进程又会把新启动转发给它，用户感觉"代码改了没生效"。
        QApplication.quit()

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

    def _startup_finalize(self) -> None:
        """事件循环起来后的收尾：先恢复上次会话，再打开命令行传入的初始项目。"""
        if self.config.restore_tabs_on_startup and self.config.active_tabs:
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

