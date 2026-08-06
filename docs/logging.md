# 日志契约

## 文件与轮转

- 目录：`%APPDATA%\mini-ide\logs\`
- 文件：`mini-ide-YYYYMMDD.log`
- 基础 logger：`mini-ide`
- 行为 logger：`mini-ide.action`，通过父 logger 写入同一日志文件。
- 单文件最大 5 MB，保留 3 个轮转副本。
- 启动时清理超过 14 天的日志。
- GUI 菜单“帮助 → 打开 mini-ide 日志”和“打开日志目录”可直接访问。

## CLI 行为日志

CLI 请求统一记录：

~~~text
[CLI] <cmd> params=<参数字典>
[CLI] <cmd> ok
[CLI] <cmd> fail: <错误信息>
[CLI] <cmd> EXCEPTION
~~~

查询当天日志：

~~~powershell
$log = "$env:APPDATA\mini-ide\logs\mini-ide-$(Get-Date -f yyyyMMdd).log"
Select-String "\[CLI\]|\[GUI\]" $log
Select-String "(fail|FAIL|WARNING|ERROR).*\[CLI\]|\[GUI\].*失败" $log
~~~

## GUI 行为日志

当前使用 `[GUI]` 行为前缀记录的操作包括：

~~~text
启动服务、停止服务、重启服务、启动模块、停止模块
进入工作区、创建工作区、同步源分支、合并工作区、删除工作区
~~~

工作区操作会记录成功、失败和冲突回滚；同步还记录是否 fetch 远端、是否保留冲突。服务操作会记录项目、模块或 profile。

启动、打开项目、会话恢复、关闭窗口隐藏到托盘、异常、主线程卡死和托盘不可用等基础运行日志使用 `mini-ide.*` logger，未必带 `[GUI]` 前缀。排查这些问题时应直接查看当天完整日志，不能只依赖 `[GUI]` 筛选。
