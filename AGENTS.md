# mini-ide

mini-ide 是本机开发指挥台，用 GUI 管理项目、聚合目录、服务、Git 状态、临时开发工作区和 Codex/cc 入口。所有回复使用简短、易懂的中文。

## 项目结构

- 技术栈：Python 3.11+、PySide6、Pygments、psutil、watchdog、sqlparse。
- 源码根目录：`_build/`。
- 逻辑层：`_build/src/core/`，负责配置、项目识别、进程、服务状态、Git、文件和工作区。
- 界面层：`_build/src/ui/`，负责 Qt 交互和展示。
- 工具层：`_build/src/util/`。
- 发布文件：根目录 `mini-ide.exe`、`mini-ide-cli.exe`、`mini-ide-runtime/`。
- 打包：`_build\scripts\build.bat`。
- 冒烟测试：在 `_build` 目录运行 `poetry run python scripts/_smoke.py`。

## 当前产品模型

### 普通项目

普通项目占一个顶层 Tab。项目页提供服务启停、日志、文件、Git、诊断和编译；多模块项目不传模块时，操作作用于全部模块。

### 聚合目录

一个聚合目录只占一个顶层 Tab，内部只有“项目”和“需求工作区”两个页面。

- 项目表中的每个项目独立启动、停止，不提供批量启动、批量停止或运行预设。
- 项目页的 Codex/cc 入口打开当前代码环境：源目录模式打开聚合目录，工作区模式打开当前任务根目录。
- 需求工作区表只展示当前聚合目录自己的工作区；双击行进入工作区，页头“退出工作区”返回源目录。
- 工作区页的操作是新建、在 Codex/cc 中打开、合并代码、删除和刷新。

### 临时开发工作区

新建工作区时，只为勾选的 Git 项目创建本地任务分支和 Worktree；分支从创建时记录的基准分支和提交创建。未勾选项目不复制，共享项目只引用源目录。

“合并代码”是用户确认后的本地安全操作：要求源目录和 Worktree 干净、分支位置正确且可以快进合并，只执行 `git merge --ff-only`，不自动解决冲突、不推送远程、不改写历史。

“删除”先检查运行服务、未提交/未跟踪文件、远端备份和未知路径。合并后的任务分支才会尝试安全删除；未合并但已推送的分支会保留，未合并且未推送或存在风险时禁止删除。

CLI 仍提供 `--workspace-review` 和 `--workspace-delete-check` 作为检查接口；界面不再提供 Review 按钮。

## CLI

外部自动化和 AI 工具只能调用根目录 `mini-ide-cli.exe`，不能用 `mini-ide.exe` 执行自动化命令。CLI 通过 IPC 控制已运行的 GUI，成功结果通常为 JSON；错误可能写入 stderr。

常用命令：

```text
--status
--list-projects
--list-modules <project>
--open <path>                  --close <project>
--start <project> [module]     --stop <project> [module]
--restart <project> [module]   --ensure-running <project> [module]
--health <project> [module]    --compile <project>
--log <project> [module]       --diagnose <project> [module]
--git-status <project>         --git-diff <project>
--git-ai-context <project>     --preflight-build
--list-aggregates              --open-aggregate <target>
--list-workspaces              --open-workspace <target>
--close-workspace
--create-development-workspace <aggregate> <name> --projects a,b
--workspace-review <target>    --workspace-delete-check <target>
--can-quit                     --quit
```

规则：

- 项目、聚合目录和工作区目标优先按规范化完整路径、稳定 ID 或唯一短名匹配；有歧义时拒绝执行并返回候选。
- 模块名必须精确匹配；项目不传模块时表示全部模块。
- `--list-workspaces` 只返回聚合目录工作区，不展示旧 `WorkspaceEntry`。
- `--open-workspace` 在原聚合 Tab 内切换，不创建新的顶层 Tab；`--close-workspace` 返回源目录。
- 退出码：`0` 成功，`1` 操作失败，`2` 参数错误，`3` GUI 未运行，`4` 超时。
- `--preflight-build` 和 `--can-quit` 都用于确认构建/退出条件；打包前必须返回 `ok=true`。
- `--compile` 只与目标项目自己的服务互斥，不得为了编译停止其它项目；编译前记录目标项目原本运行的模块，完成后只恢复这些模块。

