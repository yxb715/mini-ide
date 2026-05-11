# mini-ide

**定位**：AI 时代的开发指挥台 —— 本机 GUI，替代 IntelliJ IDEA / WebStorm / PyCharm 的"启动 + 看日志 + 看文件"场景，内存 150-250 MB。用户不写代码，代码由 Codex 写；mini-ide 负责 **启动项目 / 看日志 / 改 markdown 文档 / Git 状态可视化**。mini-ide 与外部 Codex 之间不主动通信——用户的 Codex 在自己的终端里跑，mini-ide 不暴露 MCP / HTTP API。

## 技术栈

- **Python 3.11+**（用 `pyproject.toml` 声明 `<3.14`）
- **PySide6 6.7+**（Qt 6 的 Python 绑定；主 GUI 框架）
- **Pygments**（文件预览语法高亮 + 日志区代码 token 着色）
- **psutil**（进程树管理、端口扫描）
- **watchdog**（FileIndexer 增量更新）
- **sqlparse**（日志里的 Hibernate SQL 美化）

打包用 PyInstaller（不在 pyproject.toml 依赖里，按需手动 `pip install pyinstaller` 即可，避免污染运行依赖）。`scripts/make_icon.py` 用 Pillow 生成图标，作为 dev 依赖。

Poetry 管理依赖。Python 版本范围 `>=3.11,<3.14`。

主题（Tokyo Night 风格）由 `src/ui/theme.py` 自写，**不依赖 qdarkstyle**（上游 QSS 用 `background-color` 长写覆盖简写规则，调主题反复出诡异 bug）。颜色 / 圆角 / 间距全部走 token，违反者冒烟会挂（见硬约束 15）。

## 启动方式

| 场景 | 命令 |
|---|---|
| 开发 | `run.bat`（保留 cmd 窗口看启动日志） |
| 日常使用 | 双击 `mini-ide.vbs`（静默启动，无黑框） |
| 带初始项目启动 | `mini-ide.vbs "D:\path\to\project"` 或 `python main.py <path>` —— 窗口起来后自动打开该项目 |
| 冒烟测试 | `poetry run python scripts/_smoke.py`（28 个模块 import + 全项目 hex 颜色硬扫描） |

**单实例**：已有 mini-ide 在跑时，再次启动不会开第二个窗口——新进程通过 `QLocalServer` 把 `sys.argv[1]` 转发给老实例后立刻退出；老实例 `activate_and_open()` 把自己拉到前台并打开该路径。适合配合资源管理器右键菜单「用 mini-ide 打开」之类的外部入口。

## 目录结构

