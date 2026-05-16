@echo off
setlocal

rem mini-ide one-click build script (self-contained: auto venv + deps + exe)
rem Requires Python 3.11+ on PATH. Output: mini-ide.exe in project root.

cd /d "%~dp0\.."
set "BUILD_DIR=%CD%"
set "VENV_DIR=%BUILD_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

rem ---------- 1. Locate system Python ----------
set "SYS_PY="
where python >nul 2>nul && set "SYS_PY=python"
if "%SYS_PY%"=="" where py >nul 2>nul && set "SYS_PY=py -3"
if "%SYS_PY%"=="" goto :no_python

rem ---------- 2. Create venv if missing ----------
if exist "%VENV_PY%" goto :venv_ready
echo [build] Creating venv at %VENV_DIR% ...
%SYS_PY% -m venv "%VENV_DIR%"
if errorlevel 1 goto :fail_venv
:venv_ready

rem ---------- 3. Install deps if missing ----------
"%VENV_PY%" -c "import PySide6, psutil, watchdog, sqlparse, pygments, PyInstaller" >nul 2>nul
if not errorlevel 1 goto :deps_ready
echo [build] Installing dependencies (first run, may take minutes) ...
set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PIP_DEFAULT_TIMEOUT=120"
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet PySide6 psutil watchdog sqlparse Pygments pyinstaller Pillow
if errorlevel 1 goto :fail_deps
:deps_ready

rem ---------- 4. Generate icon if missing ----------
if exist "src\resources\icon.ico" goto :icon_ready
echo [build] Generating icon ...
"%VENV_PY%" scripts\make_icon.py
if errorlevel 1 goto :fail_icon
:icon_ready

rem ---------- 5. Clean old artifacts ----------
echo [build] Cleaning old artifacts ...
if exist pyinstaller-work rmdir /s /q pyinstaller-work
if exist dist rmdir /s /q dist
if exist mini-ide.spec del /q mini-ide.spec
if exist ..\mini-ide.exe del /q ..\mini-ide.exe

rem ---------- 6. Build ----------
echo [build] Running PyInstaller ...
"%VENV_PY%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --name mini-ide ^
    --windowed ^
    --icon src\resources\icon.ico ^
    --add-data "src;src" ^
    --hidden-import psutil ^
    --hidden-import watchdog ^
    --hidden-import sqlparse ^
    --hidden-import pygments ^
    --distpath .. ^
    --workpath pyinstaller-work ^
    --specpath . ^
    main.py
if errorlevel 1 goto :fail_build

echo.
echo [build] Done. Output: mini-ide.exe (project root)
endlocal
exit /b 0

:no_python
echo [build] ERROR: python not found. Install Python 3.11+ first.
exit /b 1
:fail_venv
echo [build] venv creation failed
exit /b 1
:fail_deps
echo [build] dep install failed
exit /b 1
:fail_icon
echo [build] icon generation failed
exit /b 1
:fail_build
echo [build] PyInstaller failed
exit /b 1
