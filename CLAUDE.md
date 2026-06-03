# mini-ide

AI 时代的开发指挥台——本机 GUI，管理多个项目的启动/停止/日志/Git 状态。

## 技术栈

Python 3.11+ / PySide6 6.7+ / Pygments / psutil / watchdog / sqlparse。打包用 PyInstaller。

## 目录结构

所有源码在 `_build/` 下，根目录只放 `mini-ide.exe` + 文档。

```
_build/
├── main.py                 # 入口（CLI 分流 + 单实例 + GUI）
├── src/core/               # 纯逻辑层
│   ├── cli_client.py       # CLI 客户端（解析参数 → IPC 发命令 → 等响应）
│   ├── cli_server.py       # CLI 服务端（收 JSON 命令 → 路由到 ProjectTab）
│   ├── config.py           # AppConfig 持久化
│   ├── project_detector.py # 项目类型识别 → RunProfile
│   ├── process_runner.py   # QProcess 封装
│   ├── log_classifier.py   # 日志行分类
│   ├── file_index.py       # 文件索引
│   ├── git_ops.py          # git 命令封装
│   └── ...
├── src/ui/                 # Qt UI 组件
├── src/util/               # 工具层
└── scripts/
    ├── build.bat           # 一键打包 exe
    └── _smoke.py           # 冒烟测试
```

## 启动与打包

| 场景 | 命令 |
|---|---|
| 日常使用 | 双击根目录 `mini-ide.exe` |
| 开发态 | `cd _build && poetry run python main.py` |
| 打包 exe | `_build\scripts\build.bat`（需先关闭 mini-ide） |
| 冒烟测试 | `cd _build && poetry run python scripts/_smoke.py` |

## CLI 接口（外部 AI 工具可调用）

mini-ide 支持通过命令行控制服务生命周期。CLI 命令通过 IPC 发送给已运行的 GUI 实例：

```bash
mini-ide.exe --list-projects
mini-ide.exe --list-modules <project>
mini-ide.exe --start <project> [module]
mini-ide.exe --stop <project> [module]
mini-ide.exe --restart <project> [module]
mini-ide.exe --health <project> [module] [--timeout 60]
mini-ide.exe --compile <project>
mini-ide.exe --log <project> [module] [--tail N]
```

- `<project>` 按目录名模糊匹配（包含即可，不区分大小写）
- `<module>` 精确匹配模块名（多模块 Spring Boot 项目）
- 单模块项目不传 module；多模块不传 module 时操作所有模块
- 输出：stdout JSON；退出码 0=成功 1=失败 2=参数错误 3=未运行 4=超时
- IPC 协议：JSON 单行请求/响应，兼容老协议（非 `{` 开头按项目路径处理）

## 工作流规则

- **修改代码后必须重新打包**：跑 `_build\scripts\build.bat` 更新 `mini-ide.exe`（需先关闭正在运行的 mini-ide）
- **冒烟测试**：改完代码先跑 `_smoke.py` 确认 28 模块 import + 0 hex hits
- **禁止 hardcode 颜色**：所有颜色/圆角/间距从 `theme.py` token 拿
- **子进程必须加 `CREATE_NO_WINDOW`**
- **耗时操作必须放 QThread**
- **不引入新依赖**（CLI 用标准库 + Qt 的 QLocalSocket）

