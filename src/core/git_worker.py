"""Git 网络操作的后台 QThread 包装

git pull / git fetch 是同步阻塞调用，可达数十秒。直接放主线程会冻 UI。
这两个 worker 类原本散落在 project_tab.py 顶部，2026-04 拆出来归位。
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class GitPullWorker(QThread):
    """git pull --ff-only 后台执行。done(ok, output)"""

    done = Signal(bool, str)

    def __init__(self, cwd: str, parent=None):
        super().__init__(parent)
        self.cwd = cwd

    def run(self) -> None:
        from src.core.git_ops import git_pull
        ok, output = git_pull(self.cwd)
        self.done.emit(ok, output)


class GitFetchWorker(QThread):
    """git fetch 后台执行（不改本地）。done(ok)"""

    done = Signal(bool)

    def __init__(self, cwd: str, parent=None):
        super().__init__(parent)
        self.cwd = cwd

    def run(self) -> None:
        from src.core.git_ops import git_fetch
        ok = git_fetch(self.cwd)
        self.done.emit(ok)


class GitPushWorker(QThread):
    """git push 当前分支到上游。done(ok, output)"""

    done = Signal(bool, str)

    def __init__(self, cwd: str, parent=None):
        super().__init__(parent)
        self.cwd = cwd

    def run(self) -> None:
        from src.core.git_ops import git_push
        ok, output = git_push(self.cwd)
        self.done.emit(ok, output)


class GitMergeWorker(QThread):
    """git merge <branch> 到当前分支。done(ok, output)"""

    done = Signal(bool, str)

    def __init__(self, cwd: str, branch: str, parent=None):
        super().__init__(parent)
        self.cwd = cwd
        self.branch = branch

    def run(self) -> None:
        from src.core.git_ops import git_merge
        ok, output = git_merge(self.cwd, self.branch)
        self.done.emit(ok, output)


class GitUpdateBranchWorker(QThread):
    """把指定分支更新到远程（不切换）。
    - 当前分支：pull --ff-only
    - 非当前本地/远程分支：fetch refspec
    done(branch, ok, output)
    """

    done = Signal(str, bool, str)

    def __init__(self, cwd: str, branch: str, is_current: bool, parent=None):
        super().__init__(parent)
        self.cwd = cwd
        self.branch = branch
        self.is_current = is_current

    def run(self) -> None:
        from src.core.git_ops import update_branch_to_remote
        ok, output = update_branch_to_remote(self.cwd, self.branch, self.is_current)
        self.done.emit(self.branch, ok, output)
