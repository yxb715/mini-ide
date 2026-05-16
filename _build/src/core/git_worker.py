"""Git 网络操作的后台 QThread 包装。

mini-ide 只做静默 fetch 来刷新 ahead/behind 状态；
checkout / merge+push 走独立 worker 避免冻 UI。
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal


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


class GitCheckoutWorker(QThread):
    """后台切换分支。done(ok, error_msg)"""

    done = Signal(bool, str)

    def __init__(self, cwd: str, branch: str, parent=None):
        super().__init__(parent)
        self.cwd = cwd
        self.branch = branch

    def run(self) -> None:
        from src.core.git_ops import git_checkout
        ok, msg = git_checkout(self.cwd, self.branch)
        self.done.emit(ok, msg)


class GitMergePushWorker(QThread):
    """后台执行 fetch + merge 远程分支 + push。done(ok, msg)"""

    done = Signal(bool, str)

    def __init__(self, cwd: str, remote_branch: str, parent=None):
        super().__init__(parent)
        self.cwd = cwd
        self.remote_branch = remote_branch

    def run(self) -> None:
        from src.core.git_ops import git_merge_remote_branch
        ok, msg = git_merge_remote_branch(self.cwd, self.remote_branch)
        self.done.emit(ok, msg)
