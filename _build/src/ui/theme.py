"""主题 / 视觉规范的唯一来源（SSoT）

约定（CLAUDE.md 硬约束 15）：
- 所有颜色、圆角、间距、字号都来自这里
- 其他文件不许 hardcode 任何 hex 颜色字面量
- 唯一例外：src/ui/syntax_highlighter.py 是 Pygments 代码语法色板（语义独立）

风格定调：JetBrains Darcula —— IntelliJ IDEA 官方深色主题原版配色。
- 主底色 #3C3F41（中性灰，不偏蓝、不偏暖，长时间看不刺眼）
- 编辑器/日志底 #2B2B2B（Darcula 最有辨识度的深灰）
- 主文字 #BBBBBB（柔和米灰，不是纯白；编辑器内是 #A9B7C6）
- accent #4B6EAF（信号蓝，按钮/选中/focus）
- 关键字橙 #CC7832 / 字符串绿 #6A8759 / 类名黄 #FFC66D / 字段紫 #9876AA
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLineEdit


# ====================================================================
# Color tokens
# ====================================================================

# 背景层级（从底到顶）。
# Darcula 的层级感比 Tokyo Night 弱——主窗口/侧栏/工具栏全部 #3C3F41，
# 只有编辑器/日志区单独用 #2B2B2B 以突出"代码内容"。
BG_L0 = "#2B2B2B"   # 最深：tab bar 底、状态栏、深面板
BG_L1 = "#3C3F41"   # 主窗口背景（Darcula 主底）
BG_L2 = "#3C3F41"   # 二级面板：侧栏、工具栏、对话框（Darcula 不分这一档）
BG_L3 = "#45494A"   # 输入框 / 静态按钮 / 表头
BG_L4 = "#4B5052"   # hover 高亮
BG_L5 = "#214283"   # pressed / 选中（IDEA 选中蓝）

# 代码 / 日志区底色：Darcula 编辑器经典 #2B2B2B
BG_CODE = "#2B2B2B"

# 边框
BORDER_SUBTLE = "#323232"   # 普通边框
BORDER_STRONG = "#5E6060"   # 分隔强调
BORDER_FOCUS = "#4B6EAF"    # 焦点蓝

# 文字（4 档亮度，区分主次）
# Darcula 原版 FG #BBBBBB 在 #3C3F41 底上对比度 ~5.7:1，长时间盯着费眼；
# 这里整体提亮一档到 ~8.5:1，但仍保留"米灰"调（不上纯白避免刺眼）。
FG_PRIMARY = "#DCDCDC"      # 主文字（提亮版 Darcula UI 字色）
FG_SECONDARY = "#C5D1E0"    # 编辑器普通字色（保留蓝米白调）
FG_DIM = "#9A9A9A"          # 占位符 / disabled / 注释（也提亮一档不至于太弱）
FG_BRIGHT = "#FFFFFF"       # 反白：选中按钮等需要纯白时

# 强调色（accent，IDEA 蓝）
ACCENT = "#4B6EAF"
ACCENT_HOVER = "#5E81B5"
ACCENT_SUBTLE = "#214283"   # 选中底色（IDEA selection 蓝）
ACCENT_PRESSED = "#365880"

# 语义色 —— 跨 widget 一致使用（Darcula 语法着色）
COLOR_SUCCESS = "#6A8759"   # 字符串绿
COLOR_WARN = "#BBB529"      # 警告黄
COLOR_ERROR = "#BC3F3C"     # 错误红
COLOR_INFO = "#6897BB"      # 数字蓝
COLOR_DEBUG = "#808080"     # 注释灰
COLOR_SQL = "#9876AA"       # 字段紫
COLOR_BANNER = "#9876AA"    # Spring Boot banner / SQL 共用紫
COLOR_LINK = "#589DF6"      # IDEA 链接蓝

# 状态点三态
DOT_IDLE = "#808080"
DOT_RUNNING = "#6A8759"
DOT_WARN = "#BBB529"

# Git 相关（diff 着色 / 状态文件名）—— IDEA Git 视图配色
GIT_ADD = "#6A8759"
GIT_DEL = "#BC3F3C"
GIT_MODIFY = "#BBB529"
GIT_HUNK = "#4B6EAF"
GIT_FILE_HEAD = "#9876AA"
GIT_META = "#808080"
GIT_CONFLICT = "#CC7832"   # IDEA 冲突文件名橙
GIT_IGNORED = "#6E6E6E"    # 灰：被 .gitignore 忽略，看得见但弱化

# 诊断条（醒目的"出问题了"通知背景）
BG_DIAGNOSIS = "#4D3A1F"
BORDER_DIAGNOSIS = "#806020"

# 搜索命中高亮（IDEA find usages 用的暗绿底 + 白字）
HIGHLIGHT_MATCH_BG = "#32593D"
HIGHLIGHT_MATCH_FG = "#FFFFFF"

# 按钮配色：通用、primary、danger、success
# Darcula 普通按钮 #4C5052 灰，主按钮 #365880 蓝（OK / Apply 那种）
BG_BTN = "#4C5052"
BG_BTN_HOVER = "#5E6060"
BG_BTN_PRESSED = "#3C3F41"
BG_BTN_PRIMARY = "#365880"
BG_BTN_PRIMARY_HOVER = "#4C7196"
BG_BTN_DANGER = "#6E3A3A"
BG_BTN_DANGER_HOVER = "#8B4848"
BG_BTN_SUCCESS = "#4F6F44"
BG_BTN_SUCCESS_HOVER = "#608653"


# ====================================================================
# 兼容别名（旧代码用的常量名，保留方便渐进迁移）
# ====================================================================
# 等阶段 5 全切完后这一段可以删掉
ACCENT_LIGHT = ACCENT_HOVER
BG_DARKER = BG_L2
BG_PANEL = BG_L2
BG_HOVER = BG_L4
BORDER = BORDER_SUBTLE
TEXT_PRIMARY = FG_PRIMARY
TEXT_SECONDARY = FG_SECONDARY
COLOR_NEUTRAL = FG_DIM


# ====================================================================
# 形状 / 间距 / 字号
# ====================================================================

RADIUS_SM = 4
RADIUS_MD = 6
RADIUS_LG = 8

GAP_XS = 4
GAP_SM = 6
GAP_MD = 10
GAP_LG = 16

# Widget 高度
H_BTN = 26
H_BTN_SM = 22
H_TOOLBAR = 36
H_STATUSBAR = 24
W_LEFT_PANEL = 260

# 字号（point）
TREE_ITEM_PAD_V = 3                        # 树/列表行的上下内边距
FONT_PT_UI = 15       # UI 主字号（13+2，整套放大一档）
FONT_PT_UI_SM = 14    # 副标题 / 次级文字
FONT_PT_UI_LG = 17    # 标题
FONT_PT_CODE = 18     # 编辑器 / 日志
FONT_PT_DIFF = 16     # diff 视图

# 字体族
FONT_FAMILY_UI = '"Segoe UI", "Microsoft YaHei UI", system-ui, sans-serif'
FONT_FAMILY_CODE = '"Cascadia Mono", "Consolas", "Courier New", monospace'


# ====================================================================
# 全局 base QSS（自写，不依赖 qdarkstyle）
# ====================================================================

def _base_qss() -> str:
    """返回完整 base QSS。所有颜色 / 间距走上面的 token，改这里一次全应用生效。"""
    return f"""
