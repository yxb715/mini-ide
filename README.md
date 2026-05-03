# mini-ide

AI 时代的开发指挥台。Spring Boot / Vue / Python 项目的轻量启动器。

## 安装

```bash
cd G:\whaty\project\tools\mini-ide
poetry install
```

## 运行

```bash
poetry run python main.py
```

## 打包 exe

```bash
build.bat
```

打包后的 exe 位于 `dist/mini-ide.exe`，可直接双击运行。

## 功能

- 多项目 Tab，Workspace 编排
- 自动识别 Spring Boot (Gradle/Maven) / Vue / Python 项目
- 智能日志：错误高亮、堆栈折叠、SQL 美化、错误跳转
- AI 协同：错误一键发给 Claude Code；内置 MCP Server 支持深度集成
- 端口占用自动检测 + 一键释放
- 深色主题

## Claude Code MCP 集成

见 `docs/claude-code-mcp-setup.md`。