## 开发约束

- UI 中的扫描、Git、文件创建/删除、工作区创建/合并/删除和其它耗时操作必须在 `QThread`，不能阻塞 Qt 主线程。
- UI 颜色、圆角、间距必须使用 `_build/src/ui/theme.py` 的 token，禁止散落硬编码。
- 所有子进程使用 `CREATE_NO_WINDOW`，避免弹出控制台窗口。
- 不引入新依赖，除非用户明确同意。
- 服务启停、日志、诊断、编译和工作区生命周期必须经过 mini-ide CLI 或 GUI；不要直接运行业务项目的 Gradle、Maven、npm 或 Python 启动命令。

## 发布与安全

- 修改源码后运行 `_build\scripts\build.bat`，更新根目录 EXE 和运行时目录。
- 修改 CLI、服务生命周期、日志、诊断、编译或构建预检行为后，检查 `C:\Users\Administrator\.claude\skills\mini-ide\SKILL.md` 是否需要同步。
- 未经用户明确同意，不关闭 mini-ide。
- 用户同意关闭后，先通过 mini-ide 停止当前 IDE 中所有服务并确认停净，再关闭 IDE 和打包。
- 交付前运行冒烟测试、必要的 `compileall` 和 `git diff --check`；失败必须先修复。

## 操作日志

所有 CLI 接口调用和 GUI 关键操作统一写入 mini-ide 自身运行日志：

- **日志路径**：`%APPDATA%\mini-ide\logs\mini-ide-YYYYMMDD.log`，按天切分，保留最近 14 天。
- **日志 logger**：`mini-ide.action`（子 logger，自动 propagate 到 `mini-ide` 根 logger）。

### CLI 接口日志格式

```
[CLI] <cmd> params=<参数字典>      ← 收到命令时记录
[CLI] <cmd> ok                     ← 命令成功
[CLI] <cmd> fail: <错误信息>       ← 命令失败（WARNING 级别）
[CLI] <cmd> EXCEPTION              ← 命令抛异常（ERROR + 堆栈）
```

示例：
```
[ACTION][CLI] start params={'project': 'my-proj', 'module': 'api'}
[ACTION][CLI] start ok
```

### GUI 操作日志格式

```
[GUI] 启动服务 project=<项目名> profile=<profile名>
[GUI] 停止服务 project=<项目名>
[GUI] 重启服务 project=<项目名>
[GUI] 启动模块 project=<项目名> module=<模块名>
[GUI] 停止模块 project=<项目名> module=<模块名>
[GUI] 进入工作区 aggregate=<聚合目录名> workspace=<工作区名>
[GUI] 创建工作区成功 workspace=<路径>
[GUI] 创建工作区失败 error=<错误信息>    ← WARNING 级别
[GUI] 合并工作区成功 projects=<项目列表>
[GUI] 合并工作区失败 error=<错误信息>    ← WARNING 级别
[GUI] 删除工作区成功 kept_branches=<保留分支>
[GUI] 删除工作区失败 error=<错误信息>    ← WARNING 级别
```

### 查询方式

用 grep 过滤操作日志（Windows PowerShell）：

```powershell
# 查看全部操作记录
Select-String "\[CLI\]|\[GUI\]" $env:APPDATA\mini-ide\logs\mini-ide-$(Get-Date -f yyyyMMdd).log

# 只看失败/异常
Select-String "(fail|FAIL|WARNING|ERROR).*\[CLI\]|\[GUI\].*失败" ...
```
