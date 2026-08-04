# mini-ide

mini-ide 是本机开发指挥台，用 GUI 管理普通项目、聚合目录、服务、Git、临时开发工作区和 Codex/cc 入口。所有回复使用简短、易懂的中文。

## 项目结构

- 技术栈：Python 3.11+、PySide6、Pygments、psutil、watchdog、sqlparse。
- 源码根目录：`_build/`。
- 逻辑层：`_build/src/core/`，负责配置、项目识别、进程、服务状态、Git、文件和工作区。
- 界面层：`_build/src/ui/`，负责 Qt 交互和展示。
- 工具层：`_build/src/util/`。
- 发布文件：根目录 `mini-ide.exe`、`mini-ide-cli.exe`、`mini-ide-runtime/`。
- 打包脚本：`_build\scripts\build.bat`。
- 冒烟测试：在 `_build` 目录运行 `poetry run python scripts/_smoke.py`。

## 运行模型

### 普通项目

普通项目占一个顶层 Tab。项目页提供服务启停、日志、文件、Git、诊断和编译；多模块项目不传模块时，操作作用于全部模块。

### 聚合目录

一个聚合目录只占一个顶层 Tab，内部固定有“项目”和“需求工作区”两个页面。

聚合目录定义文件是：

```text
<聚合根目录>/.mini-ide/project.json
```

文件必须是有效的聚合配置，至少包含一个直属组件；组件路径必须位于聚合根目录内。配置记录聚合 ID、名称、直属项目、项目类型、共享标记、工作区目录和可选运行配置。

目录打开时，只要满足以下任一条件，就按聚合目录处理：

1. 目录存在 `.mini-ide/project.json`。
2. 目录已登记在全局 `AppConfig.aggregate_project_paths` 中。

配置文件本身优先于普通项目历史记录。配置文件存在但无效时，打开会报告聚合配置错误，不会降级成普通项目。

添加目录的 GUI 入口是“文件 → 添加目录...”（`Ctrl+O`）。选择目录后，弹窗提供“普通项目”和“聚合目录”：

- 选择“聚合目录”时，在后台线程扫描直属子目录并生成配置；扫描只识别含 `.git`、`build.gradle`、`pom.xml`、`package.json`、`pyproject.toml`、`requirements.txt`、`manage.py`、`go.mod` 或 Nginx 配置的目录，并排除隐藏目录和 `workspace` 目录。
- 空目录或没有可识别组件时创建失败，不生成配置。
- 选择“普通项目”只取消全局登记，不删除已有 `.mini-ide/project.json`；已有有效配置下次仍会按聚合目录打开。
- 从最近打开、拖拽或 CLI 直接打开目录时，不会重复询问；按上面的识别规则直接打开。

聚合 Tab 的项目表中，每个项目独立启动、停止、查看日志、文件、Git、诊断和编译，不提供跨项目批量启动、批量停止或运行预设。项目页的 Codex/cc 入口在源目录模式打开聚合根目录，在工作区模式打开当前任务根目录。

需求工作区表只展示当前聚合目录的工作区，支持双击进入；“退出工作区”返回聚合源目录。工作区页提供新建、Codex/cc 打开、同步源分支、合并代码、删除和刷新；界面不提供 Review 按钮。表格“落后”列只读取本地 ref，显示任务分支合计落后本地基准分支的提交数，不联网。

### 临时开发工作区

新建工作区时，只为勾选的非共享 Git 项目创建 Worktree 和本地任务分支；未勾选项目不复制，共享项目只引用源目录。工作区目录位于聚合配置的 `workspaceDirectory` 下，默认是 `workspace/<任务 ID>/`，并写入 `workspace.json`、`AGENTS.md` 和 `context.md`。

创建前会检查：项目是 Git 仓库、源目录不处于 detached HEAD、基准分支和提交可读取、任务分支及远端同名分支不存在、Worktree 不重复。创建过程失败会回滚已经创建的 Worktree、分支和元数据。

工作区状态允许 `created`、`active`、`reviewing`、`merged`。Review 只在 CLI/API 中存在：读取工作区改动文件数、任务提交、基准差异、远端是否包含任务分支、基准分支是否已包含任务分支，并据此更新状态。

“合并代码”是用户确认后的本地安全操作：

- Worktree 必须干净，且停在任务分支。
- 源目录必须干净，且停在创建时记录的基准分支。
- 任务分支必须能快进到源目录基准分支，或已经包含在基准分支中。
- 只在源目录执行 `git merge --ff-only <任务分支>`，不自动解决冲突、不推送远程、不改写历史。
- 所有项目按顺序处理；某个项目失败时，后续项目不再执行。

