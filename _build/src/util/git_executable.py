"""Resolve Git without the Git for Windows launcher wrapper."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import shutil
import sys


@lru_cache(maxsize=1)
def resolve_git_executable() -> str:
    command = shutil.which("git.exe") or shutil.which("git")
    if not command:
        return "git"

    path = Path(command)
    if sys.platform == "win32" and path.parent.name.casefold() in {"cmd", "bin"}:
        install_root = path.parent.parent
        for runtime_dir in ("mingw64", "mingw32"):
            candidate = install_root / runtime_dir / "bin" / "git.exe"
            if candidate.is_file():
                return str(candidate)
    return str(path)
