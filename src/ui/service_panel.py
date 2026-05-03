"""左侧服务面板（多模块 Spring Boot 专用）

每行：状态图标 模块名 端口/运行时长 启停按钮

交互：
- 左键行主体 → 切到该模块的日志 tab
- 右侧小按钮 → 启停（启动中/停止中时禁用）
- 顶部"全部启动 / 全部停止"按钮

对外信号：
- focusRequested(module_name)
- startRequested / stopRequested(module_name)
- startAllRequested / stopAllRequested
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.theme import (
    ACCENT, BG_BTN_DANGER, BG_BTN_DANGER_HOVER, BG_BTN_PRIMARY,
    BG_BTN_PRIMARY_HOVER, BG_L0, BG_L1, BG_L2, BG_L4, BORDER_SUBTLE,
    COLOR_SUCCESS, COLOR_WARN, DOT_IDLE, FG_BRIGHT, FG_DIM, FG_PRIMARY,
    FG_SECONDARY, FONT_PT_UI, FONT_PT_UI_SM, RADIUS_SM,
)


STATE_IDLE = "idle"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_STOPPING = "stopping"


def _row_btn_qss(border_color: str, fg_color: str) -> str:
    return (
        f"QPushButton {{ background:transparent; border:1px solid {border_color};"
        f" border-radius:3px; color:{fg_color}; font-size:{FONT_PT_UI_SM}pt;"
        f" padding:0; }}"
        f"QPushButton:hover:enabled {{ border-color:{ACCENT}; color:{FG_PRIMARY}; }}"
        f"QPushButton:disabled {{ background:transparent; }}"
    )


class _ServiceRow(QFrame):
    focusRequested = Signal(str)
    startRequested = Signal(str)
    stopRequested = Signal(str)

    def __init__(self, module_name: str, default_port: int | None = None, parent=None):
        super().__init__(parent)
        self.module_name = module_name
        self.default_port = default_port
        self._state = STATE_IDLE

        self.setObjectName("service_row")
        self.setStyleSheet(
            f"#service_row {{ background:transparent; border:none; }}"
            f"#service_row:hover {{ background:{BG_L4}; }}"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 6, 5)
        lay.setSpacing(8)

        self.lbl_icon = QLabel("▶")
        self.lbl_icon.setFixedWidth(14)
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_icon)

        self.lbl_name = QLabel(module_name)
        self.lbl_name.setStyleSheet(f"color:{FG_PRIMARY}; font-size:{FONT_PT_UI}pt;")
        lay.addWidget(self.lbl_name, 1)

        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet(f"color:{FG_SECONDARY}; font-size:{FONT_PT_UI_SM}pt;")
        lay.addWidget(self.lbl_info)

        self.btn = QPushButton("▶")
        self.btn.setFixedSize(22, 22)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn.clicked.connect(self._on_btn)
        lay.addWidget(self.btn)

        self.set_state(STATE_IDLE)

    def mousePressEvent(self, event):
        # 点到按钮时 Qt 已派给子 widget，这里只在点行空白处触发
        if event.button() == Qt.MouseButton.LeftButton:
            self.focusRequested.emit(self.module_name)
        super().mousePressEvent(event)

    def _on_btn(self):
        if self._state == STATE_RUNNING:
            self.stopRequested.emit(self.module_name)
        elif self._state == STATE_IDLE:
            self.startRequested.emit(self.module_name)

    def set_state(self, state: str, port: int | None = None, elapsed_seconds: float = 0.0):
        self._state = state
        # port 优先用本次传入的（启动后从日志抓的真实端口），否则用 application.yml 的默认值
        effective_port = port if port is not None else self.default_port
        port_str = f":{effective_port}" if effective_port else ""
        if state == STATE_IDLE:
            self.lbl_icon.setText("▶")
            self.lbl_icon.setStyleSheet(f"color:{DOT_IDLE}; font-size:{FONT_PT_UI}pt;")
            self.lbl_info.setText(f"{port_str}  (未启动)".strip() if port_str else "(未启动)")
            self.btn.setText("▶")
            self.btn.setToolTip(f"启动 {self.module_name}")
            self.btn.setEnabled(True)
            self.btn.setStyleSheet(_row_btn_qss(BORDER_SUBTLE, FG_DIM))
        elif state == STATE_STARTING:
            self.lbl_icon.setText("◐")
            self.lbl_icon.setStyleSheet(f"color:{COLOR_WARN}; font-size:{FONT_PT_UI}pt;")
            self.lbl_info.setText(f"{port_str}  启动中...".strip() if port_str else "启动中...")
            self.btn.setText("◐")
            self.btn.setToolTip(f"{self.module_name} 正在启动")
            self.btn.setEnabled(False)
            self.btn.setStyleSheet(_row_btn_qss(COLOR_WARN, COLOR_WARN))
        elif state == STATE_RUNNING:
            self.lbl_icon.setText("⏹")
            self.lbl_icon.setStyleSheet(f"color:{COLOR_SUCCESS}; font-size:{FONT_PT_UI}pt;")
            parts: list[str] = []
            if port_str:
                parts.append(port_str)
            if elapsed_seconds > 0:
                m, s = divmod(int(elapsed_seconds), 60)
                h, m = divmod(m, 60)
                parts.append(f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}")
            self.lbl_info.setText("  ".join(parts) if parts else "运行中")
            self.btn.setText("⏹")
            self.btn.setToolTip(f"停止 {self.module_name}")
            self.btn.setEnabled(True)
            self.btn.setStyleSheet(_row_btn_qss(COLOR_SUCCESS, COLOR_SUCCESS))
        elif state == STATE_STOPPING:
            self.lbl_icon.setText("◐")
            self.lbl_icon.setStyleSheet(f"color:{COLOR_WARN}; font-size:{FONT_PT_UI}pt;")
            self.lbl_info.setText(f"{port_str}  停止中...".strip() if port_str else "停止中...")
            self.btn.setText("◐")
            self.btn.setToolTip(f"{self.module_name} 正在停止")
            self.btn.setEnabled(False)
            self.btn.setStyleSheet(_row_btn_qss(COLOR_WARN, COLOR_WARN))

    def current_state(self) -> str:
        return self._state


class ServicePanel(QWidget):
    """多模块项目的左上侧服务面板"""

    focusRequested = Signal(str)
    startRequested = Signal(str)
    stopRequested = Signal(str)
    startAllRequested = Signal()
    stopAllRequested = Signal()

    def __init__(self, modules: list[tuple[str, int | None]], parent=None):
        """modules: [(module_name, default_port_or_None)]"""
        super().__init__(parent)
        self.setStyleSheet(f"QWidget {{ background:{BG_L1}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(
            f"background:{BG_L0}; border-bottom:1px solid {BORDER_SUBTLE};"
        )
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(10, 6, 6, 6)
        h_lay.setSpacing(6)

        title = QLabel(f"🧩  服务 ({len(modules)})")
        title.setStyleSheet(f"color:{FG_PRIMARY}; font-size:{FONT_PT_UI}pt;")
        h_lay.addWidget(title)
        h_lay.addStretch(1)

        self.btn_start_all = QPushButton("▶ 全部启动")
        self.btn_start_all.setFixedHeight(22)
        self.btn_start_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_all.setStyleSheet(
            f"QPushButton {{ background:{BG_BTN_PRIMARY}; color:{FG_BRIGHT};"
            f" border:none; border-radius:{RADIUS_SM}px;"
            f" font-size:{FONT_PT_UI_SM}pt; padding:2px 10px; }}"
            f"QPushButton:hover {{ background:{BG_BTN_PRIMARY_HOVER}; }}"
        )
        self.btn_start_all.clicked.connect(self.startAllRequested.emit)
        h_lay.addWidget(self.btn_start_all)

        self.btn_stop_all = QPushButton("⏹ 全部停止")
        self.btn_stop_all.setFixedHeight(22)
        self.btn_stop_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop_all.setStyleSheet(
            f"QPushButton {{ background:{BG_BTN_DANGER}; color:{FG_BRIGHT};"
            f" border:none; border-radius:{RADIUS_SM}px;"
            f" font-size:{FONT_PT_UI_SM}pt; padding:2px 10px; }}"
            f"QPushButton:hover {{ background:{BG_BTN_DANGER_HOVER}; }}"
            f"QPushButton:disabled {{ background:{BG_L2}; color:{FG_DIM}; }}"
        )
        self.btn_stop_all.clicked.connect(self.stopAllRequested.emit)
        self.btn_stop_all.setEnabled(False)
        h_lay.addWidget(self.btn_stop_all)

        root.addWidget(header)

        rows_wrap = QFrame()
        rows_wrap.setStyleSheet(f"background:{BG_L1};")
        rlay = QVBoxLayout(rows_wrap)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)

        self._rows: dict[str, _ServiceRow] = {}
        for name, port in modules:
            row = _ServiceRow(name, default_port=port, parent=rows_wrap)
            row.focusRequested.connect(self.focusRequested.emit)
            row.startRequested.connect(self.startRequested.emit)
            row.stopRequested.connect(self.stopRequested.emit)
            self._rows[name] = row
            rlay.addWidget(row)
        rlay.addStretch(1)

        root.addWidget(rows_wrap, 1)

    def update_state(self, module: str, state: str,
                     port: int | None = None, elapsed_seconds: float = 0.0) -> None:
        row = self._rows.get(module)
        if row:
            row.set_state(state, port, elapsed_seconds)
        # 任意模块在 RUNNING/STARTING 就允许「全部停止」
        any_active = any(
            r.current_state() in (STATE_RUNNING, STATE_STARTING)
            for r in self._rows.values()
        )
        self.btn_stop_all.setEnabled(any_active)

    def running_count(self) -> int:
        return sum(1 for r in self._rows.values() if r.current_state() == STATE_RUNNING)

    def total(self) -> int:
        return len(self._rows)