```
mini-ide/
├── main.py                     # 入口：单实例检查 + QApplication → MainWindow；可接 sys.argv[1] 作初始项目路径
├── mini-ide.vbs                # Windows 无黑框启动脚本（支持透传路径参数）
├── run.bat                     # 开发用：保留 cmd 窗口看实时日志
├── scripts/
│   ├── _smoke.py              # 导入冒烟测试（28 个模块）+ 全项目 hex 颜色硬扫描
│   └── make_icon.py           # 用 Pillow 生成 .ico 图标（dev 依赖才用得到）
│
├── src/core/                   # 无 Qt 依赖的纯逻辑层
│   ├── config.py              # AppConfig（%APPDATA%/mini-ide/config.json 持久化）
│   ├── project_detector.py    # 项目类型识别 → RunProfile 列表 + Spring Boot 多模块列表
│   ├── process_runner.py      # QProcess 封装 + 端口扫描 + env 清洗
│   ├── log_classifier.py      # 日志行分类（error/warn/stack/sql/spring boot 阶段 + 端口占用诊断）
│   ├── file_index.py          # 后台扫描项目文件 + watchdog 增量更新
│   ├── git_ops.py             # git 命令封装（status/log/diff/fetch/pull/branch、还原到 @{u}）
│   ├── port_scanner.py        # psutil 查指定端口占用进程 + kill（必须 QThread 调用）
│   ├── env_scanner.py         # 扫项目 .env* / application*.yml / bootstrap* 等配置文件
│   └── git_worker.py          # GitFetchWorker（QThread 包装的静默 fetch）
│
├── src/ui/                     # Qt UI 组件
│   ├── theme.py               # 主题 SSoT：颜色/形状/间距/字号 token + apply_theme（自写 QSS，不依赖 qdarkstyle）
│   ├── main_window.py         # 主窗口（菜单、Tab 容器、Tab 会话恢复、首次最大化、单实例 IPC 响应）
│   ├── styles.py              # 兼容外壳：from src.ui.theme import *（旧 import 不会立刻断）
│   ├── empty_state.py         # 没开任何项目时的引导页
│   ├── project_tab.py         # 单项目面板（工具栏 + 左侧 [服务面板?+文件树] + 右侧中心 Tab[日志+模块日志+文件 tab]）
│   ├── service_panel.py       # 多模块 Spring Boot 的左上侧服务面板（启停 + 状态 + 切日志）
│   ├── log_widget.py          # 智能日志（批量刷新、ANSI 剥离、错误计数、搜索、端口诊断按钮）
│   ├── file_tree.py           # 左侧目录树（懒加载 + 全项目搜索 + 新建/重命名/复制粘贴/删除/Codex 右键）
│   ├── file_preview.py        # FilePreviewPane：tab 内嵌的文件编辑面板（行号、语法高亮、字号、3s 自动保存）
│   ├── syntax_highlighter.py  # Pygments → QSyntaxHighlighter 适配
│   ├── quick_open.py          # PickerDialog 基类 + 最近文件 + 命令面板（FramelessDialog + 失焦关闭）
│   ├── content_search.py      # Ctrl+Shift+F 全项目内容搜索
│   ├── git_viewer.py          # Git 改动实时查看器；支持复制分支名 / 文件路径 / diff
│   ├── port_dialog.py         # 🔌 端口占用查询对话框（查询 / kill，异步 QThread）
│   ├── env_panel.py           # 📋 环境/配置文件集中面板（列出 .env*/application*.yml 点开走 FilePreviewPane）
│   └── settings_panel.py      # 项目信息面板（只读：路径/类型/包管理/主类）；不再常驻，通过命令面板弹窗
│
└── src/util/
    ├── app_log.py             # 自己的运行日志 + 崩溃捕获 + 主线程卡死 watchdog
    ├── editor.py              # 外部编辑器探测 + 打开文件到指定行（fallback 路径） + search_in_project
    ├── git_info.py            # 状态栏用的轻量 git 信息（branch/dirty/ahead/behind/changed，缓存 2s + invalidate API）
    └── notify.py              # Windows 系统通知（QSystemTrayIcon.showMessage）
```

## 关键设计 / 硬约束（改代码前必读）

### 1. Windows 子进程必须加 `CREATE_NO_WINDOW`

GUI 程序（pythonw / 打包后 exe）spawn 子进程时 Windows 会自动弹一个控制台窗口。所有调 `subprocess.run/Popen` 的地方都要加：

```python
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
subprocess.run([...], creationflags=_NO_WINDOW, ...)
```

`QProcess` 用 `_suppress_console()`：

```python
proc.setCreateProcessArgumentsModifier(lambda a: setattr(a, 'flags', a.flags | 0x08000000))
```

**违反这条会有黑窗口一闪而过 / Windows Terminal 弹 tab。** Alacritty 这类 GUI 程序 spawn 时也要加（防中间 console 闪烁）。

### 2. 子进程环境必须清 mini-ide 自己的 venv

`process_runner._build_child_env()` 会摘掉：
- `VIRTUAL_ENV` / `POETRY_ACTIVE` / `PYTHONHOME`
- PATH 里的 `<mini-ide>/.venv/Scripts`

否则 `poetry run python` / `python main.py` 会误用 mini-ide 的 venv 而不是项目自己的。

### 3. Windows 命令名解析策略（`_split_program`）

- `.bat` / `.cmd` → `cmd.exe /c` 调用
- `.exe` 或绝对路径 → 直接跑
- `python` / `python3` / `pip` / `pip3`（含 `.exe` 后缀变体，见 `_DEFER_TO_SHELL`）→ **永远** `cmd.exe /c`（让子进程干净 PATH 自己解析，避免 shutil.which 吃到父进程 venv）
- 其他（`npm` / `yarn` / `poetry` / `node`）→ 用 **child env 的 PATH** 调 `shutil.which(path=env["PATH"])`

### 4. 耗时操作必须放 QThread

