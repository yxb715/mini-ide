"""CLI 客户端：连接已运行的 mini-ide 实例，发送 JSON 命令，等待响应后退出。

CLI 模式只用 QCoreApplication + QLocalSocket（不依赖 QtWidgets）。
"""
from __future__ import annotations

import json
import os
import sys
import time

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtNetwork import QLocalSocket


def _safe_write(stream, text: str) -> None:
    """安全写入——GUI exe 无控制台时 stream 可能无效。"""
    try:
        if stream and hasattr(stream, "write"):
            stream.write(text)
            stream.flush()
    except OSError:
        pass


def _attach_console():
    """GUI exe（runw 引导器）没有控制台，CLI 模式需要附加到父进程的控制台。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # ATTACH_PARENT_PROCESS = -1
        if kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8")
            sys.stderr = open("CONOUT$", "w", encoding="utf-8")
    except Exception:
        pass


SERVER_NAME = "mini-ide-single-instance"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BAD_ARGS = 2
EXIT_NOT_RUNNING = 3
EXIT_TIMEOUT = 4


def _parse_args(argv: list[str]) -> dict | None:
    """解析 CLI 参数为 JSON 命令 dict。返回 None 表示参数错误。"""
    args = argv[1:]  # 跳过程序名
    if not args:
        return None

    cmd_name = args[0].lstrip("-")  # --list-projects → list-projects
    cmd_map = {
        "list-projects": "list-projects",
        "list-modules": "list-modules",
        "start": "start",
        "stop": "stop",
        "restart": "restart",
        "health": "health",
        "compile": "compile",
        "log": "log",
    }
    if cmd_name not in cmd_map:
        return None

    result: dict = {"cmd": cmd_map[cmd_name]}
    rest = args[1:]

    if cmd_name == "list-projects":
        return result

    if cmd_name == "list-modules":
        if not rest:
            return None
        result["project"] = rest[0]
        return result

    if cmd_name in ("start", "stop", "restart"):
        if not rest:
            return None
        result["project"] = rest[0]
        if len(rest) > 1 and not rest[1].startswith("--"):
            result["module"] = rest[1]
        return result

    if cmd_name == "health":
        if not rest:
            return None
        result["project"] = rest[0]
        i = 1
        if i < len(rest) and not rest[i].startswith("--"):
            result["module"] = rest[i]
            i += 1
        while i < len(rest):
            if rest[i] == "--timeout" and i + 1 < len(rest):
                try:
                    result["timeout"] = int(rest[i + 1])
                except ValueError:
                    return None
                i += 2
            else:
                i += 1
        if "timeout" not in result:
            result["timeout"] = 60
        return result

    if cmd_name == "compile":
        if not rest:
            return None
        result["project"] = rest[0]
        i = 1
        while i < len(rest):
            if rest[i] == "--timeout" and i + 1 < len(rest):
                try:
                    result["timeout"] = int(rest[i + 1])
                except ValueError:
                    return None
                i += 2
            else:
                i += 1
        if "timeout" not in result:
            # 冷编译 Gradle/Maven 动辄数十秒到几分钟，给宽松默认值
            result["timeout"] = 300
        return result

    if cmd_name == "log":
        if not rest:
            return None
        result["project"] = rest[0]
        i = 1
        if i < len(rest) and not rest[i].startswith("--"):
            result["module"] = rest[i]
            i += 1
        while i < len(rest):
            if rest[i] == "--tail" and i + 1 < len(rest):
                try:
                    result["tail"] = int(rest[i + 1])
                except ValueError:
                    return None
                i += 2
            else:
                i += 1
        if "tail" not in result:
            result["tail"] = 50
        return result

    return None


def run_cli(argv: list[str]) -> int:
    """CLI 模式主入口。返回退出码。"""
    _attach_console()

    cmd = _parse_args(argv)
    if cmd is None:
        _safe_write(sys.stderr, json.dumps({"ok": False, "error": "invalid arguments"}) + "\n")
        _print_usage()
        return EXIT_BAD_ARGS

    app = QCoreApplication(argv)
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(2000):
        _safe_write(sys.stderr, json.dumps({"ok": False, "error": "mini-ide is not running"}) + "\n")
        return EXIT_NOT_RUNNING

    payload = json.dumps(cmd, ensure_ascii=False) + "\n"
    sock.write(payload.encode("utf-8"))
    sock.flush()

    # 等待响应（--health / --compile 可能等很久，按命令携带的 timeout 放宽）
    if cmd["cmd"] in ("health", "compile"):
        timeout_ms = (cmd.get("timeout", 60) + 5) * 1000
    else:
        timeout_ms = 30000
    response_buf = b""
    deadline = time.time() + timeout_ms / 1000.0

    while True:
        remaining = int((deadline - time.time()) * 1000)
        if remaining <= 0:
            _safe_write(sys.stderr, json.dumps({"ok": False, "error": "timeout waiting for response"}) + "\n")
            sock.disconnectFromServer()
            return EXIT_TIMEOUT
        if sock.waitForReadyRead(min(remaining, 500)):
            response_buf += bytes(sock.readAll())
            if b"\n" in response_buf:
                break
        if sock.state() != QLocalSocket.LocalSocketState.ConnectedState:
            break

    sock.disconnectFromServer()

    if not response_buf.strip():
        _safe_write(sys.stderr, json.dumps({"ok": False, "error": "empty response"}) + "\n")
        return EXIT_FAIL

    line = response_buf.split(b"\n", 1)[0]
    try:
        result = json.loads(line)
    except json.JSONDecodeError:
        _safe_write(sys.stderr, json.dumps({"ok": False, "error": "invalid response"}) + "\n")
        return EXIT_FAIL

    _safe_write(sys.stdout, json.dumps(result, ensure_ascii=False) + "\n")

    if isinstance(result, dict) and result.get("ok") is False:
        error = result.get("error", "")
        if "timeout" in error:
            return EXIT_TIMEOUT
        if "not found" in error:
            return EXIT_BAD_ARGS
        return EXIT_FAIL
    return EXIT_OK


def _print_usage():
    _safe_write(
        sys.stderr,
        "Usage: mini-ide <command> [args...]\n"
        "Commands:\n"
        "  --list-projects              List all open projects\n"
        "  --list-modules <project>     List modules of a project\n"
        "  --start <project> [module]   Start a module\n"
        "  --stop <project> [module]    Stop a module\n"
        "  --restart <project> [module] Restart a module\n"
        "  --health <project> [module] [--timeout N]  Wait for startup\n"
        "  --compile <project>          Trigger compilation\n"
        "  --log <project> [module] [--tail N]  Get recent log lines\n"
    )
