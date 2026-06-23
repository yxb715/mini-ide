"""Pure file operation helpers used by the file tree UI."""
from __future__ import annotations

import shutil
from pathlib import Path


def is_same_or_child(path: Path, parent: Path) -> bool:
    """Return True when path is parent itself or inside parent."""
    try:
        path_resolved = path.resolve()
        parent_resolved = parent.resolve()
    except OSError:
        return False
    if path_resolved == parent_resolved:
        return True
    try:
        path_resolved.relative_to(parent_resolved)
        return True
    except ValueError:
        return False


def copy_destination(target_dir: Path, src: Path) -> Path:
    """Choose Explorer-like copy destination, avoiding name collisions."""
    is_dir = src.is_dir()
    base = src.name if is_dir else src.stem
    suffix = "" if is_dir else src.suffix
    candidate = target_dir / src.name
    if not candidate.exists():
        return candidate
    candidate = target_dir / f"{base} - 副本{suffix}"
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = target_dir / f"{base} - 副本 {index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def paste_paths(sources: list[Path], target_dir: Path, move: bool = False) -> tuple[int, list[str]]:
    """Copy or move sources into target_dir.

    Returns (changed_count, failed_messages). The caller decides how to show
    progress and errors.
    """
    failed: list[str] = []
    changed = 0
    if not target_dir.exists() or not target_dir.is_dir():
        return changed, [f"目标目录不存在：{target_dir}"]

    for src in sources:
        if not src.exists():
            failed.append(f"源路径不存在：{src}")
            continue
        try:
            if src.is_dir():
                if is_same_or_child(target_dir, src):
                    failed.append(f"不能把目录粘贴到自身或子目录：{src}")
                    continue
                if move:
                    if src.parent.resolve() == target_dir.resolve():
                        continue
                    shutil.move(str(src), str(copy_destination(target_dir, src)))
                else:
                    shutil.copytree(src, copy_destination(target_dir, src))
            elif src.is_file():
                if move:
                    if src.parent.resolve() == target_dir.resolve():
                        continue
                    shutil.move(str(src), str(copy_destination(target_dir, src)))
                else:
                    shutil.copy2(src, copy_destination(target_dir, src))
            else:
                failed.append(f"不支持的路径类型：{src}")
                continue
            changed += 1
        except OSError as e:
            failed.append(f"{src}\n  {e}")
    return changed, failed
