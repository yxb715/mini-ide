"""快速导航工具

- Ctrl+Shift+N 文件名搜索（FilePicker）
- Ctrl+E      最近打开的文件

都基于同一个 PickerDialog 基类：输入框 + 候选列表 + 上下箭头选择 + Enter 执行。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt, QEvent, QSize, QTimer, Signal
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QColor, QIcon, QKeyEvent, QPalette,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QVBoxLayout, QWidget,
)

from src.ui.theme import (
    ACCENT, ACCENT_SUBTLE, BG_CODE, BG_L1, BG_L2, BG_L4,
    BORDER_STRONG, BORDER_SUBTLE, FG_BRIGHT, FG_DIM, FG_PRIMARY, FG_SECONDARY,
    FONT_PT_UI, FONT_PT_UI_LG, FONT_PT_UI_SM, RADIUS_SM, apply_search_style,
)


def _esc(s: str) -> str:
    """HTML 转义，避免文件名/路径里的 < & 破坏富文本渲染"""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class _PickerItemDelegate(QStyledItemDelegate):
    """候选项两段式渲染：标题（文件名）亮色加粗，副标题（路径）暗色小字。

    之前 title + "\\n  " + subtitle 拼成纯文本一把渲染，全白同字号，
    一搜满屏白字根本扫不出文件名。这里用 QTextDocument 渲染富文本，
    让文件名跳出来、路径退到背景。
    """

    def _build_doc(self, index, selected: bool) -> QTextDocument | None:
        it = index.data(Qt.ItemDataRole.UserRole)
        if it is None or not isinstance(it, PickerItem):
            return None
        title = _esc(it.title)
        sub = _esc(it.subtitle) if it.subtitle else ""
        # 选中态标题用纯白，普通态用主色；路径恒用暗色
        title_color = FG_BRIGHT if selected else FG_PRIMARY
        html = (f'<span style="color:{title_color}; font-size:{FONT_PT_UI}pt;'
                f' font-weight:600;">{title}</span>')
        if sub:
            html += (f'<br/><span style="color:{FG_DIM};'
                     f' font-size:{FONT_PT_UI_SM}pt;">{sub}</span>')
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setHtml(html)
        return doc

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        doc = self._build_doc(index, selected)
        if doc is None:
            super().paint(painter, option, index)
            return
        # 背景（hover/选中）走默认主题绘制，文字我们自己画
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, widget)
        painter.save()
        painter.translate(text_rect.topLeft())
        painter.setClipRect(0, 0, text_rect.width(), text_rect.height())
        doc.setTextWidth(text_rect.width())
        ctx = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index):
        doc = self._build_doc(index, False)
        if doc is None:
            return super().sizeHint(option, index)
        w = option.rect.width() if option.rect.width() > 0 else 600
        doc.setTextWidth(w)
        return QSize(int(doc.idealWidth()), int(doc.size().height()) + 8)


@dataclass
class PickerItem:
    title: str                      # 显示的主文本（带路径）
    subtitle: str = ""              # 灰色辅助文本（右侧/下方）
    data: object = None             # 回调用的数据


class PickerDialog(QDialog):
    """通用快速选择浮窗"""

    picked = Signal(object)    # PickerItem.data

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(680, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setStyleSheet(
            f"background:{BG_L2}; border:1px solid {BORDER_STRONG}; border-bottom:none;"
        )
        hl = QVBoxLayout(header)
        hl.setContentsMargins(14, 12, 14, 6)
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet(f"color:{FG_SECONDARY}; font-size:{FONT_PT_UI_SM}pt;")
        hl.addWidget(self.title_lbl)
        self.input = QLineEdit()
        # 统一搜索框样式（白字 + 等宽 + 加大 + 提亮 placeholder）
        apply_search_style(self.input)
        self.input.textChanged.connect(self._on_query_changed)
        self.input.installEventFilter(self)
        hl.addWidget(self.input)
        root.addWidget(header)

        self.list = QListWidget()
        self.list.setStyleSheet(
            f"QListWidget {{ background:{BG_L1}; color:{FG_PRIMARY};"
            f" border:1px solid {BORDER_STRONG}; border-top:none; outline:none; }}"
            f"QListWidget::item {{ padding:6px 12px; border:none; }}"
            f"QListWidget::item:hover {{ background:{BG_L4}; }}"
            f"QListWidget::item:selected {{ background:{ACCENT_SUBTLE};"
            f" color:{FG_BRIGHT}; }}"
        )
        self.list.itemActivated.connect(self._on_activated)
        self.list.installEventFilter(self)
        # 富文本 delegate：文件名亮+加粗、路径暗+小字（替代纯文本同字号同色渲染）
        self.list.setItemDelegate(_PickerItemDelegate(self.list))
        root.addWidget(self.list, 1)

        self._fetcher: Callable[[str], list[PickerItem]] | None = None
        self._all_items: list[PickerItem] = []

        # 输入去抖：大仓库下 fetcher 是全表 O(n) 模糊打分，每个按键都跑会卡顿。
        # textChanged 只重置定时器，停顿 120ms 后才真正刷新候选列表。
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._do_refresh)
        self._pending_query = ""

    def set_fetcher(self, fetcher: Callable[[str], list[PickerItem]]) -> None:
        """fetcher(query) -> items；query 为空时返回默认列表"""
        self._fetcher = fetcher
        self._refresh("")

    def set_static_items(self, items: list[PickerItem]) -> None:
        self._all_items = items
        self.set_fetcher(self._default_fetch)

    def _default_fetch(self, query: str) -> list[PickerItem]:
        q = query.strip().lower()
        if not q:
            return self._all_items
        return [it for it in self._all_items if q in it.title.lower() or q in it.subtitle.lower()]

    def _on_query_changed(self, text: str) -> None:
        # 走去抖：仅记录最新 query 并重启定时器，停顿后由 _do_refresh 真正刷新
        self._pending_query = text
        self._debounce.start()

    def _do_refresh(self) -> None:
        self._refresh(self._pending_query)

    def _refresh(self, query: str) -> None:
        self.list.clear()
        items = self._fetcher(query) if self._fetcher else []
        for it in items[:200]:
            # 文本由 _PickerItemDelegate 富文本渲染；这里只挂数据，不再拼纯文本
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, it)
            self.list.addItem(item)
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def _on_activated(self, item: QListWidgetItem) -> None:
        picker = item.data(Qt.ItemDataRole.UserRole)
        if picker is not None:
            self.picked.emit(picker.data)
        self.accept()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(e)

    def showEvent(self, e):
        # 无边框 dialog 在 Windows 上 show() 后默认拿不到键盘焦点，必须显式激活
        super().showEvent(e)
        self.activateWindow()
        self.raise_()
        self.input.setFocus()

    def changeEvent(self, e):
        # 失焦自动关闭（点击外部 / 切到其他窗口）
        if e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.reject()
            return
        super().changeEvent(e)

    def eventFilter(self, obj, e):
        # 方向键 / Enter / ESC 在 input 或 list 上都能用
        if e.type() == e.Type.KeyPress and isinstance(e, QKeyEvent):
            k = e.key()
            if k == Qt.Key.Key_Escape:
                self.reject()
                return True
            if obj is self.input:
                if k == Qt.Key.Key_Down:
                    row = min(self.list.currentRow() + 1, self.list.count() - 1)
                    self.list.setCurrentRow(row)
                    return True
                if k == Qt.Key.Key_Up:
                    row = max(self.list.currentRow() - 1, 0)
                    self.list.setCurrentRow(row)
                    return True
                if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    item = self.list.currentItem()
                    if item:
                        self._on_activated(item)
                    return True
        return super().eventFilter(obj, e)


# ---- 具体实现 ----

def show_file_picker(indexer, on_pick: Callable[[str], None], parent=None) -> PickerDialog:
    """Ctrl+Shift+N: 按文件名搜索"""
    dlg = PickerDialog("按文件名搜索   (Ctrl+Shift+N)", parent)
    dlg.input.setPlaceholderText(f"已索引 {indexer.count()} 个文件...")

    def fetch(query: str) -> list[PickerItem]:
        hits = indexer.search(query, limit=150)
        return [
            PickerItem(title=f.name_original, subtitle=f.rel_path, data=f.abs_path)
            for f in hits
        ]

    dlg.set_fetcher(fetch)
    dlg.picked.connect(on_pick)
    return dlg


def show_recent_files(recent_abs_paths: list[str], project_root: str,
                      on_pick: Callable[[str], None], parent=None) -> PickerDialog:
    """Ctrl+E: 最近打开的文件"""
    dlg = PickerDialog("最近打开   (Ctrl+E)", parent)
    from pathlib import Path
    items: list[PickerItem] = []
    for p in recent_abs_paths:
        pp = Path(p)
        try:
            rel = str(pp.relative_to(project_root)).replace("\\", "/")
        except Exception:
            rel = p
        items.append(PickerItem(title=pp.name, subtitle=rel, data=p))
    dlg.set_static_items(items)
    dlg.picked.connect(on_pick)
    return dlg
