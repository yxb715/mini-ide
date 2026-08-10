# CLI 说明

根目录 `mini-ide-cli.exe` 是唯一的自动化入口。它通过 IPC 调用已经运行的 GUI；GUI 未运行时，只有带 `--auto-start` 的普通命令会尝试启动同目录的 `mini-ide.exe`。

## 命令

~~~text
--status
--list-projects
--list-modules <project>
--list-runtimes <project>
--open <path>
--close <project>

--start <project> [module] [--wait] [--timeout N]
--stop <project> [module]
--restart <project> [module] [--wait] [--timeout N]
--ensure-running <project> [module] [--timeout N]
--health <project> [module] [--timeout N]
--compile <project> [--timeout N]
--log <project> [module] [--tail N] [--errors] [--all-modules]
--diagnose <project> [module] [--tail N]

--git-status <project>
--git-diff <project> [--summary|--full] [--max-chars N]
--git-ai-context <project> [--summary|--full] [--max-chars N]

--list-aggregates
--open-aggregate <target>
--list-workspaces
--open-workspace <target>
--close-workspace
--create-development-workspace <aggregate> <name> --projects a,b [--description text]
--workspace-review <target>
--workspace-delete-check <target>
--workspace-sync <target> [--fetch] [--keep-conflicts]

--preflight-build [project]
--can-quit
--quit
~~~

`--status`、`--preflight-build`、`--can-quit` 和 `--quit` 不自动启动 GUI。`--can-quit` 是不退出的全局退出前检查；`--quit` 会停止所有运行中的服务并退出 GUI。窗口关闭按钮只隐藏到托盘，不能替代 `--quit`。

## 目标解析

项目、聚合目录和工作区目标按以下顺序匹配：

1. 规范化完整路径。
2. 稳定 ID 或目录名。
3. 唯一短名/模糊匹配。

多个候选时返回歧义错误和候选列表，不猜测执行。运行单元 ID 必须精确匹配；旧命令中的 `module` 是运行单元 ID 的兼容名称，省略表示目标项目的全部运行单元。

`--list-runtimes <project>` 返回运行单元 ID、显示名、分类、工作目录、启动档位、端口、健康地址、依赖、配置来源和当前状态。`--list-modules` 保留旧响应格式，已有自动化不需要立即迁移。

`--open <path>` 使用 GUI 相同的目录识别逻辑。已有聚合配置或全局登记的目录直接按聚合打开；最近打开和 CLI 不会重复询问目录类型。未登记、无配置目录通过 GUI“添加目录”或拖拽打开时，需要选择普通项目或聚合目录。

## 聚合与工作区命令

- `--list-aggregates`、`--open-aggregate` 和 `--list-workspaces` 使用全局已登记的聚合路径；未登记但存在配置的目录可先用 `--open <path>` 登记。
- `--list-workspaces` 只列聚合目录中的开发工作区，不展示旧 `WorkspaceEntry`。
- `--open-workspace` 在所属聚合 Tab 内切换，不创建新的顶层 Tab。
- `--close-workspace` 停止当前工作区环境中的服务并返回聚合源目录。
- `--workspace-delete-check` 只做删除预检，不删除文件、Worktree 或分支；真正删除由 GUI 操作执行。
- `--workspace-sync` 默认只使用本地基准分支；`--fetch` 才拉取上游；冲突默认回滚，`--keep-conflicts` 才保留冲突。

## 编译与退出检查

- `--compile <project>` 只与目标项目自己的服务互斥。编译前记录该项目原本运行的模块，编译后只恢复这些模块，不停止或启动其他项目。
- `--preflight-build <project>` 只检查目标项目；省略项目和 `--can-quit` 都执行全局退出前检查。
- `--close <project>` 会停止目标项目服务并关闭对应 Tab。
- `--quit` 会停止所有项目服务、保存会话并真正退出 GUI。

## 异步与超时

服务健康检查、等待启动、编译、Git diff、工作区创建/评审/同步/删除预检等长任务由 GUI 工作线程执行，CLI 会等待最终 JSON 响应。`--timeout` 是服务/编译等待时间；CLI IPC 还有少量响应缓冲时间。

返回码：

~~~text
0  成功
1  操作失败
2  参数错误
3  GUI 未运行
4  等待超时
~~~

完整 usage 由无效参数时输出的 usage 和 `_build/src/core/cli_client.py` 定义；当前 CLI 没有单独的 `--help` 命令。
