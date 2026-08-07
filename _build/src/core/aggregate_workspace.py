"""聚合项目、运行预设与临时开发工作区的纯逻辑模型。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.path_utils import (
    PathBoundaryError, canonical_path, is_path_within, normalized_path_key,
    relative_path_within, resolve_path_within,
)

AGGREGATE_SCHEMA_VERSION = 1
WORKSPACE_SCHEMA_VERSION = 1
AGGREGATE_CONFIG_RELATIVE_PATH = Path(".mini-ide") / "project.json"
WORKSPACE_CONFIG_NAME = "workspace.json"
WORKSPACE_STATUSES = {"created", "active", "reviewing", "merged"}
WORKSPACE_MODES = {"worktree", "shared"}
_STABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AggregateConfigError(ValueError):
    """聚合项目或开发工作区配置无效。"""


def validate_stable_id(value: Any, label: str = "id") -> str:
    stable_id = str(value or "").strip()
    if not _STABLE_ID_RE.fullmatch(stable_id):
        raise AggregateConfigError(
            f"{label} must start with a letter or digit and contain only letters, "
            "digits, '.', '_' or '-'"
        )
    return stable_id


def _required_text(raw: dict, key: str, label: str | None = None) -> str:
    value = str(raw.get(key, "") or "").strip()
    if not value:
        raise AggregateConfigError(f"missing {label or key}")
    return value


def _mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise AggregateConfigError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list:
    if not isinstance(value, list):
        raise AggregateConfigError(f"{label} must be an array")
    return value


def _unique_ids(items: list, label: str) -> None:
    seen: dict[str, str] = {}
    for item in items:
        item_id = item.id
        key = item_id.casefold()
        if key in seen:
            raise AggregateConfigError(
                f"duplicate {label} id: {seen[key]} / {item_id}"
            )
        seen[key] = item_id


def _migrate_schema(raw: dict, label: str) -> dict:
    """将无版本的早期草案配置迁移到 schemaVersion=1。"""
    data = dict(raw)
    version = data.get("schemaVersion", 0)
    if isinstance(version, bool) or not isinstance(version, int):
        raise AggregateConfigError(f"{label} schemaVersion must be an integer")
    if version == 0:
        data["schemaVersion"] = 1
        version = 1
    if version != 1:
        raise AggregateConfigError(
            f"unsupported {label} schemaVersion: {version}"
        )
    return data


@dataclass(frozen=True)
class AggregateComponent:
    id: str
    path: str
    type: str
    name: str = ""
    shared: bool = False
    overrides: dict[str, Any] = field(default_factory=dict)
    workspace_copy_files: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict, aggregate_root: str | Path) -> "AggregateComponent":
        raw = _mapping(raw, "component")
        component_id = validate_stable_id(raw.get("id"), "component id")
        path = relative_path_within(
            aggregate_root,
            _required_text(raw, "path", f"component {component_id} path"),
            label=f"component {component_id} path",
        )
        component_type = _required_text(
            raw, "type", f"component {component_id} type",
        )
        overrides = raw.get("overrides", {})
        if not isinstance(overrides, dict):
            raise AggregateConfigError(
                f"component {component_id} overrides must be an object"
            )
        component_root = resolve_path_within(
            aggregate_root, path, label=f"component {component_id} path",
        )
        raw_copy_files = raw.get("workspaceCopyFiles", [])
        copy_files = _list(
            raw_copy_files, f"component {component_id} workspaceCopyFiles",
        )
        normalized_copy_files: list[str] = []
        seen_copy_files: set[str] = set()
        for index, value in enumerate(copy_files):
            if not isinstance(value, str) or not value.strip():
                raise AggregateConfigError(
                    f"component {component_id} workspaceCopyFiles[{index}] must be a non-empty string"
                )
            relative = relative_path_within(
                component_root, value.strip(),
                label=f"component {component_id} workspaceCopyFiles[{index}]",
            )
            key = relative.casefold()
            if key in seen_copy_files:
                raise AggregateConfigError(
                    f"component {component_id} workspaceCopyFiles contains duplicate path: {relative}"
                )
            seen_copy_files.add(key)
            normalized_copy_files.append(relative)
        return cls(
            id=component_id,
            path=path,
            type=component_type,
            name=str(raw.get("name", "") or "").strip(),
            shared=bool(raw.get("shared", False)),
            overrides=dict(overrides),
            workspace_copy_files=tuple(normalized_copy_files),
        )

    def absolute_path(self, aggregate_root: str | Path) -> Path:
        return resolve_path_within(
            aggregate_root, self.path, label=f"component {self.id} path",
        )

    def to_dict(self) -> dict:
        data = {"id": self.id, "path": self.path, "type": self.type}
        if self.name:
            data["name"] = self.name
        if self.shared:
            data["shared"] = True
        if self.overrides:
            data["overrides"] = dict(self.overrides)
        if self.workspace_copy_files:
            data["workspaceCopyFiles"] = list(self.workspace_copy_files)
        return data


@dataclass(frozen=True)
class RuntimeProfile:
    id: str
    name: str
    start_groups: tuple[tuple[str, ...], ...]

    @classmethod
    def from_dict(
        cls,
        raw: dict,
        component_ids: dict[str, str],
    ) -> "RuntimeProfile":
        raw = _mapping(raw, "runtime profile")
        profile_id = validate_stable_id(raw.get("id"), "runtime profile id")
        name = _required_text(raw, "name", f"runtime profile {profile_id} name")
        raw_groups = _list(raw.get("startGroups"), "startGroups")
        if not raw_groups:
            raise AggregateConfigError(
                f"runtime profile {profile_id} must contain startGroups"
            )

        groups: list[tuple[str, ...]] = []
        used: set[str] = set()
        for group_index, raw_group in enumerate(raw_groups, 1):
            values = _list(raw_group, f"startGroups[{group_index}]")
            if not values:
                raise AggregateConfigError(
                    f"runtime profile {profile_id} contains an empty start group"
                )
            group: list[str] = []
            for value in values:
                component_key = str(value or "").strip().casefold()
                component_id = component_ids.get(component_key)
                if not component_id:
                    raise AggregateConfigError(
                        f"runtime profile {profile_id} references unknown component: {value}"
                    )
                if component_key in used:
                    raise AggregateConfigError(
                        f"runtime profile {profile_id} repeats component: {component_id}"
                    )
                used.add(component_key)
                group.append(component_id)
            groups.append(tuple(group))
        return cls(id=profile_id, name=name, start_groups=tuple(groups))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "startGroups": [list(group) for group in self.start_groups],
        }


@dataclass(frozen=True)
class AggregateProject:
    root_path: str
    id: str
    name: str
    workspace_directory: str
    components: tuple[AggregateComponent, ...]
    profiles: tuple[RuntimeProfile, ...]
    schema_version: int = AGGREGATE_SCHEMA_VERSION
    kind: str = "aggregate"

    @classmethod
    def from_dict(cls, aggregate_root: str | Path, raw: dict) -> "AggregateProject":
        root = canonical_path(aggregate_root)
        data = _migrate_schema(_mapping(raw, "aggregate project"), "aggregate project")
        kind = str(data.get("kind", "aggregate") or "aggregate")
        if kind != "aggregate":
            raise AggregateConfigError(f"unsupported project kind: {kind}")
        project_id = validate_stable_id(data.get("id"), "aggregate project id")
        name = str(data.get("name", "") or "").strip() or project_id
        workspace_directory = relative_path_within(
            root,
            str(data.get("workspaceDirectory", "workspace") or "workspace"),
            label="workspaceDirectory",
        )

        raw_components = _list(data.get("components"), "components")
        if not raw_components:
            raise AggregateConfigError("aggregate project must contain components")
        components = [
            AggregateComponent.from_dict(item, root) for item in raw_components
        ]
        _unique_ids(components, "component")
        component_ids = {item.id.casefold(): item.id for item in components}

        raw_profiles = data.get("profiles", [])
        profiles = [
            RuntimeProfile.from_dict(item, component_ids)
            for item in _list(raw_profiles, "profiles")
        ]
        _unique_ids(profiles, "runtime profile")
        return cls(
            root_path=str(root),
            id=project_id,
            name=name,
            workspace_directory=workspace_directory,
            components=tuple(components),
            profiles=tuple(profiles),
        )

    @property
    def config_path(self) -> Path:
        return canonical_path(self.root_path) / AGGREGATE_CONFIG_RELATIVE_PATH

    @property
    def workspace_root(self) -> Path:
        return resolve_path_within(
            self.root_path, self.workspace_directory, label="workspaceDirectory",
        )

    def component(self, component_id: str) -> AggregateComponent | None:
        key = component_id.casefold()
        return next((item for item in self.components if item.id.casefold() == key), None)

    def to_dict(self) -> dict:
        return {
            "schemaVersion": self.schema_version,
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "workspaceDirectory": self.workspace_directory,
            "components": [item.to_dict() for item in self.components],
            "profiles": [item.to_dict() for item in self.profiles],
        }


@dataclass(frozen=True)
class WorkspaceComponent:
    id: str
    source_repository_path: str
    mode: str
    worktree_path: str = ""
    base_branch: str = ""
    base_commit: str = ""
    task_branch: str = ""
    workspace_copy_files: tuple[str, ...] = ()

    @classmethod
    def from_dict(
        cls,
        raw: dict,
        aggregate_root: str | Path,
        workspace_root: str | Path,
    ) -> "WorkspaceComponent":
        raw = _mapping(raw, "workspace component")
        component_id = validate_stable_id(raw.get("id"), "workspace component id")
        mode = str(raw.get("mode", "") or "").strip().lower()
        if mode not in WORKSPACE_MODES:
            raise AggregateConfigError(
                f"workspace component {component_id} has invalid mode: {mode}"
            )
        source_path = resolve_path_within(
            aggregate_root,
            _required_text(
                raw, "sourceRepositoryPath",
                f"workspace component {component_id} sourceRepositoryPath",
            ),
            label=f"workspace component {component_id} sourceRepositoryPath",
        )

        worktree_path = ""
        base_branch = str(raw.get("baseBranch", "") or "").strip()
        base_commit = str(raw.get("baseCommit", "") or "").strip()
        task_branch = str(raw.get("taskBranch", "") or "").strip()
        raw_copy_files = raw.get("workspaceCopyFiles", [])
        copy_files = _list(
            raw_copy_files,
            f"workspace component {component_id} workspaceCopyFiles",
        )
        normalized_copy_files: list[str] = []
        seen_copy_files: set[str] = set()
        for index, value in enumerate(copy_files):
            if not isinstance(value, str) or not value.strip():
                raise AggregateConfigError(
                    f"workspace component {component_id} workspaceCopyFiles[{index}] "
                    "must be a non-empty string"
                )
            relative = relative_path_within(
                source_path, value.strip(),
                label=(
                    f"workspace component {component_id} "
                    f"workspaceCopyFiles[{index}]"
                ),
            )
            key = relative.casefold()
            if key in seen_copy_files:
                raise AggregateConfigError(
                    f"workspace component {component_id} workspaceCopyFiles "
                    f"contains duplicate path: {relative}"
                )
            seen_copy_files.add(key)
            normalized_copy_files.append(relative)
        if mode == "worktree":
            worktree_path = str(resolve_path_within(
                workspace_root,
                _required_text(
                    raw, "worktreePath",
                    f"workspace component {component_id} worktreePath",
                ),
                label=f"workspace component {component_id} worktreePath",
            ))
            if not base_branch or not base_commit or not task_branch:
                raise AggregateConfigError(
                    f"worktree component {component_id} requires baseBranch, "
                    "baseCommit and taskBranch"
                )
        elif raw.get("worktreePath"):
            raise AggregateConfigError(
                f"shared component {component_id} must not define worktreePath"
            )

        return cls(
            id=component_id,
            source_repository_path=str(source_path),
            mode=mode,
            worktree_path=worktree_path,
            base_branch=base_branch,
            base_commit=base_commit,
            task_branch=task_branch,
            workspace_copy_files=tuple(normalized_copy_files),
        )

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "sourceRepositoryPath": self.source_repository_path,
            "mode": self.mode,
        }
        if self.worktree_path:
            data["worktreePath"] = self.worktree_path
        if self.base_branch:
            data["baseBranch"] = self.base_branch
        if self.base_commit:
            data["baseCommit"] = self.base_commit
        if self.task_branch:
            data["taskBranch"] = self.task_branch
        if self.workspace_copy_files:
            data["workspaceCopyFiles"] = list(self.workspace_copy_files)
        return data


@dataclass(frozen=True)
class DevelopmentWorkspace:
    root_path: str
    id: str
    name: str
    created_at: str
    aggregate_project_path: str
    status: str
    components: tuple[WorkspaceComponent, ...]
    description: str = ""
    last_profile_id: str = ""
    runtime_state_ref: str = ""
    schema_version: int = WORKSPACE_SCHEMA_VERSION

    @classmethod
    def from_dict(
        cls,
        workspace_root: str | Path,
        raw: dict,
        aggregate_project: AggregateProject | None = None,
    ) -> "DevelopmentWorkspace":
        root = canonical_path(workspace_root)
        data = _migrate_schema(
            _mapping(raw, "development workspace"), "development workspace",
        )
        workspace_id = validate_stable_id(data.get("id"), "development workspace id")
        name = _required_text(data, "name", "development workspace name")
        created_at = _required_text(data, "createdAt", "createdAt")
        aggregate_path = canonical_path(
            _required_text(data, "aggregateProjectPath", "aggregateProjectPath")
        )
        if not is_path_within(root, aggregate_path, allow_equal=False):
            raise AggregateConfigError(
                "development workspace must be inside its aggregate project"
            )
        if aggregate_project:
            if normalized_path_key(aggregate_path) != normalized_path_key(
                aggregate_project.root_path
            ):
                raise AggregateConfigError("aggregateProjectPath does not match project")
            if not is_path_within(root, aggregate_project.workspace_root, allow_equal=False):
                raise AggregateConfigError(
                    "development workspace escapes workspaceDirectory"
                )

        status = str(data.get("status", "") or "").strip().lower()
        if status not in WORKSPACE_STATUSES:
            raise AggregateConfigError(f"invalid development workspace status: {status}")

        components = [
            WorkspaceComponent.from_dict(item, aggregate_path, root)
            for item in _list(data.get("components"), "workspace components")
        ]
        if not components:
            raise AggregateConfigError("development workspace must contain components")
        _unique_ids(components, "workspace component")

        if aggregate_project:
            for component in components:
                source = aggregate_project.component(component.id)
                if source is None:
                    raise AggregateConfigError(
                        f"workspace references unknown component: {component.id}"
                    )
                expected_path = source.absolute_path(aggregate_project.root_path)
                if normalized_path_key(component.source_repository_path) != normalized_path_key(
                    expected_path
                ):
                    raise AggregateConfigError(
                        f"workspace component {component.id} source path does not match project"
                    )
                if source.shared != (component.mode == "shared"):
                    raise AggregateConfigError(
                        f"workspace component {component.id} mode does not match project"
                    )

        return cls(
            root_path=str(root),
            id=workspace_id,
            name=name,
            created_at=created_at,
            aggregate_project_path=str(aggregate_path),
            status=status,
            components=tuple(components),
            description=str(data.get("description", "") or "").strip(),
            last_profile_id=str(data.get("lastProfileId", "") or "").strip(),
            runtime_state_ref=str(data.get("runtimeStateRef", "") or "").strip(),
        )

    @property
    def config_path(self) -> Path:
        return canonical_path(self.root_path) / WORKSPACE_CONFIG_NAME

    def to_dict(self) -> dict:
        data = {
            "schemaVersion": self.schema_version,
            "id": self.id,
            "name": self.name,
            "createdAt": self.created_at,
            "aggregateProjectPath": self.aggregate_project_path,
            "status": self.status,
            "components": [item.to_dict() for item in self.components],
        }
        if self.description:
            data["description"] = self.description
        if self.last_profile_id:
            data["lastProfileId"] = self.last_profile_id
        if self.runtime_state_ref:
            data["runtimeStateRef"] = self.runtime_state_ref
        return data


def aggregate_config_path(aggregate_root: str | Path) -> Path:
    return canonical_path(aggregate_root) / AGGREGATE_CONFIG_RELATIVE_PATH


def aggregate_workspace_exclusion(aggregate_root: str | Path) -> Path | None:
    """返回聚合根需要从扫描中排除的开发工作区目录。"""
    root = canonical_path(aggregate_root)
    config_path = aggregate_config_path(root)
    if not config_path.is_file():
        return None
    try:
        return load_aggregate_project(root).workspace_root
    except (AggregateConfigError, PathBoundaryError):
        # 配置损坏时仍优先避免重复扫描默认工作区；配置错误由管理页单独展示。
        return resolve_path_within(root, "workspace", label="workspaceDirectory")


def scan_exclusion_roots(scan_root: str | Path) -> tuple[Path, ...]:
    exclusion = aggregate_workspace_exclusion(scan_root)
    return (exclusion,) if exclusion is not None else ()


def is_scan_path_excluded(
    path: str | Path,
    exclusion_roots: tuple[str | Path, ...] | list[str | Path],
) -> bool:
    return any(is_path_within(path, root) for root in exclusion_roots)


def is_aggregate_workspace_path(path: str | Path) -> bool:
    """判断路径是否落在任一祖先聚合项目的 workspaceDirectory 下。"""
    target = canonical_path(path)
    for parent in (target, *target.parents):
        exclusion = aggregate_workspace_exclusion(parent)
        if exclusion is not None and is_path_within(target, exclusion):
            return True
    return False


def _read_json(path: Path, label: str) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AggregateConfigError(f"{label} not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AggregateConfigError(f"cannot read {label}: {exc}") from exc
    return _mapping(raw, label)


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def load_aggregate_project(aggregate_root: str | Path) -> AggregateProject:
    root = canonical_path(aggregate_root)
    return AggregateProject.from_dict(
        root, _read_json(aggregate_config_path(root), "aggregate project config"),
    )


def save_aggregate_project(project: AggregateProject) -> Path:
    validated = AggregateProject.from_dict(project.root_path, project.to_dict())
    _write_json_atomic(validated.config_path, validated.to_dict())
    return validated.config_path


def load_development_workspace(
    workspace_root: str | Path,
    aggregate_project: AggregateProject | None = None,
) -> DevelopmentWorkspace:
    root = canonical_path(workspace_root)
    return DevelopmentWorkspace.from_dict(
        root,
        _read_json(root / WORKSPACE_CONFIG_NAME, "development workspace config"),
        aggregate_project=aggregate_project,
    )


def save_development_workspace(
    workspace: DevelopmentWorkspace,
    aggregate_project: AggregateProject | None = None,
) -> Path:
    validated = DevelopmentWorkspace.from_dict(
        workspace.root_path,
        workspace.to_dict(),
        aggregate_project=aggregate_project,
    )
    _write_json_atomic(validated.config_path, validated.to_dict())
    return validated.config_path
