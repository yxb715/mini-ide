@echo off
setlocal

rem mini-ide 一键打包脚本
rem
rem 产物：dist\mini-ide\mini-ide.exe（整个 dist\mini-ide\ 目录可分发）
rem
rem 首次使用前请先在项目根目录跑：
rem     poetry run pip install pyinstaller
rem
rem 如缺图标，会先用 scripts\make_icon.py 生成 src\resources\icon.ico

cd /d "%~dp0\.."

if not exist "src\resources\icon.ico" (
    echo [build] 没找到 src\resources\icon.ico，先生成...
    poetry run python scripts\make_icon.py
    if errorlevel 1 (
        echo [build] 图标生成失败
        exit /b 1
    )
)

echo [build] 清理旧产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist mini-ide.spec del /q mini-ide.spec

echo [build] 调用 PyInstaller...
poetry run pyinstaller ^
    --noconfirm ^
    --clean ^
    --name mini-ide ^
    --windowed ^
    --icon src\resources\icon.ico ^
    --add-data "src;src" ^
    --collect-all PySide6 ^
    --hidden-import psutil ^
    --hidden-import watchdog ^
    --hidden-import sqlparse ^
    --hidden-import pygments ^
    main.py

if errorlevel 1 (
    echo [build] 打包失败
    exit /b 1
)

echo.
echo [build] 完成。产物：dist\mini-ide\mini-ide.exe
echo [build] 整个 dist\mini-ide\ 目录可拷贝分发
endlocal
