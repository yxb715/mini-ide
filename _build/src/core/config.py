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

    公司机器固定用 G:\\whaty\\project；否则在家目录下找常见的项目根目录名。
    都没有就返回空，由对话框 fallback。
    """
    legacy = Path(r"G:\whaty\project")
    if legacy.is_dir():
        return str(legacy)
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
class AppConfig:
    recent_projects: list[ProjectEntry] = field(default_factory=list)
    # 上次关闭时打开的 Tab，格式："project:<绝对路径>"
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
        known = {f.name for f in fields(cls)}
        raw = {k: v for k, v in raw.items() if k in known}
        cfg = cls(**raw)
        cfg.recent_projects = recent

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
