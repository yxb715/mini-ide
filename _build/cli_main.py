"""mini-ide 控制台 CLI 入口。

GUI 版 ``mini-ide.exe`` 使用 Windows GUI 子系统；部分终端和外部 AI 不会可靠
等待 GUI 子系统进程，也可能捕获不到它的 stdout。CLI 必须单独打成 console 子系统，
才能保证调用方一直阻塞到服务端返回编译/健康检查结果，并拿到真实退出码。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from src.core.cli_client import run_cli
        return run_cli(sys.argv)
    except Exception as exc:
        try:
            from src.core.cli_client import _safe_write
            _safe_write(
                sys.stderr,
                json.dumps({
                    "ok": False,
                    "error": f"cli failed: {type(exc).__name__}: {exc}",
                }, ensure_ascii=True) + "\n",
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
