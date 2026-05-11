"""git 状态读取封装

除 git_fetch 外均不改变仓库状态。主要给状态栏和 GitViewer 用。
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
        lines: list[str] = []
        truncated = False
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                if idx >= max_lines:
                    truncated = True
                    break
                lines.append(line.rstrip("\r\n"))
    except OSError as e:
        return f"(读取失败) {e}"
    header = (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
    )
    body = "\n".join("+" + ln for ln in lines)
    if truncated:
        body += f"\n\n(... 仅展示前 {max_lines} 行 ...)"
    return header + body


def current_branch(cwd: str) -> str:
    rc, out, _ = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd, timeout=3)
    return out.strip() if rc == 0 else ""


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
