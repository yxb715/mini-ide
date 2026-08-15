"""固定工作区管理页使用的只读聚合快照。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys

from src.core.aggregate_workspace import (
    AggregateProject, DevelopmentWorkspace, WORKSPACE_CONFIG_NAME,
    load_aggregate_project, load_development_workspace,
)
from src.core.development_workspace_service import workspace_behind_counts
from src.core.git_ops import list_changed_files
from src.core.config import WorkspaceEntry
from src.core.path_utils import (
    canonical_path, normalized_path_key, relative_path_within,
)
from src.core.project_detector import ProjectMeta, detect_project
from src.util.git_executable import resolve_git_executable


CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass(frozen=True)
class AggregateProjectSummary:
    root_path: str
    name: str
    component_count: int
    profile_name: str
    git_change_count: int
    component_paths: tuple[str, ...]
    project: AggregateProject | None = None
    error: str = ""


@dataclass(frozen=True)
class DevelopmentWorkspaceSummary:
    root_path: str
    name: str
    aggregate_name: str
    component_ids: tuple[str, ...]
    task_branches: tuple[str, ...]
    git_change_count: int
    runtime_paths: tuple[str, ...]
    status: str
    created_at: str
    behind_count: int = 0
    pushed_count: int = 0
    worktree_count: int = 0
    workspace: DevelopmentWorkspace | None = None
    error: str = ""


@dataclass(frozen=True)
class LegacyWorkspaceCandidate:
    workspace_name: str
    root_path: str
    source_paths: tuple[str, ...]
    project: AggregateProject | None
    omitted_candidates: tuple[str, ...] = ()
    missing_paths: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class LegacyWorkspaceSummary:
    name: str
    root_path: str
    project_count: int
    omitted_candidates: tuple[str, ...]
    missing_paths: tuple[str, ...]
    status: str
    candidate: LegacyWorkspaceCandidate | None = None
    error: str = ""


@dataclass(frozen=True)
class WorkspaceDashboardSnapshot:
    aggregate_projects: tuple[AggregateProjectSummary, ...]
    development_workspaces: tuple[DevelopmentWorkspaceSummary, ...]
    legacy_workspaces: tuple[LegacyWorkspaceSummary, ...] = ()


def _stable_id_from_name(name: str, used_ids: set[str]) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-._")
    value = value or "component"
    candidate = value
    suffix = 2
    while candidate.casefold() in used_ids:
        candidate = f"{value}-{suffix}"
        suffix += 1
    used_ids.add(candidate.casefold())
    return candidate


def suggest_stable_id(name: str) -> str:
    return _stable_id_from_name(name, set())


def _component_type(meta: ProjectMeta) -> str:
    project_type = meta.project_type
    if project_type.startswith("spring-boot") or project_type in {
        "gradle-java", "maven-java",
    }:
        return "java"
    if project_type in {"vue", "react", "next", "nuxt", "svelte", "node"}:
        return "frontend"
    if project_type in {"django", "flask", "fastapi", "python", "python-poetry"}:
        return "python"
    return project_type


def component_definition_from_path(
    aggregate_root: str | Path,
    component_path: str | Path,
    used_ids: set[str] | None = None,
):
    """从用户明确选择的目录生成组件定义；调用方必须放后台线程。"""
    from src.core.aggregate_workspace import AggregateComponent

    root = canonical_path(aggregate_root)
    path = canonical_path(component_path)
    ids = used_ids if used_ids is not None else set()
    component_id = _stable_id_from_name(path.name, ids)
    meta = detect_project(str(path)) if path.is_dir() else ProjectMeta(
        path=str(path), name=path.name, project_type="generic",
        display_type="通用目录", icon="",
    )
    relative = relative_path_within(
        root, path, label=f"component {component_id} path",
    )
    return AggregateComponent.from_dict({
        "id": component_id,
        "name": path.name,
        "path": relative,
        "type": _component_type(meta),
        "shared": not (path / ".git").exists(),
    }, root)


def scan_aggregate_components(
    aggregate_root: str | Path,
    workspace_directory: str = "workspace",
) -> tuple:
    """扫描聚合根直属候选组件；调用方必须放后台线程并让用户确认。"""
    root = canonical_path(aggregate_root)
    excluded = normalized_path_key(root / workspace_directory)
    used_ids: set[str] = set()
    components = []
    try:
        children = sorted(
            (item for item in root.iterdir() if item.is_dir()),
            key=lambda item: item.name.casefold(),
        )
    except OSError:
        return ()
    for child in children:
        if child.name.startswith(".") or normalized_path_key(child) == excluded:
            continue
        if not _looks_like_component(child):
            continue
        components.append(component_definition_from_path(root, child, used_ids))
    return tuple(components)


def _looks_like_component(path: Path) -> bool:
    markers = (
        ".git", "build.gradle", "build.gradle.kts", "pom.xml", "package.json",
        "pyproject.toml", "requirements.txt", "manage.py", "go.mod",
    )
    if any((path / marker).exists() for marker in markers):
        return True
    return (path / "nginx.exe").is_file() and (
        (path / "nginx.conf").is_file() or (path / "conf" / "nginx.conf").is_file()
    )


def build_legacy_workspace_candidate(
    workspace: WorkspaceEntry,
) -> LegacyWorkspaceCandidate:
    """把旧 Tab 组转成可预览候选，不写配置、不删除旧数据。"""
    source_paths = tuple(str(canonical_path(path)) for path in workspace.paths if path)
    if not source_paths:
        return LegacyWorkspaceCandidate(
            workspace_name=workspace.name, root_path="", source_paths=(), project=None,
            error="旧工作区没有项目路径",
        )
    parents = {normalized_path_key(Path(path).parent) for path in source_paths}
    if len(parents) != 1:
        return LegacyWorkspaceCandidate(
            workspace_name=workspace.name, root_path="", source_paths=source_paths,
            project=None, error="旧工作区项目不在同一个直接父目录下",
        )

    root = canonical_path(Path(source_paths[0]).parent)
    missing_paths = []
    try:
        config_path = root / ".mini-ide" / "project.json"
        existing = load_aggregate_project(root) if config_path.is_file() else None
        components = list(existing.components) if existing is not None else []
        used_ids = {component.id.casefold() for component in components}
        represented = {
            normalized_path_key(component.absolute_path(root))
            for component in components
        }
        for path_text in source_paths:
            path = canonical_path(path_text)
            if not path.is_dir():
                missing_paths.append(str(path))
            if normalized_path_key(path) not in represented:
                component = component_definition_from_path(root, path, used_ids)
                components.append(component)
                represented.add(normalized_path_key(path))

        if existing is not None:
            profiles = [profile.to_dict() for profile in existing.profiles]
            project_id = existing.id
            project_name = existing.name
            workspace_directory = existing.workspace_directory
        else:
            profiles = []
            project_id = _stable_id_from_name(root.name, set())
            project_name = workspace.name or root.name
            workspace_directory = "workspace"

        project = AggregateProject.from_dict(root, {
            "schemaVersion": 1,
            "id": project_id,
            "name": project_name,
            "kind": "aggregate",
            "workspaceDirectory": workspace_directory,
            "components": [item.to_dict() for item in components],
            "profiles": profiles,
        })

        represented = {
            normalized_path_key(component.absolute_path(root))
            for component in project.components
        }
        omitted = []
        try:
            for child in root.iterdir():
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if normalized_path_key(child) in represented:
                    continue
                if normalized_path_key(child) == normalized_path_key(project.workspace_root):
                    continue
                if _looks_like_component(child):
                    omitted.append(str(child))
        except OSError:
            pass

        return LegacyWorkspaceCandidate(
            workspace_name=workspace.name,
            root_path=str(root),
            source_paths=source_paths,
            project=project,
            omitted_candidates=tuple(sorted(omitted, key=str.casefold)),
            missing_paths=tuple(missing_paths),
        )
    except Exception as exc:
        return LegacyWorkspaceCandidate(
            workspace_name=workspace.name,
            root_path=str(root),
            source_paths=source_paths,
            project=None,
            missing_paths=tuple(missing_paths),
            error=str(exc),
        )


def _git_change_count(path: str | Path) -> int:
    root = Path(path)
    if not (root / ".git").exists():
        return 0
    return len(list_changed_files(str(root)))


def _task_branch_pushed(repository: str, task_branch: str) -> bool:
    try:
        result = subprocess.run(
            [
                resolve_git_executable(), "branch", "-r", "--contains", task_branch,
            ],
            cwd=repository, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, creationflags=CREATE_NO_WINDOW,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _aggregate_summary(root_path: str) -> AggregateProjectSummary:
    try:
        project = load_aggregate_project(root_path)
    except Exception as exc:
        return AggregateProjectSummary(
            root_path=str(root_path),
            name=Path(root_path).name or str(root_path),
            component_count=0,
            profile_name="",
            git_change_count=0,
            component_paths=(),
            error=str(exc),
        )

    component_paths = tuple(
        str(component.absolute_path(project.root_path))
        for component in project.components
    )
    change_count = sum(_git_change_count(path) for path in component_paths)
    profile_name = project.profiles[0].name if project.profiles else ""
    return AggregateProjectSummary(
        root_path=project.root_path,
        name=project.name,
        component_count=len(project.components),
        profile_name=profile_name,
        git_change_count=change_count,
        component_paths=component_paths,
        project=project,
    )


def _workspace_summaries(
    project: AggregateProject,
) -> list[DevelopmentWorkspaceSummary]:
    workspace_root = project.workspace_root
    if not workspace_root.is_dir():
        return []
    try:
        candidates = sorted(
            (
                path for path in workspace_root.iterdir()
                if path.is_dir() and (path / WORKSPACE_CONFIG_NAME).is_file()
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        return []

    summaries: list[DevelopmentWorkspaceSummary] = []
    for root in candidates:
        try:
            workspace = load_development_workspace(root, aggregate_project=project)
        except Exception as exc:
            summaries.append(DevelopmentWorkspaceSummary(
                root_path=str(root),
                name=root.name,
                aggregate_name=project.name,
                component_ids=(),
                task_branches=(),
                git_change_count=0,
                runtime_paths=(),
                status="invalid",
                created_at="",
                error=str(exc),
            ))
            continue

        runtime_paths = tuple(
            component.worktree_path or component.source_repository_path
            for component in workspace.components
        )
        change_count = sum(
            _git_change_count(component.worktree_path)
            for component in workspace.components
            if component.mode == "worktree"
        )
        behind_counts = workspace_behind_counts(workspace)
        worktree_components = tuple(
            component for component in workspace.components
            if component.mode == "worktree"
        )
        pushed_count = sum(
            _task_branch_pushed(
                component.source_repository_path, component.task_branch,
            )
            for component in worktree_components
        )
        summaries.append(DevelopmentWorkspaceSummary(
            root_path=workspace.root_path,
            name=workspace.name,
            aggregate_name=project.name,
            behind_count=sum(behind_counts.values()),
            pushed_count=pushed_count,
            worktree_count=len(worktree_components),
            component_ids=tuple(component.id for component in workspace.components),
            task_branches=tuple(
                component.task_branch
                for component in workspace.components
                if component.task_branch
            ),
            git_change_count=change_count,
            runtime_paths=runtime_paths,
            status=workspace.status,
            created_at=workspace.created_at,
            workspace=workspace,
        ))
    return summaries


def build_workspace_dashboard_snapshot(
    aggregate_project_paths: list[str] | tuple[str, ...],
    legacy_workspaces: list[WorkspaceEntry] | tuple[WorkspaceEntry, ...] = (),
    migration_records: dict[str, str] | None = None,
) -> WorkspaceDashboardSnapshot:
    """读取聚合定义、工作区配置和 Git 摘要；调用方必须放后台线程。"""
    aggregates: list[AggregateProjectSummary] = []
    workspaces: list[DevelopmentWorkspaceSummary] = []
    for root_path in aggregate_project_paths:
        summary = _aggregate_summary(root_path)
        aggregates.append(summary)
        if summary.project is not None:
            workspaces.extend(_workspace_summaries(summary.project))
    aggregates.sort(key=lambda item: item.name.casefold())
    workspaces.sort(key=lambda item: (item.aggregate_name.casefold(), item.name.casefold()))

    records = migration_records or {}
    legacy_summaries: list[LegacyWorkspaceSummary] = []
    for workspace in legacy_workspaces:
        candidate = build_legacy_workspace_candidate(workspace)
        migrated_path = records.get(workspace.name, "")
        if candidate.error:
            status = "invalid"
        elif (
            migrated_path
            and normalized_path_key(migrated_path) == normalized_path_key(candidate.root_path)
            and (canonical_path(migrated_path) / ".mini-ide" / "project.json").is_file()
        ):
            status = "migrated"
        else:
            status = "pending"
        legacy_summaries.append(LegacyWorkspaceSummary(
            name=workspace.name,
            root_path=candidate.root_path,
            project_count=len(candidate.source_paths),
            omitted_candidates=candidate.omitted_candidates,
            missing_paths=candidate.missing_paths,
            status=status,
            candidate=candidate,
            error=candidate.error,
        ))
    legacy_summaries.sort(key=lambda item: item.name.casefold())
    return WorkspaceDashboardSnapshot(
        tuple(aggregates), tuple(workspaces), tuple(legacy_summaries),
    )
