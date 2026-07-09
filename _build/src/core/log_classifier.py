"""日志行分类与元信息提取（纯逻辑，无 UI 依赖）

把一行日志打上标签（error/warn/info/debug/stack/sql/startup/meta/plain），
并抽取可跳转的文件定位（filepath, line, column）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

LineKind = Literal[
    "error", "warn", "info", "debug", "trace",
    "stack", "caused_by", "sql", "sql_continuation",
    "startup_banner", "startup_ready",
    "meta", "plain"
]


@dataclass
class FileJump:
    """一行日志里识别到的可跳转位置"""
    path: str
    line: int = 0
    column: int = 0
    start: int = 0      # 在原始行内的起始字符
    end: int = 0


@dataclass
class Classification:
    kind: LineKind
    jumps: list[FileJump] = field(default_factory=list)
    stack_group_id: str = ""   # 同一异常堆栈的标识（用于折叠）
    is_foldable_head: bool = False
    diagnosis: str = ""        # 识别到的常见问题提示
    diagnosis_port: int = 0    # 若诊断是端口占用类，提取出的端口号（0 表示没有）
    startup_phase: str = ""    # Spring Boot 启动阶段（如 "Connecting DB"、"Started"）


# ---- 分类模式 ----

_RE_LOG_LEVEL = re.compile(
    r"(?P<level>ERROR|WARN(?:ING)?|SEVERE|FATAL|INFO|DEBUG|TRACE)\b",
    re.IGNORECASE,
)

_RE_JAVA_STACK = re.compile(
    r"^\s+at\s+([\w$.<>]+)\(([\w\-. ]+?):(\d+)\)\s*$"
)

_RE_JAVA_STACK_NATIVE = re.compile(
    r"^\s+at\s+[\w$.<>]+\((?:Native Method|Unknown Source)\)\s*$"
)

_RE_JAVA_STACK_MORE = re.compile(r"^\s+\.{3}\s+\d+\s+more\s*$")

_RE_CAUSED_BY = re.compile(r"^(?:Caused by|Suppressed):", re.IGNORECASE)

_RE_EXCEPTION_HEAD = re.compile(
    r"^(?:[\w.$]+\.)?(\w*(?:Exception|Error))(?::\s|\s*$)"
)

# javac / gradle 编译错误：/path/Foo.java:42: error: cannot find symbol
_RE_JAVAC_ERR = re.compile(
    r"(?P<path>[A-Za-z]:[/\\][^\s:]+\.(?:java|kt|kts)|/[^\s:]+\.(?:java|kt|kts)):"
    r"(?P<line>\d+)(?::(?P<col>\d+))?:?\s*(?:(?P<sev>error|warning|警告|错误)\s*:)?"
)

# Maven 编译错误：[ERROR] /path/Foo.java:[42,5] xxx
_RE_MVN_ERR = re.compile(
    r"\[(?P<sev>ERROR|WARNING)\]\s+(?P<path>[A-Za-z]:[/\\][^\s:]+\.(?:java|kt)):\[(?P<line>\d+),(?P<col>\d+)\]"
)

_RE_SQL_LINE = re.compile(r"^(Hibernate|SQL)\s*:", re.IGNORECASE)
_RE_SQL_KEYWORDS = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH)\b",
    re.IGNORECASE,
)

_RE_SPRING_BANNER = re.compile(r":: Spring Boot ::")
_RE_SPRING_STARTED = re.compile(
    r"Started\s+\w+\s+in\s+(?P<sec>[\d.]+)\s*seconds?\b"
)
_RE_TOMCAT_STARTED = re.compile(r"Tomcat started on port.*?(\d+)")
_RE_NETTY_STARTED = re.compile(r"Netty started on port\(s\):\s*(\d+)")
_RE_DATASOURCE = re.compile(r"HikariPool.*Start completed", re.IGNORECASE)

# 前端常见
_RE_VITE_READY = re.compile(r"ready in \d+\s*ms", re.IGNORECASE)
_RE_WEBPACK_COMPILED = re.compile(r"compiled (successfully|with \d+ error)", re.IGNORECASE)

# 常见问题诊断。第三列 extract_port=True 表示 group(1) 是端口号，
# 交给调用方（UI）显示「查看占用进程」按钮。
_DIAGNOSIS_RULES: list[tuple[re.Pattern[str], str, bool]] = [
    (re.compile(r"Web server failed to start\. Port (\d+) was already in use", re.I),
     "端口已被占用：点按钮查看占用进程并释放", True),
    (re.compile(r"bind.*Address already in use.*?:(\d+)", re.I),
     "端口冲突：点按钮查看占用进程", True),
    (re.compile(r"Address already in use.*?(\d+)", re.I),
     "端口冲突：点按钮查看占用进程", True),
    (re.compile(r"EADDRINUSE[^\d]*(\d+)", re.I),
     "Node 端口冲突：点按钮查看占用进程", True),
    (re.compile(r"listen\s+EADDRINUSE.*?:(\d+)", re.I),
     "端口冲突：点按钮查看占用进程", True),
    (re.compile(r"端口\s*(\d+)\s*.*?(?:占用|被占)", re.I),
     "端口被占用：点按钮查看占用进程", True),
    (re.compile(r"Failed to configure a DataSource", re.I),
     "数据源配置缺失：检查 spring.datasource.url/username/password 或 application-*.yml 是否加载", False),
    (re.compile(r"No qualifying bean of type", re.I),
     "Bean 未找到：检查 @Component/@Service/@Autowired 注解，或是否漏了 @ComponentScan", False),
    (re.compile(r"Could not resolve placeholder '([^']+)'", re.I),
     "占位符未解析：application-*.yml 里缺少对应配置，或 profile 未激活", False),
    (re.compile(r"ClassNotFoundException:\s*([\w.$]+)", re.I),
     "类未找到：检查依赖是否引入，或是否需要 clean 后重新编译", False),
    (re.compile(r"NoClassDefFoundError", re.I),
     "运行时类缺失：若只在自动重启后偶发，优先检查旧进程/编译缓存是否清干净；稳定复现再查依赖 scope/clean 编译", False),
    (re.compile(r"cannot find symbol", re.I),
     "编译错误：符号未定义，常见原因是拼写错误或缺少 import", False),
    (re.compile(r"LOMBOK|lombok.*not.*install", re.I),
     "Lombok 未启用：检查 annotationProcessor 依赖", False),
    (re.compile(r"ENOENT.*package\.json", re.I),
     "未找到 package.json：当前目录不是 Node 项目", False),
    (re.compile(r"Missing script:", re.I),
     "npm script 不存在：检查 package.json 的 scripts 字段", False),
    (re.compile(r"Unable to access jarfile", re.I),
     "jar 文件找不到：确认已先运行打包任务（bootJar/package）", False),
    (re.compile(r"The system cannot find the path specified", re.I),
     "路径不存在：检查 gradlew/mvnw 是否存在或命令路径", False),
]


# ---- 分类实现 ----

_current_stack_id: int = 0


def _new_stack_id() -> str:
    global _current_stack_id
    _current_stack_id += 1
    return f"stk-{_current_stack_id}"


class LineContext:
    """跨行状态：识别异常堆栈跨行归组"""
    def __init__(self) -> None:
        self.current_stack_id: str = ""
        self.last_kind: LineKind = "plain"

    def reset_stack(self) -> None:
        self.current_stack_id = ""


def classify(line: str, ctx: LineContext | None = None) -> Classification:
    ctx = ctx or LineContext()
    raw = line
    stripped = line.strip()

    # --- 堆栈行 ---
    if _RE_JAVA_STACK.match(line) or _RE_JAVA_STACK_NATIVE.match(line) or _RE_JAVA_STACK_MORE.match(line):
        if not ctx.current_stack_id:
            ctx.current_stack_id = _new_stack_id()
        jumps: list[FileJump] = []
        m = _RE_JAVA_STACK.match(line)
        if m:
            # 尝试从类名反查源文件（不保证命中，只记录文件名）
            file_name = m.group(2)
            lineno = int(m.group(3))
            pos = line.find(m.group(2))
            jumps.append(FileJump(path=file_name, line=lineno, start=pos, end=pos + len(m.group(2)) + 1 + len(m.group(3))))
        ctx.last_kind = "stack"
        return Classification(kind="stack", jumps=jumps, stack_group_id=ctx.current_stack_id)

    # --- Caused by / Suppressed 视为堆栈延续，但单独标色 ---
    if _RE_CAUSED_BY.match(stripped):
        if not ctx.current_stack_id:
            ctx.current_stack_id = _new_stack_id()
        ctx.last_kind = "caused_by"
        msg, port = _find_diagnosis(stripped)
        return Classification(kind="caused_by", stack_group_id=ctx.current_stack_id, is_foldable_head=True,
                              diagnosis=msg, diagnosis_port=port)

    # --- Exception 头（通常紧随 ERROR 行，是堆栈开头）---
    if _RE_EXCEPTION_HEAD.match(stripped) and ctx.last_kind in ("error", "plain", "caused_by"):
        if not ctx.current_stack_id:
            ctx.current_stack_id = _new_stack_id()
        ctx.last_kind = "error"
        msg, port = _find_diagnosis(stripped)
        return Classification(kind="error", stack_group_id=ctx.current_stack_id, is_foldable_head=True,
                              diagnosis=msg, diagnosis_port=port)

    # 其他任意行 -> 结束当前堆栈
    prev_stack = ctx.current_stack_id
    ctx.current_stack_id = ""

    # --- SQL ---
    if _RE_SQL_LINE.match(stripped):
        ctx.last_kind = "sql"
        return Classification(kind="sql")
    if ctx.last_kind in ("sql", "sql_continuation") and _RE_SQL_KEYWORDS.match(stripped):
        ctx.last_kind = "sql_continuation"
        return Classification(kind="sql_continuation")

    # --- Spring Boot 启动信号 ---
    if _RE_SPRING_BANNER.search(line):
        ctx.last_kind = "startup_banner"
        return Classification(kind="startup_banner", startup_phase="banner")
    m = _RE_SPRING_STARTED.search(line)
    if m:
        ctx.last_kind = "startup_ready"
        return Classification(kind="startup_ready", startup_phase=f"Started in {m.group('sec')}s")
    if _RE_TOMCAT_STARTED.search(line) or _RE_NETTY_STARTED.search(line):
        ctx.last_kind = "info"
        return Classification(kind="info", startup_phase="server-listening")
    if _RE_DATASOURCE.search(line):
        ctx.last_kind = "info"
        return Classification(kind="info", startup_phase="datasource-ready")
    if _RE_VITE_READY.search(line) or _RE_WEBPACK_COMPILED.search(line):
        ctx.last_kind = "startup_ready"
        return Classification(kind="startup_ready", startup_phase="frontend-ready")

    # --- 编译错误 (javac / Maven) ---
    jumps = list(_extract_file_jumps(line))
    # 看 severity
    compile_sev: str = ""
    m_mvn = _RE_MVN_ERR.search(line)
    if m_mvn:
        compile_sev = m_mvn.group("sev").lower()
    else:
        m_jc = _RE_JAVAC_ERR.search(line)
        if m_jc and m_jc.group("sev"):
            sev = m_jc.group("sev").lower()
            if sev in ("error", "错误"):
                compile_sev = "error"
            elif sev in ("warning", "警告"):
                compile_sev = "warning"

    if compile_sev == "error":
        ctx.last_kind = "error"
        msg, port = _find_diagnosis(line)
        return Classification(kind="error", jumps=jumps, diagnosis=msg, diagnosis_port=port)
    if compile_sev == "warning":
        ctx.last_kind = "warn"
        return Classification(kind="warn", jumps=jumps)

    # --- 按日志级别分类 ---
    m_level = _RE_LOG_LEVEL.search(line)
    if m_level:
        level = m_level.group("level").upper()
        if level in ("ERROR", "SEVERE", "FATAL"):
            ctx.last_kind = "error"
            msg, port = _find_diagnosis(line)
            return Classification(kind="error", jumps=jumps, diagnosis=msg, diagnosis_port=port)
        if level.startswith("WARN"):
            ctx.last_kind = "warn"
            return Classification(kind="warn", jumps=jumps)
        if level == "INFO":
            ctx.last_kind = "info"
            return Classification(kind="info", jumps=jumps)
        if level in ("DEBUG", "TRACE"):
            ctx.last_kind = level.lower()  # type: ignore[assignment]
            return Classification(kind=level.lower(), jumps=jumps)  # type: ignore[arg-type]

    # meta 行（自己打印的 $ command / [进程结束] ...）
    if raw.startswith("$ ") or raw.startswith("[进程") or raw.startswith("  cwd:") or raw.startswith("  JAVA_") or raw.startswith("  SPRING_") or raw.startswith("  完整命令") or raw.startswith("  工作目录"):
        ctx.last_kind = "meta"
        return Classification(kind="meta")

    # 自打印的失败/取消信息 → 红色
    if raw.startswith("[已取消]") or raw.startswith("[启动失败]"):
        ctx.last_kind = "error"
        return Classification(kind="error")

    # BUILD SUCCESSFUL/FAILED
    if "BUILD SUCCESSFUL" in line or "BUILD SUCCESS" in line:
        ctx.last_kind = "info"
        return Classification(kind="info", startup_phase="build-success")
    if "BUILD FAILED" in line or "BUILD FAILURE" in line:
        ctx.last_kind = "error"
        return Classification(kind="error", diagnosis="构建失败，查看上方错误详情")

    # Gradle 任务失败标记
    if re.search(r">\s*Task\s+:.*FAILED\s*$", line):
        ctx.last_kind = "error"
        return Classification(kind="error")
    if line.startswith("FAILURE:") or line.startswith("* What went wrong:"):
        ctx.last_kind = "error"
        return Classification(kind="error")
    # Gradle 展示的子错误 "> xxxxx"
    if stripped.startswith("> ") and ctx.last_kind == "error":
        msg, port = _find_diagnosis(stripped)
        return Classification(kind="error", diagnosis=msg, diagnosis_port=port)

    ctx.last_kind = "plain"
    return Classification(kind="plain", jumps=jumps)


def _extract_file_jumps(line: str) -> list[FileJump]:
    out: list[FileJump] = []
    for m in _RE_JAVAC_ERR.finditer(line):
        out.append(FileJump(
            path=m.group("path"),
            line=int(m.group("line")),
            column=int(m.group("col") or 0),
            start=m.start("path"),
            end=m.end("line"),
        ))
    for m in _RE_MVN_ERR.finditer(line):
        out.append(FileJump(
            path=m.group("path"),
            line=int(m.group("line")),
            column=int(m.group("col")),
            start=m.start("path"),
            end=m.end("col") + 1,
        ))
    return out


def _find_diagnosis(text: str) -> tuple[str, int]:
    """返回 (提示文本, 端口号)。无匹配时返回 ("", 0)；非端口规则返回 (msg, 0)。"""
    for pattern, msg, extract_port in _DIAGNOSIS_RULES:
        m = pattern.search(text)
        if not m:
            continue
        port = 0
        if extract_port:
            try:
                port = int(m.group(1))
            except (IndexError, ValueError):
                port = 0
        return msg, port
    return "", 0
