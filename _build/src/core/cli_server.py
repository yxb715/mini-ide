"""CLI 服务端：在已运行的 mini-ide 主进程中处理 CLI 命令请求。

接收 JSON 命令 → 路由到对应 ProjectTab → 返回 JSON 响应。
集成到 main.py 的 QLocalServer 连接处理流程中。
"""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalSocket

if TYPE_CHECKING:
    from src.ui.main_window import MainWindow

from src.util import app_log

log = app_log.get_logger("cli_server")


def handle_cli_request(data: str, sock: QLocalSocket, window: "MainWindow") -> bool:
    """尝试处理 CLI JSON 请求。如果 data 不是 JSON 命令则返回 False（走老协议）。"""
    stripped = data.strip()
    if not stripped.startswith("{"):
        return False

    try:
        cmd = json.loads(stripped)
    except json.JSONDecodeError:
        return False

    if "cmd" not in cmd:
        return False

    log.info("收到 CLI 命令: %s", cmd)

    try:
        result = _dispatch(cmd, window)
    except Exception as e:
        log.exception("CLI 命令执行异常")
        result = {"ok": False, "error": str(e)}

    _send_response(sock, result)
    return True


def _send_response(sock: QLocalSocket, result: dict) -> None:
    """发送 JSON 响应并关闭连接。"""
    payload = json.dumps(result, ensure_ascii=False) + "\n"
    sock.write(payload.encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(3000)
    sock.disconnectFromServer()


def _dispatch(cmd: dict, window: "MainWindow") -> dict:
    """根据 cmd 类型路由到对应处理函数。"""
    action = cmd["cmd"]

    if action == "list-projects":
        return _cmd_list_projects(window)

    # 以下命令都需要 project 参数
    project_key = cmd.get("project")
    if not project_key:
        return {"ok": False, "error": "missing 'project' argument"}

    tab = _find_project_tab(window, project_key)
    if tab is None:
        return {"ok": False, "error": f"project not found: {project_key}"}

    if action == "list-modules":
        return _cmd_list_modules(tab)
    elif action == "start":
        return _cmd_start(tab, cmd.get("module"))
    elif action == "stop":
        return _cmd_stop(tab, cmd.get("module"))
    elif action == "restart":
        return _cmd_restart(tab, cmd.get("module"))
    elif action == "health":
        return {"ok": False, "error": "health command uses async handler"}
    elif action == "compile":
        return {"ok": False, "error": "compile command uses async handler"}
    elif action == "log":
        return _cmd_log(tab, cmd.get("module"), cmd.get("tail", 50))
    else:
        return {"ok": False, "error": f"unknown command: {action}"}


def handle_async_cli_request(
    cmd: dict, sock: QLocalSocket, window: "MainWindow"
) -> bool:
    """处理需要异步等待的命令（health / compile）。返回 True 表示已接管。"""
    action = cmd.get("cmd")
    if action not in ("health", "compile"):
        return False

    project_key = cmd.get("project")
    if not project_key:
        _send_response(sock, {"ok": False, "error": "missing 'project' argument"})
        return True

    tab = _find_project_tab(window, project_key)
    if tab is None:
        _send_response(sock, {"ok": False, "error": f"project not found: {project_key}"})
        return True

    if action == "health":
        _cmd_health_async(tab, cmd.get("module"), cmd.get("timeout", 60), sock)
        return True
    elif action == "compile":
        _cmd_compile_async(tab, sock, cmd.get("timeout", 300))
        return True
    return False


# ---- 项目查找 ----

def _find_project_tab(window: "MainWindow", key: str):
    """按目录名模糊匹配（包含即可，不区分大小写）。"""
    from src.ui.project_tab import ProjectTab
    key_lower = key.lower()
    for i in range(window.tabs.count()):
        w = window.tabs.widget(i)
        if not isinstance(w, ProjectTab):
            continue
        dir_name = w.project_meta.name.lower()
        full_path = w.project_meta.path.lower().replace("/", "\\")
        if key_lower in dir_name or key_lower in full_path:
            return w
    return None


# ---- 命令实现 ----

def _cmd_list_projects(window: "MainWindow") -> list:
    from src.ui.project_tab import ProjectTab
    projects = []
    for i in range(window.tabs.count()):
        w = window.tabs.widget(i)
        if not isinstance(w, ProjectTab):
            continue
        modules = [m[0] for m in w.project_meta.spring_boot_modules]
        projects.append({
            "name": w.project_meta.name,
            "path": w.project_meta.path,
            "type": w.project_meta.project_type,
            "modules": modules,
        })
    return projects


def _cmd_list_modules(tab) -> list:
    from src.ui.project_tab import ProjectTab
    tab: ProjectTab
    modules = []
    external = getattr(tab, "_module_external_pids", {})
    for mod_name, _path, _port, _cls in tab.project_meta.spring_boot_modules:
        runner = tab.module_runners.get(mod_name)
        state = runner.state() if runner else "stopped"
        pid = None
        running = False
        if runner and runner.is_running() and runner._proc:
            pid = runner._proc.processId()
            running = True
        elif mod_name in external:
            # 外部启动（AI / 终端 / IDE）的进程，按命令行感知到
            pid = external[mod_name]
            running = True
        modules.append({
            "name": mod_name,
            "state": "running" if running else "stopped",
            "external": mod_name in external and not (runner and runner.is_running()),
            "pid": pid if pid and pid > 0 else None,
        })
    # 单模块项目没有 spring_boot_modules，返回主 runner 状态
    if not modules:
        state = tab.runner.state()
        pid = None
        if tab.runner.is_running() and tab.runner._proc:
            pid = tab.runner._proc.processId()
        modules.append({
            "name": tab.project_meta.name,
            "state": "running" if state == "running" else "stopped",
            "pid": pid if pid and pid > 0 else None,
        })
    return modules


def _cmd_start(tab, module: str | None) -> dict:
    from src.ui.project_tab import ProjectTab
    tab: ProjectTab

    if tab._is_multi_module:
        if module:
            # 检查模块是否存在
            mod_names = [m[0] for m in tab.project_meta.spring_boot_modules]
            if module not in mod_names:
                return {"ok": False, "error": f"module not found: {module}"}
            runner = tab.module_runners.get(module)
            if runner and runner.is_running():
                return {"ok": False, "error": "already running"}
            if module in getattr(tab, "_module_external_pids", {}):
                return {"ok": False, "error": "already running (external process)"}
            tab._start_module(module)
            return {"ok": True}
        else:
            # 启动所有模块
            tab._start_all_modules()
            return {"ok": True}
    else:
        # 单模块
        if tab.runner.is_running():
            return {"ok": False, "error": "already running"}
        primary = next((p for p in tab.project_meta.profiles if p.primary), None)
        if not primary:
            return {"ok": False, "error": "no run profile found"}
        tab._start_profile(primary)
        return {"ok": True}


def _cmd_stop(tab, module: str | None) -> dict:
    from src.ui.project_tab import ProjectTab
    tab: ProjectTab

    if tab._is_multi_module:
        if module:
            mod_names = [m[0] for m in tab.project_meta.spring_boot_modules]
            if module not in mod_names:
                return {"ok": False, "error": f"module not found: {module}"}
            runner = tab.module_runners.get(module)
            if not runner or not runner.is_running():
                return {"ok": False, "error": "not running"}
            tab._stop_module(module)
            return {"ok": True}
        else:
            tab._stop_all_modules()
            return {"ok": True}
    else:
        if not tab.runner.is_running():
            return {"ok": False, "error": "not running"}
        tab._stop()
        return {"ok": True}


def _cmd_restart(tab, module: str | None) -> dict:
    """同步 restart：stop → 等进程退出（最多 10s）→ start。"""
    from src.ui.project_tab import ProjectTab
    tab: ProjectTab

    if tab._is_multi_module:
        if module:
            mod_names = [m[0] for m in tab.project_meta.spring_boot_modules]
            if module not in mod_names:
                return {"ok": False, "error": f"module not found: {module}"}
            runner = tab.module_runners.get(module)
            if runner and runner.is_running():
                runner.stop()
                # 等进程退出
                if not _wait_runner_stop(runner, 10000):
                    return {"ok": False, "error": "stop timeout"}
            tab._start_module(module)
            return {"ok": True}
        else:
            # 全部重启
            for mod_name, r in list(tab.module_runners.items()):
                if r.is_running():
                    r.stop()
            # 等所有停止
            deadline = time.time() + 10
            for mod_name, r in list(tab.module_runners.items()):
                remaining = max(0, int((deadline - time.time()) * 1000))
                _wait_runner_stop(r, remaining)
            tab._start_all_modules()
            return {"ok": True}
    else:
        if tab.runner.is_running():
            tab.runner.stop()
            if not _wait_runner_stop(tab.runner, 10000):
                return {"ok": False, "error": "stop timeout"}
        primary = next((p for p in tab.project_meta.profiles if p.primary), None)
        if not primary:
            return {"ok": False, "error": "no run profile found"}
        tab._start_profile(primary)
        return {"ok": True}


def _wait_runner_stop(runner, timeout_ms: int) -> bool:
    """阻塞等待 runner 停止。在 Qt 事件循环中用 processEvents 轮询。"""
    from PySide6.QtCore import QCoreApplication, QEventLoop
    deadline = time.time() + timeout_ms / 1000.0
    while runner.is_running() and time.time() < deadline:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
    return not runner.is_running()


def _cmd_log(tab, module: str | None, tail: int) -> dict:
    """获取最近 N 行日志。"""
    from src.ui.project_tab import ProjectTab
    tab: ProjectTab

    log_widget = None
    if tab._is_multi_module and module:
        mod_names = [m[0] for m in tab.project_meta.spring_boot_modules]
        if module not in mod_names:
            return {"ok": False, "error": f"module not found: {module}"}
        log_widget = tab.module_logs.get(module)
    else:
        log_widget = tab.log

    if log_widget is None:
        return {"lines": []}

    doc = log_widget.edit.document()
    total_blocks = doc.blockCount()
    start = max(0, total_blocks - tail)
    lines = []
    block = doc.findBlockByNumber(start)
    while block.isValid() and len(lines) < tail:
        text = block.text()
        if text:
            lines.append(text)
        block = block.next()
    return {"lines": lines}


def _get_health_markers(project_type: str) -> tuple[str, ...]:
    """按项目类型返回就绪检测关键字。"""
    if project_type.startswith("spring-boot") or project_type in ("gradle-java", "maven-java"):
        return ("Started ", "Netty started on port", "Tomcat started on port",
                "Undertow started on port", "Jetty started on port")
    if project_type in ("vue", "react", "next", "nuxt", "svelte", "node"):
        return ("Compiled successfully", "compiled successfully", "ready in ",
                "running here", "Local:", "VITE", "webpack compiled")
    if project_type in ("fastapi", "django", "flask", "python", "python-poetry"):
        return ("Uvicorn running", "Application startup complete",
                "Starting development server", "Running on http")
    # 通用兜底
    return ("Started ", "ready in ", "compiled successfully",
            "Compiled successfully", "running here", "Local:",
            "Uvicorn running", "Application startup complete")


def _cmd_health_async(tab, module: str | None, timeout: int, sock: QLocalSocket) -> None:
    """异步等待模块启动完成，检测日志中的启动标记或端口监听。"""
    from src.ui.project_tab import ProjectTab
    from src.core.process_runner import find_port_holder
    tab: ProjectTab

    start_time = time.time()
    deadline = start_time + timeout

    markers = _get_health_markers(tab.project_meta.project_type)

    def _check():
        now = time.time()
        # 客户端已断开就别再空转 / 往死 socket 写
        if sock.state() != QLocalSocket.LocalSocketState.ConnectedState:
            timer.stop()
            timer.deleteLater()
            return
        if now >= deadline:
            _send_response(sock, {"ok": False, "error": "timeout"})
            timer.stop()
            timer.deleteLater()
            return

        # 检查日志中是否有启动标记
        log_widget = None
        if tab._is_multi_module and module:
            log_widget = tab.module_logs.get(module)
        else:
            log_widget = tab.log

        if log_widget:
            doc = log_widget.edit.document()
            # 只检查最近 50 行
            total = doc.blockCount()
            start = max(0, total - 50)
            block = doc.findBlockByNumber(start)
            while block.isValid():
                text = block.text()
                for m in markers:
                    if m in text:
                        elapsed_ms = int((time.time() - start_time) * 1000)
                        _send_response(sock, {"ok": True, "elapsed_ms": elapsed_ms})
                        timer.stop()
                        timer.deleteLater()
                        return
                block = block.next()

        # 检查端口监听（多模块）
        if tab._is_multi_module and module:
            if module in tab._module_ports:
                elapsed_ms = int((time.time() - start_time) * 1000)
                _send_response(sock, {"ok": True, "elapsed_ms": elapsed_ms})
                timer.stop()
                timer.deleteLater()
                return
            # 外部启动的进程：靠命令行匹配感知到也算健康
            try:
                external = tab._detect_external_modules()
            except Exception:
                external = {}
            if module in external:
                elapsed_ms = int((time.time() - start_time) * 1000)
                _send_response(sock, {"ok": True, "elapsed_ms": elapsed_ms, "external": True})
                timer.stop()
                timer.deleteLater()
                return
        elif not tab._is_multi_module:
            # 单模块：查端口快照（后台扫描器维护的全局快照，不阻塞）
            default_port = tab.project_meta.default_port
            if default_port and find_port_holder(default_port):
                elapsed_ms = int((time.time() - start_time) * 1000)
                _send_response(sock, {"ok": True, "elapsed_ms": elapsed_ms})
                timer.stop()
                timer.deleteLater()
                return

    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(_check)
    timer.start()
    # 立即检查一次
    _check()


def _cmd_compile_async(tab, sock: QLocalSocket, timeout: int = 300) -> None:
    """触发编译并等待完成。"""
    from src.ui.project_tab import ProjectTab
    tab: ProjectTab

    compile_prof = tab._find_compile_profile()
    if not compile_prof:
        _send_response(sock, {"ok": False, "error": "no compile profile found"})
        return

    if tab.runner.is_running():
        _send_response(sock, {"ok": False, "error": "another task is running"})
        return

    # 记录编译开始位置以便收集输出
    doc = tab.log.edit.document()
    start_block = doc.blockCount()

    tab._start_profile(compile_prof)

    # 状态用 list 装，方便闭包改写；done 防止 finished 与超时重复响应
    state = {"done": False}

    def _collect_output() -> str:
        end_block = doc.blockCount()
        output_lines = []
        block = doc.findBlockByNumber(start_block)
        count = 0
        while block.isValid() and count < (end_block - start_block):
            text = block.text()
            if text:
                output_lines.append(text)
            block = block.next()
            count += 1
        return "\n".join(output_lines[-200:])  # 最多 200 行

    def _finish(payload: dict) -> None:
        if state["done"]:
            return
        state["done"] = True
        deadline_timer.stop()
        deadline_timer.deleteLater()
        try:
            tab.runner.finished.disconnect(_on_finished)
        except (RuntimeError, TypeError):
            pass
        if sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
            _send_response(sock, payload)

    def _on_finished(exit_code: int):
        output = _collect_output()
        ok = exit_code == 0
        _finish({"ok": ok, "exit_code": exit_code, "output": output})

    def _on_deadline():
        # 超时：编译还在跑，返回超时（不强停编译，让它在 GUI 里继续）
        _finish({"ok": False, "error": "compile timeout", "output": _collect_output()})

    tab.runner.finished.connect(_on_finished)

    deadline_timer = QTimer()
    deadline_timer.setSingleShot(True)
    deadline_timer.setInterval(max(1, timeout) * 1000)
    deadline_timer.timeout.connect(_on_deadline)
    deadline_timer.start()
