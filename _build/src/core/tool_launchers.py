"""Launch command builders for external coding tools."""
from __future__ import annotations

import os
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

_CODEX_DESKTOP_APP_PATHS = [
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\Codex Desktop.exe"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\Codex Desktop.exe"),
]

_CODEX_DESKTOP_UNINSTALL_ROOTS = [
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def find_powershell() -> str:
    for name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _codex_desktop_candidate(value: str) -> Path | None:
    raw = os.path.expandvars((value or "").strip())
    if not raw:
        return None
    if raw.startswith('"'):
        end = raw.find('"', 1)
        raw = raw[1:end] if end > 1 else raw.strip('"')
    raw = raw.rsplit(",", 1)[0].strip()
    candidate = Path(raw)
    return candidate if candidate.is_file() else None


def find_codex_desktop() -> Path | None:
    """Locate the installed Codex Desktop executable without hard-coding a drive."""
    configured = _codex_desktop_candidate(os.environ.get("CODEX_DESKTOP_EXE", ""))
    if configured:
        return configured

    for hive, subkey in _CODEX_DESKTOP_APP_PATHS:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _kind = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        candidate = _codex_desktop_candidate(str(value))
        if candidate:
            return candidate

    for hive, root in _CODEX_DESKTOP_UNINSTALL_ROOTS:
        try:
            with winreg.OpenKey(hive, root) as uninstall_root:
                subkey_count = winreg.QueryInfoKey(uninstall_root)[0]
                subkeys = [winreg.EnumKey(uninstall_root, index) for index in range(subkey_count)]
        except OSError:
            continue
        for name in subkeys:
            try:
                with winreg.OpenKey(hive, f"{root}\\{name}") as key:
                    display_name = str(winreg.QueryValueEx(key, "DisplayName")[0])
                    if not display_name.lower().startswith("codex desktop"):
                        continue
                    display_icon = str(winreg.QueryValueEx(key, "DisplayIcon")[0])
            except OSError:
                continue
            candidate = _codex_desktop_candidate(display_icon)
            if candidate:
                return candidate
    return None


def open_in_codex_args(target_dir: Path) -> list[str]:
    """Open a new Codex Desktop session for target_dir."""
    executable = find_codex_desktop()
    return [str(executable), "--", "--cwd", str(target_dir)] if executable else []


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
