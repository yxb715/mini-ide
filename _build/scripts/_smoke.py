"""冒烟测试：所有模块能 import + theme 之外没有 hex 颜色 hardcode

第二项（hex 扫描）是 CLAUDE.md 硬约束 15 的强制执行：
任何 widget 文件里出现 #abcdef 这种字面量都会让冒烟失败。
颜色必须来自 src/ui/theme.py 的 token。
"""
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 不论从哪 cwd 跑，都把项目根加到 sys.path
sys.path.insert(0, str(ROOT))

modules = [
    "src.core.config",
    "src.core.project_detector",
    "src.core.process_runner",
    "src.core.log_classifier",
    "src.core.git_worker",
    "src.core.external_detector",
    "src.ui.theme",
    "src.ui.styles",
    "src.ui.log_widget",
    "src.ui.settings_panel",
    "src.ui.service_panel",
    "src.ui.project_tab",
    "src.ui.empty_state",
    "src.ui.file_tree",
    "src.ui.file_preview",
    "src.ui.syntax_highlighter",
    "src.core.file_index",
    "src.core.controller_index",
    "src.ui.quick_open",
    "src.ui.content_search",
    "src.core.git_ops",
    "src.core.port_scanner",
    "src.core.env_scanner",
    "src.ui.git_viewer",
    "src.ui.port_dialog",
    "src.ui.env_panel",
    "src.ui.main_window",
    "src.util.editor",
    "src.util.git_info",
    "src.util.notify",
]


def import_check() -> list[str]:
    failed: list[str] = []
    for m in modules:
        try:
            __import__(m)
            print(f"[OK]   {m}", flush=True)
        except Exception as e:
            print(f"[FAIL] {m}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
            failed.append(m)
    return failed


# 允许出现 hex 字面量的文件（语义独立的"色板"，不属于 UI 主题范畴）
_HEX_WHITELIST = {
    "src/ui/theme.py",          # 主题 token 的唯一来源
    "src/ui/styles.py",          # 兼容外壳，转发到 theme
    "src/ui/syntax_highlighter.py",  # Pygments 代码语法色板（OneDark 风格）
}

# 抓任何位置的 hex 颜色字面量（包括 inline QSS 字符串内部，如 "background:#abc;"）
# \b 收尾保证只匹配 6 位（不抓 #abcdef00 之类的 8 位带 alpha 颜色，本项目不用）
_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
# Python 行注释起始：行首是 # 或前面只有空白 + #
_COMMENT_RE = re.compile(r"^\s*#")


def hex_scan() -> list[tuple[str, int, str]]:
    """扫描 src/ 下所有 .py，返回 [(rel_path, lineno, line)] 表示违规命中"""
    hits: list[tuple[str, int, str]] = []
    src_dir = ROOT / "src"
    for py in src_dir.rglob("*.py"):
        rel = py.relative_to(ROOT).as_posix()
        if rel in _HEX_WHITELIST:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _COMMENT_RE.match(line):
                continue   # 整行注释里写颜色不算 hardcode
            if _HEX_RE.search(line):
                hits.append((rel, i, line.rstrip()))
    return hits


if __name__ == "__main__":
    failed = import_check()

    print()
    print("Hex hardcode scan (CLAUDE.md hard rule 15):")
    hex_hits = hex_scan()
    if not hex_hits:
        print("[OK]   no hex literals outside theme/syntax")
    else:
        # Windows console 默认 GBK，行内容里的 emoji 会让 print 抛 UnicodeEncodeError，
        # 用 errors='replace' 兜底——把不能编码的字符替成 '?' 不影响诊断
        enc = (sys.stdout.encoding or "utf-8")
        for rel, ln, line in hex_hits:
            safe = line.encode(enc, errors="replace").decode(enc, errors="replace")
            print(f"[HEX]  {rel}:{ln}: {safe}")

    total_fail = len(failed) + len(hex_hits)
    print()
    print(f"Result: imports {len(modules) - len(failed)}/{len(modules)}, "
          f"hex hits {len(hex_hits)}", flush=True)
    sys.exit(1 if total_fail else 0)