“同步源分支”是反向操作，只改 Worktree，不改源目录工作区：

- 只要求 Worktree 干净且停在任务分支，不要求源目录干净或停在基准分支。
- 默认只使用源仓库本地基准分支 ref。
- 传入 `--fetch` 或 GUI 勾选远端更新时，对有上游的基准分支执行 `git fetch --prune`；本地和上游一方领先时使用较新的一方，两端分叉时拒绝同步。
- 在 Worktree 执行普通 `git merge`（不 rebase、不改写历史）。冲突默认执行 `git merge --abort` 回滚；只有用户二次确认或显式传入 `--keep-conflicts` 才保留冲突供手工解决。
- 成功同步或确认当前已包含基准提交后，更新 `workspace.json` 对应组件的 `baseCommit`；工作区状态为 `merged` 且发生同步时退回 `active`。

“删除”先做预检：

- 任务目录只能包含 `workspace.json`、`AGENTS.md`、`context.md` 和受管理的 Worktree 目录；未知文件会阻止删除。
- 仍有服务运行、Worktree 有未提交或未跟踪文件时阻止删除。
- 未合并且没有远端备份的任务分支阻止删除；未合并但已推送的分支需要用户确认并保留分支。
- 合并后的任务分支尝试 `git branch -d`；无论分支是否最终删除，都会先移除 Worktree 并执行 `git worktree prune`。
- 只有元数据、Worktree 和任务根目录都能安全处理时才删除任务目录；不删除源目录、不删除远端分支。

所有扫描、Git、文件创建/删除、工作区创建/合并/同步/删除和其他耗时操作必须在 `QThread` 或 CLI 工作线程执行，不能阻塞 Qt 主线程。

## CLI

外部自动化和 AI 工具只能调用根目录 `mini-ide-cli.exe`，不能用 `mini-ide.exe` 执行自动化命令。CLI 通过 IPC 控制已经运行的 GUI；成功结果通常为 JSON，错误可能写入 stderr。

常用命令：

```text
--status
--list-projects
--list-modules <project>
--open <path>
--close <project>
--start <project> [module]
--stop <project> [module]
--restart <project> [module]
--ensure-running <project> [module]
--health <project> [module]
--compile <project>
--log <project> [module]
--diagnose <project> [module]
--git-status <project>
--git-diff <project>
--git-ai-context <project>
--list-aggregates
--open-aggregate <target>
--list-workspaces
--open-workspace <target>
--close-workspace
--create-development-workspace <aggregate> <name> --projects a,b [--description text]
--workspace-review <target>
--workspace-delete-check <target>
--workspace-sync <target> [--fetch] [--keep-conflicts]
--preflight-build
--can-quit
--quit
```

规则：

- 项目、聚合目录和工作区目标优先按规范化完整路径、稳定 ID 或唯一短名匹配；有歧义时拒绝执行并返回候选。
- `--open <path>` 走与 GUI 相同的目录识别逻辑；有效 `.mini-ide/project.json` 会自动注册并打开聚合 Tab。
- `--list-aggregates`、`--open-aggregate` 和 `--list-workspaces` 使用全局已登记的聚合路径；未登记但存在配置的目录先用 `--open <path>` 打开即可登记。
- `--open-workspace` 在所属聚合 Tab 内切换，不创建新的顶层 Tab；`--close-workspace` 返回源目录。
- `--list-workspaces` 只列聚合目录内部的开发工作区，不展示旧 `WorkspaceEntry`。
- `--workspace-sync` 默认只用本地基准分支；`--fetch` 才联网拉取上游；冲突默认回滚，只有显式 `--keep-conflicts` 才把冲突留在 Worktree。返回 `needs_conflict_confirmation=true` 表示冲突已回滚，需要用户确认后才能重试。
- `--workspace-delete-check` 只执行删除预检，不删除任何文件或分支；真正删除由 GUI 删除操作执行。
- `--auto-start` 可让 CLI 在 GUI 未运行时启动同目录的 `mini-ide.exe`；`--status`、`--preflight-build`、`--can-quit` 和 `--quit` 不自动拉起 GUI。
- 模块名必须精确匹配；项目不传模块时表示该项目的全部模块。
- 退出码：`0` 成功，`1` 操作失败，`2` 参数错误，`3` GUI 未运行，`4` 超时。
- `--preflight-build` 和 `--can-quit` 用于确认构建/退出条件；打包前必须返回 `ok=true`。
- `--compile` 只与目标项目自己的服务互斥：编译前记录目标项目原本运行的模块，完成后只恢复这些模块，不能为了编译停止其他项目。

