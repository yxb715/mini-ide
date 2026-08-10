"""项目运行单元的通用描述。

运行单元是一个可以独立启动、停止、查看日志和执行健康检查的目标。
识别器负责生成它，UI、CLI 和生命周期控制器只消费这份结构化描述。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeUnit:
    """项目内一个可独立控制的运行目标。"""

    id: str
    name: str
    kind: str
    cwd: str
    start_profile: str = ""
    profile_names: tuple[str, ...] = ()
    expected_port: int | None = None
    health_check_path: str = ""
    stop_command: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    source: str = "detected"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """返回 CLI/诊断可安全序列化的描述，不包含可执行对象。"""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "cwd": self.cwd,
            "start_profile": self.start_profile,
            "profiles": list(self.profile_names),
            "expected_port": self.expected_port,
            "health_check_path": self.health_check_path,
            "stop": list(self.stop_command),
            "depends_on": list(self.depends_on),
            "source": self.source,
            "metadata": dict(self.metadata),
        }


def runtime_kind_for_project(project_type: str) -> str:
    """把技术栈映射成显示分类，生命周期控制不依赖这个分类。"""
    if project_type in {"vue", "react", "next", "nuxt", "svelte", "node", "frontend-workspace"}:
        return "frontend"
    if project_type in {"django", "flask", "fastapi", "python", "python-poetry"}:
        return "python"
    if project_type == "nginx":
        return "nginx"
    if project_type.startswith("spring-") or project_type in {"gradle-java", "maven-java"}:
        return "service"
    if project_type == "go":
        return "service"
    return "project"


def runtime_start_groups(units: list[RuntimeUnit]) -> list[list[RuntimeUnit]]:
    """按依赖生成可并行启动的分组，依赖环会明确报错。"""
    remaining = {unit.id.casefold(): unit for unit in units}
    known = set(remaining)
    for unit in units:
        for dependency in unit.depends_on:
            if dependency.casefold() not in known:
                raise ValueError(
                    f"runtime {unit.id} depends on unknown unit: {dependency}"
                )
    completed: set[str] = set()
    groups: list[list[RuntimeUnit]] = []
    while remaining:
        ready = [
            unit for unit in units
            if unit.id.casefold() in remaining
            and all(dependency.casefold() in completed for dependency in unit.depends_on)
        ]
        if not ready:
            cycle = ", ".join(unit.id for unit in remaining.values())
            raise ValueError(f"runtime dependency cycle: {cycle}")
        groups.append(ready)
        for unit in ready:
            key = unit.id.casefold()
            remaining.pop(key, None)
            completed.add(key)
    return groups
