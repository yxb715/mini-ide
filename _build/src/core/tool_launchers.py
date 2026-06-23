"""Launch command builders for external coding tools."""
from __future__ import annotations

import shutil
import winreg
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

_CC_REGISTRY_COMMANDS = [
    (winreg.HKEY_CURRENT_USER, r"Software\Classes\Directory\shell\Claude Code\command"),
    (winreg.HKEY_CURRENT_USER, r"Software\Classes\Directory\Background\shell\Claude Code\command"),
    (winreg.HKEY_CLASSES_ROOT, r"Directory\shell\Claude Code\command"),
    (winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell\Claude Code\command"),
]


def find_powershell() -> str:
    for name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def open_in_codex_args(target_dir: Path) -> list[str]:
    """Build a portable Codex launcher for the current machine."""
    target = str(target_dir)
    wt = shutil.which("wt.exe") or shutil.which("wt")
    ps = find_powershell()
    if wt and ps:
        return [wt, "-w", "0", "new-tab", "-d", target, ps, "-NoExit", "-NoLogo", "-Command", "codex"]

    cmd = shutil.which("cmd.exe") or "cmd.exe"
    if ps:
        return [cmd, "/c", "start", "", "/D", target, ps, "-NoExit", "-NoLogo", "-Command", "codex"]
    return [cmd, "/c", "start", "", "/D", target, cmd, "/k", "codex"]


def open_in_cc_command(target_dir: Path) -> str:
    """Return the command used to open Claude Code in target_dir."""
    target = str(target_dir)
    for hive, subkey in _CC_REGISTRY_COMMANDS:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                command, _kind = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if command:
            return command.replace("%1", target).replace("%V", target)

    wt = shutil.which("wt.exe") or shutil.which("wt")
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if wt and pwsh:
        pwsh_name = Path(pwsh).name
        return f'"{wt}" -d "{target}" {pwsh_name} -NoExit -Command claude'
    ps = shutil.which("powershell.exe") or shutil.which("powershell")
    if ps:
        target_literal = "'" + target.replace("'", "''") + "'"
        ps_command = f"Set-Location -LiteralPath {target_literal}; claude"
        return (
            f'"{ps}" -NoExit -NoLogo -NoProfile '
            f'-ExecutionPolicy Bypass -Command "{ps_command}"'
        )
    return ""
