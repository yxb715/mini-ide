"""项目服务生命周期控制器。

ProjectTab 负责界面和信号连接；这里集中承接启动、停止、重启、健康探测、
编译启动等服务生命周期动作，避免 CLI 直接触碰 ProjectTab 私有状态。
"""
from __future__ import annotations

import time

from PySide6.QtCore import QCoreApplication, QEventLoop

from src.core.launch_tracker import LaunchTracker


class ProjectServiceController:
    def __init__(self, tab):
        self.tab = tab
        self._launches = LaunchTracker(tab.project_meta.project_type)
        # prepare_compile() 和真正 runner.start() 之间虽然都在 GUI 主线程，仍用显式
        # reservation 表达不变量，避免 processEvents 或未来重构重新引入启停竞态。
        self._compile_reserved = False

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

    # ---- 项目级构建 / 服务互斥 ----

    def running_service_names_for_build(self) -> list[str]:
        """返回会与构建目录冲突的本项目服务。

        多模块项目的编译 runner 与模块 runner 是两套对象，必须在这里统一判断；
        否则 Gradle 编译覆盖 build/classes 时，运行中的 JVM 仍可能懒加载到半套 class。
        """
        from src.core.process_runner import is_port_listening

        tab = self.tab
        running: list[str] = []
        if tab._is_multi_module:
            for name, runner in tab.module_runners.items():
                if self.runner_busy(runner):
                    running.append(name)
            try:
                tab._detect_and_apply_external()
            except Exception:
                pass
            running.extend(tab._module_external_pids.keys())
            # 外部感知依赖 3 秒端口快照；编译门闩不能容忍这段缓存空窗，
            # 再对每个已知端口做一次同步直连确认。
            for name in self.module_names():
                port = self.module_port(name)
                if port and is_port_listening(port):
                    running.append(name)
            # GUI 仍可能用项目级 runner 启动主 profile，也要算服务。
            current = getattr(tab, "_current_profile", None)
            if self.runner_busy(tab.runner) and current and current.kind == "run":
                running.append(tab.project_meta.name)
        else:
            current = getattr(tab, "_current_profile", None)
            if self.runner_busy(tab.runner) and current and current.kind == "run":
                running.append(tab.project_meta.name)
            port = tab.project_meta.default_port
            if port and is_port_listening(port):
                running.append(tab.project_meta.name)
        return list(dict.fromkeys(sorted(running)))

    def build_start_error(self) -> dict | None:
        running = self.running_service_names_for_build()
        if running:
            return {
                "ok": False,
                "code": "services_running",
                "error": "services are running; stop them before compile",
                "running_modules": running,
            }
        if self._compile_reserved or self.runner_busy(self.tab.runner):
            return {
                "ok": False,
                "code": "project_task_running",
                "error": "another project task is running",
            }
        return None

    def service_start_error(self) -> dict | None:
        tab = self.tab
        if self._compile_reserved:
            return {
                "ok": False,
                "code": "build_in_progress",
                "error": "project build is being prepared; start/restart is blocked",
            }
        if self.runner_busy(tab.runner):
            current = getattr(tab, "_current_profile", None)
            # 单模块项目的 runner 本身就是当前服务；start/restart 的原有逻辑会处理
            # already running / stop→start。其它情况都是项目级构建任务冲突。
            if tab._is_multi_module or current is None or current.kind != "run":
                return {
                    "ok": False,
                    "code": "build_in_progress",
                    "error": "project build/task is running; start/restart is blocked",
                }
        return None

    def profile_start_error(self, profile) -> dict | None:
        if profile.kind == "run":
            return self.service_start_error()
        # compile/build/clean/test 都可能写入或删除构建输出；统一使用同一门闩。
        if profile.kind in ("compile", "build", "clean", "test"):
            return self.build_start_error()
        if self.runner_busy(self.tab.runner):
            return {
                "ok": False,
                "code": "project_task_running",
                "error": "another project task is running",
            }
        return None

    # ---- 本轮启动代次 ----

    def launch_target(self, module: str | None) -> str:
        return module or self.tab.project_meta.name

    def begin_launch(self, module: str | None) -> int:
        return self._launches.begin(self.launch_target(module))

    def launch_started(self, module: str | None, generation: int, runner) -> None:
        pid_getter = getattr(runner, "process_id", None)
        pid = pid_getter() if callable(pid_getter) else None
        self._launches.started(self.launch_target(module), generation, pid)

    def launch_failed(self, module: str | None, generation: int) -> None:
        self._launches.failed_to_start(self.launch_target(module), generation)

    def launch_finished(self, module: str | None, exit_code: int) -> None:
        self._launches.finished(self.launch_target(module), exit_code)

    def record_service_output(self, module: str | None, line: str) -> None:
        self._launches.record_output(self.launch_target(module), line)

    def snapshot_launch_generations(self, module: str | None) -> dict[str, int | None]:
        return {
            target: self._launches.generation(target)
            for target in self.target_modules(module)
        }

    def start(self, module: str | None = None) -> dict:
        tab = self.tab
        if tab.project_meta.project_type == "nginx":
            if module and module != tab.project_meta.name:
                return {"ok": False, "error": f"module not found: {module}"}
            if tab._detect_nginx_status().running:
                return {"ok": False, "error": "already running"}
            return {"ok": bool(tab._start_nginx())}

        conflict = self.service_start_error()
        if conflict:
            return conflict

        if tab._is_multi_module:
            if module:
                invalid = self.validate_module(module)
                if invalid:
                    return invalid
                runner = tab.module_runners.get(module)
                if runner and self.runner_busy(runner):
                    return {"ok": False, "error": "already running"}
                tab._detect_and_apply_external()
                ok = bool(tab._start_module(module, silent=True))
                return {"ok": True} if ok else {"ok": False, "error": "failed to start module"}
            ok = bool(tab._start_all_modules(silent=True))
            return {"ok": True} if ok else {"ok": False, "error": "failed to start one or more modules"}

        if self.runner_busy(tab.runner):
            return {"ok": False, "error": "already running"}
        primary = next((p for p in tab.project_meta.profiles if p.primary), None)
        if not primary:
            return {"ok": False, "error": "no run profile found"}
        ok = bool(tab._start_profile(primary))
        return {"ok": True} if ok else {"ok": False, "error": "failed to start project"}

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
                running = runner and self.runner_busy(runner)
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
            if self.runner_busy(tab.runner):
                tab._stop()
                if not self.wait_runner_stop(tab.runner, 10000):
                    return {"ok": False, "error": "stop timeout"}
            tab._stop_all_modules(silent=True)
            for runner in list(tab.module_runners.values()):
                if self.runner_busy(runner):
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

        if not self.runner_busy(tab.runner):
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

        conflict = self.service_start_error()
        if conflict:
            return conflict

        if tab._is_multi_module:
            if module:
                invalid = self.validate_module(module)
                if invalid:
                    return invalid
                runner = tab.module_runners.get(module)
                port = self.module_port(module)
                if runner and self.runner_busy(runner):
                    runner.stop()
                    if not self.wait_module_stopped(module, 15000, port):
                        return {"ok": False, "error": "stop timeout"}
                tab._detect_and_apply_external()
                if module in tab._module_external_pids:
                    if not tab._takeover_external(module):
                        return {"ok": False, "error": "stop timeout"}
                    if not self.wait_module_stopped(module, 8000, port):
                        return {"ok": False, "error": "stop timeout"}
                ok = bool(tab._start_module(module, silent=True))
                return {"ok": True} if ok else {"ok": False, "error": "failed to restart module"}

            ports = {
                mod_name: self.module_port(mod_name)
                for mod_name, _path, _port, _cls in tab.project_meta.spring_boot_modules
            }
            for runner in list(tab.module_runners.values()):
                if self.runner_busy(runner):
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
            ok = bool(tab._start_all_modules(silent=True))
            return {"ok": True} if ok else {"ok": False, "error": "failed to restart one or more modules"}

        port = tab.project_meta.default_port
        if self.runner_busy(tab.runner):
            tab.runner.stop()
            if not self.wait_project_stopped(15000, port):
                return {"ok": False, "error": "stop timeout"}
        primary = next((p for p in tab.project_meta.profiles if p.primary), None)
        if not primary:
            return {"ok": False, "error": "no run profile found"}
        ok = bool(tab._start_profile(primary))
        return {"ok": True} if ok else {"ok": False, "error": "failed to restart project"}

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
        if runner.state() != "idle":
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

    def module_health_ok(
        self,
        module: str,
        generation: int | None = None,
    ) -> tuple[bool, dict]:
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

        if self._launches.is_superseded(module, generation):
            return False, {
                "module": module,
                "source": "superseded",
                "error": "launch was superseded by a newer start/restart",
            }

        runner = tab.module_runners.get(module)
        launch = self._launches.state(module, generation)
        if runner and runner.is_running():
            if launch and launch.ready:
                return True, {
                    "module": module,
                    "source": "log",
                    "generation": launch.generation,
                    "pid": launch.runner_pid,
                }
            port = self.module_port(module)
            holder_pid = self._module_port_holder_since(module, port, launch)
            if holder_pid is not None:
                return True, {
                    "module": module,
                    "source": "port",
                    "port": port,
                    "pid": holder_pid,
                    "generation": launch.generation if launch else None,
                }
            return False, {
                "module": module,
                "source": "pending",
                "generation": launch.generation if launch else None,
            }

        # 一旦本轮启动有明确代次，就绝不能退回旧端口/旧外部进程作为成功依据。
        if launch is not None:
            return False, {
                "module": module,
                "source": "process_exited",
                "error": "process exited before becoming ready",
                "exit_code": launch.exit_code,
                "generation": launch.generation,
            }

        try:
            external = tab._detect_external_modules()
        except Exception:
            external = {}
        if module in external:
            pid, port = external[module]
            return True, {
                "module": module,
                "source": "external",
                "pid": pid,
                "port": port,
            }
        return False, {"module": module, "source": "pending"}

    def single_project_health_ok(
        self,
        generation: int | None = None,
    ) -> tuple[bool, dict]:
        from src.core.process_runner import find_port_holder

        tab = self.tab
        if tab.project_meta.project_type == "nginx":
            status = tab._detect_nginx_status()
            if status.running and status.ports:
                return True, {"source": "nginx", "pid": status.master_pid, "ports": status.ports}
            return False, {"source": "pending"}

        target = tab.project_meta.name
        if self._launches.is_superseded(target, generation):
            return False, {
                "source": "superseded",
                "error": "launch was superseded by a newer start/restart",
            }
        launch = self._launches.state(target, generation)
        if tab.runner.is_running():
            if launch and launch.ready:
                return True, {
                    "source": "log",
                    "generation": launch.generation,
                    "pid": launch.runner_pid,
                }
            default_port = tab.project_meta.default_port
            if default_port:
                holders = find_port_holder(default_port)
                holder_pid = self._new_holder_pid(holders, launch)
                if holder_pid is not None:
                    return True, {
                        "source": "port",
                        "port": default_port,
                        "pid": holder_pid,
                        "generation": launch.generation if launch else None,
                    }
            return False, {
                "source": "pending",
                "generation": launch.generation if launch else None,
            }

        if launch is not None:
            return False, {
                "source": "process_exited",
                "error": "process exited before becoming ready",
                "exit_code": launch.exit_code,
                "generation": launch.generation,
            }
        default_port = tab.project_meta.default_port
        holders = find_port_holder(default_port) if default_port else []
        if holders:
            return True, {
                "source": "external",
                "port": default_port,
                "pid": holders[0].get("pid"),
            }
        return False, {"source": "pending"}

    def _module_port_holder_since(self, module: str, port: int | None, launch) -> int | None:
        if not port:
            return None
        try:
            detected = self.tab._detect_external_modules()
        except Exception:
            return None
        item = detected.get(module)
        if not item:
            return None
        pid, detected_port = item
        if detected_port != port:
            return None
        return self._new_holder_pid([{"pid": pid}], launch)

    @staticmethod
    def _new_holder_pid(holders: list[dict], launch) -> int | None:
        """只接受本轮启动之后创建的监听进程，避免旧端口冒充新服务。"""
        if launch is None:
            return holders[0].get("pid") if holders else None
        import psutil

        for holder in holders:
            pid = holder.get("pid")
            if not pid:
                continue
            try:
                process = psutil.Process(pid)
                if launch.runner_pid and (
                    pid == launch.runner_pid
                    or any(parent.pid == launch.runner_pid for parent in process.parents())
                ):
                    return int(pid)
                # Gradle daemon 可能早于 QProcess 存在，应用 JVM 不一定是 wrapper
                # 的后代；这类情况再用本轮开始后的创建时间确认。Windows 时间戳
                # 留 0.5 秒容差，既覆盖时钟精度，又不会接受真正的旧服务。
                if process.create_time() >= launch.started_at - 0.5:
                    return int(pid)
            except (psutil.Error, OSError, ValueError, TypeError):
                continue
        return None

    def prepare_compile(self) -> dict:
        """校验并预留一次编译；调用方须先挂 finished，再 start_prepared_compile。"""
        tab = self.tab
        compile_prof = tab._find_compile_profile()
        if not compile_prof:
            return {"ok": False, "error": "no compile profile found"}
        conflict = self.build_start_error()
        if conflict:
            return conflict
        self._compile_reserved = True
        return {
            "ok": True,
            "runner": tab.runner,
            "profile": compile_prof,
        }

    def start_prepared_compile(self, prepared: dict) -> dict:
        if not self._compile_reserved:
            return {"ok": False, "error": "compile reservation is no longer valid"}
        profile = prepared.get("profile")
        try:
            ok = bool(self.tab._start_profile(profile, enforce_guard=False))
        finally:
            self._compile_reserved = False
        if not ok:
            return {"ok": False, "error": "failed to start compile process"}
        return {"ok": True}

    def abort_prepared_compile(self) -> None:
        self._compile_reserved = False

    def begin_compile(self) -> dict:
        """兼容内部旧调用；CLI 应使用 prepare→挂信号→start，避免丢 finished。"""
        prepared = self.prepare_compile()
        if not prepared.get("ok"):
            return prepared
        started = self.start_prepared_compile(prepared)
        if not started.get("ok"):
            return started
        return {"ok": True, "runner": prepared["runner"]}
