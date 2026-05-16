"""Git 网络操作的后台 QThread 包装。

mini-ide 只做静默 fetch 来刷新 ahead/behind 状态；pull / push / merge
这类会改仓库状态的操作交给外部 AI / 终端处理。
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
