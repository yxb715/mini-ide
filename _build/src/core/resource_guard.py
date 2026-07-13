"""资源守卫

后台线程定期检测系统内存和 node.exe 进程数量，
当超过阈值时自动杀掉多余 node 子进程，防止 OOM 把整机搞崩。

设计原则：
- 只杀 node.exe（前端 dev server fork 出来的 worker），不动 java/nginx 等服务进程
- 优先杀非 mini-ide 管理的 node 进程（通过父进程链判断归属）
- 触发条件：内存 > 85% 且 node 进程 > 10，或 node 进程 >= 30（硬上限）
- 15 秒冷却期，避免反复触发
- 杀进程前先 log 告警，方便事后排查
"""
from __future__ import annotations

import threading
import time

import psutil

from src.util import app_log

log = app_log.get_logger("resource_guard")

# ---- 配置 ----
_MEMORY_THRESHOLD_PERCENT = 85  # 系统内存使用率阈值
_NODE_PROCESS_HARD_LIMIT = 30   # node.exe 进程数硬上限（超过就开杀）
_NODE_PROCESS_KEEP = 10         # 杀完后保留的最大 node 进程数
_CHECK_INTERVAL = 5.0           # 检查间隔（秒）
_COOLDOWN_SECONDS = 15.0        # 触发一次保护后的冷却期

# ---- 状态 ----
_last_trigger_time: float = 0.0
_guard_thread_started = False
_enabled = True

# ---- 统计（供 UI/CLI 查询）----
_stats = {
    "total_kills": 0,
    "last_trigger": None,  # ISO 时间字符串
    "last_reason": "",
}
_stats_lock = threading.Lock()


def set_enabled(enabled: bool) -> None:
    """开关守卫"""
    global _enabled
    _enabled = enabled
    log.info("资源守卫 %s", "已启用" if enabled else "已禁用")


def get_stats() -> dict:
    """返回守卫统计信息"""
    with _stats_lock:
        return dict(_stats)


def _is_managed_by_mini_ide(proc: psutil.Process) -> bool:
    """判断某个进程是否由 mini-ide 启动（沿父进程链往上找 mini-ide.exe）"""
    try:
        visited = set()
        p = proc
        for _ in range(10):  # 最多追溯 10 层
            parent = p.parent()
            if parent is None or parent.pid in visited:
                return False
            visited.add(parent.pid)
            if parent.name().lower() == "mini-ide.exe":
                return True
            p = parent
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
    return False


def _get_node_processes() -> tuple[list[psutil.Process], list[psutil.Process]]:
    """获取所有 node.exe 进程，分为 managed（mini-ide 启动的）和 unmanaged（外部的）

    返回 (managed, unmanaged)，各自按内存占用降序排列
    """
    managed = []
    unmanaged = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == "node.exe":
                if _is_managed_by_mini_ide(proc):
                    managed.append(proc)
                else:
                    unmanaged.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    # 按内存占用降序，优先杀内存大的
    key_fn = lambda p: p.info.get("memory_info").rss if p.info.get("memory_info") else 0
    managed.sort(key=key_fn, reverse=True)
    unmanaged.sort(key=key_fn, reverse=True)
    return managed, unmanaged


def _check_and_protect() -> None:
    """一次检查+保护循环"""
    global _last_trigger_time

    if not _enabled:
        return

    # 冷却期内不触发
    now = time.time()
    if now - _last_trigger_time < _COOLDOWN_SECONDS:
        return

    # 检查系统内存
    mem = psutil.virtual_memory()
    mem_percent = mem.percent

    # 获取所有 node 进程（分为 mini-ide 管理的和外部的）
    managed, unmanaged = _get_node_processes()
    total_count = len(managed) + len(unmanaged)

    # 判断是否需要介入
    need_protect = False
    reason = ""

    if mem_percent >= _MEMORY_THRESHOLD_PERCENT and total_count > _NODE_PROCESS_KEEP:
        need_protect = True
        reason = f"内存 {mem_percent:.0f}% >= {_MEMORY_THRESHOLD_PERCENT}%，node 进程 {total_count} 个(管理{len(managed)}/外部{len(unmanaged)})"
    elif total_count >= _NODE_PROCESS_HARD_LIMIT:
        need_protect = True
        reason = f"node 进程数 {total_count} >= 硬上限 {_NODE_PROCESS_HARD_LIMIT}"

    if not need_protect:
        return

    # 优先杀外部的（非 mini-ide 管理的），这些通常是 AI 工具或其他来源 fork 的
    killable = unmanaged
    if not killable:
        # 如果全是 mini-ide 管理的，也得保命，杀内存最大的那些
        killable = managed
        log.warning("资源告警: %s，所有 node 进程都是 mini-ide 管理的，将杀掉多余的", reason)

    if not killable:
        return

    # 计算要杀多少个：保留 _NODE_PROCESS_KEEP 个
    safe_count = len(managed) if killable is unmanaged else 0
    to_keep = max(_NODE_PROCESS_KEEP - safe_count, 0)
    to_kill = killable[to_keep:]  # 已按内存降序，保留小的，杀大的

    if not to_kill:
        return

    _last_trigger_time = now
    log.warning("⚠️ 资源守卫触发: %s，将杀掉 %d 个 node 进程", reason, len(to_kill))

    killed = 0
    for proc in to_kill:
        try:
            pid = proc.pid
            mem_mb = proc.memory_info().rss / 1024 / 1024
            cmdline = " ".join(proc.cmdline()[:3])[:100]
            proc.kill()
            killed += 1
            log.info("  已杀: PID=%d, 内存=%.0fMB, cmd=%s", pid, mem_mb, cmdline)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    log.warning("资源守卫完成: 杀掉 %d/%d 个 node 进程", killed, len(to_kill))

    # 更新统计
    with _stats_lock:
        _stats["total_kills"] += killed
        _stats["last_trigger"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _stats["last_reason"] = reason


def _guard_loop() -> None:
    """后台守卫线程主循环"""
    while True:
        try:
            _check_and_protect()
        except Exception as e:
            log.error("资源守卫异常: %s", e)
        time.sleep(_CHECK_INTERVAL)


def ensure_started() -> None:
    """确保守卫线程已启动（幂等，可多次调用）"""
    global _guard_thread_started
    if _guard_thread_started:
        return
    _guard_thread_started = True
    t = threading.Thread(target=_guard_loop, daemon=True, name="resource-guard")
    t.start()
    log.info("资源守卫已启动 (内存阈值=%d%%, node硬上限=%d, 保留=%d)",
             _MEMORY_THRESHOLD_PERCENT, _NODE_PROCESS_HARD_LIMIT, _NODE_PROCESS_KEEP)
