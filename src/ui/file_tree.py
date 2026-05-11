"""左侧文件树

只读浏览项目目录。懒加载子目录（避免大项目卡顿）。
过滤框用 FileIndexer 全局搜索，不需要展开目录。
单击目录行即可展开，不用非得点小箭头。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QFile, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QStackedWidget,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from src.ui.theme import (
    ACCENT_SUBTLE, BG_L2, BG_L4, FG_BRIGHT, FG_PRIMARY, apply_search_style,
)
from src.util.editor import open_in_editor, open_folder, reveal_in_explorer


# 永远隐藏：只藏操作系统垃圾文件。构建产物/依赖目录（build/dist/node_modules/.git 等）
# 都照常显示，跟资源管理器保持一致——目录树是懒加载的，显示成本几乎为零；
# 搜索性能由 FileIndexer 自己的 ignored_dirs 另行保护。
_ALWAYS_HIDDEN = {
    ".DS_Store", "Thumbs.db", "desktop.ini",
}


# QTreeView 的箭头/行样式：更大的分支指示器 + 稍高的行，点起来不费眼
_TREE_STYLE = f"""
QTreeWidget {{
    background:{BG_L2};
    color:{FG_PRIMARY};
    border:none;
    outline:none;
}}
QTreeWidget::item {{
    height: 22px;
    padding: 1px 2px;
}}
QTreeWidget::item:hover {{ background:{BG_L4}; }}
QTreeWidget::item:selected {{ background:{ACCENT_SUBTLE}; color:{FG_BRIGHT}; }}
QTreeWidget::branch {{
    background:{BG_L2};
}}
QTreeWidget::branch:has-children:!has-siblings:closed,
QTreeWidget::branch:closed:has-children:has-siblings {{
    image: none;
    border-image: none;
    background: url(x);
}}
"""


# 右键菜单"▶ 运行"显示的可执行脚本类型（按用户场景：bat/cmd/ps1/exe）
_RUNNABLE_SCRIPT_EXTS = {".bat", ".cmd", ".ps1", ".exe"}


class _MultiSelectTree(QTreeWidget):
    """ExtendedSelection 下右键点击时保存 selection 快照。

    Qt 默认 mousePressEvent 会改写 selection（右键点未选中项 → 替换为单项；
    点已多选项理论上保留，但 PySide6 在 Windows 上观察到也被替换）。这里在 super
    调用之前先抓快照，customContextMenuRequested 触发时优先用快照而不是
    selectedItems() 的实时值，保证菜单操作的是用户右键瞬间的真实选区。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._right_press_snapshot: list = []

    def mousePressEvent(self, event):
        # 右键 press：在 super 改 selection 之前先抓快照
        # （super 还是要调，否则 contextMenuEvent / customContextMenuRequested 不会触发）
        if event.button() == Qt.MouseButton.RightButton:
            idx = self.indexAt(event.pos())
            item = self.itemFromIndex(idx) if idx.isValid() else None
            current = self.selectedItems()
            if item is not None and len(current) > 1:
                clicked_path = item.data(0, Qt.ItemDataRole.UserRole)
                selected_paths = {it.data(0, Qt.ItemDataRole.UserRole) for it in current}
                if clicked_path in selected_paths:
                    self._right_press_snapshot = list(current)
                else:
                    self._right_press_snapshot = []
            else:
                self._right_press_snapshot = []
        super().mousePressEvent(event)

    def take_right_press_snapshot(self) -> list:
        snap = self._right_press_snapshot
        self._right_press_snapshot = []
        return snap


