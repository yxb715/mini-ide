"""CLI 服务端：在已运行的 mini-ide 主进程中处理 CLI 命令请求。

接收 JSON 命令 → 路由到对应 ProjectTab → 返回 JSON 响应。
集成到 main.py 的 QLocalServer 连接处理流程中。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalSocket

if TYPE_CHECKING:
    from src.ui.main_window import MainWindow

from src.core import git_context
from src.util import app_log

log = app_log.get_logger("cli_server")


def _app_version() -> str:
    """读取打包元信息里的版本号，失败时返回空串。"""
    try:
        import tomllib
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return str(data.get("tool", {}).get("poetry", {}).get("version", ""))
    except Exception:
        return ""


def _project_snapshot(tab) -> dict:
    """返回项目级快照，供 status / preflight / diagnose 复用。"""
    states = tab.service_states(refresh_external=True)
    modules = [s.to_cli_module() for s in states if s.kind != "script"]
    scripts = [s.to_running_item() for s in states if s.kind == "script" and s.is_active]
    return {
        "name": tab.project_meta.name,
        "path": tab.project_meta.path,
        "type": tab.project_meta.project_type,
        "display_type": tab.project_meta.display_type,
        "package_manager": tab.project_meta.package_manager,
        "default_port": tab.project_meta.default_port,
        "health_check_path": tab.project_meta.health_check_path,
        "modules": modules,
        "scripts": scripts,
        "running": [s.to_running_item() for s in states if s.is_active],
    }


def _all_project_tabs(window: "MainWindow") -> list:
    from src.ui.project_tab import ProjectTab
    tabs = []
    for i in range(window.tabs.count()):
        w = window.tabs.widget(i)
        if isinstance(w, ProjectTab):
            tabs.append(w)
    return tabs


def _collect_running(window: "MainWindow") -> list[dict]:
    items: list[dict] = []
    for tab in _all_project_tabs(window):
        try:
            items.extend(tab.running_service_items(refresh_external=True))
        except Exception:
            log.exception("收集运行服务失败: %s", tab.project_meta.name)
    return items


def _workspace_payload(ws, active_name: str = "") -> dict:
    return {
        "name": ws.name,
        "paths": list(ws.paths),
        "project_count": len(ws.paths),
        "last_opened_at": ws.last_opened_at,
        "active": bool(active_name and ws.name == active_name),
    }


def _log_widget_for(tab, module: str | None):
    return tab.service_controller.log_widget(module)


def _iter_log_lines(log_widget, tail: int) -> list[str]:
    if log_widget is None:
        return []
    doc = log_widget.edit.document()
    total_blocks = doc.blockCount()
    start = max(0, total_blocks - max(0, tail))
    lines = []
    block = doc.findBlockByNumber(start)
    while block.isValid() and len(lines) < tail:
        text = block.text()
        if text:
            lines.append(text)
        block = block.next()
    return lines


def _validate_module(tab, module: str | None) -> dict | None:
    return tab.service_controller.validate_module(module)


def _looks_error_line(line: str) -> bool:
    low = line.lower()
    return any(x in low for x in (
        " error", "[error", "exception", "traceback", "failed", "failure",
        "caused by", "端口", "占用", "错误", "失败",
    ))


class _GitDiffWorker(QObject):
    done = Signal(dict)

    def __init__(self, root: str, mode: str, max_chars: int, parent=None):
        super().__init__(parent)
        self.root = root
        self.mode = mode
        self.max_chars = max_chars

    def run(self) -> None:
        try:
            summary = git_context.summary_payload(self.root, include_files=True)
            if not summary.get("ok"):
                self.done.emit(summary)
                return
            if self.mode != "full":
                summary["text"] = git_context.build_ai_text(summary)
                self.done.emit(summary)
                return
            repo = summary["repo"]
            diff, truncated = git_context.limited_diff_text(repo, max_chars=self.max_chars)
            summary["truncated"] = truncated
            summary["text"] = git_context.build_ai_text(summary, diff=diff)
            self.done.emit(summary)
        except Exception as e:
            log.exception("生成 CLI git diff 失败")
            self.done.emit({"ok": False, "error": str(e)})

    @Slot(dict)
    def dispose(self, _payload: dict) -> None:
        self.deleteLater()


class _GitDiffResponder(QObject):
    def __init__(self, sock: QLocalSocket, thread: QThread, worker: QObject, parent=None):
        super().__init__(parent)
        self.sock = sock
        self.thread = thread
        self.worker = worker

    @Slot(dict)
    def finish(self, payload: dict) -> None:
        if self.sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
            _send_response(self.sock, payload)
        self.thread.quit()

    @Slot()
    def cleanup(self) -> None:
        try:
            _active_workers.remove((self.thread, self.worker, self))
        except ValueError:
            pass
        self.thread.deleteLater()
        self.deleteLater()


_active_workers: list[tuple[QThread, QObject, QObject]] = []


def _run_git_diff_async(tab, mode: str, max_chars: int, sock: QLocalSocket) -> None:
    thread = QThread()
    worker = _GitDiffWorker(tab.project_meta.path, mode, max_chars)
    responder = _GitDiffResponder(sock, thread, worker)
    worker.moveToThread(thread)

    worker.done.connect(responder.finish)
    worker.done.connect(worker.dispose)
    thread.started.connect(worker.run)
    thread.finished.connect(responder.cleanup)
    _active_workers.append((thread, worker, responder))
    thread.start()


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

    if action == "status":
        return _cmd_status(window)

    if action == "list-projects":
        return _cmd_list_projects(window)

    if action == "open":
        return _cmd_open(window, cmd.get("path", ""))

    if action == "list-workspaces":
        return _cmd_list_workspaces(window)

    if action == "open-workspace":
        return _cmd_open_workspace(window, cmd.get("name", ""))

    if action == "close-workspace":
        return _cmd_close_workspace(window)

    if action == "preflight-build":
        return _cmd_preflight_build(window)

    if action == "quit":
        return _cmd_quit(window)

    # 以下命令都需要 project 参数
    project_key = cmd.get("project")
    if not project_key:
        return {"ok": False, "error": "missing 'project' argument"}

    tab = _find_project_tab(window, project_key)
    if tab is None:
        return {"ok": False, "error": f"project not found: {project_key}"}

    if action == "list-modules":
        return _cmd_list_modules(tab)
    elif action == "close":
        return _cmd_close_project(window, tab)
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
        return _cmd_log(
            tab,
            cmd.get("module"),
            cmd.get("tail", 50),
            errors=bool(cmd.get("errors")),
            all_modules=bool(cmd.get("all_modules")),
        )
    elif action == "diagnose":
        return _cmd_diagnose(tab, cmd.get("module"), cmd.get("tail", 120))
    elif action == "git-status":
        return _cmd_git_status(tab)
    elif action in ("git-diff", "git-ai-context"):
        return {"ok": False, "error": f"{action} command uses async handler"}
    else:
        return {"ok": False, "error": f"unknown command: {action}"}


def handle_async_cli_request(
    cmd: dict, sock: QLocalSocket, window: "MainWindow"
) -> bool:
    """处理需要异步等待的命令。返回 True 表示已接管。"""
    action = cmd.get("cmd")
    if action in ("start", "restart") and not cmd.get("wait"):
        return False
    if action not in (
        "health", "compile", "ensure-running", "start", "restart",
        "git-diff", "git-ai-context",
    ):
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
    elif action == "ensure-running":
        _cmd_ensure_running_async(tab, cmd.get("module"), cmd.get("timeout", 60), sock)
        return True
    elif action in ("start", "restart"):
        _cmd_start_or_restart_wait_async(
            tab, action, cmd.get("module"), cmd.get("timeout", 60), sock,
        )
        return True
    elif action in ("git-diff", "git-ai-context"):
        mode = cmd.get("mode", "summary")
        max_chars = int(cmd.get("max_chars", 30000))
        _run_git_diff_async(tab, mode, max_chars, sock)
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

def _stop_tab_all(tab) -> tuple[int, bool]:
    """停掉一个 ProjectTab 下所有运行中的服务，返回 (服务数, 是否全部停净)。

    覆盖三类进程，与关闭窗口时的清理保持一致：
      1. mini-ide 自己拉起的（单模块主 runner / 多模块 module_runners / 脚本 runner）
      2. 多模块里被外部（CLI/终端/IDE）启动、靠端口快照感知到的孤儿进程 → 按 PID 杀
    silent=True 全程不弹确认框（CLI 场景无人值守）。
    """
    from src.ui.project_tab import ProjectTab
    tab: ProjectTab
    count = tab.stop_all_services(include_external=True, silent=True)
    stopped = True
    if count:
        stopped = tab.wait_services_stopped(timeout_ms=15000)
    return count, stopped


def _cmd_status(window: "MainWindow") -> dict:
    from src.core.workspace_manager import ensure_workspace_candidates
    changed = ensure_workspace_candidates(window.config)
    if changed:
        window.config.save()
        try:
            window._rebuild_workspace_menu()
        except Exception:
            log.exception("刷新工作区菜单失败")
    current = window.tabs.currentWidget()
    current_project = None
    if current is not None and hasattr(current, "project_meta"):
        current_project = {
            "name": current.project_meta.name,
            "path": current.project_meta.path,
        }
    projects = [_project_snapshot(tab) for tab in _all_project_tabs(window)]
    running = []
    for p in projects:
        running.extend(p.get("running", []))
    return {
        "ok": True,
        "ide": "mini-ide",
        "version": _app_version(),
        "project_count": len(projects),
        "current_project": current_project,
        "active_workspace_name": window.config.active_workspace_name,
        "startup_restore_mode": window.config.startup_restore_mode,
        "projects": projects,
        "running": running,
        "workspaces": [
            _workspace_payload(ws, window.config.active_workspace_name)
            for ws in window.config.workspaces
        ],
    }


def _cmd_open(window: "MainWindow", path: str) -> dict:
    if not path:
        return {"ok": False, "error": "missing 'path' argument"}
    p = Path(path)
    if not p.is_dir():
        return {"ok": False, "error": f"path is not a directory: {path}"}
    tab = window.open_project(str(p), quiet=True)
    if tab is None:
        return {"ok": False, "error": f"open project failed: {path}"}
    return {"ok": True, "project": _project_snapshot(tab)}


def _cmd_close_project(window: "MainWindow", tab) -> dict:
    running_before = tab.running_service_items(refresh_external=True)
    name = tab.project_meta.name
    path = tab.project_meta.path
    ok = window.close_project_tab(tab, quiet=True)
    if not ok:
        return {
            "ok": False,
            "error": "close project failed",
            "project": {"name": name, "path": path},
            "running": running_before,
        }
    return {
        "ok": True,
        "project": {"name": name, "path": path},
        "stopped": len(running_before),
        "services": running_before,
    }


def _cmd_list_workspaces(window: "MainWindow") -> dict:
    from src.core.workspace_manager import ensure_workspace_candidates
    changed = ensure_workspace_candidates(window.config)
    if changed:
        window.config.save()
        try:
            window._rebuild_workspace_menu()
        except Exception:
            log.exception("刷新工作区菜单失败")
    return {
        "ok": True,
        "active_workspace_name": window.config.active_workspace_name,
        "startup_restore_mode": window.config.startup_restore_mode,
        "workspaces": [
            _workspace_payload(ws, window.config.active_workspace_name)
            for ws in window.config.workspaces
        ],
    }


def _find_workspace(window: "MainWindow", name: str):
    if not name:
        return None
    exact = window.config.find_workspace(name)
    if exact:
        return exact
    key = name.lower()
    matches = [ws for ws in window.config.workspaces if key in ws.name.lower()]
    return matches[0] if len(matches) == 1 else None


def _cmd_open_workspace(window: "MainWindow", name: str) -> dict:
    from src.core.workspace_manager import mark_workspace_opened
    ws = _find_workspace(window, name)
    if not ws:
        return {"ok": False, "error": f"workspace not found: {name}"}
    opened = []
    failed = []
    for path in ws.paths:
        if not Path(path).is_dir():
            failed.append({"path": path, "error": "path not found"})
            continue
        tab = window.open_project(path, quiet=True)
        if tab is None:
            failed.append({"path": path, "error": "open failed"})
            continue
        opened.append({"name": tab.project_meta.name, "path": tab.project_meta.path})
    mark_workspace_opened(window.config, ws)
    window.config.save()
    try:
        window._rebuild_workspace_menu()
    except Exception:
        log.exception("刷新工作区菜单失败")
    return {
        "ok": bool(opened) or not ws.paths,
        "workspace": _workspace_payload(ws, window.config.active_workspace_name),
        "opened": opened,
        "failed": failed,
    }


def _cmd_close_workspace(window: "MainWindow") -> dict:
    from src.core.workspace_manager import close_workspace_paths
    current_paths = window._current_project_paths()
    active = window.config.active_workspace_name
    ws = window.config.find_workspace(active) if active else None
    paths = set(ws.paths) if ws else set(current_paths)
    closed = []
    failed = []
    for i in range(window.tabs.count() - 1, -1, -1):
        tab = window.tabs.widget(i)
        if not hasattr(tab, "project_meta"):
            continue
        if tab.project_meta.path not in paths:
            continue
        running = tab.running_service_items(refresh_external=True)
        if window.close_project_tab(tab, quiet=True):
            closed.append({
                "name": tab.project_meta.name,
                "path": tab.project_meta.path,
                "stopped": len(running),
                "services": running,
            })
        else:
            failed.append({
                "name": tab.project_meta.name,
                "path": tab.project_meta.path,
                "running": running,
            })
    if failed:
        return {"ok": False, "error": "close workspace failed", "closed": closed, "failed": failed}
    close_workspace_paths(window.config, current_paths)
    window.config.save()
    try:
        window._rebuild_workspace_menu()
    except Exception:
        log.exception("刷新工作区菜单失败")
    return {"ok": True, "closed": closed, "active_workspace_name": window.config.active_workspace_name}


def _cmd_preflight_build(window: "MainWindow") -> dict:
    running = _collect_running(window)
    if running:
        return {
            "ok": False,
            "error": "running services must be stopped before build/quit",
            "can_build": False,
            "running": running,
        }
    return {"ok": True, "can_build": True, "running": []}


def _cmd_quit(window: "MainWindow") -> dict:
    """停掉所有项目的所有服务，持久化会话后退出 mini-ide。

    退出动作延迟到响应发回客户端之后执行（QTimer.singleShot），否则主进程
    先退导致 CLI 端收不到确认、报「empty response」。
    """
    from src.ui.project_tab import ProjectTab
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    stopped_total = 0
    projects = []
    timed_out = []
    for i in range(window.tabs.count()):
        w = window.tabs.widget(i)
        if not isinstance(w, ProjectTab):
            continue
        try:
            running_items = w.running_service_items(refresh_external=True)
            n, stopped = _stop_tab_all(w)
        except Exception:
            log.exception("停止项目服务失败: %s", w.project_meta.name)
            n = 0
            stopped = False
        if n:
            projects.append({
                "name": w.project_meta.name,
                "stopped": n,
                "services": running_items,
            })
            stopped_total += n
        if not stopped:
            timed_out.append({
                "name": w.project_meta.name,
                "services": running_items,
            })

    if timed_out:
        log.warning("CLI quit：停止超时，取消退出；projects=%s", timed_out)
        return {
            "ok": False,
            "error": "stop timeout",
            "stopped": stopped_total,
            "projects": projects,
            "timed_out": timed_out,
        }

    # 持久化窗口几何 + Tab 会话，下次启动能恢复（与 closeEvent 行为一致）
    try:
        window._persist_geometry()
        window._persist_tabs()
        window.config.save()
    except Exception:
        log.exception("退出前持久化失败")

    log.info("CLI quit：停止 %d 个服务，准备退出；projects=%s", stopped_total, projects)
    # 延迟退出，确保响应已写回客户端
    QTimer.singleShot(300, QApplication.quit)
    return {"ok": True, "stopped": stopped_total, "projects": projects}


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
    states = tab.service_controller.states(refresh_external=True)
    # 对 CLI 来说，脚本运行不是“模块”，避免 list-modules 混入右键脚本进程。
    modules = [s.to_cli_module() for s in states if s.kind != "script"]
    return modules


def _cmd_start(tab, module: str | None) -> dict:
    return tab.service_controller.start(module)


def _cmd_stop(tab, module: str | None) -> dict:
    result = tab.service_controller.stop(module)
    if result.get("ok") is False and result.get("error") not in ("not running",):
        log.warning("CLI stop failed: %s", result)
    return result


def _cmd_restart(tab, module: str | None) -> dict:
    return tab.service_controller.restart(module)


def _cmd_log(
    tab,
    module: str | None,
    tail: int,
    errors: bool = False,
    all_modules: bool = False,
) -> dict:
    """获取最近 N 行日志。支持错误过滤和多模块汇总。"""
    from src.ui.project_tab import ProjectTab
    tab: ProjectTab

    if all_modules and tab._is_multi_module:
        logs: dict[str, list[str]] = {}
        for mod_name, _path, _port, _cls in tab.project_meta.spring_boot_modules:
            lines = _iter_log_lines(tab.module_logs.get(mod_name), tail)
            if errors:
                lines = [line for line in lines if _looks_error_line(line)]
            logs[mod_name] = lines
        project_lines = _iter_log_lines(tab.log, tail)
        if errors:
            project_lines = [line for line in project_lines if _looks_error_line(line)]
        return {"ok": True, "logs": logs, "project_lines": project_lines}

    invalid = _validate_module(tab, module)
    if invalid:
        return invalid
    lines = _iter_log_lines(_log_widget_for(tab, module), tail)
    if errors:
        lines = [line for line in lines if _looks_error_line(line)]
    return {"ok": True, "lines": lines}


def _cmd_diagnose(tab, module: str | None, tail: int) -> dict:
    invalid = _validate_module(tab, module)
    if invalid:
        return invalid
    snapshot = _project_snapshot(tab)
    target_states = snapshot["modules"]
    if module:
        target_states = [s for s in target_states if s.get("name") == module]
    log_widget = _log_widget_for(tab, module)
    lines = _iter_log_lines(log_widget, tail)
    error_lines = [line for line in lines if _looks_error_line(line)]
    diagnosis = ""
    if log_widget is not None and hasattr(log_widget, "diagnosis_summary"):
        diagnosis = log_widget.diagnosis_summary(max_chars=4000)
    hints = []
    for state in target_states:
        if state.get("detail_state") == "running_external":
            hints.append("服务由 mini-ide 外部进程占用，当前 IDE 没有完整启动日志；建议停止后由 mini-ide 重新启动。")
        elif state.get("detail_state") == "stopped" and not lines:
            hints.append("当前没有运行进程，也没有本次日志上下文。")
        elif error_lines:
            hints.append("近期日志包含错误关键字，请优先查看 error_lines。")
    return {
        "ok": True,
        "project": {
            "name": tab.project_meta.name,
            "path": tab.project_meta.path,
            "type": tab.project_meta.project_type,
            "default_port": tab.project_meta.default_port,
        },
        "module": module,
        "states": target_states,
        "diagnosis": diagnosis,
        "error_lines": error_lines[-80:],
        "recent_lines": lines[-tail:],
        "hints": list(dict.fromkeys(hints)),
    }


def _cmd_git_status(tab) -> dict:
    return git_context.summary_payload(tab.project_meta.path, include_files=True)


def _get_health_markers(project_type: str) -> tuple[str, ...]:
    """兼容旧内部调用；标记的唯一来源在 launch_tracker。"""
    from src.core.launch_tracker import health_markers
    return health_markers(project_type)


def _cmd_health_async(
    tab,
    module: str | None,
    timeout: int,
    sock: QLocalSocket,
    generations: dict[str, int | None] | None = None,
) -> None:
    """异步等待模块启动完成，检测日志中的启动标记或端口监听。"""
    from src.ui.project_tab import ProjectTab
    tab: ProjectTab

    start_time = time.time()
    deadline = start_time + timeout

    if generations is None:
        generations = tab.service_controller.snapshot_launch_generations(module)

    def _generation(target: str) -> int | None:
        return generations.get(target) if generations else None

    def _terminal_error(detail: dict) -> bool:
        error = detail.get("error")
        if not error:
            return False
        payload = {"ok": False, "error": error}
        payload.update(detail)
        _send_response(sock, payload)
        timer.stop()
        timer.deleteLater()
        return True

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

        if tab._is_multi_module:
            if module:
                ok, detail = tab.service_controller.module_health_ok(
                    module, _generation(module),
                )
                if ok:
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    payload = {"ok": True, "elapsed_ms": elapsed_ms}
                    payload.update(detail)
                    _send_response(sock, payload)
                    timer.stop()
                    timer.deleteLater()
                    return
                if _terminal_error(detail):
                    return
            else:
                details = []
                all_ok = True
                for mod_name, _path, _port, _cls in tab.project_meta.spring_boot_modules:
                    ok, detail = tab.service_controller.module_health_ok(
                        mod_name, _generation(mod_name),
                    )
                    details.append(detail)
                    all_ok = all_ok and ok
                    if not ok and detail.get("error"):
                        _send_response(sock, {
                            "ok": False,
                            "error": detail["error"],
                            "module": mod_name,
                            "modules": details,
                        })
                        timer.stop()
                        timer.deleteLater()
                        return
                if details and all_ok:
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    _send_response(sock, {
                        "ok": True,
                        "elapsed_ms": elapsed_ms,
                        "modules": details,
                    })
                    timer.stop()
                    timer.deleteLater()
                    return
            return

        target = tab.project_meta.name
        ok, detail = tab.service_controller.single_project_health_ok(
            _generation(target),
        )
        if ok:
            elapsed_ms = int((time.time() - start_time) * 1000)
            payload = {"ok": True, "elapsed_ms": elapsed_ms}
            payload.update(detail)
            _send_response(sock, payload)
            timer.stop()
            timer.deleteLater()
            return
        if _terminal_error(detail):
            return

    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(_check)
    timer.start()
    # 立即检查一次
    _check()


def _active_modules(tab, module: str | None) -> list[str]:
    return tab.service_controller.active_modules(module)


def _target_modules(tab, module: str | None) -> list[str]:
    return tab.service_controller.target_modules(module)


def _cmd_ensure_running_async(
    tab,
    module: str | None,
    timeout: int,
    sock: QLocalSocket,
) -> None:
    invalid = _validate_module(tab, module)
    if invalid:
        _send_response(sock, invalid)
        return
    targets = _target_modules(tab, module)
    active = set(_active_modules(tab, module))
    already = all(name in active for name in targets)
    if not already:
        start_result = _cmd_start(tab, module)
        if start_result.get("ok") is False and start_result.get("error") != "already running":
            _send_response(sock, start_result)
            return
    generations = tab.service_controller.snapshot_launch_generations(module)
    _cmd_health_async(tab, module, timeout, sock, generations)


def _cmd_start_or_restart_wait_async(
    tab,
    action: str,
    module: str | None,
    timeout: int,
    sock: QLocalSocket,
) -> None:
    invalid = _validate_module(tab, module)
    if invalid:
        _send_response(sock, invalid)
        return
    if action == "restart":
        result = _cmd_restart(tab, module)
    else:
        result = _cmd_start(tab, module)
    if result.get("ok") is False and result.get("error") != "already running":
        _send_response(sock, result)
        return
    generations = tab.service_controller.snapshot_launch_generations(module)
    _cmd_health_async(tab, module, timeout, sock, generations)


def _cmd_compile_async(tab, sock: QLocalSocket, timeout: int = 300) -> None:
    """触发编译并等待完成。"""
    from src.ui.project_tab import ProjectTab
    tab: ProjectTab

    prepared = tab.service_controller.prepare_compile()
    if not prepared.get("ok"):
        _send_response(sock, prepared)
        return
    runner = prepared["runner"]

    # finished/output 信号必须在 runner.start 之前连接。极快命令可能在 start 返回前
    # 就结束，先启动再 connect 会永远收不到 finished，只能错误地等到超时。
    state = {"done": False}
    output_lines: list[str] = []

    def _collect_output() -> str:
        return "\n".join(output_lines[-200:])

    def _on_output(_stream: str, line: str) -> None:
        if line:
            output_lines.append(line)
            if len(output_lines) > 200:
                del output_lines[:-200]

    def _finish(payload: dict) -> None:
        if state["done"]:
            return
        state["done"] = True
        deadline_timer.stop()
        deadline_timer.deleteLater()
        try:
            runner.finished.disconnect(_on_finished)
        except (RuntimeError, TypeError):
            pass
        try:
            runner.outputLine.disconnect(_on_output)
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

    deadline_timer = QTimer()
    deadline_timer.setSingleShot(True)
    deadline_timer.setInterval(max(1, timeout) * 1000)
    deadline_timer.timeout.connect(_on_deadline)
    deadline_timer.start()

    runner.outputLine.connect(_on_output)
    runner.finished.connect(_on_finished)
    try:
        started = tab.service_controller.start_prepared_compile(prepared)
    except Exception as exc:
        tab.service_controller.abort_prepared_compile()
        log.exception("CLI compile start failed")
        _finish({"ok": False, "error": f"failed to start compile: {exc}"})
        return
    if not started.get("ok"):
        _finish(started)