/* ===== 顶层 ===== */
QMainWindow, QWidget {{
    background: {BG_L1};
    color: {FG_PRIMARY};
    font-family: {FONT_FAMILY_UI};
    font-size: {FONT_PT_UI}pt;
}}

QFrame {{
    background: transparent;
    color: {FG_PRIMARY};
}}

QDialog {{
    background: {BG_L2};
    color: {FG_PRIMARY};
}}

/* ===== 文字 ===== */
QLabel {{
    background: transparent;
    color: {FG_PRIMARY};
}}
QLabel[role="title"] {{
    color: {FG_PRIMARY};
    font-size: {FONT_PT_UI_LG}pt;
    font-weight: 600;
}}
QLabel[role="subtitle"] {{
    color: {FG_SECONDARY};
    font-size: {FONT_PT_UI_SM}pt;
}}
QLabel[role="hint"] {{
    color: {FG_DIM};
    font-size: {FONT_PT_UI_SM}pt;
}}

/* ===== 按钮 ===== */
QPushButton {{
    background: {BG_BTN};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: {RADIUS_SM}px;
    padding: 5px 14px;
    min-height: {H_BTN - 10}px;
}}
QPushButton:hover {{ background: {BG_BTN_HOVER}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background: {BG_BTN_PRESSED}; }}
QPushButton:disabled {{ background: {BG_L2}; color: {FG_DIM}; border-color: {BORDER_SUBTLE}; }}
QPushButton:focus {{ border-color: {BORDER_FOCUS}; }}