主线程任何 > 100ms 的操作都会冻结 UI。已经移出主线程的：
- 端口扫描（`process_runner._port_scanner_loop`）—— **走 daemon `threading.Thread` 而非 QThread**（不需要 Qt 信号，纯后台快照），3s 刷一次 `_port_snapshot`，UI 只读快照不阻塞
- FileIndexer 初次扫描（`_IndexWorker` QThread）
- 内容搜索（`SearchWorker` QThread + 流式回传）
- 健康检查（`HealthProbe` QThread）
- **Git 网络操作**：`GitFetchWorker`（`src/core/git_worker.py`）只做静默 `git fetch`；pull / push / merge / checkout 不在 GUI 里做

### 5. 日志批量刷新

`LogWidget._pending` 队列 + 50ms `_flush_timer`。Gradle 初扫会飙到 1000+ 行/秒，逐行 insert 到 `QPlainTextEdit` 会卡主线程。批量刷一次再统一自动滚动一次。

### 6. 配置兼容（新增/删除字段都要管）

`AppConfig.load()` 里：

- **新增字段**：直接加 default 值，老 config.json 没这个字段也无痛升级
- **删除字段**：`load()` 里用 `dataclass.fields()` **过滤掉未知 key 再传给 `cls(**raw)`**，否则老 config.json 里残留的字段会让启动直接 `TypeError`
- **字段含义改了**：在 `load()` 里加迁移（例：`file_open_mode: "auto" → "preview"`、`window_geometry` 加 `maximized` 字段）

删字段时绝对不能漏过滤——历史上每次漏掉都直接启动崩溃。

### 7. 后台 fetch + 静默提示（不弹系统通知）

每个 `ProjectTab` 启动后 8 秒 + 每 5 分钟跑一次 `git fetch`（`GitFetchWorker`），完成后清 `git_info._CACHE` 重读 ahead/behind。`behind > 0` 时**只**把分支按钮染橙（`COLOR_WARN` token）+ 改 tooltip 提示远程有新提交；分支菜单只提供复制分支名，拉取/合并/切分支交给外部 AI / 终端处理。

**不要加回系统通知**：弹窗会在用户专注写代码时打断，分支按钮染色已经足够显眼。

### 8. 文件预览的自动保存

`FilePreviewPane`（tab 内嵌面板，不是对话框）每次 `textChanged` 重启 3 秒 `_autosave_timer`（debounce）。`_autosave` 用临时文件 + rename 原子替换，**失败不弹框**只在状态条（`lbl_status`）显示"自动保存失败"。外层（`ProjectTab._on_tab_close_requested` 关 tab、`request_close` 关项目）必须调 `pane.flush_save()`——它会停掉 pending 定时器并立刻触发一次落盘。`Ctrl+S` 仍可手动立即保存。**没有保存按钮**（已被自动保存替代），也**没有"编辑/预览"模式切换**（默认就是编辑），只有二进制 / >2MB / 读取失败时切只读。

### 9. 单实例 + IPC 转发（`main.py`）

mini-ide 只允许一个进程运行。启动流程：

1. `main.py` 先用 `QLocalSocket` 尝试连 `SERVER_NAME = "mini-ide-single-instance"`
2. **连上了**（说明已有实例在跑）→ 把 `sys.argv[1]`（命令行传入的项目路径，可能为空）写过去 → **立刻退出本进程**
3. **连不上** → 本进程是第一个，正常建 `MainWindow` + `QLocalServer.listen(SERVER_NAME)` 接后续连接
4. `MainWindow.__init__` 在 `_startup_finalize` 里**先恢复会话，再打开 pending 初始项目**——顺序不能换（否则初始项目会被会话覆盖到不显眼位置）
5. 老实例收到转发：`activate_and_open(path)` → 打开项目 + `showNormal` + `raise_` + `activateWindow` 把自己拉前台

这套机制支持"资源管理器右键 → 用 mini-ide 打开"之类的外部入口。**改入口时**必须保证第 2 步的 500ms connectTimeout 不被拉长，否则每次启动都要等半秒；改 `activate_and_open` 时必须保留三件套（showNormal + raise_ + activateWindow），少一个在某些 Windows 版本上就拉不到前台。

### 10. 中心 Tab 容器（`ProjectTab.center_tabs`）

`ProjectTab` 的 splitter 右侧是一个 `QTabWidget`，里面混装三种 tab：

