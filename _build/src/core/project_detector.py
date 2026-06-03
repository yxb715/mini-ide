"""项目类型自动识别

打开一个目录时，返回 ProjectMeta：
- 项目类型（Spring Boot Gradle/Maven、Vue/React、Python Poetry/Django/Flask ...）
- 可执行的命令集（RunProfile）
- 默认端口、健康检查路径、主类、spring profiles 等元信息

识别顺序：先看 build.gradle/pom.xml → package.json → pyproject.toml/requirements.txt
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------- 数据结构 ----------

@dataclass
class RunProfile:
    """一个可执行的命令档位（编译/启动/测试/清理...）"""
    name: str                       # 唯一 key，如 "compile"、"bootRun"
    label: str                      # UI 按钮显示
    command: list[str]              # argv 数组，直接传 subprocess
    kind: str = "run"               # run | compile | build | clean | test
    icon: str = ""                  # 按钮图标
    description: str = ""           # tooltip
    primary: bool = False           # 是否首选按钮（如 bootRun）
    pre_compile: bool = False       # 执行前是否需要强制编译


@dataclass
class ProjectMeta:
    path: str
    name: str
    project_type: str                # 内部标识：spring-boot-gradle / vue / django ...
    display_type: str                # 用户可见："Spring Boot (Gradle)"
    icon: str                        # emoji
    profiles: list[RunProfile] = field(default_factory=list)
    package_manager: str = ""        # gradle / maven / npm / yarn / pnpm / poetry / pip
    default_port: int | None = None
    health_check_path: str = ""
    ignored_dirs: list[str] = field(default_factory=list)
    main_class: str = ""             # Spring Boot 主类（如能识别）
    spring_profiles: list[str] = field(default_factory=list)  # application-*.yml 扫描结果
    notes: list[str] = field(default_factory=list)            # 识别过程中的提示
    # Spring Boot 多模块场景下所有带 @SpringBootApplication 的子模块：
    # [(name, abs_path, port_or_None, main_class)]。
    # ProjectTab 用来渲染左侧服务面板；≥2 个才算多模块。
    # port 从该模块自己的 application*.yml 扫出，扫不到填 None（运行时仍能启动）。
    # main_class 是 @SpringBootApplication 主类全限定名，供外部进程感知按命令行匹配。
    spring_boot_modules: list[tuple[str, str, int | None, str]] = field(default_factory=list)


# ---------- 工具函数 ----------

def _read(path: Path, limit: int = 500_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _file_exists(root: Path, *names: str) -> Path | None:
    for n in names:
        p = root / n
        if p.exists():
            return p
    return None


def _gradle_cmd(root: Path) -> str:
    """返回可用的 gradle 调用命令（优先项目自带 wrapper）"""
    if sys.platform == "win32":
        wrapper = root / "gradlew.bat"
        if wrapper.exists():
            return str(wrapper)
        if shutil.which("gradle"):
            return "gradle"
        return "gradlew.bat"
    wrapper = root / "gradlew"
    return str(wrapper) if wrapper.exists() else "gradle"


def _mvn_cmd(root: Path) -> str:
    if sys.platform == "win32":
        wrapper = root / "mvnw.cmd"
        if wrapper.exists():
            return str(wrapper)
        return "mvn.cmd" if shutil.which("mvn.cmd") else "mvn"
    wrapper = root / "mvnw"
    return str(wrapper) if wrapper.exists() else "mvn"


def _scan_spring_profiles(root: Path) -> tuple[list[str], int | None, str]:
    """扫描 application-*.yml/properties，返回 (profiles, port, context-path)"""
    profiles: list[str] = []
    port: int | None = None
    ctx = ""
    candidates: list[Path] = []
    for sub in ("src/main/resources", "src/main/webapp/WEB-INF", "config"):
        base = root / sub
        if base.is_dir():
            candidates.extend(base.glob("application*.yml"))
            candidates.extend(base.glob("application*.yaml"))
            candidates.extend(base.glob("application*.properties"))
    for f in candidates:
        m = re.match(r"application-(.+)\.(yml|yaml|properties)$", f.name)
        if m:
            profiles.append(m.group(1))
        content = _read(f, limit=30_000)
        if port is None:
            m_port = re.search(r"^\s*(?:server\.)?port\s*[:=]\s*(\d+)", content, re.MULTILINE)
            if not m_port:
                m_port = re.search(r"server:\s*\n(?:\s+.*\n)*?\s+port:\s*(\d+)", content)
            if m_port:
                port = int(m_port.group(1))
        if not ctx:
            m_ctx = re.search(r"context-path\s*[:=]\s*(\S+)", content)
            if m_ctx:
                ctx = m_ctx.group(1).strip('"\'').rstrip("/")
    return sorted(set(profiles)), port, ctx


def _detect_spring_boot_main(root: Path) -> str:
    """扫描 java 源码找 @SpringBootApplication 主类"""
    src = root / "src" / "main" / "java"
    if not src.is_dir():
        return ""
    for java in src.rglob("*.java"):
        try:
            content = java.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "@SpringBootApplication" in content:
            pkg_m = re.search(r"^\s*package\s+([\w.]+)\s*;", content, re.MULTILINE)
            cls_m = re.search(r"public\s+class\s+(\w+)", content)
            if pkg_m and cls_m:
                return f"{pkg_m.group(1)}.{cls_m.group(1)}"
            if cls_m:
                return cls_m.group(1)
    return ""


def _parse_gradle_subprojects(root: Path) -> list[tuple[str, Path]]:
    """解析 settings.gradle(.kts) 里的 include，返回 [(module_name, module_path), ...]"""
    settings = _file_exists(root, "settings.gradle", "settings.gradle.kts")
    if not settings:
        return []
    content = _read(settings)
    modules: list[tuple[str, Path]] = []
    seen: set[str] = set()
    # 匹配 include 'a', 'b', ":c" 等
    for m in re.finditer(r"""include\s*\(?\s*([^)\n]+)\)?""", content):
        entries = m.group(1)
        for tok in re.finditer(r"""['"]([^'"]+)['"]""", entries):
            name = tok.group(1).lstrip(":").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            # 默认模块目录 = 模块名（带 : 的转成 /）
            module_dir = root / name.replace(":", "/")
            if module_dir.is_dir():
                modules.append((name, module_dir))
            else:
                # 也可能在 settings 里用 project(':x').projectDir = ... 指定了，先按常规存
                modules.append((name, module_dir))
    return modules


def _find_spring_boot_modules(root: Path, subprojects: list[tuple[str, Path]]) -> list[tuple[str, Path, str]]:
    """在子模块里找哪些有 @SpringBootApplication。返回 [(module_name, path, main_class)]"""
    hits: list[tuple[str, Path, str]] = []
    for name, path in subprojects:
        if not path.is_dir():
            continue
        main_cls = _detect_spring_boot_main(path)
        if main_cls:
            hits.append((name, path, main_cls))
    return hits


# ---------- 具体识别器 ----------

def _detect_gradle(root: Path) -> ProjectMeta | None:
    build = _file_exists(root, "build.gradle", "build.gradle.kts")
    if not build:
        return None
    settings = _file_exists(root, "settings.gradle", "settings.gradle.kts")
    content = _read(build)
    settings_content = _read(settings) if settings else ""

    name = root.name
    m_name = re.search(r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]", settings_content)
    if m_name:
        name = m_name.group(1)

    is_spring = bool(
        re.search(r"org\.springframework\.boot", content) or
        re.search(r"spring-boot-starter", content)
    )

    gcmd = _gradle_cmd(root)
    # --no-daemon：禁用 Gradle 常驻守护进程。
    # 守护进程是独立于客户端长期驻留的进程，不挂在 mini-ide 启动的进程树下；
    # bootRun 真正 fork 出的服务进程由守护进程托管，于是「停止」顺着自己启动的
    # 进程线递归杀树时漏掉它——界面立刻显示「未启动」，但服务仍活着占着端口。
    # 下次启动撞端口、残留逐轮堆积，根因即此。改为 --no-daemon 后服务直接挂在
    # mini-ide 能管到的进程树下，停止即可连根清理。代价：每次启动少了守护进程
    # 预热复用，慢几秒，开发态可接受。
    project_flag = ["-p", str(root), "--no-daemon"]
    ignored = ["build", ".gradle", "out", ".idea"]

    if is_spring:
        # 多模块识别：settings.gradle 里有 include 时，bootRun 必须指定模块，
        # 否则每个 Spring Boot 模块都会被触发（包括无 main class 的共享库会失败）。
        subprojects = _parse_gradle_subprojects(root)
        boot_modules = _find_spring_boot_modules(root, subprojects)

        # 启动模块前缀：单模块为空；多模块选第一个带 @SpringBootApplication 的模块
        run_module = ""
        main_class = _detect_spring_boot_main(root)
        notes: list[str] = []
        # 四元组：(模块名, 绝对路径, port_or_None, 主类全限定名)。
        # 主类用于外部进程感知——gradle bootRun 把 classpath 塞进 Temp jar 后，
        # 进程命令行里没有项目路径，只剩主类名，靠它才能认出本项目的服务进程。
        module_list: list[tuple[str, str, int | None, str]] = []
        if boot_modules:
            run_module = boot_modules[0][0]
            main_class = boot_modules[0][2]
            # 每个子模块独立扫它自己的 application.yml 取 port，填到服务面板上显示
            per_module_scan = [
                (name, path, cls, _scan_spring_profiles(path))
                for name, path, cls in boot_modules
            ]
            module_list = [(name, str(path), scan[1], cls)
                           for name, path, cls, scan in per_module_scan]
            if len(boot_modules) > 1:
                names = ", ".join(m[0] for m in boot_modules)
                notes.append(f"检测到多个 Spring Boot 模块: {names}")
            # 主启动模块的 profile 和 port 用来填 ProjectMeta 的默认值
            _, port, ctx = per_module_scan[0][3]
            profiles_list = per_module_scan[0][3][0]
        else:
            profiles_list, port, ctx = _scan_spring_profiles(root)

        prefix = f":{run_module}:" if run_module else ""

        def _with_module(task: str) -> str:
            return f"{prefix}{task}" if prefix else task

        run_profiles: list[RunProfile] = [
            RunProfile(
                name="compile",
                label="编译",
                command=[gcmd, *project_flag,
                         _with_module("compileJava"), _with_module("compileTestJava"),
                         "--rerun-tasks"],
                kind="compile",
                icon="🔨",
                description="强制全量编译（含测试代码，绕过缓存）",
                primary=False,
            ),
            RunProfile(
                name="bootRun",
                label="启动",
                command=[gcmd, *project_flag, _with_module("bootRun")],
                kind="run",
                icon="▶",
                description=f"启动 Spring Boot 应用" + (f"（模块 {run_module}）" if run_module else ""),
                primary=True,
                pre_compile=True,
            ),
            RunProfile(
                name="clean",
                label="Clean",
                command=[gcmd, *project_flag, "clean"],
                kind="clean",
                icon="🧹",
                description="清理 build 目录",
            ),
        ]

        # 多模块时给其他 Spring Boot 模块各加一个备选启动按钮
        for mod_name, _mod_path, _cls in boot_modules[1:]:
            run_profiles.append(RunProfile(
                name=f"bootRun:{mod_name}",
                label=f"启动 {mod_name}",
                command=[gcmd, *project_flag, f":{mod_name}:bootRun"],
                kind="run", icon="▶",
                description=f"启动模块 {mod_name}",
                pre_compile=True,
            ))

        return ProjectMeta(
            path=str(root),
            name=name,
            project_type="spring-boot-gradle",
            display_type="Spring Boot (Gradle)" + (" · 多模块" if boot_modules else ""),
            icon="☕",
            profiles=run_profiles,
            package_manager="gradle",
            default_port=port or 8080,
            health_check_path=f"{ctx}/actuator/health" if ctx else "/actuator/health",
            ignored_dirs=ignored,
            main_class=main_class,
            spring_profiles=profiles_list,
            notes=notes,
            spring_boot_modules=module_list,
        )

    # 普通 Gradle Java
    return ProjectMeta(
        path=str(root),
        name=name,
        project_type="gradle-java",
        display_type="Java (Gradle)",
        icon="☕",
        package_manager="gradle",
        ignored_dirs=ignored,
        profiles=[
            RunProfile("compile", "编译", [gcmd, *project_flag, "compileJava", "compileTestJava", "--rerun-tasks"], kind="compile", icon="🔨"),
            RunProfile("run", "启动", [gcmd, *project_flag, "run"], kind="run", icon="▶", primary=True, pre_compile=True),
            RunProfile("clean", "Clean", [gcmd, *project_flag, "clean"], kind="clean", icon="🧹"),
        ],
    )


def _detect_maven(root: Path) -> ProjectMeta | None:
    pom = root / "pom.xml"
    if not pom.exists():
        return None
    content = _read(pom)
    is_spring = bool(
        re.search(r"<artifactId>\s*spring-boot-starter-parent\s*</artifactId>", content) or
        re.search(r"<groupId>\s*org\.springframework\.boot\s*</groupId>", content)
    )
    m_name = re.search(r"<artifactId>\s*([\w\-.]+)\s*</artifactId>", content)
    name = m_name.group(1) if m_name else root.name
    mvn = _mvn_cmd(root)
    ignored = ["target", ".idea"]

    if is_spring:
        profiles_list, port, ctx = _scan_spring_profiles(root)
        main_class = _detect_spring_boot_main(root)
        return ProjectMeta(
            path=str(root),
            name=name,
            project_type="spring-boot-maven",
            display_type="Spring Boot (Maven)",
            icon="☕",
            package_manager="maven",
            default_port=port or 8080,
            health_check_path=f"{ctx}/actuator/health" if ctx else "/actuator/health",
            ignored_dirs=ignored,
            main_class=main_class,
            spring_profiles=profiles_list,
            profiles=[
                RunProfile("compile", "编译", [mvn, "-f", str(pom), "compile", "test-compile"], kind="compile", icon="🔨"),
                RunProfile("bootRun", "启动", [mvn, "-f", str(pom), "spring-boot:run"], kind="run", icon="▶", primary=True, pre_compile=True),
                RunProfile("clean", "Clean", [mvn, "-f", str(pom), "clean"], kind="clean", icon="🧹"),
            ],
        )
    return ProjectMeta(
        path=str(root),
        name=name,
        project_type="maven-java",
        display_type="Java (Maven)",
        icon="☕",
        package_manager="maven",
        ignored_dirs=ignored,
        profiles=[
            RunProfile("compile", "编译", [mvn, "-f", str(pom), "compile", "test-compile"], kind="compile", icon="🔨"),
            RunProfile("exec", "启动", [mvn, "-f", str(pom), "exec:java"], kind="run", icon="▶", primary=True, pre_compile=True),
            RunProfile("clean", "Clean", [mvn, "-f", str(pom), "clean"], kind="clean", icon="🧹"),
        ],
    )


def _detect_node(root: Path) -> ProjectMeta | None:
    pkg = root / "package.json"
    if not pkg.exists():
        return None
    try:
        data = json.loads(_read(pkg))
    except json.JSONDecodeError:
        return None
    name = data.get("name") or root.name
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    scripts = data.get("scripts", {}) or {}

    has_vue = "vue" in deps
    has_react = "react" in deps
    has_next = "next" in deps
    has_nuxt = "nuxt" in deps
    has_svelte = "svelte" in deps

    # 包管理器：优先看 lockfile
    if (root / "pnpm-lock.yaml").exists():
        pm = "pnpm"
    elif (root / "yarn.lock").exists():
        pm = "yarn"
    elif (root / "bun.lockb").exists():
        pm = "bun"
    else:
        pm = "npm"
    run_word = "run" if pm in ("npm", "yarn", "bun") else "run"
    # yarn 可省略 run，但统一写上兼容性更好

    if has_next:
        ptype, disp, icon, port = "next", "Next.js", "▲", 3000
    elif has_nuxt:
        ptype, disp, icon, port = "nuxt", "Nuxt", "💚", 3000
    elif has_vue:
        ptype, disp, icon, port = "vue", "Vue", "💚", 5173
    elif has_react:
        ptype, disp, icon, port = "react", "React", "⚛", 3000
    elif has_svelte:
        ptype, disp, icon, port = "svelte", "Svelte", "🔥", 5173
    else:
        ptype, disp, icon, port = "node", "Node.js", "🟨", None

    # 从 scripts 里猜 dev/build/preview
    dev_script = next((s for s in ("dev", "serve", "start") if s in scripts), None)
    build_script = "build" if "build" in scripts else None
    preview_script = next((s for s in ("preview", "start:preview") if s in scripts), None)
    test_script = "test" if "test" in scripts else None

    profiles: list[RunProfile] = []
    profiles.append(RunProfile(
        name="install",
        label="安装依赖",
        command=[pm, "install"],
        kind="compile", icon="📦",
        description=f"使用 {pm} 安装依赖",
    ))
    if dev_script:
        profiles.append(RunProfile(
            name="dev",
            label="开发启动",
            command=[pm, run_word, dev_script] if pm != "npm" else [pm, "run", dev_script],
            kind="run", icon="▶", primary=True,
            description=f"{pm} run {dev_script}",
        ))
    if build_script:
        profiles.append(RunProfile(
            name="build",
            label="打包",
            command=[pm, "run", build_script] if pm == "npm" else [pm, run_word, build_script],
            kind="build", icon="🔒",
        ))
    if preview_script:
        profiles.append(RunProfile(
            name="preview",
            label="预览",
            command=[pm, "run", preview_script] if pm == "npm" else [pm, run_word, preview_script],
            kind="run", icon="👁",
        ))
    if test_script:
        profiles.append(RunProfile(
            name="test",
            label="测试",
            command=[pm, "run", test_script] if pm == "npm" else [pm, run_word, test_script],
            kind="test", icon="🧪",
        ))

    # 任何未识别的 script 也给个"更多命令"入口
    extras = [s for s in scripts if s not in {"dev", "serve", "start", "build", "preview", "test", "start:preview"}]
    for s in extras[:10]:
        profiles.append(RunProfile(
            name=f"script:{s}",
            label=f"▸ {s}",
            command=[pm, "run", s] if pm == "npm" else [pm, run_word, s],
            kind="run", icon="",
            description=scripts.get(s, ""),
        ))

    return ProjectMeta(
        path=str(root),
        name=name,
        project_type=ptype,
        display_type=disp,
        icon=icon,
        package_manager=pm,
        default_port=port,
        ignored_dirs=["node_modules", "dist", ".nuxt", ".next", ".output", "build"],
        profiles=profiles,
    )


def _detect_python(root: Path) -> ProjectMeta | None:
    pyproject = root / "pyproject.toml"
    requirements = root / "requirements.txt"
    manage_py = root / "manage.py"

    if not (pyproject.exists() or requirements.exists() or manage_py.exists()
            or (root / "setup.py").exists() or (root / "Pipfile").exists()):
        return None

    is_poetry = False
    name = root.name
    entry_script = ""
    pyproject_text = ""
    if pyproject.exists():
        pyproject_text = _read(pyproject)
        txt = pyproject_text
        if "[tool.poetry]" in txt:
            is_poetry = True
        m_name = re.search(r'name\s*=\s*["\']([^"\']+)["\']', txt)
        if m_name:
            name = m_name.group(1)
        m_entry = re.search(r"(?:scripts|entry-points)[\s\S]*?([\w_]+)\s*=\s*[\"']([\w\.]+):([\w_]+)", txt)
        if m_entry:
            entry_script = f"{m_entry.group(2)}:{m_entry.group(3)}"

    py_cmd = "poetry run python" if is_poetry else "python"
    pip_cmd = "poetry install" if is_poetry else "pip install -r requirements.txt"
    pm = "poetry" if is_poetry else "pip"

    # 识别 Django/Flask/FastAPI（所有 Python 项目统一用 🐍 图标便于一眼分辨）
    if manage_py.exists():
        ptype, disp, icon = "django", "Django", "🐍"
        run_cmd = py_cmd.split() + ["manage.py", "runserver"]
        port = 8000
    else:
        for candidate in ("main.py", "app.py", "run.py", "server.py", "wsgi.py", "web.py", "start.py"):
            if (root / candidate).exists():
                entry_script = candidate
                break
        # 候选名都没命中 → 扫根目录顶层 .py，挑第一个含 `if __name__ == "__main__":` 的脚本
        if not entry_script:
            try:
                for p in sorted(root.glob("*.py")):
                    if 'if __name__' in _read(p, limit=20_000):
                        entry_script = p.name
                        break
            except OSError:
                pass
        content = ""
        if entry_script and not ":" in entry_script:
            content = _read(root / entry_script, limit=20_000)
        deps_blob = ""
        if requirements.exists():
            deps_blob += _read(requirements)
        if pyproject_text:
            deps_blob += "\n" + pyproject_text
        has_fastapi = "fastapi" in content.lower() or "fastapi" in deps_blob.lower()
        has_flask = "flask" in content.lower() or "flask" in deps_blob.lower()

        if has_fastapi:
            ptype, disp, icon = "fastapi", "FastAPI", "🐍"
            self_runner = None
            for candidate in ("run.py", "main.py", "app.py", "server.py", "start.py"):
                p = root / candidate
                if p.exists() and "uvicorn.run(" in _read(p, limit=20_000):
                    self_runner = candidate
                    break
            if self_runner:
                run_cmd = py_cmd.split() + [self_runner]
            else:
                run_cmd = py_cmd.split() + ["-m", "uvicorn", f"{Path(entry_script).stem}:app" if entry_script else "main:app", "--reload"]
            port = 8000
        elif has_flask:
            ptype, disp, icon = "flask", "Flask", "🐍"
            run_cmd = py_cmd.split() + [entry_script or "app.py"]
            port = 5000
        else:
            ptype, disp, icon = "python-poetry" if is_poetry else "python", "Python (Poetry)" if is_poetry else "Python", "🐍"
            run_cmd = py_cmd.split() + [entry_script or "main.py"]
            port = None

    profiles = [
        RunProfile("install", "安装依赖", pip_cmd.split(), kind="compile", icon="📦"),
        RunProfile("run", "启动", run_cmd, kind="run", icon="▶", primary=True),
    ]
    if manage_py.exists():
        profiles.append(RunProfile("migrate", "迁移", py_cmd.split() + ["manage.py", "migrate"], kind="build", icon="🗃"))
        profiles.append(RunProfile("shell", "Shell", py_cmd.split() + ["manage.py", "shell"], kind="run", icon="🐚"))

    return ProjectMeta(
        path=str(root),
        name=name,
        project_type=ptype,
        display_type=disp,
        icon=icon,
        package_manager=pm,
        default_port=port,
        ignored_dirs=["__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache", "build", "dist", "*.egg-info"],
        profiles=profiles,
    )


# ---------- 对外入口 ----------

def detect_project(path: str) -> ProjectMeta:
    """按优先级探测项目类型，识别不出时返回 generic。"""
    root = Path(path)

    for detector in (_detect_gradle, _detect_maven, _detect_node, _detect_python):
        meta = detector(root)
        if meta:
            return meta

    return ProjectMeta(
        path=str(root),
        name=root.name,
        project_type="generic",
        display_type="通用目录",
        icon="📦",
        ignored_dirs=[".git", ".idea", ".vscode", "node_modules", "build", "target", "__pycache__"],
        notes=["未识别出已知的项目类型，请手动配置运行命令"],
    )