QPushButton[role="primary"] {{
    background: {BG_BTN_PRIMARY};
    color: {FG_BRIGHT};
    border: 1px solid {BG_BTN_PRIMARY};
}}
QPushButton[role="primary"]:hover {{ background: {BG_BTN_PRIMARY_HOVER}; border-color: {BG_BTN_PRIMARY_HOVER}; }}
QPushButton[role="primary"]:pressed {{ background: {ACCENT_PRESSED}; }}

QPushButton[role="danger"] {{
    background: {BG_BTN_DANGER};
    color: {COLOR_ERROR};
    border: 1px solid {BG_BTN_DANGER};
}}
QPushButton[role="danger"]:hover {{ background: {BG_BTN_DANGER_HOVER}; border-color: {BG_BTN_DANGER_HOVER}; color: {FG_BRIGHT}; }}

QPushButton[role="success"] {{
    background: {BG_BTN_SUCCESS};
    color: {COLOR_SUCCESS};
    border: 1px solid {BG_BTN_SUCCESS};
}}
QPushButton[role="success"]:hover {{ background: {BG_BTN_SUCCESS_HOVER}; }}

/* ===== ToolButton（紧凑型按钮）===== */
QToolButton {{
    background: transparent;
    color: {FG_PRIMARY};
    border: 1px solid transparent;
    border-radius: {RADIUS_SM}px;
    padding: 3px 8px;
}}
QToolButton:hover {{ background: {BG_L4}; }}
QToolButton:pressed {{ background: {BG_L5}; }}
QToolButton:disabled {{ color: {FG_DIM}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* ===== 输入框 ===== */
QLineEdit {{
    background: {BG_L3};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: {RADIUS_SM}px;
    padding: 4px 8px;
    selection-background-color: {ACCENT_SUBTLE};
    selection-color: {FG_BRIGHT};
}}
QLineEdit:focus {{ border-color: {BORDER_FOCUS}; }}
QLineEdit:disabled {{ color: {FG_DIM}; background: {BG_L2}; }}
QLineEdit[placeholderText] {{ color: {FG_DIM}; }}

/* ===== 搜索框（命令面板 / 全项目搜索 / 文件树过滤 / 日志搜索 / Ctrl+F）=====
   高对比白字 + 等宽字体 + 加大字号 + 加深底色，让用户输入的查询词醒目突出。
   通过 widget setProperty("role", "search") 应用，或调 apply_search_style() 一并
   把 placeholder 颜色提亮（QSS 选择器改不了 placeholder，需走 palette）。 */
QLineEdit[role="search"] {{
    background: {BG_L1};
    color: {FG_BRIGHT};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_SM}px;
    padding: 6px 10px;
    font-family: {FONT_FAMILY_CODE};
    font-size: {FONT_PT_UI_LG}pt;
    selection-background-color: {ACCENT_SUBTLE};
    selection-color: {FG_BRIGHT};
}}
QLineEdit[role="search"]:focus {{ border-color: {BORDER_FOCUS}; }}
QLineEdit[role="search"]:disabled {{ color: {FG_DIM}; background: {BG_L2}; }}

/* ===== 代码 / 日志区 ===== */
QPlainTextEdit, QTextEdit {{
    background-color: {BG_CODE};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    selection-background-color: {ACCENT_SUBTLE};
    selection-color: {FG_BRIGHT};
    font-family: {FONT_FAMILY_CODE};
    font-size: {FONT_PT_CODE}pt;
}}
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {BORDER_FOCUS}; }}