- **tab 0 = 项目级 `LogWidget`**（标题「📋 日志」），两侧 close 按钮都 `setTabButton(0, …, None)` 置空——**永远不能被关**。单模块项目的启动日志、多模块项目的编译/Clean 日志都走这里。
- **模块日志 tab（多模块 Spring Boot 项目专用）**：每启动一个子模块就 `_ensure_module_log_tab(name)` 加一个标题为「📋 <模块名>」的 tab。关 tab 会触发 `_stop_module`（见硬约束 11）。
- **文件预览 tab**：`FilePreviewPane`，通过 `_show_preview(path, line, col)` 添加；同一文件用 `_file_panes: dict[abs_path_lower, pane]` 去重（Windows 文件系统不区分大小写，key 必须 `.lower()`）。

`tabCloseRequested` 统一走 `_on_tab_close_requested(index)`：`index == 0` 直接 return；模块日志 tab 走"弹确认 → stop runner → 等 finished 回调里 `_remove_module_tab`"；文件 tab 走 `pane.flush_save()` → `removeTab` → `deleteLater`，并从 `_file_panes` 清 key。

`Ctrl+W` 是项目作用域（`WidgetWithChildrenShortcut`），转发到 `_on_tab_close_requested(currentIndex)`；在日志 tab（index 0）时按 Ctrl+W 什么都不做。`ProjectTab.request_close` 必须：① 停 `self.runner` 和 `self.module_runners` 里所有 running 的 runner；② 遍历 `_file_panes.values()` 调 `flush_save`——否则刚敲的字还在 pending 定时器里会丢。

**不要**把文件预览改回 `QDialog` 弹窗形式（用户明确要求内嵌 tab）。

### 11. 多模块 Spring Boot → 多 runner + 服务面板（`ProjectTab` + `ServicePanel`）

Spring Cloud 这种"一个父 Gradle 仓库 + N 个 `@SpringBootApplication` 子模块"的场景，走的是**多模块分支**：

- `project_detector._detect_gradle` 识别到 ≥2 个子模块时填充 `ProjectMeta.spring_boot_modules = [(name, abs_path), ...]`；`ProjectTab.__init__` 据此置 `self._is_multi_module = True`。
- 多模块项目：`self.runner` 只跑项目级 profile（编译 / Clean）；每个子模块一个独立的 `ProcessRunner`，存在 `self.module_runners: dict[module_name, ProcessRunner]`。日志按模块路由到 `self.module_logs: dict[module_name, LogWidget]`，每个 LogWidget 是一个中心 Tab 容器里的独立 tab。
- 左侧 splitter 纵向分两栏：上是 `ServicePanel`（启停按钮 + 状态图标 + 运行时长 + 「全部启动」），下是 `FileTree`。单模块项目左侧只有 `FileTree`，`ServicePanel` 根本不创建——**单模块行为 100% 不变**，这是硬约束。
- 工具栏的「启动」按钮多模块时 `setVisible(False)`。多模块的启停入口只有两个：左侧服务面板的启停按钮、命令面板动态列出的「▶ 启动 xxx / ⏹ 停止 xxx」。
- 状态栏：多模块时显示「N/M 运行中」聚合状态（由 `_refresh_aggregate_state` 管）；单模块时走 `_on_state` 的老逻辑。

**关 tab = 停进程**的实现（重要）：用户关一个运行中的模块 tab 时，`_on_tab_close_requested` 弹确认 → `self._closing_modules.add(module)` 后 `runner.stop()`；**不立即 removeTab**。进程实际退出后 `_on_module_finished` 发现该模块在 `_closing_modules` 里，这时才调 `_remove_module_tab` 清理 runner / log widget / tab。不能反过来（先 removeTab 再停进程）——因为 Qt 信号延迟到达时 log widget 已经 `deleteLater`，lambda 回调访问会 crash。

**不要**把多模块改回"一个 ProjectTab 一个 runner"的结构（那样就只能串行启动）。**不要**在 ServicePanel 外再加一个"启动所有"按钮——工具栏必须保持极简。

## 项目识别（`project_detector.py`）

按优先级探测：Gradle → Maven → package.json → Python。返回 `ProjectMeta`，关键字段 `project_type`：

- Java 系：`spring-boot-gradle` / `spring-boot-maven` / `gradle-java` / `maven-java`
- 前端系：`vue` / `react` / `next` / `nuxt` / `svelte` / `node`
- Python 系：`python` / `python-poetry` / `django` / `fastapi` / `flask`
- 兜底：`generic`

