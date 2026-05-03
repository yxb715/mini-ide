"""Git 变更 & 日志查看器（只读）

用 Tab 切换：「本地改动」(git status + diff) / 「提交历史」(git log + 单 commit 详情)。
做不了 commit/push/merge 这些危险操作，那些还是回 IDEA。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFile, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QSyntaxHighlighter
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from src.core import git_ops
from src.ui.theme import (
    FG_SECONDARY, GIT_ADD, GIT_DEL, GIT_FILE_HEAD, GIT_HUNK, GIT_META,
    GIT_MODIFY,
)


class DiffHighlighter(QSyntaxHighlighter):
    """对 diff 输出上色：+绿、-红、@蓝、index 灰"""

    def __init__(self, document):
        super().__init__(document)
        self._fmt_add = self._mk(GIT_ADD)
        self._fmt_del = self._mk(GIT_DEL)
        self._fmt_hunk = self._mk(GIT_HUNK, bold=True)
        self._fmt_meta = self._mk(GIT_META, italic=True)
        self._fmt_file = self._mk(GIT_FILE_HEAD, bold=True)

    @staticmethod
    def _mk(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Weight.DemiBold)
        if italic:
            fmt.setFontItalic(True)
        return fmt

    def highlightBlock(self, text: str) -> None:
        if not text:
            return
        if text.startswith("diff --git") or text.startswith("+++ ") or text.startswith("--- "):
            self.setFormat(0, len(text), self._fmt_file)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self._fmt_hunk)
        elif text.startswith("+") and not text.startswith("+++"):
            self.setFormat(0, len(text), self._fmt_add)
        elif text.startswith("-") and not text.startswith("---"):
            self.setFormat(0, len(text), self._fmt_del)
        elif text.startswith("index ") or text.startswith("new file") or text.startswith("deleted file"):
            self.setFormat(0, len(text), self._fmt_meta)


class GitViewer(QDialog):

    # 用户选择「跳转到文件」：参数是绝对路径。ProjectTab 连接这里打开内嵌 tab
    openFileRequested = Signal(str)

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self.root = git_ops.repo_root(project_root)
        self.setWindowTitle(f"Git 查看器 — {self.root}")
        self.resize(1200, 780)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        header = QHBoxLayout()
        branch = git_ops.current_branch(self.root)
        lbl = QLabel(
            f"🌿 {branch or '(无分支)'} "
            f"<span style='color:{FG_SECONDARY};'>@ {self.root}</span>"
        )
        lbl.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(lbl, 1)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._reload)
        header.addWidget(btn_refresh)
        root.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_changes_tab(), "本地改动")
        self.tabs.addTab(self._build_log_tab(), "提交历史")
        root.addWidget(self.tabs, 1)

        self._reload()

    # ---- 改动 Tab ----

    def _build_changes_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        split = QSplitter(Qt.Orientation.Horizontal)

        self.changes_list = QListWidget()
        self.changes_list.setMinimumWidth(260)
        self.changes_list.currentItemChanged.connect(self._on_change_selected)
        self.changes_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.changes_list.customContextMenuRequested.connect(self._on_changes_context_menu)
        split.addWidget(self.changes_list)

        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        f = QFont()
        f.setFamilies(["Cascadia Mono", "Consolas"])
        f.setPointSize(10)
        self.diff_view.setFont(f)
        DiffHighlighter(self.diff_view.document())
        split.addWidget(self.diff_view)

        split.setSizes([280, 900])
        lay.addWidget(split)
        return w

    def _load_changes(self) -> None:
        self.changes_list.clear()
        files = git_ops.list_changed_files(self.root)
        if not files:
            self.diff_view.setPlainText("(工作区很干净，无改动)")
            return
        for f in files:
            prefix = "+ " if f.status.startswith("?") else f.status + " "
            item = QListWidgetItem(f"{prefix}{f.path}")
            item.setData(Qt.ItemDataRole.UserRole, f)
            color = GIT_ADD if f.status.startswith("?") else (
                GIT_DEL if "D" in f.status else GIT_MODIFY
            )
            item.setForeground(QColor(color))
            self.changes_list.addItem(item)
        self.changes_list.setCurrentRow(0)

    def _on_change_selected(self, current, _prev) -> None:
        if not current:
            self.diff_view.setPlainText("")
            return
        f: git_ops.ChangedFile = current.data(Qt.ItemDataRole.UserRole)
        if f.status.startswith("?"):
            text = git_ops.get_untracked_preview(self.root, f.path)
        else:
            # 组合 staged + unstaged
            parts = []
            if f.is_staged:
                parts.append("=== Staged ===\n" + git_ops.get_file_diff(self.root, f.path, staged=True))
            if f.is_unstaged:
                parts.append("=== Unstaged ===\n" + git_ops.get_file_diff(self.root, f.path, staged=False))
            text = "\n\n".join(parts) or "(无 diff 内容)"
        self.diff_view.setPlainText(text)

    def _on_changes_context_menu(self, pos) -> None:
        item = self.changes_list.itemAt(pos)
        if item is None:
            return
        f: git_ops.ChangedFile = item.data(Qt.ItemDataRole.UserRole)
        abs_path = Path(self.root) / f.path
        file_exists = abs_path.exists() and abs_path.is_file()

        menu = QMenu(self.changes_list)
        act_jump = menu.addAction("↗ 跳转到文件", lambda: self._jump_to_file(f))
        if not file_exists:
            act_jump.setEnabled(False)
            act_jump.setText("↗ 跳转到文件（已删除）")
        menu.addSeparator()
        menu.addAction("↺ 还原到远程版本（@{u}）", lambda: self._restore_changed_file(f))
        menu.exec(self.changes_list.viewport().mapToGlobal(pos))

    def _jump_to_file(self, f: git_ops.ChangedFile) -> None:
        abs_path = Path(self.root) / f.path
        if not abs_path.is_file():
            QMessageBox.information(
                self, "跳转失败",
                f"文件不存在或不可打开：\n\n{abs_path}",
            )
            return
        self.openFileRequested.emit(str(abs_path))
        # 打开 tab 后关闭 viewer，让主界面露出来
        self.accept()

    def _restore_changed_file(self, f: git_ops.ChangedFile) -> None:
        if not git_ops.has_upstream(self.root):
            QMessageBox.warning(
                self, "无法还原",
                "当前分支未配置远程 upstream（@{u}），无法还原到远程版本。\n"
                "请先 push 建立上游，或手动切到有 upstream 的分支。",
            )
            return

        is_untracked = f.status.startswith("?")
        in_upstream = False if is_untracked else git_ops.file_exists_in_upstream(self.root, f.path)

        if is_untracked:
            mode = "trash_only"
            msg = (
                f"该文件未入库，远程 @{{u}} 里没有对应版本。\n"
                f"将把以下文件移到系统回收站：\n\n{f.path}"
            )
        elif in_upstream:
            mode = "checkout"
            msg = (
                f"将把以下文件还原到远程 @{{u}} 版本，丢弃所有本地改动（含已暂存）：\n\n{f.path}\n\n"
                f"操作不可撤销。"
            )
        else:
            mode = "unstage_trash"
            msg = (
                f"该文件在远程 @{{u}} 上不存在（本地新增且未 push）。\n"
                f"将从暂存区移除并把文件移到系统回收站：\n\n{f.path}"
            )

        ret = QMessageBox.question(
            self, "还原确认", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        if mode == "checkout":
            ok, err = git_ops.restore_file_to_upstream(self.root, f.path)
            if not ok:
                QMessageBox.warning(self, "还原失败", f"{f.path}\n\n{err or '(git 未返回错误信息)'}")
                return
        else:
            if mode == "unstage_trash" and f.is_staged:
                ok, err = git_ops.unstage_file(self.root, f.path)
                if not ok:
                    QMessageBox.warning(self, "还原失败", f"移除暂存失败：{f.path}\n\n{err}")
                    return
            abs_path = str(Path(self.root) / f.path)
            if Path(abs_path).exists():
                try:
                    ok, _ = QFile.moveToTrash(abs_path)
                except (OSError, RuntimeError) as e:
                    QMessageBox.warning(self, "还原失败", f"{f.path}\n\n{e}")
                    return
                if not ok:
                    QMessageBox.warning(self, "还原失败", f"无法将以下路径移到回收站：\n\n{abs_path}")
                    return

        self._load_changes()

    # ---- 日志 Tab ----

    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        split = QSplitter(Qt.Orientation.Horizontal)

        self.log_list = QListWidget()
        self.log_list.setMinimumWidth(420)
        self.log_list.currentItemChanged.connect(self._on_commit_selected)
        split.addWidget(self.log_list)

        self.commit_view = QPlainTextEdit()
        self.commit_view.setReadOnly(True)
        self.commit_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        f = QFont()
        f.setFamilies(["Cascadia Mono", "Consolas"])
        f.setPointSize(10)
        self.commit_view.setFont(f)
        DiffHighlighter(self.commit_view.document())
        split.addWidget(self.commit_view)

        split.setSizes([440, 760])
        lay.addWidget(split)
        return w

    def _load_log(self) -> None:
        self.log_list.clear()
        commits = git_ops.get_log(self.root, limit=80)
        if not commits:
            self.commit_view.setPlainText("(无提交历史)")
            return
        for c in commits:
            refs = f"  [{c.refs}]" if c.refs else ""
            text = f"{c.sha}  {c.date}  {c.author}\n  {c.subject}{refs}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.log_list.addItem(item)
        self.log_list.setCurrentRow(0)

    def _on_commit_selected(self, current, _prev) -> None:
        if not current:
            self.commit_view.setPlainText("")
            return
        c: git_ops.Commit = current.data(Qt.ItemDataRole.UserRole)
        self.commit_view.setPlainText(git_ops.get_commit_diff(self.root, c.sha_full))

    def _reload(self) -> None:
        self._load_changes()
        self._load_log()
