"""工作区业务逻辑。

MainWindow 只负责菜单和弹窗；候选识别、保存、关闭后的配置状态更新
放在这里，避免窗口类继续膨胀。
"""
from __future__ import annotations

import time
from pathlib import Path

from src.core.config import AppConfig, WorkspaceEntry


def looks_like_workspace_parent(paths: list[str]) -> bool:
    names = {Path(p).name.lower() for p in paths}
    has_backend = bool(names & {"server", "backend", "api"})
    has_frontend = any(
        n.startswith("webapp") or n in {"frontend", "front", "ui"}
        for n in names
    )
    has_gateway = "nginx" in names or "gateway" in names
    return (
        (has_backend and has_frontend)
        or (has_backend and has_gateway)
        or (has_frontend and has_gateway)
    )


def ensure_workspace_candidates(config: AppConfig) -> bool:
    """从最近项目中自动补充工作区候选，返回配置是否有变化。"""
    groups: dict[str, list[str]] = {}
    for p in config.recent_projects:
        path = Path(p.path)
        if not path.parent:
            continue
        parent = str(path.parent)
        groups.setdefault(parent, []).append(p.path)

    changed = False
    for parent, paths in groups.items():
        unique = []
        for p in paths:
            if p not in unique and Path(p).is_dir():
                unique.append(p)
        if len(unique) < 2:
            continue
        if not looks_like_workspace_parent(unique):
            continue
        name = Path(parent).name
        if config.find_workspace(name):
            continue
        config.upsert_workspace(WorkspaceEntry(
            name=name, paths=unique, last_opened_at=0.0,
        ))
        changed = True
    return changed


def save_workspace(config: AppConfig, name: str, paths: list[str]) -> WorkspaceEntry:
    ws = WorkspaceEntry(name=name, paths=paths, last_opened_at=time.time())
    config.upsert_workspace(ws)
    config.active_workspace_name = name
    return ws


def mark_workspace_opened(config: AppConfig, ws: WorkspaceEntry) -> None:
    ws.last_opened_at = time.time()
    config.active_workspace_name = ws.name
    config.upsert_workspace(ws)


def close_workspace_paths(config: AppConfig, current_paths: list[str]) -> set[str]:
    """返回需要关闭的项目路径，并同步关闭后的恢复策略。"""
    current = config.active_workspace_name
    if current and config.find_workspace(current):
        ws = config.find_workspace(current)
        paths = set(ws.paths if ws else [])
    else:
        paths = set(current_paths)
    config.active_workspace_name = ""
    if config.startup_restore_mode == "workspace":
        config.startup_restore_mode = "last_session"
    return paths