`project_type` 决定项目图标、默认启动配置、前端 build 按钮等。多模块 Gradle 自动识别 `settings.gradle` 里的 `include`，找带 `@SpringBootApplication` 的模块，默认启用第一个，其他作为备选启动按钮（命令面板里能看到）。

## 日志分类（`log_classifier.py`）

按顺序匹配：stack 行 → Caused by → SQL → Spring Boot 启动信号 → javac/maven 编译错误（带 jumps）→ 按 log level → BUILD SUCCESSFUL/FAILED → Gradle 任务失败 → 自打印的 `[已取消]` / `[启动失败]` → plain。

**跨行状态**：`LineContext` 持有 `current_stack_id`，连续的 `at ...` 行归为同一 stack group 可折叠。

**诊断规则**：`_DIAGNOSIS_RULES` 三元组 `(pattern, msg, extract_port: bool)`，命中后日志底部条显示一句提示。`extract_port=True` 表示 `pattern` 的 `group(1)` 是端口号，classifier 会把它塞进 `Classification.diagnosis_port`，`LogWidget` 的诊断栏据此显示「🔌 查看端口 8080 占用进程」按钮，点了 emit `portDiagnosisRequested(port)` → `ProjectTab` 打开 `PortDialog` 预填该端口并自动查询。加新规则往列表里追加三元组即可。

## 图标映射

| 项目类型 | 图标 | 理由 |
|---|---|---|
| Spring Boot / Java Gradle/Maven | ☕ | Java = 咖啡 |
| Vue / Nuxt | 💚 | Vue 品牌绿 |
| React | ⚛ | 原子 = React logo |
| Next.js | ▲ | Next logo 三角形 |
| Svelte | 🔥 | Svelte 橘红 |
| Node.js | 🟨 | JS 黄 |
| Python / Django / FastAPI / Flask | 🐍 | Python = 蛇 |

## 行为规范（用户反馈驱动）

- **默认 `file_open_mode = "preview"`**：点文件树里的文件**永远**在中心 Tab 容器里开一个 `FilePreviewPane`（语法高亮、自动保存、默认编辑模式），不弹"用什么软件打开"；同一文件再次点击只聚焦已有 tab
- **工具栏极简**：项目 Tab 顶部 toolbar 只有 `[启动 ↔ 停止 切换]` + `[⌘ 命令面板]`；前端项目（vue/react/next/nuxt/svelte/node）且 `package.json` 带 `build` script 时多一个「🔒 打包」按钮。重启 / 编译 / Clean / Git 改动 / 端口查询 / 环境文件 / 项目信息等次级命令**全部**进命令面板（`Ctrl+Shift+P`）；文件树 / 内容搜索 / 打开项目目录这类也全在命令面板里
- **状态栏内容**：`● 状态` `已运行 N` `✓ 启动完成` `🌿 分支 ▾` `📝 N 处改动` —— 端口监听显示已移除（用户认为各项目自定义端口，无需统一显示）
- **分支按钮**：点击弹下拉菜单，只提供当前分支 / 本地分支 / 远程分支的复制；不做拉取、合并、切分支
- **改动按钮**：dirty 时橙色，干净时灰色，点击打开实时 `GitViewer`
- **首次启动默认最大化**：无 `window_geometry` 记录时 `showMaximized()`；之后按上次状态恢复（最大化也记得）
- **Ctrl+Shift+N 聚焦左侧过滤框**（不是弹独立窗口）
- **Ctrl+W**：关闭中心 Tab 里当前的文件 tab（日志 tab 忽略）
- **默认项目目录**：`config.default_project_dir = "G:\whaty\project"`
- **Tab 会话恢复**：关闭时记项目 tab 列表，下次启动按顺序恢复（`config.active_tabs`）；运行状态不恢复；**中心 Tab 容器里的文件 tab 不恢复**（每次重开项目都只有日志 tab）

## 文件树右键菜单（按从上到下顺序）

1. ▶ 运行（仅 .bat/.cmd/.ps1/.exe 文件显示，置顶）
2. 复制 / 剪切 / 粘贴（支持文件和目录；可接收资源管理器复制进来的路径）
3. ✏ 重命名（文件和目录都支持；根目录不可重命名；目标已存在拒绝；Windows 大小写改名放行）
4. 📄 新建 Markdown 文件 / 新建文件 / 新建目录（目录上 → 该目录；文件上 → 同级目录）
5. 在 Alacritty 打开
6. 在 codex 中打开
7. 在资源管理器中显示
8. 复制：文件名 / 不含扩展名 / 绝对路径 / 相对路径
9. 🗑 删除到回收站（`QFile.moveToTrash`，根目录不可删）

