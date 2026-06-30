"""IDE 窗口内轻提示。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from src.ui.theme import (
    FG_PRIMARY, FG_SECONDARY, FONT_PT_UI, FONT_PT_UI_SM,
    GAP_LG, GAP_MD, RADIUS_MD,
    TOAST_ERROR_BG, TOAST_ERROR_BORDER,
    TOAST_INFO_BG, TOAST_INFO_BORDER,
    TOAST_SUCCESS_BG, TOAST_SUCCESS_BORDER,
)


class AppToast(QFrame):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("app_toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

        self._title = QLabel(self)
        self._title.setObjectName("toast_title")
        self._title.setTextFormat(Qt.TextFormat.PlainText)

        self._message = QLabel(self)
        self._message.setObjectName("toast_message")
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(GAP_LG, GAP_MD, GAP_LG, GAP_MD)
        lay.setSpacing(2)
        lay.addWidget(self._title)
        lay.addWidget(self._message)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        parent.installEventFilter(self)

    def show_message(self, kind: str, title: str, message: str, timeout_ms: int) -> None:
        title = title.strip()
        message = message.strip()
        self._title.setText(title)
        self._title.setVisible(bool(title))
        self._message.setText(message)
        self.setStyleSheet(self._style_for(kind))

        parent = self.parentWidget()
        if parent:
            max_width = max(220, min(420, parent.width() - GAP_LG * 2))
            content_width = max(120, max_width - GAP_LG * 2)
            self.setMinimumWidth(min(280, max_width))
            self.setMaximumWidth(max_width)
            self._title.setMaximumWidth(content_width)
            self._message.setMaximumWidth(content_width)

        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(timeout_ms)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parentWidget() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            self._place()
        return super().eventFilter(obj, event)

    def _place(self) -> None:
        parent = self.parentWidget()
        if not parent:
            return
        self.adjustSize()
        x = max(GAP_LG, parent.width() - self.width() - GAP_LG)
        y = max(GAP_LG, parent.height() - self.height() - GAP_LG)
        self.move(x, y)

    def _style_for(self, kind: str) -> str:
        if kind == "success":
            bg = TOAST_SUCCESS_BG
            border = TOAST_SUCCESS_BORDER
        elif kind == "error":
            bg = TOAST_ERROR_BG
            border = TOAST_ERROR_BORDER
        else:
            bg = TOAST_INFO_BG
            border = TOAST_INFO_BORDER
        return (
            f"QFrame#app_toast {{ background:{bg};"
            f" border:1px solid {border}; border-radius:{RADIUS_MD}px; }}"
            f"QLabel#toast_title {{ background:transparent; color:{FG_PRIMARY};"
            f" font-size:{FONT_PT_UI}pt; font-weight:600; }}"
            f"QLabel#toast_message {{ background:transparent; color:{FG_SECONDARY};"
            f" font-size:{FONT_PT_UI_SM}pt; }}"
        )
