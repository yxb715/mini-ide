"""根据项目类型选择对应的 JetBrains IDE 可执行路径

- 前端项目 (vue/react/next/nuxt/svelte/node) → WebStorm
- Python 项目 (python/python-poetry/django/fastapi/flask) → PyCharm
- 其他（Java 系 + generic）→ IntelliJ IDEA

优先在 G:\\Program Files\\JetBrains\\ 下找最新版本（按目录名倒序），
找不到时 fallback 到 PATH 里的 idea64 / webstorm64 / pycharm64。
"""
from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

_JETBRAINS_ROOT = Path(r"G:\Program Files\JetBrains")

_FRONTEND_TYPES = {"vue", "react", "next", "nuxt", "svelte", "node"}
_PYTHON_TYPES = {"python", "python-poetry", "django", "fastapi", "flask"}


@lru_cache(maxsize=8)
def _find(dir_prefix: str, exe_name: str, path_alias: str) -> str | None:
    """在 JetBrains 目录下找指定 IDE，找不到 fallback 到 PATH"""
    if _JETBRAINS_ROOT.exists():
        # 例：dir_prefix='IntelliJ IDEA' → 匹配 'IntelliJ IDEA 2025.2.1' 等
        candidates = sorted(
            _JETBRAINS_ROOT.glob(f"{dir_prefix}*"), reverse=True,
        )
        for d in candidates:
            exe = d / "bin" / exe_name
            if exe.exists():
                return str(exe)
    # fallback：用户可能装在别处但 launcher 加到了 PATH
    return shutil.which(path_alias)


def pick_ide_for(project_type: str) -> tuple[str, str] | None:
    """根据项目类型返回 (显示名, 可执行路径)；找不到返回 None"""
    if project_type in _FRONTEND_TYPES:
        exe = _find("WebStorm", "webstorm64.exe", "webstorm")
        return ("WebStorm", exe) if exe else None
    if project_type in _PYTHON_TYPES:
        exe = _find("PyCharm", "pycharm64.exe", "pycharm")
        return ("PyCharm", exe) if exe else None
    # 默认 IDEA（Java 系、generic、未知类型）
    exe = _find("IntelliJ IDEA", "idea64.exe", "idea64")
    return ("IDEA", exe) if exe else None
