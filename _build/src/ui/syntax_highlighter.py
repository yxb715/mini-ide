"""基于 Pygments 的 Qt 语法高亮器

用在文件预览窗口。支持 600+ 种语言（Java/Kotlin/Vue/JS/TS/Python/YAML/JSON/
SQL/HTML/CSS/XML/Groovy/Gradle/Properties...）。

实现思路：
- 一次 tokenize 整个文档，把 token 按 block_number 分桶
- highlightBlock 时只查表，O(1) 应用每个 token 的 format
- 文件大小 > MAX_HIGHLIGHT_BYTES 时自动退化为无高亮（避免卡住）
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import (
    QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument,
)

try:
    from pygments import lex
    from pygments.lexer import Lexer
    from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
    from pygments.token import Token, STANDARD_TYPES
    from pygments.util import ClassNotFound
    _PYGMENTS_OK = True
except ImportError:
    _PYGMENTS_OK = False


MAX_HIGHLIGHT_BYTES = 500_000   # 超过 500KB 不高亮，性能优先


# ---- GitHub 代码高亮色板 ----

_COLORS = {
    "default":     "#abb2bf",
    "keyword":     "#c678dd",
    "keyword.declaration": "#c678dd",
    "keyword.namespace":   "#c678dd",
    "keyword.constant":    "#d19a66",
    "keyword.type":        "#e5c07b",
    "name":        "#abb2bf",
    "name.function": "#61afef",
    "name.function.magic": "#61afef",
    "name.class":  "#e5c07b",
    "name.builtin":"#e5c07b",
    "name.builtin.pseudo": "#e06c75",
    "name.decorator": "#61afef",
    "name.exception": "#e5c07b",
    "name.tag":    "#e06c75",
    "name.attribute": "#d19a66",
    "name.namespace": "#e5c07b",
    "name.constant": "#d19a66",
    "name.variable": "#e06c75",
    "name.variable.instance": "#e06c75",
    "name.label":  "#e06c75",
    "string":      "#98c379",
    "string.doc":  "#7f848e",
    "string.affix":"#98c379",
    "string.interpol": "#56b6c2",
    "string.escape": "#56b6c2",
    "string.regex":"#56b6c2",
    "string.symbol":"#98c379",
    "number":      "#d19a66",
    "operator":    "#56b6c2",
    "operator.word": "#c678dd",
    "punctuation": "#abb2bf",
    "comment":     "#5c6370",
    "comment.preproc": "#c678dd",
    "comment.special": "#c678dd",
    "generic.heading": "#61afef",
    "generic.subheading": "#61afef",
    "generic.deleted": "#e06c75",
    "generic.inserted": "#98c379",
    "generic.emph":"#abb2bf",
    "generic.strong":"#abb2bf",
    "literal":     "#d19a66",
    "error":       "#e06c75",
}

_LIGHT_COLORS = {
    "default": "#24292f", "keyword": "#cf222e", "keyword.declaration": "#cf222e",
    "keyword.namespace": "#cf222e", "keyword.constant": "#0550ae",
    "keyword.type": "#953800", "name": "#24292f", "name.function": "#8250df",
    "name.function.magic": "#8250df", "name.class": "#953800",
    "name.builtin": "#0550ae", "name.builtin.pseudo": "#0550ae",
    "name.decorator": "#8250df", "name.exception": "#953800",
    "name.tag": "#116329", "name.attribute": "#0550ae",
    "name.namespace": "#953800", "name.constant": "#0550ae",
    "name.variable": "#24292f", "name.variable.instance": "#24292f",
    "name.label": "#0550ae", "string": "#0a3069", "string.doc": "#6e7781",
    "string.affix": "#0a3069", "string.interpol": "#0a3069",
    "string.escape": "#0a3069", "string.regex": "#0a3069",
    "string.symbol": "#0a3069", "number": "#0550ae", "operator": "#cf222e",
    "operator.word": "#cf222e", "punctuation": "#24292f", "comment": "#6e7781",
    "comment.preproc": "#cf222e", "comment.special": "#8250df",
    "generic.heading": "#0550ae", "generic.subheading": "#0550ae",
    "generic.deleted": "#cf222e", "generic.inserted": "#1a7f37",
    "generic.emph": "#24292f", "generic.strong": "#24292f",
    "literal": "#0550ae", "error": "#cf222e",
}

_ITALIC_TOKENS = {"comment", "comment.preproc", "comment.special", "generic.emph", "string.doc"}
_BOLD_TOKENS = {"keyword", "name.class", "name.function", "generic.strong", "generic.heading"}


def _fmt_for(token_type) -> QTextCharFormat:
    from src.ui.theme import is_dark_theme

    colors = _COLORS if is_dark_theme() else _LIGHT_COLORS
    # token.Keyword 这种点分标识统一化成小写字符串
    key = str(token_type).lower()
    # pygments token name: "Token.Keyword.Declaration" -> "keyword.declaration"
    if key.startswith("token."):
        key = key[6:]

    color = None
    italic = False
    bold = False
    # 从最具体到最宽泛查找
    parts = key.split(".")
    while parts:
        k = ".".join(parts)
        if k in colors and color is None:
            color = colors[k]
        if k in _ITALIC_TOKENS:
            italic = True
        if k in _BOLD_TOKENS:
            bold = True
        parts.pop()
    if color is None:
        color = colors["default"]

    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if italic:
        fmt.setFontItalic(True)
    if bold:
        fmt.setFontWeight(QFont.Weight.DemiBold)
    return fmt


_FORMAT_CACHE: dict[str, QTextCharFormat] = {}


def _get_format(token_type) -> QTextCharFormat:
    key = str(token_type)
    cached = _FORMAT_CACHE.get(key)
    if cached is None:
        cached = _fmt_for(token_type)
        _FORMAT_CACHE[key] = cached
    return cached


# ---- 文件名 → lexer ----

_LANG_BY_EXT = {
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala", ".groovy": "groovy",
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "jsx",
    ".vue": "html+twig",   # pygments 没 vue lexer，用 html+embedded JS 近似；也可 fallback
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "scss", ".less": "less",
    ".json": "json", ".jsonc": "json",
    ".xml": "xml", ".pom": "xml",
    ".yml": "yaml", ".yaml": "yaml",
    ".toml": "toml",
    ".properties": "properties", ".ini": "ini", ".cfg": "ini",
    ".sh": "bash", ".bash": "bash",
    ".bat": "batch", ".cmd": "batch", ".ps1": "powershell",
    ".sql": "sql",
    ".md": "markdown", ".markdown": "markdown",
    ".dockerfile": "docker",
    ".gradle": "groovy",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".go": "go", ".rs": "rust",
    ".rb": "ruby", ".php": "php",
    ".dart": "dart", ".swift": "swift",
    ".log": None,
}


def get_lexer_for(path: str, content: str = ""):
    """根据路径挑一个合适的 lexer。返回 None 表示不做高亮"""
    if not _PYGMENTS_OK:
        return None
    p = Path(path)
    name = p.name.lower()
    ext = p.suffix.lower()

    # 特殊文件名
    if name == "dockerfile":
        return _safe_lexer("docker")
    if name == "makefile":
        return _safe_lexer("makefile")
    if name.startswith("gradlew"):
        return None

    lang = _LANG_BY_EXT.get(ext)
    if lang is None and ext in _LANG_BY_EXT:
        return None   # 显式标记不高亮
    if lang:
        return _safe_lexer(lang)

    # 让 pygments 根据文件名猜
    try:
        return get_lexer_for_filename(name, stripnl=False)
    except Exception:
        # 基于内容猜（成本高，只在没有后缀时尝试）
        if content:
            try:
                from pygments.lexers import guess_lexer
                return guess_lexer(content[:4096], stripnl=False)
            except Exception:
                return None
        return None


def _safe_lexer(name: str):
    try:
        return get_lexer_by_name(name, stripnl=False)
    except ClassNotFound:
        return None


# ---- 高亮器 ----

class PygmentsHighlighter(QSyntaxHighlighter):
    """把 Pygments 的 token 流转成 Qt 的 block-level 高亮"""

    def __init__(self, document: QTextDocument, lexer=None):
        super().__init__(document)
        self._lexer = lexer
        # block_number -> list[(start_in_block, length, QTextCharFormat)]
        self._block_formats: dict[int, list[tuple[int, int, QTextCharFormat]]] = {}
        # 编辑后 200ms debounce 重新 tokenize：单次按键不立即触发，
        # 大文件不会高频卡顿；停止打字 200ms 颜色刷新（肉眼几乎不可察觉的延迟）。
        # 不挂这个的话编辑后旧 token 索引留着，反注释 `#` 后行还显示注释灰色。
        self._retokenize_timer = QTimer(self)
        self._retokenize_timer.setSingleShot(True)
        self._retokenize_timer.setInterval(200)
        self._retokenize_timer.timeout.connect(self.retokenize)
        document.contentsChange.connect(self._on_contents_change)
        if lexer:
            self.retokenize()

    def _on_contents_change(self, position: int, chars_removed: int, chars_added: int) -> None:
        if chars_removed == 0 and chars_added == 0:
            return   # contentsChange 偶发零变更触发，跳过
        self._retokenize_timer.start()

    def set_lexer(self, lexer) -> None:
        self._lexer = lexer
        self.retokenize()

    def refresh_theme(self) -> None:
        _FORMAT_CACHE.clear()
        self.retokenize()

    def retokenize(self) -> None:
        """根据当前文档全文重新 tokenize，建立 block→formats 索引"""
        self._block_formats.clear()
        if not self._lexer or not _PYGMENTS_OK:
            self.rehighlight()
            return
        text = self.document().toPlainText()
        if len(text.encode("utf-8", errors="ignore")) > MAX_HIGHLIGHT_BYTES:
            self.rehighlight()
            return

        # 累加 block 起点位置
        block_starts: list[int] = [0]
        pos = 0
        for ch in text:
            if ch == "\n":
                block_starts.append(pos + 1)
            pos += 1

        def find_block(offset: int) -> int:
            # 二分（但 Python 没 builtin 2-sided，用 bisect）
            import bisect
            idx = bisect.bisect_right(block_starts, offset) - 1
            return max(0, idx)

        try:
            tokens = self._lexer.get_tokens_unprocessed(text)
        except Exception:
            self.rehighlight()
            return

        for start, ttype, token_text in tokens:
            if not token_text:
                continue
            fmt = _get_format(ttype)
            # 如果 token 跨行，按行切分
            remaining = token_text
            cur = start
            while remaining:
                nl = remaining.find("\n")
                if nl < 0:
                    seg_len = len(remaining)
                    b_idx = find_block(cur)
                    off = cur - block_starts[b_idx]
                    self._block_formats.setdefault(b_idx, []).append((off, seg_len, fmt))
                    cur += seg_len
                    remaining = ""
                else:
                    seg_len = nl
                    if seg_len > 0:
                        b_idx = find_block(cur)
                        off = cur - block_starts[b_idx]
                        self._block_formats.setdefault(b_idx, []).append((off, seg_len, fmt))
                    cur += seg_len + 1
                    remaining = remaining[seg_len + 1:]

        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        entries = self._block_formats.get(self.currentBlock().blockNumber())
        if not entries:
            return
        for offset, length, fmt in entries:
            self.setFormat(offset, length, fmt)
