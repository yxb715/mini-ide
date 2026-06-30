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
    "src.core.file_actions",
    "src.core.file_preview_model",
    "src.core.git_context",
    "src.core.service_state",
    "src.core.tool_launchers",
    "src.core.workspace_manager",
    "src.core.log_classifier",
    "src.core.git_worker",
    "src.core.external_detector",
    "src.ui.theme",
    "src.ui.styles",
    "src.ui.log_widget",
    "src.ui.project_service_controller",
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
    "src.ui.toast",
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


def service_state_check() -> list[str]:
    """轻量验证服务状态模型的 CLI 兼容输出。"""
    from src.core.service_state import (
        KIND_MODULE, STATE_RUNNING_EXTERNAL, STATE_STOPPED,
        ServiceState, running_items,
    )

    failed: list[str] = []
    stopped = ServiceState(
        project="p", module="m", kind=KIND_MODULE, state=STATE_STOPPED,
    )
    external = ServiceState(
        project="p", module="m", kind=KIND_MODULE,
        state=STATE_RUNNING_EXTERNAL, pid=1234, port=8080,
    )
    cli_stopped = stopped.to_cli_module()
    cli_external = external.to_cli_module()
    if cli_stopped["state"] != "stopped" or cli_stopped["external"]:
        failed.append("stopped service should keep legacy stopped output")
    if cli_external["state"] != "running" or not cli_external["external"]:
        failed.append("external service should keep legacy running output")
    if running_items([stopped, external]) != [external.to_running_item()]:
        failed.append("running_items should filter inactive services")

    if failed:
        for msg in failed:
            print(f"[FAIL] service_state: {msg}", flush=True)
    else:
        print("[OK]   service_state compatibility", flush=True)
    return failed


def cli_parse_check() -> list[str]:
    """验证公开 CLI 的关键参数能解析成稳定 JSON 命令。"""
    from src.core.cli_client import _parse_args

    cases = [
        (["mini-ide.exe", "--status"], {"cmd": "status"}),
        (["mini-ide.exe", "--open", r"E:\demo"], {"cmd": "open", "path": r"E:\demo"}),
        (["mini-ide.exe", "--close", "server"], {"cmd": "close", "project": "server"}),
        (["mini-ide.exe", "--list-workspaces"], {"cmd": "list-workspaces"}),
        (["mini-ide.exe", "--open-workspace", "race", "dev"], {"cmd": "open-workspace", "name": "race dev"}),
        (["mini-ide.exe", "--close-workspace"], {"cmd": "close-workspace"}),
        (["mini-ide.exe", "--start", "server", "gateway", "--wait", "--timeout", "90"],
         {"cmd": "start", "project": "server", "module": "gateway", "wait": True, "timeout": 90}),
        (["mini-ide.exe", "--ensure-running", "server", "gateway", "--timeout", "120"],
         {"cmd": "ensure-running", "project": "server", "module": "gateway", "timeout": 120}),
        (["mini-ide.exe", "--log", "server", "--all-modules", "--errors", "--tail", "200"],
         {"cmd": "log", "project": "server", "all_modules": True, "errors": True, "tail": 200}),
        (["mini-ide.exe", "--diagnose", "server", "gateway"],
         {"cmd": "diagnose", "project": "server", "module": "gateway", "tail": 120}),
        (["mini-ide.exe", "--git-diff", "server", "--full", "--max-chars", "12000"],
         {"cmd": "git-diff", "project": "server", "mode": "full", "max_chars": 12000}),
        (["mini-ide.exe", "--preflight-build"], {"cmd": "preflight-build"}),
        (["mini-ide.exe", "--can-quit"], {"cmd": "preflight-build"}),
    ]

    failed: list[str] = []
    for argv, expected in cases:
        actual = _parse_args(argv)
        if actual != expected:
            failed.append(f"{argv[1]} parsed as {actual!r}, expected {expected!r}")
    if _parse_args(["mini-ide.exe", "--unknown"]) is not None:
        failed.append("unknown command should fail parsing")

    if failed:
        for msg in failed:
            print(f"[FAIL] cli_parse: {msg}", flush=True)
    else:
        print("[OK]   cli_parse compatibility", flush=True)
    return failed