刷新时**保留展开状态 + 滚动位置**（`_collect_expanded` / `_apply_expanded`），整树刷新和单节点刷新都用同一套机制。

## 文件预览/编辑（`file_preview.py`）

`FilePreviewPane`（QWidget 子类，作为中心 Tab 容器的 tab 内容）。

- 顶栏：`[相对路径] [状态条] [外部编辑器] [📁 资源管理器显示]`
- **默认就是编辑模式**——没有"编辑/预览"切换按钮，也没有"源码/渲染"切换；只有二进制 / >2MB / 读取失败 / 文件不存在时 setReadOnly(True) 并在状态条提示原因
- 字号：固定 13pt（`file_preview._FONT_PT`）。**不要引入字号调节 UI**——想改尺寸直接改常量。**坑**：全局 QSS 里的 `QPlainTextEdit { font-size: 12px; }` 会覆盖 `setFont(pointSize=...)`，必须在 `CodeView.__init__` 里贴实例级 QSS（`self.setStyleSheet("QPlainTextEdit { font-size: Npt; }")`）才能强制生效——不要删这行 setStyleSheet。
- Markdown：没有单独的渲染视图，直接按源码编辑（要看渲染找外部 Typora 等）
- 自动保存：编辑停止 3 秒 debounce 写盘（见硬约束 8）；外层关 tab 前必须调 `flush_save()`
- 对外 API：`get_path()` / `is_dirty()` / `goto_line(line, col)` / `flush_save() -> bool`；信号 `saved(path)` / `dirtyChanged(bool)`（外层用来给 tab 标题加 `*` 星号）
- 二进制文件 / 超过 2MB 的文件只读 + 不可编辑
- 快捷键作用域都是 `WidgetWithChildrenShortcut`（Ctrl+F 搜索 / Ctrl+S 保存）；**不**在 pane 里注册 Ctrl+W，交给 `ProjectTab` 统一处理

## 常见坑

1. **不要**在主线程跑 `psutil.net_connections()`（Windows 上 200-800ms，直接冻 UI）——`port_scanner.list_port_listeners` / `PortDialog` 已全部走 QThread
2. **不要**让默认工具栏按钮变多（用户反复要求精简）。当前 toolbar 只有「启动/停止」+「⌘ 命令面板」，前端项目有 build 时多「🔒 打包」；此外已删除过 🌲 文件树开关 / 🔍 文件搜索 / 📁 打开目录 / ⚙ 设置面板开关，都不要加回来
3. **不要**给 stdout/stderr 日志加 ANSI 颜色（强制 NO_COLOR=1 + client 侧 `_strip_ansi` 双保险）
4. **不要**在 ProjectTab 构造里同步扫文件（FileIndexer 是 async 的 QThread）
5. **不要**在 `git fetch` 上偷懒走主线程（必须 QThread，否则点一下卡几秒）；不要把 pull / push / merge / checkout 重新做成 GUI 按钮
6. **不要**给 `FramelessWindowHint` 的 dialog 用 `show()` 后不主动 `activateWindow()`（Windows 上拿不到键盘焦点，ESC 关不掉）—— `quick_open.PickerDialog` 用 `showEvent` 抢焦点 + `changeEvent` 失焦自动关
7. 改 config 字段：**新增**加 default、**删除**靠 `load()` 里 `dataclass.fields()` 过滤未知 key（见硬约束 6）
8. **不要**重新加上"发到 AI / Codex"按钮：用户已明确放弃这个方向。AI 协作走外部 Codex（用户自己在终端开），mini-ide 不主动推送也不暴露 MCP / HTTP 接口。只保留复制分支名 / 路径 / diff / 当前异常这类剪贴板辅助能力
9. **不要**做链路追踪 / APM / 服务网格 / 配置中心可视化等"全功能 IDE"功能：定位是指挥台，不是 IDEA 替代品。功能完整度是无底洞，要拼的是 AI 时代下的差异化体验
10. **不要**重新引入 MCP / HTTP server：当前 mini-ide 启动后**不监听任何端口**。如果以后真要做"AI 看运行时"，先选定方案再加
11. **不要**把 `FilePreviewPane` 改回 `QDialog` 弹窗形式。文件预览走中心 Tab 容器内嵌（同文件去重聚焦、Ctrl+W 关、关 tab 前自动 flush_save），用户要的是 IDE 风格而非到处弹窗
12. **不要**重新加"编辑/预览"模式切换，也**不要**给 Markdown 加渲染视图。默认就是源码编辑 + 自动保存，想看 md 渲染走外部 Typora
13. **不要**重新引入 Workspace / 跨项目编排概念：典型的过度抽象。多服务并行启动的需求已由"多模块 Spring Boot → ProjectTab 内部的 ServicePanel + 多 runner"满足（见硬约束 11）；跨项目场景用户自己开多个顶级 tab 就够。未来再冒出"统一管理 N 个不同目录项目"的需求，也**不要**用"容器/集合/工作区"这种抽象概念去包它
14. **关窗口必须显式 `QApplication.quit()`**：`notify.py` 第一次 `QSystemTrayIcon.show()` 后 tray 成为持久 UI 引用，Qt 不会因 MainWindow 关闭自动退出事件循环——进程变成 pythonw.exe 僵尸留在后台，单实例 IPC 检测到僵尸后又把新启动转发给它，表现成"代码改了没生效"。`MainWindow.closeEvent` 末尾必须 `QApplication.quit()`，不要删这行

