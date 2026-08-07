# 需求工作区规则

需求工作区属于聚合目录。聚合定义位于 `<aggregate>/.mini-ide/project.json`，至少包含一个直属组件；组件路径和 `workspaceDirectory` 必须位于聚合根目录内。

## 界面

需求工作区表只展示当前聚合目录的工作区，支持双击进入；“退出工作区”返回聚合源目录。工作区页提供新建、Codex/cc 打开、同步源分支、合并代码、删除和刷新，不提供 Review 按钮。

“落后”列只读取本地 ref，显示任务分支合计落后本地基准分支的提交数，不联网。工作区内的共享项目继续使用源目录，非共享项目使用 Worktree。

## 创建

创建工作区只为勾选的非共享 Git 项目创建 Worktree 和本地任务分支：

- 未勾选项目不复制。
- 共享项目只引用源目录，不创建 Worktree。
- 默认目录为 `workspace/<任务 ID>/`，写入 `workspace.json`、`AGENTS.md` 和 `context.md`。
- 组件可在聚合配置中通过 `workspaceCopyFiles` 声明需要从源项目复制到 Worktree 的本地文件，例如 `.env`；只接受项目内相对路径和普通文件。
- 创建前检查 Git 仓库、源目录非 detached HEAD、基准分支和提交可读、任务分支及远端同名分支不存在、Worktree 不重复。
- 白名单文件缺失、越界、是软链接或目标已存在时创建失败；任一步骤失败都回滚已经创建的 Worktree、分支和元数据。

示例：

```json
{
  "id": "server",
  "path": "server",
  "type": "java",
  "workspaceCopyFiles": [".env"]
}
```

`workspaceCopyFiles` 不支持通配符，也不会自动复制其它未跟踪或被 Git 忽略的文件。

工作区只允许 `created`、`active`、`reviewing`、`merged` 四种状态；当前创建流程通常直接进入 `active`。

## Review

Review 只通过 CLI/API 提供，界面没有 Review 按钮。它读取每个 Worktree 的：

- 改动文件数和 diff stat。
- 任务分支相对基准的提交。
- 任务分支是否已推送到远端。
- 基准分支是否已包含任务分支。

Review 会据此更新工作区状态。CLI 响应保留编译、健康检查、测试结果字段，但当前 Review 不主动执行这些检查，默认值为“未记录”。

## 合并代码

这是用户确认后的本地安全操作，不推送远程、不改写历史：

- Worktree 必须干净且停在任务分支。
- 源目录必须干净且停在创建时记录的基准分支。
- 基准分支必须是任务分支的祖先，或者任务分支已经包含在基准分支中。
- 在源目录执行 `git merge --ff-only <任务分支>`。
- 多个项目按配置顺序处理；某个项目失败后，后续项目不再执行。

成功后工作区状态为 `merged`。

## 同步源分支

同步只改 Worktree，不改源目录工作区：

- 只要求 Worktree 干净且停在任务分支，不要求源目录干净或停在基准分支。
- 默认只使用源仓库本地基准分支 ref。
- `--fetch` 或 GUI 勾选远端更新时，对有上游的基准分支执行 `git fetch --prune`。
- 本地基准和上游一方领先时使用较新的一方；两端分叉时拒绝同步。
- 在 Worktree 执行普通 `git merge`，不 rebase、不改写历史。
- 冲突默认执行 `git merge --abort` 回滚；二次确认或 `--keep-conflicts` 才保留冲突供手工处理。
- 成功同步或确认已包含基准提交后，更新 `workspace.json` 对应组件的 `baseCommit`。
- 已合并工作区发生同步后，状态退回 `active`。

## 删除

删除先执行预检：

- 任务目录只能有 `workspace.json`、`AGENTS.md`、`context.md` 和受管理的 Worktree 目录。
- 运行中的服务、Worktree 未提交/未跟踪文件或未知文件都会阻止删除。
- 未合并且没有远端备份的任务分支阻止删除。
- 未合并但已有远端备份时需要确认，并保留任务分支。
- 合并后的任务分支尝试 `git branch -d`；无论分支是否删除，都会移除 Worktree 并执行 `git worktree prune`。
- 只在元数据、Worktree 和任务根目录都能安全处理时删除任务目录；不删除源目录和远端分支。

可能耗时的扫描、Git、文件批处理和工作区生命周期操作不得阻塞 Qt 主线程；GUI 使用 `QThread`，CLI 使用独立工作线程。
