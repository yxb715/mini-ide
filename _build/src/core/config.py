"""应用配置持久化

跨会话保持，存储位置：%APPDATA%/mini-ide/
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    target = base / "mini-ide"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _default_project_dir_guess() -> str:
    """猜测默认项目目录（每台机器自适应，猜不到返回空串）。

    在当前用户家目录下找常见的项目根目录名；都没有就返回空，由对话框 fallback。
    """
    home = Path.home()
    for name in ("Developer", "Projects", "projects", "project", "workspace", "dev", "code"):
        cand = home / name
        if cand.is_dir():
            return str(cand)
    return ""


CONFIG_PATH = _config_dir() / "config.json"
LOG_DIR = _config_dir() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ProjectEntry:
    """已打开过的项目（用于最近项目列表和 Tab 恢复）"""
    path: str
    name: str = ""
    project_type: str = ""
    last_opened_at: float = 0.0


@dataclass
class WorkspaceEntry:
    """一组经常一起打开/关闭的项目"""
    name: str
    paths: list[str] = field(default_factory=list)
    last_opened_at: float = 0.0


@dataclass
class AppConfig:
    recent_projects: list[ProjectEntry] = field(default_factory=list)
    workspaces: list[WorkspaceEntry] = field(default_factory=list)
    # 已打开的聚合项目定义根目录；定义正文仍保存在各项目 .mini-ide/project.json。
    aggregate_project_paths: list[str] = field(default_factory=list)
    # 旧 WorkspaceEntry 名称 -> 已确认迁移到的聚合项目根目录。
    legacy_workspace_migrations: dict[str, str] = field(default_factory=dict)
    # 旧字段只为读取兼容保留，不再驱动全局工作区 UI。
    active_workspace_name: str = ""
    # last_session：恢复上次 Tab；旧 workspace 值也按 last_session；none：不恢复。
    startup_restore_mode: str = "last_session"
    # 上次关闭时打开的 Tab：普通项目或聚合目录；旧 workspace:management 会被忽略。
    active_tabs: list[str] = field(default_factory=list)
    active_tab_index: int = 0
    restore_tabs_on_startup: bool = True
    # 含 x/y/w/h（int）和 maximized（bool）
    window_geometry: dict = field(default_factory=dict)
    # 编辑器命令：空则自动探测（VS Code → IDEA → Notepad++ → Sublime）
    # 仅在 file_open_mode=external 时生效。默认 preview 模式不依赖外部程序。
    editor_cmd: str = ""
    # 双击文件 / 点错误跳转时的行为：
    #   preview（默认）：用内置预览窗口（带语法高亮、可编辑保存、Ctrl+F 搜索）
    #   external：用 VS Code / IDEA 等外部编辑器
    #   auto：先试外部，失败回落内置
    file_open_mode: str = "preview"
    show_memory_usage: bool = True
    # 每个 Tab 保留的最大日志行数（超过会自动淘汰最旧）。
    # 10000 行 ≈ 1MB 显存；降低能省内存，但看历史日志范围变短。
    max_log_blocks: int = 10000
    # "打开项目" 对话框默认定位到的目录。空字符串：fallback 到最近项目父目录或家目录。
    # 不写死具体路径——每台机器首次启动时由 load() 自动探测（见 _default_project_dir_guess）。
    default_project_dir: str = ""
    # 聚合项目页左侧项目树宽度；用户拖动分隔条后跨会话保留。
    aggregate_sidebar_width: int = 320

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_PATH.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        # 过滤掉 dataclass 里已经不存在的字段，避免删字段后老 config 启动炸
        from dataclasses import fields
        entry_known = {f.name for f in fields(ProjectEntry)}
        recent = [
            ProjectEntry(**{k: v for k, v in p.items() if k in entry_known})
            for p in raw.pop("recent_projects", [])
        ]
        workspace_known = {f.name for f in fields(WorkspaceEntry)}
        workspaces = [
            WorkspaceEntry(**{k: v for k, v in w.items() if k in workspace_known})
            for w in raw.pop("workspaces", [])
        ]
        known = {f.name for f in fields(cls)}
        raw = {k: v for k, v in raw.items() if k in known}
        cfg = cls(**raw)
        cfg.recent_projects = recent
        cfg.workspaces = workspaces

        # 老配置迁移：默认改成内置预览（用户反馈外部编辑器链路不符合预期）
        if cfg.file_open_mode == "auto":
            cfg.file_open_mode = "preview"
            cfg.save()

        if not cfg.default_project_dir:
            guess = _default_project_dir_guess()
            if guess:
                cfg.default_project_dir = guess
                cfg.save()

        return cfg

    def save(self) -> None:
        data: dict[str, Any] = asdict(self)
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def touch_project(self, entry: ProjectEntry) -> None:
        self.recent_projects = [p for p in self.recent_projects if p.path != entry.path]
        self.recent_projects.insert(0, entry)
        self.recent_projects = self.recent_projects[:20]

    def find_project(self, path: str) -> ProjectEntry | None:
        return next((p for p in self.recent_projects if p.path == path), None)

    def upsert_workspace(self, workspace: WorkspaceEntry) -> None:
        paths: list[str] = []
        for p in workspace.paths:
            if p and p not in paths:
                paths.append(p)
        workspace.paths = paths
        self.workspaces = [w for w in self.workspaces if w.name != workspace.name]
        self.workspaces.insert(0, workspace)
        self.workspaces = self.workspaces[:20]

    def find_workspace(self, name: str) -> WorkspaceEntry | None:
        return next((w for w in self.workspaces if w.name == name), None)

    def register_aggregate_project(self, path: str) -> None:
        """按规范化路径注册聚合项目，同时保留最近使用顺序。"""
        from src.core.path_utils import canonical_path, normalized_path_key

        canonical = str(canonical_path(path))
        key = normalized_path_key(canonical)
        self.aggregate_project_paths = [
            item for item in self.aggregate_project_paths
            if normalized_path_key(item) != key
        ]
        self.aggregate_project_paths.insert(0, canonical)
        self.aggregate_project_paths = self.aggregate_project_paths[:50]

    def unregister_aggregate_project(self, path: str) -> None:
        """取消人工聚合分类；目录内已有定义保留，避免静默丢数据。"""
        from src.core.path_utils import normalized_path_key

        key = normalized_path_key(path)
        self.aggregate_project_paths = [
            item for item in self.aggregate_project_paths
            if normalized_path_key(item) != key
        ]

    def is_aggregate_project(self, path: str) -> bool:
        """只有用户明确登记过的目录才按聚合目录打开。"""
        from src.core.path_utils import normalized_path_key

        key = normalized_path_key(path)
        return any(
            normalized_path_key(item) == key
            for item in self.aggregate_project_paths
        )

    def mark_legacy_workspace_migrated(self, name: str, path: str) -> None:
        """只记录迁移结果，不删除或改写旧 WorkspaceEntry。"""
        from src.core.path_utils import canonical_path

        self.legacy_workspace_migrations[name] = str(canonical_path(path))
