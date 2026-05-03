"""项目文件索引

启动时扫描一次（后台线程），支持按文件名 fuzzy 搜索。
watchdog 订阅文件系统变化，自动增量更新。忽略常见的生成目录。
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal


IGNORED_DIRS = {
    ".git", ".idea", ".vscode", ".gradle", ".mvn", ".nuxt", ".next", ".output",
    "build", "target", "node_modules", "dist", "out", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".cache", ".angular",
    "bin", "obj", ".parcel-cache",
}

IGNORED_EXTS = {
    ".class", ".jar", ".war", ".pyc", ".pyo", ".o", ".a", ".so", ".dll", ".exe",
    ".lock", ".sum",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf",
    ".mp3", ".mp4", ".zip", ".7z", ".tar", ".gz",
}

MAX_FILES = 50_000           # 超过这个数只索引前 N 个，避免巨型单仓库炸内存


@dataclass
class IndexedFile:
    rel_path: str            # 相对项目根
    abs_path: str
    name: str                # 小写文件名（用于匹配）
    name_original: str       # 原始大小写（用于显示）

    def __lt__(self, other: "IndexedFile") -> bool:
        return self.rel_path < other.rel_path


class FileIndexer(QObject):
    """一个项目对应一个 FileIndexer 实例"""

    indexed = Signal(int)        # 扫描完成，参数=文件总数
    file_added = Signal(str)     # abs_path
    file_removed = Signal(str)

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self.root = Path(project_root).resolve()
        self._lock = threading.RLock()
        self._files: list[IndexedFile] = []
        self._abs_set: set[str] = set()
        self._observer = None
        self._worker: _IndexWorker | None = None

    # ---- public ----

    def start_async_scan(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._worker = _IndexWorker(str(self.root))
        self._worker.done.connect(self._apply_snapshot)
        self._worker.start()

    def files(self) -> list[IndexedFile]:
        with self._lock:
            return list(self._files)

    def count(self) -> int:
        with self._lock:
            return len(self._files)

    def search(self, query: str, limit: int = 100) -> list[IndexedFile]:
        """按文件名 fuzzy 搜索"""
        query = query.strip().lower()
        if not query:
            with self._lock:
                return self._files[:limit]
        matches: list[tuple[int, IndexedFile]] = []
        with self._lock:
            files = self._files
        for f in files:
            score = _fuzzy_score(f.name, query, f.rel_path.lower())
            if score > 0:
                matches.append((score, f))
        matches.sort(key=lambda p: (-p[0], p[1].rel_path))
        return [m[1] for m in matches[:limit]]

    def stop(self) -> None:
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=1)
            except Exception:
                pass
            self._observer = None

    # ---- 内部 ----

    def _apply_snapshot(self, files: list[IndexedFile]) -> None:
        with self._lock:
            self._files = sorted(files)
            self._abs_set = {f.abs_path for f in files}
        self.indexed.emit(len(files))
        self._start_watchdog()

    def _start_watchdog(self) -> None:
        if self._observer:
            return
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            return

        parent = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                parent._on_fs_change(event.src_path, added=True)

            def on_deleted(self, event):
                if event.is_directory:
                    return
                parent._on_fs_change(event.src_path, added=False)

            def on_moved(self, event):
                if event.is_directory:
                    return
                parent._on_fs_change(event.src_path, added=False)
                parent._on_fs_change(event.dest_path, added=True)

        observer = Observer()
        observer.schedule(Handler(), str(self.root), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer

    def _on_fs_change(self, path: str, added: bool) -> None:
        try:
            p = Path(path).resolve()
        except OSError:
            return
        if not p.is_relative_to(self.root):
            return
        if _should_ignore(p, self.root):
            return
        key = str(p)
        with self._lock:
            if added:
                if key in self._abs_set:
                    return
                rel = str(p.relative_to(self.root)).replace("\\", "/")
                entry = IndexedFile(
                    rel_path=rel, abs_path=key,
                    name=p.name.lower(), name_original=p.name,
                )
                # 插入排序位置（保持 _files 有序）
                from bisect import insort
                insort(self._files, entry)
                self._abs_set.add(key)
                self.file_added.emit(key)
            else:
                if key not in self._abs_set:
                    return
                self._files = [f for f in self._files if f.abs_path != key]
                self._abs_set.discard(key)
                self.file_removed.emit(key)


class _IndexWorker(QThread):
    done = Signal(list)

    def __init__(self, root: str):
        super().__init__()
        self.root = root

    def run(self) -> None:
        files: list[IndexedFile] = []
        root_path = Path(self.root)
        try:
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = [d for d in dirnames
                               if d not in IGNORED_DIRS and not d.startswith(".") or d in (".env",)]
                for fn in filenames:
                    ext = Path(fn).suffix.lower()
                    if ext in IGNORED_EXTS:
                        continue
                    abs_p = Path(dirpath) / fn
                    try:
                        rel = str(abs_p.relative_to(root_path)).replace("\\", "/")
                    except ValueError:
                        continue
                    files.append(IndexedFile(
                        rel_path=rel,
                        abs_path=str(abs_p),
                        name=fn.lower(),
                        name_original=fn,
                    ))
                    if len(files) >= MAX_FILES:
                        self.done.emit(files)
                        return
        except OSError:
            pass
        self.done.emit(files)


def _should_ignore(p: Path, root: Path) -> bool:
    try:
        rel = p.relative_to(root)
    except ValueError:
        return True
    parts = rel.parts
    for part in parts[:-1]:
        if part in IGNORED_DIRS:
            return True
        if part.startswith(".") and part not in (".env",):
            return True
    if p.suffix.lower() in IGNORED_EXTS:
        return True
    return False


def _fuzzy_score(name: str, query: str, rel_path: str) -> int:
    """简单打分：文件名连续匹配最高分，子串匹配次之，字符顺序匹配最低"""
    if not query:
        return 1
    if query == name:
        return 1000
    if name.startswith(query):
        return 800
    if query in name:
        return 500
    if query in rel_path:
        return 300
    # 字符顺序匹配（a-b-c 能匹配 ab-c）
    idx = 0
    for ch in query:
        pos = name.find(ch, idx)
        if pos < 0:
            return 0
        idx = pos + 1
    return 100
