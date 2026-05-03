"""端口占用查询 + 杀进程（纯逻辑，无 Qt 依赖）

用 psutil 遍历网络连接找占用指定端口的进程。
Windows 上 net_connections() 可能 200-800ms，**调用方必须放 QThread**，
不要在主线程直接调（见 CLAUDE.md 常见坑 1）。
"""
from __future__ import annotations

from dataclasses import dataclass

import psutil


@dataclass
class PortOwner:
    pid: int
    name: str            # 进程名，如 java.exe / node.exe
    cmdline: str         # 命令行（截断到 500 字符）
    status: str          # LISTEN / ESTABLISHED / ...
    address: str         # 0.0.0.0:8080 / 127.0.0.1:8080 / [::]:8080


def list_port_listeners(port: int) -> list[PortOwner]:
    """查所有占用指定 port 的进程（LISTEN + ESTABLISHED 都算）。
    同一 PID 去重。无权限 / 拿不到进程信息时返回部分结果而非抛错。
    """
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, RuntimeError, OSError):
        return []

    out: list[PortOwner] = []
    seen_pids: set[int] = set()
    for c in conns:
        if not c.laddr or c.laddr.port != port:
            continue
        pid = c.pid
        if pid is None or pid in seen_pids:
            continue
        seen_pids.add(pid)
        name = "?"
        cmdline = ""
        try:
            p = psutil.Process(pid)
            name = p.name() or "?"
            cmdline = " ".join(p.cmdline())[:500]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        out.append(PortOwner(
            pid=pid,
            name=name,
            cmdline=cmdline,
            status=str(c.status) if c.status else "",
            address=f"{c.laddr.ip}:{c.laddr.port}",
        ))
    return out


def kill_process(pid: int, force: bool = False) -> tuple[bool, str]:
    """杀进程。默认 terminate，3s 内不退则升级到 kill。
    force=True 直接 kill。
    返回 (ok, message)。
    """
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True, f"进程 {pid} 已不存在"

    try:
        if force:
            p.kill()
        else:
            p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            if not force:
                p.kill()
                try:
                    p.wait(timeout=2)
                except psutil.TimeoutExpired:
                    return False, "kill 后仍未退出（可能权限不足）"
    except psutil.AccessDenied as e:
        return False, f"权限不足：{e}"
    except psutil.NoSuchProcess:
        return True, "已结束"
    return True, "已结束"
