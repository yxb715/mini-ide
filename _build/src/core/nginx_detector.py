"""Nginx process detection for local project directories."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

import psutil


@dataclass(frozen=True)
class NginxStatus:
    running: bool
    master_pid: int | None = None
    pids: list[int] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    configured_ports: list[int] = field(default_factory=list)
    conf_path: str = ""
    reason: str = ""


def is_nginx_dir(path: str | Path) -> bool:
    root = Path(path)
    return (root / "nginx.exe").is_file() and _conf_path(root).is_file()


def detect_nginx(path: str | Path, port_snapshot: dict[int, list[dict]] | None = None) -> NginxStatus:
    """Return running status for the nginx instance that belongs to path."""
    root = Path(path).resolve()
    conf = _conf_path(root)
    if not (root / "nginx.exe").is_file() or not conf.is_file():
        return NginxStatus(False, reason="不是 nginx 目录")

    exe_norm = _norm(root / "nginx.exe")
    conf_norm = _norm(conf)
    if port_snapshot is not None:
        pids = _matching_pids_from_snapshot(port_snapshot, exe_norm, conf_norm, _norm(str(root)))
        file_pid = _pid_from_file(root / "running.pid")
        if file_pid and _pid_belongs_to_nginx(file_pid, exe_norm, conf_norm, _norm(str(root))):
            pids.add(file_pid)
        if not pids:
            return NginxStatus(
                False,
                ports=[],
                configured_ports=_configured_ports(conf),
                conf_path=str(conf),
                reason="未检测到 nginx.exe 进程",
            )
        ports = _ports_for_pids(pids, port_snapshot)
        configured_ports = _configured_ports(conf)
        return NginxStatus(
            True,
            master_pid=file_pid if file_pid in pids else min(pids),
            pids=sorted(pids),
            ports=ports or configured_ports,
            configured_ports=configured_ports,
            conf_path=str(conf),
            reason="检测到本目录的 nginx.exe 进程",
        )

    matched: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline", "ppid"]):
        try:
            if (proc.info.get("name") or "").lower() != "nginx.exe":
                continue
            exe = _norm(proc.info.get("exe") or "")
            cmd = _cmdline(proc.info.get("cmdline") or [])
            if exe == exe_norm or conf_norm in _norm_text(cmd) or _norm(str(root)) in _norm_text(cmd):
                matched.append(proc)
        except (psutil.Error, OSError):
            continue

    if not matched:
        return NginxStatus(
            False,
            ports=[],
            configured_ports=_configured_ports(conf),
            conf_path=str(conf),
            reason="未检测到 nginx.exe 进程",
        )

    pids = sorted({p.pid for p in matched})
    master_pid = _master_pid(matched)
    ports = _ports_for_pids(set(pids), port_snapshot)
    configured_ports = _configured_ports(conf)
    return NginxStatus(
        True,
        master_pid=master_pid,
        pids=pids,
        ports=ports or configured_ports,
        configured_ports=configured_ports,
        conf_path=str(conf),
        reason="检测到本目录的 nginx.exe 进程",
    )


def _conf_path(root: Path) -> Path:
    direct = root / "nginx.conf"
    if direct.is_file():
        return direct
    return root / "conf" / "nginx.conf"


def _norm(value: str | Path) -> str:
    try:
        return str(Path(value).resolve()).replace("\\", "/").lower()
    except (OSError, RuntimeError, ValueError):
        return str(value).replace("\\", "/").lower()


def _norm_text(value: str) -> str:
    return value.replace("\\", "/").lower()


def _cmdline(parts: list[str] | tuple[str, ...] | str) -> str:
    if isinstance(parts, str):
        return parts
    return " ".join(str(p) for p in parts)


def _matching_pids_from_snapshot(
    snapshot: dict[int, list[dict]],
    exe_norm: str,
    conf_norm: str,
    root_norm: str,
) -> set[int]:
    pids: set[int] = set()
    for holders in snapshot.values():
        for holder in holders:
            if (holder.get("name") or "").lower() != "nginx.exe":
                continue
            pid = holder.get("pid")
            if pid is None:
                continue
            exe = _norm_text(holder.get("exe") or "")
            cmd = _norm_text(holder.get("cmdline") or "")
            if exe == exe_norm or conf_norm in cmd or root_norm in cmd:
                pids.add(int(pid))
    return pids


def _pid_from_file(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def _pid_belongs_to_nginx(pid: int, exe_norm: str, conf_norm: str, root_norm: str) -> bool:
    try:
        proc = psutil.Process(pid)
        if proc.name().lower() != "nginx.exe":
            return False
        exe = _norm(proc.exe())
        cmd = _norm_text(_cmdline(proc.cmdline()))
        return exe == exe_norm or conf_norm in cmd or root_norm in cmd
    except (psutil.Error, OSError):
        return False


def _master_pid(procs: list[psutil.Process]) -> int | None:
    pids = {p.pid for p in procs}
    for proc in procs:
        try:
            if proc.ppid() not in pids:
                return proc.pid
        except psutil.Error:
            continue
    return min(pids) if pids else None


def _ports_for_pids(pids: set[int], snapshot: dict[int, list[dict]] | None) -> list[int]:
    if not pids:
        return []
    if snapshot is not None:
        ports = {
            int(port)
            for port, holders in snapshot.items()
            for holder in holders
            if holder.get("pid") in pids
        }
        return sorted(ports)
    ports: set[int] = set()
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, psutil.Error):
        return []
    for conn in conns:
        if conn.pid not in pids or not conn.laddr:
            continue
        if conn.status not in (psutil.CONN_LISTEN, "LISTEN"):
            continue
        ports.add(int(conn.laddr.port))
    return sorted(ports)


def _configured_ports(conf: Path) -> list[int]:
    try:
        text = conf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    ports: set[int] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        for m in re.finditer(r"(?:^|[{\s])listen\s+([^;]+)", line):
            first = m.group(1).split()[0].strip()
            port_text = first.rsplit(":", 1)[-1].strip("[]")
            if port_text.isdigit():
                ports.add(int(port_text))
    return sorted(ports)
