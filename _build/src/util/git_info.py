"""Git 分支信息（只读）

在项目路径里跑轻量 git 命令，失败静默。在 Windows 下必须加 CREATE_NO_WINDOW
否则 GUI 程序每次调 git 都会闪一个 cmd 窗口。结果短缓存，保证 AI 改动/提交后状态栏及时刷新。
"""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from src.util.git_executable import resolve_git_executable

_CACHE: dict[str, tuple[float, "GitInfo | None"]] = {}
_TTL = 2.0    # 秒，状态栏每 3s 刷一次；短缓存保证 AI 提交后改动数及时归零

# Windows：防止 GUI 程序调 subprocess 时弹黑框
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
_POPEN_KW = {"creationflags": _NO_WINDOW} if _NO_WINDOW else {}


@dataclass
class GitInfo:
    branch: str = ""
    dirty: bool = False
    ahead: int = 0
    behind: int = 0
    changed: int = 0     # 已改 + 已暂存 + 未跟踪 的文件总数


def _git(args: list[str], cwd: str) -> str:
    return subprocess.run(
        [resolve_git_executable(), *args],
        cwd=cwd, capture_output=True, text=True, timeout=3,
        **_POPEN_KW,
    ).stdout


def get_info(project_path: str, changed_count: int | None = None) -> GitInfo | None:
    now = time.time()
    cached = _CACHE.get(project_path)
    if changed_count is None and cached and now - cached[0] < _TTL:
        return cached[1]

    root = Path(project_path)
    if not (root / ".git").exists():
        found = False
        for p in root.parents:
            if (p / ".git").exists():
                root = p
                found = True
                break
        if not found:
            _CACHE[project_path] = (now, None)
            return None

    try:
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], str(root)).strip()
        if changed_count is None:
            status = _git(["status", "--porcelain"], str(root))
            status_lines = [ln for ln in status.splitlines() if ln.strip()]
            changed = len(status_lines)
        else:
            changed = changed_count
        dirty = changed > 0
        ahead = behind = 0
        try:
            cnt = _git(["rev-list", "--left-right", "--count", "@{u}...HEAD"], str(root)).strip()
            if cnt:
                parts = cnt.split()
                if len(parts) == 2:
                    behind, ahead = int(parts[0]), int(parts[1])
        except (subprocess.SubprocessError, ValueError):
            pass
        info: GitInfo | None = GitInfo(
            branch=branch, dirty=dirty, ahead=ahead, behind=behind, changed=changed,
        )
    except (subprocess.SubprocessError, OSError):
        info = None

    _CACHE[project_path] = (now, info)
    return info


def invalidate(project_path: str) -> None:
    """让指定路径的缓存失效，下一次 get_info 重新查 git。

    替代以前外部直接 `git_info._CACHE.pop(path, None)` 的私有访问。
    """
    _CACHE.pop(project_path, None)


def format_status(info: GitInfo | None) -> str:
    if not info or not info.branch:
        return ""
    text = f"🌿 {info.branch}"
    if info.dirty:
        text += " •"
    if info.ahead:
        text += f" ↑{info.ahead}"
    if info.behind:
        text += f" ↓{info.behind}"
    return text
