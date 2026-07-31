"""跨配置复用的路径规范化和目录边界检查。"""
from __future__ import annotations

import os
from pathlib import Path


class PathBoundaryError(ValueError):
    """配置路径逃逸出允许目录。"""


def canonical_path(value: str | Path, base: str | Path | None = None) -> Path:
    """返回不要求目标已存在的规范化绝对路径。"""
    path = Path(value).expanduser()
    if not path.is_absolute() and base is not None:
        path = Path(base).expanduser() / path
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return Path(os.path.abspath(os.path.normpath(str(path))))


def normalized_path_key(value: str | Path, base: str | Path | None = None) -> str:
    """返回适合 Windows 路径比较的大小写无关 key。"""
    path = canonical_path(value, base=base)
    return os.path.normcase(os.path.normpath(str(path))).casefold()


def is_path_within(
    path: str | Path,
    parent: str | Path,
    *,
    allow_equal: bool = True,
) -> bool:
    """判断 path 是否位于 parent 内，兼容不同盘符和大小写。"""
    path_key = normalized_path_key(path)
    parent_key = normalized_path_key(parent)
    if path_key == parent_key:
        return allow_equal
    try:
        return os.path.commonpath([path_key, parent_key]) == parent_key
    except ValueError:
        return False


def resolve_path_within(
    root: str | Path,
    value: str | Path,
    *,
    allow_root: bool = False,
    label: str = "path",
) -> Path:
    """将相对/绝对路径解析到 root 内，越界时直接拒绝。"""
    root_path = canonical_path(root)
    target = canonical_path(value, base=root_path)
    if not is_path_within(target, root_path, allow_equal=allow_root):
        raise PathBoundaryError(f"{label} escapes root: {value}")
    return target


def relative_path_within(
    root: str | Path,
    value: str | Path,
    *,
    allow_root: bool = False,
    label: str = "path",
) -> str:
    """解析并返回使用正斜杠的安全相对路径。"""
    root_path = canonical_path(root)
    target = resolve_path_within(
        root_path, value, allow_root=allow_root, label=label,
    )
    return os.path.relpath(str(target), str(root_path)).replace("\\", "/")
