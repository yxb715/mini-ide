"""系统通知：用 QSystemTrayIcon 显示托盘气泡"""
from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon, QApplication


_TRAY: QSystemTrayIcon | None = None


def _get_tray() -> QSystemTrayIcon | None:
    global _TRAY
    if _TRAY is not None:
        return _TRAY
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    app = QApplication.instance()
    icon = app.windowIcon() if app else QIcon()
    if icon.isNull():
        from PySide6.QtWidgets import QStyle
        icon = app.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
    _TRAY = QSystemTrayIcon(icon)
    _TRAY.setToolTip("mini-ide")
    _TRAY.show()
    return _TRAY


def notify_info(title: str, message: str) -> None:
    tray = _get_tray()
    if tray:
        tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)


def notify_success(title: str, message: str) -> None:
    tray = _get_tray()
    if tray:
        tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)


def notify_error(title: str, message: str) -> None:
    tray = _get_tray()
    if tray:
        tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Critical, 7000)
