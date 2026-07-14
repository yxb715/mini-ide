# mini-ide

mini-ide 是本机开发指挥台：用一个 GUI 管理多个项目的启动、停止、日志、Git 状态、工作区和 AI 操作入口。

## 基本信息

- 技术栈：Python 3.11+ / PySide6 / Pygments / psutil / watchdog / sqlparse。
- 源码目录：`_build/`。
- 发布产物：根目录 `mini-ide.exe`（GUI）+ `mini-ide-cli.exe`（控制台 CLI）+ `mini-ide-runtime/`。
- 打包脚本：`_build\scripts\build.bat`。
- 冒烟测试：`cd _build && poetry run python scripts/_smoke.py`。

## 代码边界

- `src/core/`：纯逻辑层，放配置、项目识别、进程封装、服务状态、工作区、文件操作、Git 上下文等可测试逻辑。
- `src/ui/`：Qt UI 层，只做交互、展示和 UI 协调；耗时操作必须放 `QThread`。
- `src/util/`：跨层工具。
- CLI 通过 `cli_client.py` / `cli_server.py` 走 IPC 控制已运行的 GUI 实例，不要绕过 mini-ide 直接启动业务项目。

## CLI 能力

外部 AI 工具必须通过根目录 `mini-ide-cli.exe` 调用 CLI。它是 console 子系统，会可靠等待长时间编译/健康检查并返回真实 stdout、stderr 和退出码。`mini-ide.exe` 仅作为 GUI 入口保留兼容参数，自动化不得使用它。连上 GUI 后 stdout 返回 JSON；参数错误、mini-ide 未运行、等待超时等客户端侧错误可能写到 stderr。

常用命令：

```bash
mini-ide-cli.exe --status
mini-ide-cli.exe --list-projects
mini-ide-cli.exe --list-modules <project>
mini-ide-cli.exe --open <path>
mini-ide-cli.exe --close <project>
mini-ide-cli.exe --list-workspaces
mini-ide-cli.exe --open-workspace <name>
mini-ide-cli.exe --close-workspace
mini-ide-cli.exe --start <project> [module] [--wait] [--timeout N]
mini-ide-cli.exe --stop <project> [module]
mini-ide-cli.exe --restart <project> [module] [--wait] [--timeout N]
mini-ide-cli.exe --ensure-running <project> [module] [--timeout N]
mini-ide-cli.exe --health <project> [module] [--timeout N]
mini-ide-cli.exe --compile <project>
mini-ide-cli.exe --log <project> [module] [--tail N] [--errors] [--all-modules]
mini-ide-cli.exe --diagnose <project> [module] [--tail N]
mini-ide-cli.exe --git-status <project>
mini-ide-cli.exe --git-diff <project> [--summary|--full] [--max-chars N]
mini-ide-cli.exe --git-ai-context <project> [--summary|--full] [--max-chars N]
mini-ide-cli.exe --preflight-build
mini-ide-cli.exe --can-quit
mini-ide-cli.exe --quit
```

规则：

- `<project>` 按目录名或完整路径模糊匹配，不区分大小写。
- `<module>` 精确匹配模块名；多模块项目不传 module 时表示操作全部模块。
- 退出码：`0=成功`，`1=失败`，`2=参数错误`，`3=mini-ide 未运行`，`4=超时`。
- 打包前必须先确认 `--preflight-build` 或 `--can-quit` 返回 `ok=true`。
- 构建任务与本项目服务强制互斥：任一模块仍在运行/启动/停止时，`--compile` 会返回 `code=services_running`；编译进行中，启动/重启会返回 `code=build_in_progress`。改代码后的安全顺序是记录运行模块 → 全部停止 → 编译并确认成功 → 只恢复原来运行的模块。

## 必守规则

- **修改代码后要重新打包发布**：跑 `_build\scripts\build.bat` 更新根目录 `mini-ide.exe`、`mini-ide-cli.exe` 和 `mini-ide-runtime/`。
- **更新 mini-ide 能力后要同步检查全局 skill**：如果 CLI、服务启停、日志、诊断、编译、打包前检查等行为有变化，必须检查 `C:\Users\Administrator\.claude\skills\mini-ide\SKILL.md` 是否需要同步更新。
- **未经用户明确同意，禁止关闭 mini-ide**。
- **用户同意关闭后，必须先通过 mini-ide 停止当前 IDE 中正在运行的所有服务**，包括后端、前端、Python、脚本等；确认停净后，才允许关闭 IDE 和打包。
- **服务启停、日志诊断、编译、工作区管理都必须走 mini-ide CLI 或 GUI 能力**，不要直接运行 `gradle/mvn/npm/python` 启动业务项目。
- **禁止 hardcode 颜色、圆角、间距**：UI 样式从 `src/ui/theme.py` token 获取。
- **子进程必须加 `CREATE_NO_WINDOW`**，避免弹出黑窗口。
- **不引入新依赖**，除非用户明确确认。
- **完成前必须跑冒烟测试和必要的编译检查**；失败要先修复，不能带着失败结果交付。
