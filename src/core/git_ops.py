"""git 只读操作封装

纯 shell 调用，不改变仓库状态。主要给 GitViewer 用。
Windows 下加 CREATE_NO_WINDOW 避免弹 cmd 窗口。
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_POPEN_KW = {"creationflags": _NO_WINDOW} if _NO_WINDOW else {}


@dataclass
class ChangedFile:
    status: str          # 两字符状态码：' M', 'M ', 'A ', 'D ', '??' 等
    path: str            # 相对仓库根
    is_staged: bool      # 是否已进入 staged
    is_unstaged: bool    # 是否有工作区改动


@dataclass
class Commit:
    sha: str             # 短 hash
    sha_full: str        # 完整 hash
    author: str
    email: str
    date: str
    subject: str
    refs: str = ""


def _run(args: list[str], cwd: str, timeout: int = 10) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, **_POPEN_KW,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (subprocess.SubprocessError, OSError) as e:
        return -1, "", str(e)


def is_git_repo(path: str) -> bool:
    rc, out, _ = _run(["rev-parse", "--show-toplevel"], path, timeout=3)
    return rc == 0 and bool(out.strip())


def repo_root(path: str) -> str:
    rc, out, _ = _run(["rev-parse", "--show-toplevel"], path, timeout=3)
    if rc != 0:
        return path
    return out.strip().replace("/", "\\") if sys.platform == "win32" else out.strip()


def list_changed_files(cwd: str) -> list[ChangedFile]:
    """等价于 git status --porcelain，分清楚 staged / unstaged"""
    rc, out, _ = _run(["status", "--porcelain=v1"], cwd)
    if rc != 0:
        return []
    results: list[ChangedFile] = []
    for raw in out.splitlines():
        if len(raw) < 3:
            continue
        staged_flag = raw[0]
        unstaged_flag = raw[1]
        path = raw[3:].strip()
        # 处理 rename "old -> new"
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        # 去除外层引号
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        results.append(ChangedFile(
            status=f"{staged_flag}{unstaged_flag}",
            path=path,
            is_staged=staged_flag not in (" ", "?"),
            is_unstaged=unstaged_flag not in (" ",),
        ))
    return results


def get_file_diff(cwd: str, path: str, staged: bool = False) -> str:
    """获取单个文件的 diff。staged=True 时显示已暂存的改动，否则显示未暂存的"""
    args = ["diff", "--no-color", "-U3"]
    if staged:
        args.append("--staged")
    args += ["--", path]
    rc, out, err = _run(args, cwd, timeout=15)
    if rc != 0 and not out:
        return f"(获取 diff 失败)\n{err}"
    return out


def get_untracked_preview(cwd: str, path: str, max_lines: int = 200) -> str:
    """对未跟踪的新文件展示「全部添加」的伪 diff，方便看"""
    p = Path(cwd) / path
    if not p.exists() or p.is_dir():
        return f"(文件不存在或是目录) {path}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(读取失败) {e}"
    lines = text.splitlines()
    header = (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
    )
    body = "\n".join("+" + ln for ln in lines[:max_lines])
    if len(lines) > max_lines:
        body += f"\n\n(... 还有 {len(lines) - max_lines} 行 ...)"
    return header + body


def get_log(cwd: str, limit: int = 50, path: str | None = None) -> list[Commit]:
    fmt = "%h%x1f%H%x1f%an%x1f%ae%x1f%ad%x1f%s%x1f%D"
    args = ["log", f"-n{limit}", f"--pretty=format:{fmt}", "--date=short"]
    if path:
        args += ["--", path]
    rc, out, _ = _run(args, cwd, timeout=15)
    if rc != 0:
        return []
    commits: list[Commit] = []
    for raw in out.splitlines():
        parts = raw.split("\x1f")
        if len(parts) < 6:
            continue
        commits.append(Commit(
            sha=parts[0], sha_full=parts[1], author=parts[2], email=parts[3],
            date=parts[4], subject=parts[5],
            refs=parts[6] if len(parts) > 6 else "",
        ))
    return commits


def get_commit_diff(cwd: str, sha: str) -> str:
    rc, out, err = _run(["show", "--no-color", "--stat", sha], cwd, timeout=20)
    if rc != 0:
        return f"(读取 commit 失败) {err}"
    return out


def get_commit_files(cwd: str, sha: str) -> list[str]:
    rc, out, _ = _run(["show", "--name-only", "--pretty=format:", sha], cwd, timeout=10)
    if rc != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def current_branch(cwd: str) -> str:
    rc, out, _ = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd, timeout=3)
    return out.strip() if rc == 0 else ""


def has_uncommitted_changes(cwd: str) -> bool:
    rc, out, _ = _run(["status", "--porcelain"], cwd, timeout=5)
    return rc == 0 and bool(out.strip())


def list_branches(cwd: str, include_remote: bool = True) -> dict:
    """返回 {'current': str, 'local': [str], 'remote': [str]}。
    remote 已过滤掉 HEAD 软连接（如 origin/HEAD -> origin/main）。
    """
    cur = current_branch(cwd)
    rc, out, _ = _run(["branch", "--list", "--format=%(refname:short)"], cwd, timeout=5)
    local = [ln.strip() for ln in out.splitlines() if ln.strip()] if rc == 0 else []

    remote: list[str] = []
    if include_remote:
        rc2, out2, _ = _run(["branch", "-r", "--format=%(refname:short)"], cwd, timeout=5)
        if rc2 == 0:
            local_set = set(local)
            for ln in out2.splitlines():
                name = ln.strip()
                if not name or "->" in name:
                    continue
                # 远程分支若已有同名本地分支就不重复列
                short = name.split("/", 1)[1] if "/" in name else name
                if short in local_set:
                    continue
                remote.append(name)
    return {"current": cur, "local": local, "remote": remote}


def git_fetch(cwd: str) -> bool:
    """后台拉远程引用（不改本地分支）。失败静默返回 False。
    注意：此函数同步执行网络操作，调用方必须放后台线程，否则冻结 UI。
    """
    rc, _, _ = _run(["fetch", "--quiet", "--all"], cwd, timeout=60)
    return rc == 0


def has_upstream(cwd: str) -> bool:
    """当前分支是否配置了上游（@{u}）—— 没有上游 pull 会失败，菜单上用它来灰化按钮"""
    rc, _, _ = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd, timeout=3)
    return rc == 0


def git_pull(cwd: str, ff_only: bool = True) -> tuple[bool, str]:
    """从远程拉取。默认 --ff-only 避免意外合并提交。
    返回 (ok, combined_output)。用户界面拿到的是 stdout+stderr 合并串。
    """
    args = ["pull"]
    if ff_only:
        args.append("--ff-only")
    rc, out, err = _run(args, cwd, timeout=120)
    combined = (out or "") + (err or "")
    return rc == 0, combined.strip() or ("已是最新" if rc == 0 else "(git 未返回输出)")


def git_push(cwd: str) -> tuple[bool, str]:
    """推送当前分支到上游。返回 (ok, combined_output)。
    无上游时 git 自己会报错，调用方负责提示用户。
    同步阻塞调用，必须放后台线程。
    """
    rc, out, err = _run(["push"], cwd, timeout=120)
    combined = (out or "") + (err or "")
    return rc == 0, combined.strip() or ("已推送" if rc == 0 else "(git 未返回输出)")


def branch_upstream(cwd: str, branch: str) -> str:
    """查指定本地分支配置的上游 ref（如 'origin/develop'），无上游返回空串。
    用 for-each-ref 而不是 rev-parse @{u}，因为后者只能查当前分支。
    """
    rc, out, _ = _run(
        ["for-each-ref", "--format=%(upstream:short)", f"refs/heads/{branch}"],
        cwd, timeout=3,
    )
    return out.strip() if rc == 0 else ""


def update_branch_to_remote(cwd: str, branch: str, is_current: bool) -> tuple[bool, str]:
    """把指定分支更新到它的远程版本（不切换分支）。

    - 当前分支：走 git pull --ff-only（必须用 pull，因为 fetch <branch>:<branch> 不能更新已 checkout 的分支）
    - 非当前分支：git fetch <remote> <remote_branch>:<branch> —— 这是 git 原地推进本地分支的标准写法
    - 远程分支字符串（如 'origin/develop'，本地无同名）：fetch 该 remote 即可，远程 ref 自然更新

    返回 (ok, combined_output)。同步阻塞调用，必须放后台线程。
    """
    if is_current:
        return git_pull(cwd, ff_only=True)

    # 远程引用形式：origin/develop —— 直接 fetch 该 remote
    if "/" in branch:
        remote = branch.split("/", 1)[0]
        rb = branch.split("/", 1)[1]
        rc, out, err = _run(["fetch", remote, rb], cwd, timeout=120)
        combined = (out or "") + (err or "")
        return rc == 0, combined.strip() or ("已 fetch" if rc == 0 else "(git 未返回输出)")

    # 本地分支形式：查它的上游，再用 fetch refspec 原地推进
    upstream = branch_upstream(cwd, branch)
    if not upstream or "/" not in upstream:
        return False, f"分支「{branch}」没有配置上游，无法更新"
    remote = upstream.split("/", 1)[0]
    remote_branch = upstream.split("/", 1)[1]
    rc, out, err = _run(
        ["fetch", remote, f"{remote_branch}:{branch}"], cwd, timeout=120,
    )
    combined = (out or "") + (err or "")
    if rc == 0:
        return True, combined.strip() or f"已更新 {branch} ← {upstream}"
    return False, combined.strip() or "(git 未返回输出)"


def git_merge(cwd: str, branch: str) -> tuple[bool, str]:
    """把指定分支合并到当前分支（无 --ff-only，因为常常需要走 merge commit）。
    冲突时 git 返回非 0 且把冲突文件留在工作区，调用方负责把 stderr 透给用户。
    返回 (ok, combined_output)。
    """
    rc, out, err = _run(["merge", "--no-edit", branch], cwd, timeout=120)
    combined = (out or "") + (err or "")
    return rc == 0, combined.strip() or ("已合并" if rc == 0 else "(git 未返回输出)")


def restore_file_to_upstream(cwd: str, path: str) -> tuple[bool, str]:
    """把单个文件还原到远程 upstream（@{u}）版本，同时丢弃 staged + unstaged 改动。
    调用前应确保 has_upstream(cwd) 为 True 且 file_exists_in_upstream 为 True。
    返回 (ok, stderr)。
    """
    rc, _, err = _run(["checkout", "@{u}", "--", path], cwd, timeout=20)
    return rc == 0, (err or "").strip()


def file_exists_in_upstream(cwd: str, path: str) -> bool:
    """检查 path 在 @{u} 上是否存在（blob 或 tree）。有 upstream 才能调。"""
    rc, _, _ = _run(
        ["cat-file", "-e", f"@{{u}}:{path}"], cwd, timeout=5,
    )
    return rc == 0


def unstage_file(cwd: str, path: str) -> tuple[bool, str]:
    """从暂存区移除（git rm --cached），工作区文件不动。返回 (ok, stderr)。"""
    rc, _, err = _run(["rm", "--cached", "-f", "--", path], cwd, timeout=10)
    return rc == 0, (err or "").strip()


def switch_branch(cwd: str, branch: str) -> tuple[bool, str]:
    """切换分支。
    - 普通本地分支：git checkout <branch>
    - 远程分支（如 origin/foo）：若本地无同名分支，git checkout -b foo --track origin/foo；
      有则直接 checkout 同名本地分支
    返回 (ok, stderr)
    """
    target = branch
    if "/" in branch:
        short = branch.split("/", 1)[1]
        rc_chk, _, _ = _run(["rev-parse", "--verify", short], cwd, timeout=3)
        if rc_chk != 0:
            rc, _, err = _run(
                ["checkout", "-b", short, "--track", branch], cwd, timeout=20,
            )
            return rc == 0, (err or "").strip()
        target = short
    rc, _, err = _run(["checkout", target], cwd, timeout=20)
    return rc == 0, (err or "").strip()