class FileTree(QWidget):

    fileActivated = Signal(str)          # 双击文件时（带绝对路径）
    openInEditorRequested = Signal(str)  # 右键→用编辑器打开
    fileCreated = Signal(str)            # 右键→新建文件，参数=新文件绝对路径
    scriptRunRequested = Signal(str)     # 右键→运行脚本（.bat/.cmd/.ps1/.exe），参数=脚本绝对路径

    def __init__(self, root_path: str, indexer=None, project_type: str = "", parent=None):
        """indexer 是 FileIndexer 实例；有它时，过滤框使用全局索引搜索。
        project_type 用来决定右键菜单里弹哪个 JetBrains IDE（IDEA / WebStorm / PyCharm）。
        """
        super().__init__(parent)
        self.root_path = Path(root_path)
        self.indexer = indexer
        self.project_type = project_type
        self.setMinimumWidth(240)
        self._build()
        self._reload()

    def focus_filter(self) -> None:
        """供外部（如 Ctrl+Shift+N）调用：聚焦过滤框并全选"""
        self.filter_input.setFocus()
        self.filter_input.selectAll()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部过滤
        top = QFrame()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(6, 4, 6, 4)
        tl.setSpacing(4)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("过滤文件名（全项目）")
        apply_search_style(self.filter_input)
        self.filter_input.textChanged.connect(self._apply_filter)
        tl.addWidget(self.filter_input, 1)

        btn_refresh = QToolButton()
        btn_refresh.setText("↻")
        btn_refresh.setToolTip("刷新文件树")
        btn_refresh.clicked.connect(self._reload)
        tl.addWidget(btn_refresh)

        btn_expand = QToolButton()
        btn_expand.setText("＋")
        btn_expand.setToolTip("展开全部（谨慎，大项目可能慢）")
        btn_expand.clicked.connect(self._expand_all_safe)
        tl.addWidget(btn_expand)

        btn_collapse = QToolButton()
        btn_collapse.setText("－")
        btn_collapse.setToolTip("折叠全部")
        btn_collapse.clicked.connect(lambda: self.tree.collapseAll())
        tl.addWidget(btn_collapse)

        root.addWidget(top)

        # 主体：树 + 搜索结果列表切换
        self.stack = QStackedWidget()

        # 树
        self.tree = _MultiSelectTree()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setIndentation(18)
        self.tree.setAnimated(False)
        self.tree.setExpandsOnDoubleClick(False)
        # Ctrl/Shift 多选：批量删除/复制路径等
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setStyleSheet(_TREE_STYLE)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        # 单击目录就展开/折叠，比点小箭头友好
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemDoubleClicked.connect(self._on_item_activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.stack.addWidget(self.tree)

        # 搜索结果扁平列表
        self.search_list = QListWidget()
        self.search_list.setStyleSheet(
            f"QListWidget {{ background:{BG_L2}; color:{FG_PRIMARY}; border:none; outline:none; }}"
            f"QListWidget::item {{ padding: 6px 10px; }}"
            f"QListWidget::item:hover {{ background:{BG_L4}; }}"
            f"QListWidget::item:selected {{ background:{ACCENT_SUBTLE}; color:{FG_BRIGHT}; }}"
        )
        self.search_list.itemActivated.connect(self._on_search_item_activated)
        self.search_list.itemClicked.connect(self._on_search_item_activated)
        self.search_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.search_list.customContextMenuRequested.connect(self._on_search_context_menu)
        self.stack.addWidget(self.search_list)

        root.addWidget(self.stack, 1)

    # ---- 加载 ----

    def _reload(self) -> None:
        # 保存展开状态 + 滚动位置，重建后恢复，避免每次刷新整个树都收起来
        expanded = self._collect_expanded(self.tree.invisibleRootItem())
        scroll_pos = self.tree.verticalScrollBar().value()

        self.tree.clear()
        root_item = QTreeWidgetItem([self.root_path.name])
        root_item.setData(0, Qt.ItemDataRole.UserRole, str(self.root_path))
        root_item.setData(0, Qt.ItemDataRole.UserRole + 1, "dir")
        self.tree.addTopLevelItem(root_item)
        self._populate_children(root_item, self.root_path)
        self.tree.expandItem(root_item)

        if expanded:
            self._apply_expanded(root_item, expanded)
            self.tree.verticalScrollBar().setValue(scroll_pos)

    def _collect_expanded(self, root_item: QTreeWidgetItem) -> set[str]:
        """收集当前所有处于展开状态的目录节点的绝对路径"""
        out: set[str] = set()
        def walk(item: QTreeWidgetItem) -> None:
            for i in range(item.childCount()):
                ch = item.child(i)
                if ch.data(0, Qt.ItemDataRole.UserRole + 1) == "dir" and ch.isExpanded():
                    p = ch.data(0, Qt.ItemDataRole.UserRole)
                    if p:
                        out.add(p)
                    walk(ch)
        walk(root_item)
        return out

    def _apply_expanded(self, root_item: QTreeWidgetItem, paths: set[str]) -> None:
        """对路径在 paths 集合里的目录节点逐个 expand（会触发懒加载）"""
        def walk(item: QTreeWidgetItem) -> None:
            for i in range(item.childCount()):
                ch = item.child(i)
                if ch.data(0, Qt.ItemDataRole.UserRole + 1) == "dir":
                    p = ch.data(0, Qt.ItemDataRole.UserRole)
                    if p in paths:
                        self.tree.expandItem(ch)
                        walk(ch)
        walk(root_item)

    def _populate_children(self, parent_item: QTreeWidgetItem, parent_path: Path) -> None:
        """加载直接子项。目录带懒加载占位符。"""
        try:
            entries = list(parent_path.iterdir())
        except OSError:
            return
        dirs = sorted(
            [p for p in entries if p.is_dir() and p.name not in _ALWAYS_HIDDEN],
            key=lambda p: p.name.lower(),
        )
        files = sorted(
            [p for p in entries if p.is_file() and p.name not in _ALWAYS_HIDDEN],
            key=lambda p: p.name.lower(),
        )
        for d in dirs:
            node = QTreeWidgetItem(["📁  " + d.name])
            node.setData(0, Qt.ItemDataRole.UserRole, str(d))
            node.setData(0, Qt.ItemDataRole.UserRole + 1, "dir")
            node.addChild(QTreeWidgetItem(["(loading...)"]))
            parent_item.addChild(node)
        for f in files:
            node = QTreeWidgetItem(["  " + f.name])
            node.setData(0, Qt.ItemDataRole.UserRole, str(f))
            node.setData(0, Qt.ItemDataRole.UserRole + 1, "file")
            parent_item.addChild(node)

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.childCount() == 1 and item.child(0).text(0) == "(loading...)":
            item.removeChild(item.child(0))
            path = Path(item.data(0, Qt.ItemDataRole.UserRole))
            self._populate_children(item, path)

    def _expand_all_safe(self) -> None:
        """展开全部前先把所有懒加载节点都加载一遍"""
        self._eagerly_load_all(self.tree.invisibleRootItem())
        self.tree.expandAll()

    def _eagerly_load_all(self, node: QTreeWidgetItem) -> None:
        # 触发当前节点的懒加载
        if node.childCount() == 1 and node.child(0).text(0) == "(loading...)":
            node.removeChild(node.child(0))
            path_str = node.data(0, Qt.ItemDataRole.UserRole)
            if path_str:
                self._populate_children(node, Path(path_str))
        for i in range(node.childCount()):
            child = node.child(i)
            if child.data(0, Qt.ItemDataRole.UserRole + 1) == "dir":
                self._eagerly_load_all(child)

    # ---- 过滤 ----

    def _apply_filter(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.stack.setCurrentWidget(self.tree)
            return
        if self.indexer is None:
            # 无索引：回退到只过滤当前已加载节点（能力有限）
            self.stack.setCurrentWidget(self.tree)
            self._filter_tree(self.tree.invisibleRootItem(), text.lower())
            return
        # 有 FileIndexer：切换到扁平结果列表，全项目搜
        self.stack.setCurrentWidget(self.search_list)
        self.search_list.clear()
        hits = self.indexer.search(text, limit=300)
        if not hits:
            ph = QListWidgetItem(f"(无匹配)  共索引 {self.indexer.count()} 个文件")
            ph.setFlags(Qt.ItemFlag.NoItemFlags)
            ph.setForeground(Qt.GlobalColor.darkGray)
            self.search_list.addItem(ph)
            return
        for f in hits:
            item = QListWidgetItem(f"  {f.name_original}\n    {f.rel_path}")
            item.setData(Qt.ItemDataRole.UserRole, f.abs_path)
            self.search_list.addItem(item)

    def _filter_tree(self, item: QTreeWidgetItem, text: str) -> bool:
        any_visible = False
        for i in range(item.childCount()):
            child = item.child(i)
            kind = child.data(0, Qt.ItemDataRole.UserRole + 1)
            name = child.text(0).lower()
            if kind == "dir":
                child_match = self._filter_tree(child, text)
                visible = child_match or (not text) or (text in name)
                child.setHidden(not visible)
                any_visible = any_visible or visible
            else:
                visible = (not text) or (text in name)
                child.setHidden(not visible)
                any_visible = any_visible or visible
        return any_visible

    # ---- 事件 ----

    def _on_item_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        """单击目录 → 切换展开；单击文件什么都不做（双击打开）。
        Ctrl/Shift 按下时是在做多选——不要顺手把目录展开打断选择。
        """
        mods = QApplication.keyboardModifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            return
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if kind == "dir":
            item.setExpanded(not item.isExpanded())

    def _on_item_activated(self, item: QTreeWidgetItem, _col: int) -> None:
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if kind == "file":
            path = item.data(0, Qt.ItemDataRole.UserRole)
            self.fileActivated.emit(path)
        elif kind == "dir":
            item.setExpanded(not item.isExpanded())

    def _on_search_item_activated(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.fileActivated.emit(path)

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if not item:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
        # 多选场景：优先用 mousePressEvent 抓的快照（避免 super 改 selection 影响判断）；
        # 快照为空时回退到 selectedItems()。判断走 path 字符串，不依赖对象身份。
        snapshot = self.tree.take_right_press_snapshot()
        selected = snapshot if snapshot else self.tree.selectedItems()
        selected_paths = {it.data(0, Qt.ItemDataRole.UserRole) for it in selected}
        if path in selected_paths and len(selected) > 1:
            batch = [(Path(it.data(0, Qt.ItemDataRole.UserRole)),
                      it.data(0, Qt.ItemDataRole.UserRole + 1), it)
                     for it in selected
                     if it.data(0, Qt.ItemDataRole.UserRole)]
        else:
            batch = [(Path(path), kind, item)]
        self._show_context_menu(self.tree.mapToGlobal(pos), path, kind,
                                tree_item=item, batch=batch)

    def _on_search_context_menu(self, pos) -> None:
        item = self.search_list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        # 搜索列表项不是 QTreeWidgetItem，tree_item 传 None → 走磁盘扫描兜底
        self._show_context_menu(self.search_list.mapToGlobal(pos), path, "file",
                                tree_item=None, batch=[(Path(path), "file", None)])

    def _show_context_menu(self, global_pos, path: str, kind: str | None,
                           tree_item=None, batch=None) -> None:
        if not path:
            return
        p = Path(path)
        filename = p.name
        # 新建文件的目标目录：在目录上 → 该目录；在文件上 → 文件所在目录
        target_dir = p if kind == "dir" else p.parent

        menu = QMenu(self)

        # 顶置：可执行脚本（.bat/.cmd/.ps1/.exe）的运行入口——常用，要在最显眼的位置
        if kind == "file" and p.suffix.lower() in _RUNNABLE_SCRIPT_EXTS:
            menu.addAction(f"▶  运行 {filename}",
                           lambda fp=str(p): self.scriptRunRequested.emit(fp))
            menu.addSeparator()

        # 新建文件（最常用：md）+ 新建目录
        menu.addAction("📄  新建 Markdown 文件…",
                       lambda d=target_dir: self._create_new_file(d, default_ext=".md"))
        menu.addAction("📄  新建文件…",
                       lambda d=target_dir: self._create_new_file(d, default_ext=""))
        menu.addAction("📁  新建目录…",
                       lambda d=target_dir: self._create_new_dir(d))
        menu.addSeparator()

        # 在文件所在目录（或选中目录）打开终端并进入 Codex
        menu.addAction("codex", lambda d=target_dir: self._open_codex(d))
        # JetBrains IDE：根据项目类型选 IDEA / WebStorm / PyCharm
        from src.util.jetbrains import pick_ide_for
        ide = pick_ide_for(self.project_type)
        if ide is not None:
            ide_name, ide_exe = ide
            menu.addAction(f"用 {ide_name} 打开",
                           lambda exe=ide_exe: self._open_in_jetbrains(exe))
        if kind == "dir":
            menu.addAction("打开目录", lambda: open_folder(path))
        menu.addAction("在资源管理器中显示", lambda: reveal_in_explorer(path))
        menu.addSeparator()
        menu.addAction("复制文件名", lambda: QApplication.clipboard().setText(filename))
        menu.addAction("复制文件名（不含扩展名）",
                       lambda: QApplication.clipboard().setText(p.stem))
        menu.addAction("复制绝对路径", lambda: QApplication.clipboard().setText(path))
        try:
            rel = str(p.relative_to(self.root_path)).replace("\\", "/")
        except ValueError:
            rel = path
        menu.addAction("复制相对路径", lambda r=rel: QApplication.clipboard().setText(r))
        # 文件系统修改：重命名 + 删除（分到底部一段，避免误点）
        menu.addSeparator()
        rename_label = "✏  重命名目录…" if kind == "dir" else "✏  重命名…"
        menu.addAction(rename_label, lambda: self._rename_path(p, kind, tree_item))
        # 删除：单选时按 kind 走原文案；多选时显示数量
        if batch and len(batch) > 1:
            del_label = f"🗑  删除选中的 {len(batch)} 项到回收站"
        else:
            del_label = "🗑  删除目录到回收站" if kind == "dir" else "🗑  删除到回收站"
        menu.addAction(del_label, lambda b=batch: self._delete_paths(b))

        menu.exec(global_pos)

    def _rename_path(self, p: Path, kind: str | None, tree_item=None) -> None:
        # 不允许重命名项目根目录
        if p.resolve() == self.root_path.resolve():
            QMessageBox.warning(self, "重命名", "不能重命名项目根目录")
            return
        desc = "目录" if kind == "dir" else "文件"
        new_name, ok = QInputDialog.getText(
            self, f"重命名{desc}",
            f"输入新名称（当前：{p.name}）：",
            text=p.name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == p.name:
            return
        # 阻止跨目录注入
        if "/" in new_name or "\\" in new_name or new_name in (".", ".."):
            QMessageBox.warning(self, "重命名", "新名称不能包含路径分隔符")
            return
        new_path = p.with_name(new_name)
        # Windows 文件系统不区分大小写：仅改大小写时 exists() 仍为 True，要放行
        if new_name.lower() != p.name.lower() and new_path.exists():
            QMessageBox.warning(self, "重命名", f"目标已存在：{new_name}")
            return
        try:
            p.rename(new_path)
        except OSError as e:
            QMessageBox.warning(self, "重命名失败", f"{p}\n\n{e}")
            return
        # 重命名后排序位置可能变化，刷新父目录节点最稳
        self._refresh_dir_node(p.parent)
        # 搜索列表里也得重新过滤一遍
        if self.stack.currentWidget() is self.search_list:
            self._apply_filter(self.filter_input.text())

    def _delete_paths(self, batch) -> None:
        """直接删除（不弹二次确认），支持多选批量。
        batch: [(Path, kind, QTreeWidgetItem|None), ...]

        Windows 上 QFile.moveToTrash 走 shell IFileOperation：返回 ok=True 时
        文件可能还没真正从磁盘消失（shell 异步写回收站元数据，50-300ms 延迟），
        这时 Path.iterdir() 还能看到被删路径，依赖扫描刷新反而像"没刷"；此外
        路径字符串比对受 Windows 路径规范化（盘符大小写 / \\ vs /）影响并不可靠。
        用传进来的 QTreeWidgetItem 引用直接 removeChild 最稳。
        """
        # 过滤掉项目根目录
        items = [(p, k, ti) for (p, k, ti) in batch
                 if p.resolve() != self.root_path.resolve()]
        if not items:
            QMessageBox.warning(self, "删除", "不能删除项目根目录")
            return
        # 父先删（路径短的先）：父被 removeChild 后子的 tree_item.parent() 会是 None，
        # 同时子的磁盘路径也已经随父进了回收站，下面用 exists() + parent() 双重判空跳过。
        items.sort(key=lambda x: len(str(x[0])))
        failed = []
        for p, _kind, tree_item in items:
            if not p.exists():
                continue  # 已经随父一起进回收站了
            try:
                # PySide6 上 QFile.moveToTrash 返回单个 bool（不是 tuple）；
                # 老版本/Qt C++ 的 (bool, path) 二元组在这里不适用，不要解包。
                result = QFile.moveToTrash(str(p))
                ok = bool(result[0]) if isinstance(result, tuple) else bool(result)
            except (OSError, RuntimeError) as e:
                failed.append(f"{p}\n  {e}")
                continue
            if not ok:
                failed.append(str(p))
                continue
            if tree_item is not None and tree_item.parent() is not None:
                tree_item.parent().removeChild(tree_item)
            elif tree_item is None:
                # 搜索列表删除路径——扫磁盘兜底；watchdog 事件跟上后就正确了
                self._refresh_dir_node(p.parent)
        if failed:
            QMessageBox.warning(self, "部分删除失败", "\n\n".join(failed[:5]))
        if self.stack.currentWidget() is self.search_list:
            self._apply_filter(self.filter_input.text())

    def _open_in_jetbrains(self, exe: str) -> None:
        """让 JetBrains IDE 打开项目根目录（IDEA/WebStorm/PyCharm 用法一致）"""
        no_window = 0x08000000 if sys.platform == "win32" else 0
        try:
            subprocess.Popen(
                [exe, str(self.root_path)],
                creationflags=no_window,
                close_fds=True,
            )
        except OSError as e:
            QMessageBox.warning(self, "启动失败", f"{exe}\n\n{e}")

    def _open_codex(self, target_dir: Path) -> None:
        """在指定目录下打开 PowerShell 并启动 codex（不阻塞 mini-ide）"""
        exe = shutil.which("powershell") or shutil.which("powershell.exe")
        if not exe:
            QMessageBox.warning(
                self, "未找到 PowerShell",
                "PATH 里找不到 powershell。\n"
                "请在 cmd 里运行 `where powershell` 确认。",
            )
            return
        target_literal = "'" + str(target_dir).replace("'", "''") + "'"
        try:
            subprocess.Popen(
                [
                    exe,
                    "-NoExit",
                    "-NoLogo",
                    "-NoProfile",
                    "-Command",
                    f"Set-Location -LiteralPath {target_literal}; codex",
                ],
                close_fds=True,
            )
        except OSError as e:
            QMessageBox.warning(self, "启动 codex 失败", f"{e}")

    def _create_new_file(self, target_dir: Path, default_ext: str = "") -> None:
        """在指定目录下新建文件。用户输入文件名，自动补 default_ext 后缀。"""
        try:
            rel_dir = target_dir.relative_to(self.root_path)
            rel_text = str(rel_dir).replace("\\", "/") if str(rel_dir) != "." else "/"
        except ValueError:
            rel_text = str(target_dir)
        prompt = f"在 {rel_text} 下新建文件名："
        default_name = "新文档" + default_ext if default_ext else ""
        name, ok = QInputDialog.getText(self, "新建文件", prompt, text=default_name)
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        # 阻止跨目录注入（用户写了 ../xxx 或绝对路径都拒绝）
        if "/" in name or "\\" in name or name in (".", ".."):
            QMessageBox.warning(self, "新建文件", "文件名不能包含路径分隔符")
            return
        # 没写后缀且默认有就补上
        if default_ext and "." not in name:
            name += default_ext
        new_path = target_dir / name
        if new_path.exists():
            QMessageBox.warning(self, "新建文件", f"文件已存在：{new_path.name}")
            return
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            new_path.write_text("", encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "新建文件失败", str(e))
            return
        # 刷新树到新文件所在目录
        self._refresh_dir_node(target_dir)
        self.fileCreated.emit(str(new_path))

    def _create_new_dir(self, target_dir: Path) -> None:
        """在指定目录下新建子目录。"""
        try:
            rel_dir = target_dir.relative_to(self.root_path)
            rel_text = str(rel_dir).replace("\\", "/") if str(rel_dir) != "." else "/"
        except ValueError:
            rel_text = str(target_dir)
        prompt = f"在 {rel_text} 下新建目录名："
        name, ok = QInputDialog.getText(self, "新建目录", prompt, text="新目录")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        # 阻止跨目录注入
        if "/" in name or "\\" in name or name in (".", ".."):
            QMessageBox.warning(self, "新建目录", "目录名不能包含路径分隔符")
            return
        new_path = target_dir / name
        if new_path.exists():
            QMessageBox.warning(self, "新建目录", f"已存在：{new_path.name}")
            return
        try:
            new_path.mkdir(parents=True)
        except OSError as e:
            QMessageBox.warning(self, "新建目录失败", str(e))
            return
        self._refresh_dir_node(target_dir)

    def _refresh_dir_node(self, dir_path: Path) -> None:
        """重新加载某个目录节点的子项，并保留子树展开状态。
        找不到节点（路径不在已展开范围内）则整树刷新。
        """
        node = self._find_node_by_path(self.tree.invisibleRootItem(), str(dir_path))
        if node is None:
            self._reload()
            return
        # 保存子树展开状态
        sub_expanded = self._collect_expanded(node)
        # 清掉子项重新填充
        node.takeChildren()
        self._populate_children(node, dir_path)
        node.setExpanded(True)
        if sub_expanded:
            self._apply_expanded(node, sub_expanded)

    def _find_node_by_path(self, parent: QTreeWidgetItem, target: str) -> QTreeWidgetItem | None:
        for i in range(parent.childCount()):
            child = parent.child(i)
            data = child.data(0, Qt.ItemDataRole.UserRole)
            if data == target:
                return child
            if child.data(0, Qt.ItemDataRole.UserRole + 1) == "dir":
                # 仅递归到已加载（非懒加载占位）的目录里找
                if not (child.childCount() == 1 and child.child(0).text(0) == "(loading...)"):
                    found = self._find_node_by_path(child, target)
                    if found:
                        return found
        # 顶层根节点也要比对
        if parent is self.tree.invisibleRootItem() and self.tree.topLevelItemCount() > 0:
            top = self.tree.topLevelItem(0)
            if top.data(0, Qt.ItemDataRole.UserRole) == target:
                return top
        return None