/* ===== 树 / 列表 ===== */
QTreeView, QTreeWidget, QListView, QListWidget {{
    background: {BG_L2};
    color: {FG_PRIMARY};
    border: none;
    outline: none;
    alternate-background-color: {BG_L2};
}}
QTreeView::item, QTreeWidget::item, QListView::item, QListWidget::item {{
    padding: {TREE_ITEM_PAD_V}px 4px;
    border: none;
}}
QTreeView::item:hover, QTreeWidget::item:hover, QListView::item:hover, QListWidget::item:hover {{
    background: {BG_L4};
}}
QTreeView::item:selected, QTreeWidget::item:selected, QListView::item:selected, QListWidget::item:selected {{
    background: {ACCENT_SUBTLE};
    color: {FG_BRIGHT};
}}

/* ===== Tab ===== */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {BORDER_SUBTLE};
    background: {BG_L1};
    top: -1px;
}}
QTabBar {{
    background: {BG_L0};
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background: {BG_L0};
    color: {FG_SECONDARY};
    padding: 6px 16px;
    border: none;
    border-right: 1px solid {BORDER_SUBTLE};
    min-width: 80px;
    font-size: {FONT_PT_UI}pt;
}}
QTabBar::tab:hover:!selected {{
    background: {BG_L2};
    color: {FG_PRIMARY};
}}
QTabBar::tab:selected {{
    background: {ACCENT_SUBTLE};
    color: {FG_BRIGHT};
    border-top: 3px solid {ACCENT};
}}
QTabBar::close-button {{
    subcontrol-position: right;
    border-radius: {RADIUS_SM}px;
    margin: 2px;
}}
QTabBar::close-button:hover {{ background: {BG_BTN_DANGER}; }}

/* ===== 菜单 ===== */
QMenuBar {{
    background: {BG_L2};
    color: {FG_PRIMARY};
    border-bottom: 1px solid {BORDER_SUBTLE};
}}
QMenuBar::item {{
    background: transparent;
    padding: 4px 10px;
}}
QMenuBar::item:selected {{ background: {BG_L4}; }}

QMenu {{
    background: {BG_L2};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 18px;
    border-radius: {RADIUS_SM}px;
}}
QMenu::item:selected {{ background: {ACCENT_SUBTLE}; color: {FG_BRIGHT}; }}
QMenu::item:disabled {{ color: {FG_DIM}; }}
QMenu::separator {{
    height: 1px;
    background: {BORDER_SUBTLE};
    margin: 4px 6px;
}}

/* ===== 状态栏 ===== */
QStatusBar {{
    background: {BG_L0};
    color: {FG_SECONDARY};
    border-top: 1px solid {BORDER_SUBTLE};
    min-height: {H_STATUSBAR}px;
}}
QStatusBar::item {{ border: none; }}

/* ===== 工具栏 ===== */
QToolBar {{
    background: {BG_L2};
    border-bottom: 1px solid {BORDER_SUBTLE};
    spacing: {GAP_XS}px;
    padding: {GAP_XS}px {GAP_SM}px;
}}

