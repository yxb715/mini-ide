"""Git context formatting shared by CLI and Git viewer."""
from __future__ import annotations

from src.core import git_ops

AI_DIFF_LIMIT_CHARS = 30000


def status_label(f: git_ops.ChangedFile) -> str:
    status = f.status
    if status == "??" or status.startswith("?"):
        return "未跟踪"
    if "U" in status or status in ("AA", "DD"):
        return "冲突"
    if "D" in status:
        return "删除"
    if "A" in status:
        return "新增"
    if f.is_staged and f.is_unstaged:
        return "暂存+修改"
    if f.is_staged:
        return "已暂存"
    return "修改"


def sort_changed_files(files: list[git_ops.ChangedFile]) -> list[git_ops.ChangedFile]:
    order = {
        "冲突": 0,
        "修改": 1,
        "暂存+修改": 2,
        "已暂存": 3,
        "新增": 4,
        "未跟踪": 5,
        "删除": 6,
    }
    return sorted(files, key=lambda f: (order.get(status_label(f), 99), f.path.lower()))


def files_by_top_dir(files: list[git_ops.ChangedFile]) -> dict[str, list[git_ops.ChangedFile]]:
    groups: dict[str, list[git_ops.ChangedFile]] = {}
    for f in files:
        key = f.path.split("/", 1)[0] if "/" in f.path else "(根目录)"
        groups.setdefault(key, []).append(f)
    return groups


def summary_payload(
    root: str,
    include_files: bool = True,
    files: list[git_ops.ChangedFile] | None = None,
    stats: dict[str, tuple[int, int]] | None = None,
    branch: str | None = None,
) -> dict:
    if not git_ops.is_git_repo(root):
        return {"ok": False, "error": "not a git repository"}
    repo = git_ops.repo_root(root)
    return summary_from_changes(
        repo=repo,
        branch=git_ops.current_branch(repo) if branch is None else branch,
        files=files if files is not None else git_ops.list_changed_files(repo),
        stats=stats if stats is not None else git_ops.diff_numstat(repo),
        include_files=include_files,
    )


def summary_from_changes(
    repo: str,
    branch: str,
    files: list[git_ops.ChangedFile],
    stats: dict[str, tuple[int, int]] | None = None,
    include_files: bool = True,
) -> dict:
    files = sort_changed_files(files)
    stats = stats or {}

    counts: dict[str, int] = {}
    payload_files = []
    for f in files:
        label = status_label(f)
        counts[label] = counts.get(label, 0) + 1
        item = {
            "path": f.path,
            "status": f.status,
            "label": label,
            "staged": f.is_staged,
            "unstaged": f.is_unstaged,
        }
        if f.path in stats:
            item["additions"], item["deletions"] = stats[f.path]
        payload_files.append(item)

    groups = {name: len(items) for name, items in files_by_top_dir(files).items()}
    result = {
        "ok": True,
        "repo": repo,
        "branch": branch,
        "changed_count": len(files),
        "counts": counts,
        "groups": groups,
    }
    if include_files:
        result["files"] = payload_files
    return result


def build_ai_text(summary: dict, diff: str = "") -> str:
    lines = [
        "Git 改动上下文",
        f"仓库：{summary.get('repo', '')}",
        f"分支：{summary.get('branch') or '(无分支)'}",
        f"改动文件数：{summary.get('changed_count', 0)}",
    ]
    counts = summary.get("counts") or {}
    if counts:
        lines.append("类型：" + "，".join(f"{k} {v}" for k, v in counts.items()))
    groups = summary.get("groups") or {}
    if groups:
        lines.append("影响目录：" + "，".join(f"{k}({v})" for k, v in groups.items()))
    files = summary.get("files") or []
    if files:
        lines.append("")
        lines.append("文件列表：")
        for f in files:
            stat = ""
            if "additions" in f or "deletions" in f:
                stat = f" +{f.get('additions', 0)} -{f.get('deletions', 0)}"
            lines.append(f"- [{f.get('label')}] {f.get('path')}{stat}")
    if diff:
        lines.append("")
        lines.append("Diff：")
        lines.append(diff)
    return "\n".join(lines)


def file_diff_text(repo: str, f: git_ops.ChangedFile, untracked_max_lines: int = 200) -> str:
    if f.status == "??" or f.status.startswith("?"):
        return git_ops.get_untracked_preview(repo, f.path, max_lines=untracked_max_lines)
    parts = []
    if f.is_staged:
        parts.append("=== Staged ===\n" + git_ops.get_file_diff(repo, f.path, staged=True))
    if f.is_unstaged:
        parts.append("=== Unstaged ===\n" + git_ops.get_file_diff(repo, f.path, staged=False))
    return "\n\n".join(parts) or "(无 diff 内容)"


def limited_diff_text(
    repo: str,
    files: list[git_ops.ChangedFile] | None = None,
    max_chars: int = AI_DIFF_LIMIT_CHARS,
    untracked_max_lines: int = 200,
    include_label: bool = True,
) -> tuple[str, bool]:
    files = sort_changed_files(files if files is not None else git_ops.list_changed_files(repo))
    parts: list[str] = []
    total = 0
    truncated = False
    for f in files:
        diff = file_diff_text(repo, f, untracked_max_lines=untracked_max_lines)
        if not diff.strip():
            continue
        label = f" ({status_label(f)})" if include_label else ""
        chunk = f"\n\n--- {f.path}{label} ---\n{diff}"
        if total + len(chunk) > max_chars:
            remaining = max(0, max_chars - total)
            if remaining:
                parts.append(chunk[:remaining])
            truncated = True
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts).strip(), truncated
