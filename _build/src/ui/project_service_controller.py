"""项目服务生命周期控制器。

ProjectTab 负责界面和信号连接；这里集中承接启动、停止、重启、健康探测、
编译启动等服务生命周期动作，避免 CLI 直接触碰 ProjectTab 私有状态。
"""
from __future__ import annotations

import time

from PySide6.QtCore import QCoreApplication, QEventLoop


class ProjectServiceController:
    def __init__(self, tab):
        self.tab = tab

    def validate_module(self, module: str | None) -> dict | None:
        if module and self.tab._is_multi_module:
            names = self.module_names()
            if module not in names:
                return {"ok": False, "error": f"module not found: {module}"}
        return None

    def module_names(self) -> list[str]:
        return [m[0] for m in self.tab.project_meta.spring_boot_modules]

    def log_widget(self, module: str | None):
        if self.tab._is_multi_module and module:
            return self.tab.module_logs.get(module)
        return self.tab.log

    def states(self, refresh_external: bool = True):
        return self.tab.service_states(refresh_external=refresh_external)

    def running_items(self, refresh_external: bool = True) -> list[dict]:
        return self.tab.running_service_items(refresh_external=refresh_external)

    def stop_all(self, include_external: bool = True, silent: bool = True) -> int:
        return self.tab.stop_all_services(include_external=include_external, silent=silent)

    def wait_stopped(self, timeout_ms: int = 12000) -> bool:
        return self.tab.wait_services_stopped(timeout_ms=timeout_ms)

    def start(self, module: str | None = None) -> dict:
        tab = self.tab
        if tab.project_meta.project_type == "nginx":
            if module and module != tab.project_meta.name:
                return {"ok": False, "error": f"module not found: {module}"}
            if tab._detect_nginx_status().running:
                return {"ok": False, "error": "already running"}
            return {"ok": bool(tab._start_nginx())}

        if tab._is_multi_module:
            if module:
                invalid = self.validate_module(module)
                if invalid:
                    return invalid
                runner = tab.module_runners.get(module)
                if runner and runner.is_running():
                    return {"ok": False, "error": "already running"}
                tab._detect_and_apply_external()
                tab._start_module(module, silent=True)
                return {"ok": True}
            tab._start_all_modules(silent=True)
            return {"ok": True}

        if tab.runner.is_running():
            return {"ok": False, "error": "already running"}
        primary = next((p for p in tab.project_meta.profiles if p.primary), None)
        if not primary:
            return {"ok": False, "error": "no run profile found"}
        tab._start_profile(primary)
        return {"ok": True}

    def stop(self, module: str | None = None) -> dict:
        tab = self.tab
        if tab.project_meta.project_type == "nginx":
            if module and module != tab.project_meta.name:
                return {"ok": False, "error": f"module not found: {module}"}
            if not tab._detect_nginx_status().running:
                return {"ok": False, "error": "not running"}
            return {"ok": bool(tab._stop_nginx_external())}

        if tab._is_multi_module:
            try:
                tab._detect_and_apply_external()
            except Exception:
                # 调用方会记录异常日志；这里保持命令可继续执行。
                pass
            if module:
                invalid = self.validate_module(module)
                if invalid:
                    return invalid
                runner = tab.module_runners.get(module)
                running = runner and runner.is_running()
                external = module in tab._module_external_pids
                if not running and not external:
                    return {"ok": False, "error": "not running"}
                port = self.module_port(module)
                if running:
                    runner.stop()
                    if not self.wait_module_stopped(module, 10000, port):
                        return {"ok": False, "error": "stop timeout"}
                if module in tab._module_external_pids:
                    if not tab._takeover_external(module):
                        return {"ok": False, "error": "stop timeout"}
                    if not self.wait_module_stopped(module, 8000, port):
                        return {"ok": False, "error": "stop timeout"}
                tab._refresh_status_row()
                return {"ok": True}

            ports = {
                mod_name: self.module_port(mod_name)
                for mod_name, _path, _port, _cls in tab.project_meta.spring_boot_modules
            }
            if tab.runner.is_running():
                tab._stop()
                if not self.wait_runner_stop(tab.runner, 10000):
                    return {"ok": False, "error": "stop timeout"}
            tab._stop_all_modules(silent=True)
            for runner in list(tab.module_runners.values()):
                if runner.is_running():
                    if not self.wait_runner_stop(runner, 10000):
                        return {"ok": False, "error": "stop timeout"}
            try:
                tab._detect_and_apply_external()
                for mod_name in list(tab._module_external_pids):
                    if not tab._takeover_external(mod_name):
                        return {"ok": False, "error": "stop timeout"}
            except Exception:
                pass
            for mod_name, port in ports.items():
                if not self.wait_module_stopped(mod_name, 8000, port):
                    return {"ok": False, "error": "stop timeout"}
            tab._refresh_status_row()
            return {"ok": True}

        if not tab.runner.is_running():
            return {"ok": False, "error": "not running"}
        port = tab.project_meta.default_port
        tab._stop()
        if not self.wait_project_stopped(10000, port):
            return {"ok": False, "error": "stop timeout"}
        return {"ok": True}

    def restart(self, module: str | None = None) -> dict:
        tab = self.tab
        if tab.project_meta.project_type == "nginx":
            if module and module != tab.project_meta.name:
                return {"ok": False, "error": f"module not found: {module}"}
            if tab._detect_nginx_status().running:
                tab._stop_nginx_external()
            return {"ok": bool(tab._start_nginx())}

        if tab._is_multi_module:
            if module:
                invalid = self.validate_module(module)
                if invalid:
                    return invalid
                runner = tab.module_runners.get(module)
                port = self.module_port(module)
                if runner and runner.is_running():
                    runner.stop()
                    if not self.wait_module_stopped(module, 15000, port):
                        return {"ok": False, "error": "stop timeout"}
                tab._detect_and_apply_external()
                if module in tab._module_external_pids:
                    if not tab._takeover_external(module):
                        return {"ok": False, "error": "stop timeout"}
                    if not self.wait_module_stopped(module, 8000, port):
                        return {"ok": False, "error": "stop timeout"}
                tab._start_module(module, silent=True)
                return {"ok": True}

            ports = {
                mod_name: self.module_port(mod_name)
                for mod_name, _path, _port, _cls in tab.project_meta.spring_boot_modules
            }
            for runner in list(tab.module_runners.values()):
                if runner.is_running():
                    runner.stop()
            deadline = time.time() + 15
            for runner in list(tab.module_runners.values()):
                remaining = max(0, int((deadline - time.time()) * 1000))
                if remaining <= 0 or not self.wait_runner_stop(runner, remaining):
                    return {"ok": False, "error": "stop timeout"}
            try:
                tab._detect_and_apply_external()
                for mod_name in list(tab._module_external_pids):
                    if not tab._takeover_external(mod_name):
                        return {"ok": False, "error": "stop timeout"}
            except Exception:
                pass
            for mod_name, port in ports.items():
                remaining = max(0, int((deadline - time.time()) * 1000))
                if remaining <= 0 or not self.wait_module_stopped(mod_name, remaining, port):
                    return {"ok": False, "error": "stop timeout"}
            tab._start_all_modules(silent=True)
            return {"ok": True}

        port = tab.project_meta.default_port
        if tab.runner.is_running():
            tab.runner.stop()
            if not self.wait_project_stopped(15000, port):
                return {"ok": False, "error": "stop timeout"}
        primary = next((p for p in tab.project_meta.profiles if p.primary), None)
        if not primary:
            return {"ok": False, "error": "no run profile found"}
        tab._start_profile(primary)
        return {"ok": True}

    def wait_runner_stop(self, runner, timeout_ms: int) -> bool:
        # 必须等进程真正退出（状态回到 idle）才算停好，不能一离开 running 就返回。
        # stop() 会先把状态置为 stopping、再到后台慢慢杀进程树；若这里只看
        # is_running()（stopping 时即为 False）就提前返回，调用方会立刻重启并把
        # 「手动停止」标记清掉，等旧进程真正退出时就被误判成异常崩溃，弹红框。
        deadline = time.time() + timeout_ms / 1000.0
        while self.runner_busy(runner) and time.time() < deadline:
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
        return not self.runner_busy(runner)

    def runner_busy(self, runner) -> bool:
        if runner.state() in ("running", "stopping"):
            return True
        cleanup_pending = getattr(runner, "stop_cleanup_pending", None)
        return bool(cleanup_pending and cleanup_pending())

    def module_port(self, module: str) -> int | None:
        if module in self.tab._module_ports:
            return self.tab._module_ports.get(module)
        for mod_name, _path, expected_port, _cls in self.tab.project_meta.spring_boot_modules:
            if mod_name == module:
                return expected_port
        return None

    def wait_module_stopped(
        self,
        module: str,
        timeout_ms: int,
        port: int | None = None,
    ) -> bool:
        from src.core.process_runner import is_port_listening

        tab = self.tab
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            runner = tab.module_runners.get(module)
            active = bool(runner and self.runner_busy(runner))
            try:
                tab._detect_and_apply_external()
            except Exception:
                pass
            if module in tab._module_external_pids:
                active = True
            if port and is_port_listening(port):
                active = True
            if not active:
                return True
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
        return False

    def wait_project_stopped(self, timeout_ms: int, port: int | None = None) -> bool:
        from src.core.process_runner import is_port_listening

        tab = self.tab
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            active = self.runner_busy(tab.runner)
            if port and is_port_listening(port):
                active = True
            if not active:
                return True
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
        return False

    def target_modules(self, module: str | None) -> list[str]:
        if self.tab._is_multi_module:
            names = self.module_names()
            return [module] if module else names
        return [self.tab.project_meta.name]

    def active_modules(self, module: str | None) -> list[str]:
        active = []
        for state in self.states(refresh_external=True):
            if state.kind == "script":
                continue
            if module and state.module != module:
                continue
            if state.is_running:
                active.append(state.module)
        return active

    def module_health_ok(self, module: str, markers: tuple[str, ...]) -> tuple[bool, dict]:
        from src.core.process_runner import is_port_listening

        tab = self.tab
        if tab.project_meta.project_type == "nginx":
            status = tab._detect_nginx_status()
            if status.running and status.ports:
                return True, {
                    "module": module,
                    "source": "nginx",
                    "pid": status.master_pid,
                    "ports": status.ports,
                }
            return False, {"module": module, "source": "pending"}

        runner = tab.module_runners.get(module)
        if runner and runner.is_running() and self._log_has_marker(tab.module_logs.get(module), markers):
            return True, {"module": module, "source": "log"}
        port = tab._module_ports.get(module)
        if port and is_port_listening(port):
            return True, {"module": module, "source": "port", "port": port}
        try:
            external = tab._detect_external_modules()
        except Exception:
            external = {}
        if module in external:
            pid = external[module]
            return True, {"module": module, "source": "external", "pid": pid}
        return False, {"module": module, "source": "pending"}

    def single_project_health_ok(self, markers: tuple[str, ...]) -> tuple[bool, dict]:
        from src.core.process_runner import find_port_holder

        tab = self.tab
        if tab.project_meta.project_type == "nginx":
            status = tab._detect_nginx_status()
            if status.running and status.ports:
                return True, {"source": "nginx", "pid": status.master_pid, "ports": status.ports}
            return False, {"source": "pending"}

        if tab.runner.is_running() and self._log_has_marker(tab.log, markers):
            return True, {"source": "log"}
        default_port = tab.project_meta.default_port
        if default_port and find_port_holder(default_port):
            return True, {"source": "port", "port": default_port}
        return False, {"source": "pending"}

    def _log_has_marker(self, log_widget, markers: tuple[str, ...], tail: int = 50) -> bool:
        if log_widget is None:
            return False
        doc = log_widget.edit.document()
        total = doc.blockCount()
        start = max(0, total - tail)
        block = doc.findBlockByNumber(start)
        while block.isValid():
            text = block.text()
            if any(marker in text for marker in markers):
                return True
            block = block.next()
        return False

    def begin_compile(self) -> dict:
        tab = self.tab
        compile_prof = tab._find_compile_profile()
        if not compile_prof:
            return {"ok": False, "error": "no compile profile found"}
        if tab.runner.is_running():
            return {"ok": False, "error": "another task is running"}
        doc = tab.log.edit.document()
        start_block = doc.blockCount()
        tab._start_profile(compile_prof)
        return {
            "ok": True,
            "runner": tab.runner,
            "doc": doc,
            "start_block": start_block,
        }
