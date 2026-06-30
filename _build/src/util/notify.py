"""应用内轻提示。"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget


_HOST: QWidget | None = None
_TOAST = None


def install(host: QWidget) -> None:
    global _HOST, _TOAST
    _HOST = host
    _TOAST = None


def _valid_host() -> QWidget | None:
    global _HOST
    if _HOST is not None:
        try:
            if _HOST.isVisible():
                return _HOST
        except RuntimeError:
            _HOST = None
    app = QApplication.instance()
    if not app:
        return None
    for widget in app.topLevelWidgets():
        if isinstance(widget, QMainWindow) and widget.isVisible():
            central = widget.centralWidget()
            if central is not None:
                return central
    return None


def _get_toast():
    global _TOAST
    host = _valid_host()
    if host is None:
        return None
    try:
        if _TOAST is not None and _TOAST.parentWidget() is host:
            return _TOAST
    except RuntimeError:
        _TOAST = None
    from src.ui.toast import AppToast
    _TOAST = AppToast(host)
    return _TOAST


def notify_info(title: str, message: str) -> None:
    toast = _get_toast()
    if toast:
        toast.show_message("info", title, message, 2500)


def notify_success(title: str, message: str) -> None:
    toast = _get_toast()
    if toast:
        toast.show_message("success", title, message, 2500)


def notify_error(title: str, message: str) -> None:
    toast = _get_toast()
    if toast:
        toast.show_message("error", title, message, 4500)
