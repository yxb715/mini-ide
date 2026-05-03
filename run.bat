@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
REM Launch mini-ide via poetry (console visible for debug)
poetry run python main.py
endlocal
