"""外部进程感知（纯逻辑，无 Qt 依赖）

从后台端口快照里按命令行匹配 Spring Boot 模块，认出「非 mini-ide 启动」的
进程——AI / 终端 / IDE 直接拉起、或跨 mini-ide 重启后遗留的孤儿。ProjectTab
用它来：批量启动前去重、CLI 静默接管、服务面板显示外部运行状态。

从 ProjectTab 抽出来的动机：
  1. 这块本是纯函数（只读 spring_boot_modules + 端口快照，不碰 UI），却埋在
     1700 行的上帝类里、无法单测。
  2. 原实现每轮（每 3s）对每个模块重算一次 match keys、对每个 holder 的 cmdline
     重复做 replace+lower。这里把 keys 在构造时预算一次、cmd 归一化按 holder 只算
     一次，砍掉多模块项目每 3s 在 UI 线程上的重复字符串开销。
"""
from __future__ import annotations

from typing import Iterable


def build_match_keys(mod_name: str, mod_path: str, main_class: str = "") -> list[str]:
    """生成用于匹配进程命令行的关键字（已归一化为正斜杠小写）。

    与原 ProjectTab._module_match_keys 等价。"""
    keys: list[str] = []
    norm_path = mod_path.replace("\\", "/").lower().rstrip("/")
    if norm_path:
        keys.append(norm_path)
    # gradle 模块名 a:b → 路径片段 a/b；普通模块名直接作为路径片段
    seg = mod_name.replace(":", "/").lower().strip("/")
    if seg:
        keys.append(f"/{seg}/")
        keys.append(f"/{seg}.jar")
        keys.append(f"/{seg}-")  # 带版本号的 jar：timing-service-1.0.jar
    # 主类全限定名：gradle bootRun 把 classpath 塞进 Temp jar 后，进程命令行里
    # 没有项目路径，只剩主类名。这是认出本项目服务进程的关键信号，且带包名前缀
    # （com.shwhaty.xxx）不会误撞 Chrome 等无关进程。
    if main_class:
        keys.append(main_class.lower())
    return keys


class ExternalProcessDetector:
    """按命令行把端口快照里的进程匹配到本项目的 Spring Boot 模块。

    构造时一次性预算每个模块的 match keys 和归一化主类名（项目模块列表在
    ProjectTab 生命周期内不变），之后每次 detect 只做匹配、不再重算。
    """

    def __init__(self, project_path: str,
                 modules: Iterable[tuple[str, str, object, str]]):
        # 项目根路径（归一化）——用于排除与本项目无关的同名进程
        self._proj = (project_path or "").replace("\\", "/").lower().rstrip("/")
        # 预算：module -> (keys, 归一化主类名)
        self._module_keys: list[tuple[str, list[str], str]] = []
        for mod_name, mod_path, _port, main_class in modules:
            keys = build_match_keys(mod_name, mod_path, main_class)
            norm_cls = main_class.replace("\\", "/").lower() if main_class else ""
            self._module_keys.append((mod_name, keys, norm_cls))

    def detect(self, snapshot: dict[int, list[dict]]) -> dict[str, tuple[int, int]]:
        """从端口快照匹配各模块，返回 {module: (pid, port)}。

        snapshot 形如 {port: [{"pid", "name", "cmdline"}, ...]}（process_runner
        的 port_snapshot 输出）。只读，不阻塞。
        """
        if not snapshot:
            return {}

        # cmdline 归一化按 holder 只算一次（原实现对每个模块都重算一遍）
        # normalized: [(pid, port, norm_cmd), ...]
        normalized: list[tuple[int, int, str]] = []
        for port, holders in snapshot.items():
            for h in holders:
                pid = h.get("pid")
                if pid is None:
                    continue
                cmd = (h.get("cmdline") or "").replace("\\", "/").lower()
                if cmd:
                    normalized.append((pid, port, cmd))

        result: dict[str, tuple[int, int]] = {}
        used_pids: set[int] = set()
        for mod_name, keys, norm_cls in self._module_keys:
            for pid, port, cmd in normalized:
                if pid in used_pids:
                    continue
                # 闸门：必须确认是「本项目」的进程，否则别的项目同名模块、
                # 甚至 Chrome (...\Application\chrome.exe) 会误撞模块名。
                # 两个可靠信号满足其一即可：
                #   1. 命令行含本项目根路径（IDE/终端直接 java -jar、展开 classpath）
                #   2. 命令行含本模块主类全限定名（gradle bootRun 把 classpath 塞进
                #      Temp jar，命令行里没有项目路径，只能靠主类认）
                in_project = bool(self._proj) and self._proj in cmd
                in_main_class = bool(norm_cls) and norm_cls in cmd
                if not (in_project or in_main_class):
                    continue
                if any(k in cmd for k in keys):
                    result[mod_name] = (pid, port)
                    used_pids.add(pid)
                    break
        return result