def file_action_check() -> list[str]:
    """验证文件树粘贴命名和安全边界的纯逻辑。"""
    import tempfile

    from src.core.file_actions import copy_destination, is_same_or_child, paste_paths

    failed: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "target"
        target.mkdir()
        src_file = root / "note.md"
        src_file.write_text("a", encoding="utf-8")
        existing = target / "note.md"
        existing.write_text("b", encoding="utf-8")
        if copy_destination(target, src_file).name != "note - 副本.md":
            failed.append("copy_destination should use Explorer-like duplicate name")

        src_dir = root / "dir"
        child_dir = src_dir / "child"
        child_dir.mkdir(parents=True)
        if not is_same_or_child(child_dir, src_dir):
            failed.append("is_same_or_child should identify child directory")
        changed, errors = paste_paths([src_dir], child_dir, move=False)
        if changed != 0 or not errors:
            failed.append("paste_paths should block copying a directory into its child")

    if failed:
        for msg in failed:
            print(f"[FAIL] file_actions: {msg}", flush=True)
    else:
        print("[OK]   file_actions rules", flush=True)
    return failed


def file_preview_model_check() -> list[str]:
    """验证预览核心判断：编码、行尾、注释前缀。"""
    from src.core.file_preview_model import (
        detect_encoding, detect_newline, image_suffix_format, line_comment_prefix,
        should_wrap_text,
    )

    failed: list[str] = []
    text, enc = detect_encoding("中文".encode("gbk"))
    if text != "中文" or enc != "gbk":
        failed.append("detect_encoding should preserve GBK text")
    if detect_newline("a\r\nb\r\n") != "\r\n":
        failed.append("detect_newline should detect CRLF")
    if line_comment_prefix("Demo.java") != "//":
        failed.append("line_comment_prefix should map Java to //")
    if line_comment_prefix("Dockerfile") != "#":
        failed.append("line_comment_prefix should map Dockerfile to #")
    if not should_wrap_text("readme.md"):
        failed.append("markdown should use soft wrap")
    if image_suffix_format("icon.PNG") != "png":
        failed.append("image_suffix_format should normalize image suffix")

    if failed:
        for msg in failed:
            print(f"[FAIL] file_preview_model: {msg}", flush=True)
    else:
        print("[OK]   file_preview_model rules", flush=True)
    return failed


def git_context_check() -> list[str]:
    """验证 Git AI 上下文的标签、排序和摘要格式。"""
    from src.core.git_context import build_ai_text, sort_changed_files, status_label, summary_from_changes
    from src.core.git_ops import ChangedFile

    failed: list[str] = []
    files = [
        ChangedFile("??", "new.txt", False, True),
        ChangedFile("M ", "src/app.py", True, False),
        ChangedFile(" M", "README.md", False, True),
    ]
    labels = [status_label(f) for f in files]
    if labels != ["未跟踪", "已暂存", "修改"]:
        failed.append(f"unexpected labels: {labels}")
    sorted_paths = [f.path for f in sort_changed_files(files)]
    if sorted_paths[0] != "README.md":
        failed.append(f"unexpected git sort order: {sorted_paths}")
    summary = summary_from_changes(
        repo=r"E:\repo",
        branch="main",
        files=files,
        stats={"src/app.py": (3, 1)},
        include_files=True,
    )
    text = build_ai_text(summary)
    if "影响目录：" not in text or "- [已暂存] src/app.py +3 -1" not in text:
        failed.append("git summary text should include groups and numstat")

    if failed:
        for msg in failed:
            print(f"[FAIL] git_context: {msg}", flush=True)
    else:
        print("[OK]   git_context rules", flush=True)
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
    model_failed = service_state_check()
    cli_failed = cli_parse_check()
    file_failed = file_action_check()
    preview_failed = file_preview_model_check()
    git_failed = git_context_check()

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

    total_fail = (
        len(failed) + len(model_failed) + len(cli_failed)
        + len(file_failed) + len(preview_failed) + len(git_failed) + len(hex_hits)
    )
    print()
    print(f"Result: imports {len(modules) - len(failed)}/{len(modules)}, "
          f"hex hits {len(hex_hits)}", flush=True)
    sys.exit(1 if total_fail else 0)