/* ===== Splitter（隐形 handle，hover 才高亮）===== */
QSplitter::handle {{
    background: {BORDER_SUBTLE};
}}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background: {ACCENT}; }}

/* ===== 滚动条（VSCode 风：默认低调，hover 显形）===== */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BG_L4};
    border-radius: 5px;
    min-height: 30px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {BG_L5}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BG_L4};
    border-radius: 5px;
    min-width: 30px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {BG_L5}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ===== CheckBox ===== */
QCheckBox {{
    color: {FG_PRIMARY};
    spacing: 6px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 3px;
    background: {BG_L3};
}}
QCheckBox::indicator:hover {{ border-color: {BORDER_FOCUS}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox:disabled {{ color: {FG_DIM}; }}

/* ===== GroupBox ===== */
QGroupBox {{
    background: transparent;
    color: {FG_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: {RADIUS_MD}px;
    margin-top: 12px;
    padding-top: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {FG_SECONDARY};
}}

/* ===== 表头 / 表格 ===== */
QHeaderView::section {{
    background: {BG_L2};
    color: {FG_SECONDARY};
    padding: 4px 8px;
    border: none;
    border-right: 1px solid {BORDER_SUBTLE};
    border-bottom: 1px solid {BORDER_SUBTLE};
}}
QHeaderView::section:hover {{ background: {BG_L4}; color: {FG_PRIMARY}; }}
QTableView, QTableWidget {{
    background: {BG_L1};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    gridline-color: {BORDER_SUBTLE};
    selection-background-color: {ACCENT_SUBTLE};
    selection-color: {FG_BRIGHT};
}}
QTableView::item, QTableWidget::item {{ padding: 4px 6px; }}

/* ===== ToolTip ===== */
QToolTip {{
    background: {BG_L3};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    padding: 4px 8px;
}}

/* ===== MessageBox ===== */
QMessageBox {{
    background: {BG_L2};
    color: {FG_PRIMARY};
}}
QMessageBox QLabel {{ color: {FG_PRIMARY}; }}
"""


def apply_theme(app: QApplication) -> None:
    """主题入口：把完整 base QSS 套到 QApplication。

    完全自写 —— 不依赖 qdarkstyle / qt_material 等第三方主题。
    所有 widget 样式都来自上面 token 渲染的 QSS。
    """
    app.setStyleSheet(_base_qss())


# 兼容旧名（别的地方现在还在 import apply_dark_theme）
def apply_dark_theme(app: QApplication) -> None:
    apply_theme(app)


def apply_search_style(line_edit: QLineEdit) -> None:
    """把 QLineEdit 标记为搜索框样式：白字 + 等宽 + 加大字号 + 提亮 placeholder。

    所有可视为"搜索/查询"的输入框都应该过这一步：
    - 命令面板 / 最近文件（quick_open）
    - 全项目内容搜索（content_search）
    - 文件树顶部过滤框（file_tree）
    - 日志搜索栏（log_widget）
    - 文件预览 Ctrl+F 搜索（file_preview）

    端口号、扩展名筛选这类"功能性输入"也走这套，保持视觉一致。
    """
    line_edit.setProperty("role", "search")
    pal = line_edit.palette()
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(FG_SECONDARY))
    line_edit.setPalette(pal)
    # 注意：早期版本在这里调 style().unpolish/polish 想强制让 [role="search"]
    # 选择器立即生效，但在某些 widget（FilePreviewPane 构造期 search_input
    # 还没 attach 到父级时）会让 Qt 内部状态错乱，悄无声息地阻塞 widget
    # 后续显示——双击文件没反应、也没异常。
    # 实际上 setProperty 在 widget 第一次 polish 之前调用，Qt 会在 polish
    # 时自然吸收，不需要手动 repolish。已显示后再切换 role 才需要 repolish，
    # 但当前所有 caller 都是在 __init__ 里立刻调用，不存在那种情况。