15. **禁止 hardcode 任何颜色 / 圆角 / 间距 / 字号**——必须从 `src/ui/theme.py` 的 token 拿。

   `theme.py` 是主题 SSoT（Single Source of Truth），定义了：
   - 颜色：`BG_L0..L5` / `BG_CODE` / `BG_DIAGNOSIS`、`FG_PRIMARY` / `FG_SECONDARY` / `FG_DIM` / `FG_BRIGHT`、`ACCENT*`、`COLOR_SUCCESS/WARN/ERROR/INFO/SQL/BANNER/LINK/DEBUG`、`DOT_*`、`GIT_*`、`BG_BTN_*`
   - 形状：`RADIUS_SM/MD/LG`、`GAP_*`、`H_BTN/BTN_SM/TOOLBAR/STATUSBAR`、`W_LEFT_PANEL`
   - 字号：`FONT_PT_UI/UI_SM/UI_LG/CODE/DIFF`，字体族 `FONT_FAMILY_UI/CODE`
   - QSS 入口：`apply_theme(app)` —— 全自写 base QSS，不再依赖 qdarkstyle

   写 widget QSS 必须用 f-string 把 token 拼进去：`f"color:{FG_SECONDARY};"`。
   `scripts/_smoke.py` 会扫描 `src/` 下所有 .py，命中 `#[0-9a-fA-F]{6}` 字面量就让冒烟挂掉
   （白名单：`theme.py` / `styles.py`（兼容外壳）/ `syntax_highlighter.py`（Pygments 代码色板，语义独立））。

   想改主题就改 `theme.py` 一个文件。**绝对不要**在 widget 里写 hex——hardcode 散落各处会反复出"改了没生效""一处一种灰"的 bug。

## 修 bug 流程

1. 打开 `%APPDATA%\mini-ide\logs\mini-ide-YYYYMMDD.log` 看启动 / 进程 / 崩溃记录（崩溃栈完整）
2. `poetry run python scripts/_smoke.py` 跑一遍：① 28/28 模块 import 通过 ② 0 hex hits（违反硬约束 15 会列出文件:行号）
3. 改完用 `run.bat` 启动，看控制台输出实时日志
4. 打开 `G:\whaty\project\race\server`（多模块 Spring Boot）测 Java 路径
5. 打开 `G:\whaty\project\race\webapp` 测 Vue 路径
6. 打开 `G:\whaty\project\race\py-prediction` 测 Python / Poetry 路径

## 沟通风格（和用户协作）

- 用户**不亲自写代码**，所有修改由 AI 完成
- 回答要直接、具体，给出文件路径 + 行号
- 改 3 个以上文件前先复述计划
- 用户对 UI 噪音 / 冗余按钮 / 工具栏繁杂非常敏感，默认选"少即是多"
- 把工期和复杂度的权衡透明化，让用户拍板
- 用户能接受甚至偏好"砍功能"的建议，只要理由清晰；不要害怕提议删东西
