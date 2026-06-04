"""调用外部编辑器打开文件到指定行

优先级：用户配置的 editor_cmd > VS Code (code) > IDEA (idea64.exe) > 系统默认
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
_POPEN_KW = {"creationflags": _NO_WINDOW}

# 文件管理器名字（菜单/按钮文案用）
REVEAL_LABEL = "在资源管理器中显示"


def open_in_editor(path: str, line: int = 0, column: int = 0,
                   editor_cmd: str = "", project_root: str = "") -> bool:
    """打开文件到指定行。

    优先级：
    1. 用户配置的 editor_cmd（config.json 里 editor_cmd）
    2. 自动探测：VS Code → IDEA → WebStorm → PyCharm → Notepad++ → Sublime
    3. 系统默认关联程序（一般是记事本）

    都失败返回 False（调用方可回落到内置预览窗口）。
    """
    p = Path(path)
    if not p.is_absolute() and project_root:
        candidate = search_in_project(Path(project_root), p.name)
        if candidate:
            p = candidate
    if not p.exists():
        return False

    target = str(p)

    if editor_cmd.strip():
        if _run_editor(editor_cmd, target, line, column):
            return True
        # 用户指定的命令失败时继续往下 fallback，不直接返回

    # 自动探测常见编辑器
    candidates: list[tuple[str, str]] = [
        ("code", "code -g"),            # VS Code
        ("code.cmd", "code.cmd -g"),
        ("idea64.exe", "idea64 --line"),
        ("idea.exe", "idea --line"),
        ("idea", "idea --line"),
        ("webstorm64.exe", "webstorm64 --line"),
        ("webstorm", "webstorm --line"),
        ("pycharm64.exe", "pycharm64 --line"),
        ("pycharm", "pycharm --line"),
        ("notepad++.exe", "notepad++"),
        ("subl.exe", "subl"),
        ("subl", "subl"),
    ]
    for probe, cmd in candidates:
        if shutil.which(probe):
            if _run_editor(cmd, target, line, column):
                return True

    # 兜底：系统默认关联
    return QDesktopServices.openUrl(QUrl.fromLocalFile(target))


def has_any_editor() -> bool:
    """是否至少有一个已知外部编辑器可用"""
    for probe in ("code", "code.cmd", "idea64.exe", "idea", "webstorm", "pycharm",
                  "notepad++.exe", "subl"):
        if shutil.which(probe):
            return True
    return False


def open_folder(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


# 办公文档类型：双击应交给系统默认关联程序（docx→WPS/Word、pdf→默认阅读器…），
# 而不是塞进内置文本预览（会显示"二进制不可预览"）或代码编辑器（乱码）。
# 刻意不含 zip/exe/dll（双击 exe 会执行、有风险）和图片（内置看图更顺手）。
_OFFICE_DOC_EXTS = {
    # Word 系
    ".doc", ".docx", ".docm", ".dot", ".dotx", ".dotm", ".rtf", ".odt",
    # Excel 系
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".ods",
    # PowerPoint 系
    ".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".pot", ".potx", ".odp",
    # PDF
    ".pdf",
    # WPS 原生格式
    ".wps", ".et", ".dps", ".ett", ".wpt", ".dpt",
    # Visio / Project
    ".vsd", ".vsdx", ".mpp",
}


def is_office_doc(path: str) -> bool:
    """是否为应交给系统默认程序打开的办公文档类型"""
    return Path(path).suffix.lower() in _OFFICE_DOC_EXTS


def open_with_system_default(path: str) -> bool:
    """用系统默认关联程序打开文件，等价于在资源管理器里双击。

    Windows 优先走 os.startfile（即 ShellExecute，双击的底层调用），
    失败回落到 QDesktopServices.openUrl。文件不存在返回 False。
    """
    p = Path(path)
    if not p.exists():
        return False
    try:
        os.startfile(str(p))  # type: ignore[attr-defined]  # Windows 专属
        return True
    except (OSError, AttributeError):
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


def reveal_in_explorer(path: str) -> None:
    """在资源管理器中选中文件"""
    p = Path(path)
    if not p.exists():
        return
    subprocess.Popen(["explorer", "/select,", str(p)], **_POPEN_KW)


# ---- 内部 ----

def _run_editor(cmd: str, target: str, line: int, column: int) -> bool:
    """解析 editor_cmd，追加 target 并根据编辑器格式带上行号"""
    parts = shlex.split(cmd, posix=False)
    if not parts:
        return False
    prog = parts[0].lower()
    extra = parts[1:]

    if "code" in prog:  # VS Code: code -g file:line:col
        goto = target if line <= 0 else (f"{target}:{line}" + (f":{column}" if column else ""))
        if "-g" not in extra:
            extra = ["-g"] + extra
        args = [parts[0], *extra, goto]
    elif "idea" in prog or "webstorm" in prog or "pycharm" in prog or "rustrover" in prog or "clion" in prog:
        # JetBrains 系：xxx --line 42 --column 10 file
        args = [parts[0]]
        if line > 0:
            args += ["--line", str(line)]
        if column > 0:
            args += ["--column", str(column)]
        args += extra + [target]
    elif "notepad++" in prog:
        # Notepad++: notepad++ -n42 file
        args = [parts[0], *extra]
        if line > 0:
            args.append(f"-n{line}")
        args.append(target)
    elif "subl" in prog:
        # Sublime: subl file:line:col
        goto = target if line <= 0 else (f"{target}:{line}" + (f":{column}" if column else ""))
        args = [parts[0], *extra, goto]
    elif prog in ("notepad", "notepad.exe"):
        # 记事本：不支持跳行，直接打开
        args = [parts[0], target]
    else:
        args = [parts[0], *extra, target]

    try:
        prog_args = _resolve_launch(args)
        subprocess.Popen(prog_args, close_fds=True, **_POPEN_KW)
        return True
    except OSError:
        return False


def _resolve_launch(args: list[str]) -> list[str]:
    """把命令名解析成可被 CreateProcess 直接启动的形式。

    Windows 下 subprocess.Popen 不走 shell，CreateProcess 既不查 PATHEXT、
    也不能直接执行 .cmd/.bat（VS Code 的 `code` 实际是 code.cmd 包装器）。
    所以这里用 shutil.which 解析全路径，遇到批处理就改走 `cmd /c`。
    """
    if not args:
        return args
    prog = args[0]
    rest = args[1:]
    lower = prog.lower()
    if lower.endswith((".bat", ".cmd")):
        return ["cmd", "/c", prog, *rest]
    if lower.endswith(".exe") or os.path.isabs(prog):
        return args
    resolved = shutil.which(prog)
    if resolved:
        if resolved.lower().endswith((".bat", ".cmd")):
            return ["cmd", "/c", resolved, *rest]
        return [resolved, *rest]
    return args


def search_in_project(root: Path, filename: str) -> Path | None:
    """在项目目录里按文件名查找（忽略 build/.gradle/node_modules 等），只取第一个匹配。

    公开函数，外部模块（如 ProjectTab）可直接调用。
    """
    skip = {"build", ".gradle", "target", "node_modules", "dist", ".idea", "__pycache__",
            ".next", ".nuxt", "out", ".venv", "venv"}
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            if filename in filenames:
                return Path(dirpath) / filename
    except OSError:
        pass
    return None