## 开发约束

- UI 中的扫描、Git、文件创建/删除、工作区生命周期和其他耗时操作必须使用 `QThread`，不能阻塞 Qt 主线程。
- UI 颜色、圆角、间距必须使用 `_build/src/ui/theme.py` 的 token，禁止散落硬编码颜色。
- Windows 子进程必须使用 `CREATE_NO_WINDOW`，避免弹出控制台窗口；GUI 启动 CLI 自动拉起时使用独立进程模式。
- 不引入新依赖，除非用户明确同意。
- 服务启停、日志、诊断、编译和工作区生命周期必须经过 mini-ide CLI 或 GUI；不要直接运行业务项目的 Gradle、Maven、npm 或 Python 启动命令。

## 发布与安全

- 修改源码后运行 `_build\scripts\build.bat`，更新根目录 EXE 和 `mini-ide-runtime/`。
- `build.bat` 会生成 GUI `mini-ide.exe` 和控制台 `mini-ide-cli.exe`；根目录 EXE 被运行中的 IDE 锁定时，必须先获得用户同意，再通过 CLI 停止服务并退出 IDE，不能强杀进程或绕过锁定。
- 修改 CLI、服务生命周期、日志、诊断、编译或构建预检行为后，检查 `C:\Users\Administrator\.claude\skills\mini-ide\SKILL.md` 是否需要同步。
- 未经用户明确同意，不关闭 mini-ide。
- 用户同意关闭后，先通过 `mini-ide-cli.exe --status`/`--can-quit` 确认服务状态，再通过 `--quit` 停止服务并退出 IDE；打包完成后重新启动根目录 `mini-ide.exe` 并用 CLI 验证 IPC。
- 交付前运行冒烟测试、必要的 `compileall` 和 `git diff --check`；失败必须先修复。

## 操作日志

所有 CLI 接口调用和 GUI 关键操作统一写入 mini-ide 自身运行日志：

- 日志路径：`%APPDATA%\mini-ide\logs\mini-ide-YYYYMMDD.log`。
- 日志基础 logger：`mini-ide`；行为日志使用子 logger `mini-ide.action`，通过父 logger 写入同一文件。
- 按天生成日志文件；单文件最大 5 MB，保留 3 个轮转副本；启动时再清理超过 14 天的日志。
- GUI 可通过“帮助 → 打开 mini-ide 日志”或“打开日志目录”访问日志。

CLI 接口日志格式：

```text
[CLI] <cmd> params=<参数字典>
[CLI] <cmd> ok
[CLI] <cmd> fail: <错误信息>
[CLI] <cmd> EXCEPTION
```

GUI 行为日志使用 `[GUI]` 前缀，当前覆盖：

```text
[GUI] 启动服务 project=<项目名> profile=<profile名>
[GUI] 停止服务 project=<项目名>
[GUI] 重启服务 project=<项目名>
[GUI] 启动模块 project=<项目名> module=<模块名>
[GUI] 停止模块 project=<项目名> module=<模块名>
[GUI] 进入工作区 aggregate=<聚合目录名> workspace=<工作区名>
[GUI] 创建工作区成功 workspace=<路径>
[GUI] 创建工作区失败 error=<错误信息>
[GUI] 同步源分支 aggregate=<聚合目录名> workspace=<工作区名> fetch_remote=<是否拉远端> keep_conflicts=<是否保留冲突>
[GUI] 同步源分支成功 synced=<已同步项目> kept_conflicts=<保留冲突项目>
[GUI] 同步源分支存在冲突并已回滚 projects=<项目列表>
[GUI] 同步源分支失败 error=<错误信息>
[GUI] 合并工作区成功 projects=<项目列表>
[GUI] 合并工作区失败 error=<错误信息>
[GUI] 删除工作区成功 kept_branches=<保留分支>
[GUI] 删除工作区失败 error=<错误信息>
```

PowerShell 查询示例：

```powershell
$log = "$env:APPDATA\mini-ide\logs\mini-ide-$(Get-Date -f yyyyMMdd).log"
Select-String "\[CLI\]|\[GUI\]" $log
Select-String "(fail|FAIL|WARNING|ERROR).*\[CLI\]|\[GUI\].*失败" $log
```
