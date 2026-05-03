"""扫项目内的环境/配置文件（.env / application.yml / application-*.properties 等）

只读扫描，不解析也不修改。给 EnvPanel 用。
os.walk + dirnames 剪枝，避开 node_modules/.git/target/build 这些巨坑目录。
限制深度防在单体 mono-repo 里失控。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# 文件名模式：只收环境/配置类，不收 settings.gradle/pom.xml 这种构建脚本
_PATTERNS = [
    re.compile(r"^\.env(?:$|\.[\w.-]+$)"),                  # .env / .env.local / .env.development.local
    re.compile(r"^application(?:-[\w.-]+)?\.ya?ml$"),       # application.yml / application-dev.yaml
    re.compile(r"^application(?:-[\w.-]+)?\.properties$"),  # application-prod.properties
    re.compile(r"^bootstrap(?:-[\w.-]+)?\.ya?ml$"),         # Spring Cloud bootstrap
    re.compile(r"^bootstrap(?:-[\w.-]+)?\.properties$"),
    re.compile(r".+\.env$"),                                 # foo.env / local.env
]

# 扫描时剪枝，这些目录不进
_IGNORE_DIRS = {
    "node_modules", ".git", ".gradle", ".idea", ".vscode",
    "target", "build", "dist", "out", ".next", ".nuxt",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".cache", "coverage", "logs", "tmp",
}


def list_env_files(project_root: str, max_depth: int = 5) -> list[Path]:
    """返回项目根下识别到的所有环境/配置文件绝对路径，按相对路径深度 + 字母序排。
    max_depth=5 足够覆盖 Spring Boot 多模块的 xxx/src/main/resources/application.yml。
    """
    root = Path(project_root)
    if not root.is_dir():
        return []

    out: list[Path] = []
    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str):
        # dirnames 原地过滤，这样 os.walk 不会进这些子目录
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]

        # 深度剪枝：算相对根的层数
        try:
            rel_dir = Path(dirpath).relative_to(root)
        except ValueError:
            continue
        depth = 0 if rel_dir == Path(".") else len(rel_dir.parts)
        if depth >= max_depth:
            dirnames[:] = []

        for fn in filenames:
            if any(p.match(fn) for p in _PATTERNS):
                out.append(Path(dirpath) / fn)

    def sort_key(p: Path) -> tuple[int, str]:
        try:
            rel = p.relative_to(root)
        except ValueError:
            return (99, str(p).lower())
        return (len(rel.parts), str(rel).lower())

    out.sort(key=sort_key)
    return out
