"""进程管理

用 QProcess 启动进程，psutil 递归杀子进程（Gradle/Maven 启动的 JVM 进程必须这么杀）。
实时发 outputLine 信号，UI 订阅后写入日志区。
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field

import psutil
from PySide6.QtCore import QObject, QProcess, Signal

from src.util import app_log

log = app_log.get_logger("runner")


STREAM_STDOUT = "stdout"
STREAM_STDERR = "stderr"


@dataclass
class RunContext:
    """一次运行的上下文"""
    cwd: str
    env: dict[str, str] = field(default_factory=dict)
    jvm_opts: str = ""
    spring_profile: str = ""
    extra_args: list[str] = field(default_factory=list)


class ProcessRunner(QObject):
    """单个子进程的生命周期管理。一个 ProjectTab 内串行复用一个 Runner。"""

    outputLine = Signal(str, str)        # (stream, line)
    started = Signal()
    finished = Signal(int)               # exit_code（-1 表示手动杀掉）
    stateChanged = Signal(str)           # idle | running | stopping

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._state = "idle"
        self._started_at: float = 0.0
        self._manually_stopped = False
        self._stdout_buf = b""
        self._stderr_buf = b""

    # ---- public API ----

    def is_running(self) -> bool:
        return self._state == "running"

    def state(self) -> str:
        return self._state

    def elapsed_seconds(self) -> float:
        if not self._started_at:
            return 0.0
        return time.time() - self._started_at

    def start(self, command: list[str], ctx: RunContext) -> bool:
        """启动进程。返回是否启动成功（已在运行时返回 False）"""
        if self.is_running():
            return False

        env = _build_child_env(ctx)

        full_cmd = list(command) + list(ctx.extra_args or [])
        display = " ".join(shlex.quote(c) if " " in c else c for c in full_cmd)
        self.outputLine.emit("meta", f"$ {display}")
        self.outputLine.emit("meta", f"  cwd: {ctx.cwd}")
        if ctx.spring_profile:
            self.outputLine.emit("meta", f"  SPRING_PROFILES_ACTIVE={ctx.spring_profile}")
        if ctx.jvm_opts:
            self.outputLine.emit("meta", f"  JAVA_TOOL_OPTIONS={ctx.jvm_opts}")

        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(ctx.cwd)
        self._proc.setProcessEnvironment(_env_to_qt(env))
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        _suppress_console(self._proc)
        self._proc.readyReadStandardOutput.connect(self._on_stdout)
        self._proc.readyReadStandardError.connect(self._on_stderr)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)

        self._stdout_buf = b""
        self._stderr_buf = b""
        self._manually_stopped = False

        # Windows 下 .bat/.cmd 脚本必须通过 cmd /c 启动
        # 把已经清洗过的 env 传进去，用干净的 PATH 解析命令名，避免吃到 mini-ide 自己的 venv
        program, args = _split_program(full_cmd, env=env)
        log.info("启动进程: %s %s | cwd=%s | profile=%s | jvm=%s",
                 program, " ".join(args), ctx.cwd, ctx.spring_profile, ctx.jvm_opts)
        self._proc.start(program, args)
        if not self._proc.waitForStarted(5000):
            err = self._proc.errorString() or "(未知原因)"
            log.error("进程启动失败: %s | error=%s | args=%s", program, err, args)
            self.outputLine.emit(STREAM_STDERR, f"[启动失败] {program} 无法启动：{err}")
            self.outputLine.emit(STREAM_STDERR, f"  完整命令: {program} {' '.join(args)}")
            self.outputLine.emit(STREAM_STDERR, f"  工作目录: {ctx.cwd}")
            self._set_state("idle")
            return False

        self._started_at = time.time()
        self._set_state("running")
        self.started.emit()
        return True

    def stop(self, timeout_ms: int = 5000) -> None:
        """优雅停止：先 SIGTERM 整棵进程树，超时则 SIGKILL"""
        if not self._proc or self._state != "running":
            return
        self._set_state("stopping")
        self._manually_stopped = True
        pid = int(self._proc.processId())
        if pid > 0:
            _kill_tree(pid, graceful_timeout=timeout_ms / 1000)
        else:
            self._proc.kill()

    # ---- internal ----

    def _set_state(self, s: str) -> None:
        self._state = s
        self.stateChanged.emit(s)

    def _drain(self, buf_attr: str, new_data: bytes, stream: str) -> None:
        """把字节流按行切分发射（处理 CRLF/CR/LF 和半行）"""
        buf = getattr(self, buf_attr) + new_data
        while True:
            idx = -1
            for sep in (b"\r\n", b"\n", b"\r"):
                pos = buf.find(sep)
                if pos >= 0 and (idx < 0 or pos < idx):
                    idx = pos
                    sep_len = len(sep)
            if idx < 0:
                break
            line = buf[:idx].decode("utf-8", errors="replace")
            buf = buf[idx + sep_len:]
            self.outputLine.emit(stream, line)
        setattr(self, buf_attr, buf)

    def _flush_remaining(self) -> None:
        for attr, stream in (("_stdout_buf", STREAM_STDOUT), ("_stderr_buf", STREAM_STDERR)):
            remaining = getattr(self, attr)
            if remaining:
                self.outputLine.emit(stream, remaining.decode("utf-8", errors="replace"))
                setattr(self, attr, b"")

    def _on_stdout(self) -> None:
        if self._proc:
            data = bytes(self._proc.readAllStandardOutput().data())
            self._drain("_stdout_buf", data, STREAM_STDOUT)

    def _on_stderr(self) -> None:
        if self._proc:
            data = bytes(self._proc.readAllStandardError().data())
            self._drain("_stderr_buf", data, STREAM_STDERR)

    def _on_finished(self, exit_code: int, _status) -> None:
        self._flush_remaining()
        code = -1 if self._manually_stopped else int(exit_code)
        elapsed = self.elapsed_seconds()
        tag = "已手动停止" if self._manually_stopped else (f"退出码 {exit_code}")
        log.info("进程结束: exit=%s manual=%s elapsed=%.1fs",
                 exit_code, self._manually_stopped, elapsed)
        self.outputLine.emit("meta", f"[进程结束] {tag}  耗时 {elapsed:.1f}s")
        self._started_at = 0.0
        self._set_state("idle")
        self.finished.emit(code)

    def _on_error(self, err) -> None:
        log.error("进程错误: %s", err)
        self.outputLine.emit(STREAM_STDERR, f"[进程错误] {err}")


# ---------- helpers ----------

def _env_to_qt(env: dict[str, str]):
    from PySide6.QtCore import QProcessEnvironment
    qenv = QProcessEnvironment()
    for k, v in env.items():
        qenv.insert(k, v)
    return qenv


def _suppress_console(proc: QProcess) -> None:
    """Windows 下阻止 cmd.exe 弹出控制台窗口

    QProcess 默认会继承父进程的 console 设置，GUI 程序（pythonw/打包后的 exe）
    启动 cmd.exe 时会弹一个黑框。用 CREATE_NO_WINDOW 标志压掉。
    """
    if sys.platform != "win32":
        return
    try:
        CREATE_NO_WINDOW = 0x08000000

        def _modifier(args):
            args.flags |= CREATE_NO_WINDOW

        proc.setCreateProcessArgumentsModifier(_modifier)
    except AttributeError:
        # 老版 PySide6 没有这个 API，回退：什么都不做
        pass


def _split_program(cmd: list[str], env: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """Windows 下启动策略

    - `.bat` / `.cmd` → cmd.exe /c
    - `python` / `pip` / `node` 这类命令名 **永远不做预解析**，直接交给 cmd.exe /c，
      让子进程用它自己（已经清掉 mini-ide venv）的 PATH 去找正确的 exe。
      否则 shutil.which 会用 mini-ide 的 PATH 找到 mini-ide 自己的 python.exe。
    - 其他命令名（npm/yarn/pnpm/poetry 等）用传入的 env['PATH'] 解析，
      找到真文件后再分 .cmd→cmd.exe /c 还是直接跑 .exe。
    - 绝对路径 + .exe 直接跑。
    """
    if not cmd:
        return "", []
    program = cmd[0]
    args = cmd[1:]
    if sys.platform != "win32":
        return program, args

    lower = program.lower()
    if lower.endswith((".bat", ".cmd")):
        return "cmd.exe", ["/c", program, *args]
    if lower.endswith(".exe") or os.path.isabs(program):
        return program, args

    # 这些命令必须用子进程干净 PATH 去找，不能让父进程 venv 污染
    _DEFER_TO_SHELL = {"python", "python3", "python.exe", "python3.exe",
                       "pip", "pip3", "pip.exe", "pip3.exe"}
    if lower in _DEFER_TO_SHELL:
        return "cmd.exe", ["/c", program, *args]

    # 其他命令名：用 child env 的 PATH 解析
    search_path = env.get("PATH") if env else None
    resolved = shutil.which(program, path=search_path)
    if resolved:
        if resolved.lower().endswith((".bat", ".cmd")):
            return "cmd.exe", ["/c", resolved, *args]
        return resolved, args

    # 兜底：交给 cmd.exe /c
    return "cmd.exe", ["/c", program, *args]


def _build_child_env(ctx: "RunContext") -> dict[str, str]:
    """构建干净的子进程环境

    **关键**：把 mini-ide 自己的 venv 从环境里摘掉，否则 poetry/python 会误用
    mini-ide 的 venv 而不是项目自己的 venv。
    """
    env = os.environ.copy()

    # 1. 摘除 mini-ide 自己的 venv 痕迹
    self_venv = os.environ.get("VIRTUAL_ENV", "")
    if self_venv and _is_mini_ide_venv(self_venv):
        env.pop("VIRTUAL_ENV", None)
        # 从 PATH 里删掉 <venv>/Scripts 或 <venv>/bin
        bad_parts = {os.path.join(self_venv, "Scripts"), os.path.join(self_venv, "bin")}
        path = env.get("PATH", "")
        sep = os.pathsep
        kept = [p for p in path.split(sep) if p and os.path.normpath(p) not in
                {os.path.normpath(b) for b in bad_parts}]
        env["PATH"] = sep.join(kept)
        # POETRY_ACTIVE 也得清，否则 poetry 以为当前已经在 venv 内
        env.pop("POETRY_ACTIVE", None)
        # 其他工具用的激活标记
        env.pop("PYTHONHOME", None)

    # 2. 读取项目根目录的 .env 文件（KEY=VALUE 格式，# 注释，不展开变量引用）
    _load_dotenv(ctx.cwd, env)

    # 3. 用户自定义 env 覆盖（优先级高于 .env）
    env.update(ctx.env or {})

    # 3. 强制子进程用 UTF-8 输出
    # 背景：Windows 上 Java/Python 子进程的 stdout/stderr 默认用系统活动代码页
    # （中文系统 = GBK / CP936），mini-ide 按 UTF-8 解码遇到 GBK 字节就替换成 ?，
    # 典型表现：IOException 本地化消息里的中文句号"。"变 "?"。
    #
    # Java：
    #   -Dfile.encoding=UTF-8     所有 Java 版本都认；Java 18+ 默认本就是 UTF-8 (JEP 400)
    #   -Dstdout.encoding=UTF-8   Java 19+ 精准生效（不动 file.encoding）
    #   -Dstderr.encoding=UTF-8   同上
    # 副作用：JVM 启动时会打一行 "Picked up JAVA_TOOL_OPTIONS: ..."，可接受
    java_utf8 = "-Dfile.encoding=UTF-8 -Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8"
    existing_jto = env.get("JAVA_TOOL_OPTIONS", "")
    parts = [java_utf8]
    if existing_jto:
        parts.append(existing_jto)
    if ctx.jvm_opts:
        parts.append(ctx.jvm_opts)
    env["JAVA_TOOL_OPTIONS"] = " ".join(parts)

    if ctx.spring_profile:
        env.setdefault("SPRING_PROFILES_ACTIVE", ctx.spring_profile)

    # 4. 让输出实时、不带终端颜色控制符
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Python 子进程：强制 stdout/stderr 用 UTF-8（同上，避免 GBK 默认）
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("FORCE_COLOR", "0")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")

    return env


def _load_dotenv(cwd: str, env: dict[str, str]) -> None:
    """读取 cwd/.env，把 KEY=VALUE 注入到 env（已有的 key 不覆盖）。"""
    dotenv = os.path.join(cwd, ".env")
    try:
        with open(dotenv, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    env.setdefault(key, value)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _is_mini_ide_venv(venv_path: str) -> bool:
    """判断某个 VIRTUAL_ENV 是不是 mini-ide 自己的 .venv"""
    try:
        norm = os.path.normpath(os.path.abspath(venv_path)).lower()
        # 简单启发：路径里含 "mini-ide" 且以 .venv / venv 结尾
        base = os.path.basename(norm)
        if base not in (".venv", "venv"):
            return False
        parent = os.path.basename(os.path.dirname(norm))
        return parent == "mini-ide"
    except Exception:
        return False


def _kill_tree(pid: int, graceful_timeout: float = 3.0) -> None:
    """递归终结进程树：先 terminate，超时则 kill"""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = [parent, *parent.children(recursive=True)]
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=graceful_timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass


# ---------- 端口工具 ----------

# psutil.net_connections() 在 Windows 上要遍历所有 TCP 连接，一次调用 200-800ms，
# 直接在主线程扫会卡拖拽。我们用后台线程定期更新快照，UI 永远读缓存（非阻塞）。
_PORT_SNAPSHOT_INTERVAL = 3.0
_port_snapshot: dict[int, list[dict]] = {}
_port_lock = threading.Lock()
_port_thread_started = False


def _scan_once() -> dict[int, list[dict]]:
    port_map: dict[int, list[dict]] = {}
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, psutil.Error):
        return port_map
    pid_to_ports: dict[int, set[int]] = {}
    for conn in conns:
        if not conn.laddr or not conn.pid:
            continue
        if conn.status not in (psutil.CONN_LISTEN, psutil.CONN_ESTABLISHED, "LISTEN"):
            continue
        pid_to_ports.setdefault(conn.pid, set()).add(conn.laddr.port)
    for pid, ports in pid_to_ports.items():
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            cmdline = " ".join(proc.cmdline()[:5])
        except psutil.Error:
            name, cmdline = "?", ""
        holder = {"pid": pid, "name": name, "cmdline": cmdline}
        for p in ports:
            port_map.setdefault(p, []).append(holder)
    return port_map


def _port_scanner_loop() -> None:
    global _port_snapshot
    while True:
        try:
            snap = _scan_once()
            with _port_lock:
                _port_snapshot = snap
        except Exception:
            pass
        time.sleep(_PORT_SNAPSHOT_INTERVAL)


def _ensure_port_scanner() -> None:
    global _port_thread_started
    if _port_thread_started:
        return
    _port_thread_started = True
    t = threading.Thread(target=_port_scanner_loop, daemon=True, name="port-scanner")
    t.start()


def find_port_holder(port: int) -> list[dict]:
    """返回占用指定端口的进程列表（从后台快照读取，不阻塞）"""
    _ensure_port_scanner()
    with _port_lock:
        return list(_port_snapshot.get(port, []))


def invalidate_port_cache() -> None:
    """kill 端口后主动触发一次刷新（后台线程下一周期刷新，这里只清空）"""
    global _port_snapshot
    with _port_lock:
        _port_snapshot = {}


def kill_pid(pid: int) -> bool:
    """一键杀掉指定 PID 及其子进程"""
    try:
        _kill_tree(pid, graceful_timeout=2.0)
        invalidate_port_cache()
        return True
    except psutil.Error:
        return False
