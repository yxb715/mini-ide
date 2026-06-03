#!/usr/bin/env bash
# mini-ide one-click build script for macOS / Linux
# (self-contained: auto venv + deps + app bundle)
#
# Requires Python 3.11+ on PATH.
# Output:
#   macOS: dist/mini-ide.app  (+ symlink ../mini-ide.app)
#   Linux: dist/mini-ide      (+ symlink ../mini-ide)
set -euo pipefail

# ---------- 0. Locate dirs ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BUILD_DIR"
VENV_DIR="$BUILD_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"

# ---------- 1. Locate system Python ----------
SYS_PY=""
if command -v python3 >/dev/null 2>&1; then SYS_PY="python3"
elif command -v python >/dev/null 2>&1; then SYS_PY="python"
else
  echo "[build] ERROR: python3 not found. Install Python 3.11+ first." >&2
  exit 1
fi

# ---------- 2. Create venv if missing ----------
if [ ! -x "$VENV_PY" ]; then
  echo "[build] Creating venv at $VENV_DIR ..."
  "$SYS_PY" -m venv "$VENV_DIR"
fi

# ---------- 3. Install deps if missing ----------
if ! "$VENV_PY" -c "import PySide6, psutil, watchdog, sqlparse, pygments, PyInstaller" >/dev/null 2>&1; then
  echo "[build] Installing dependencies (first run, may take minutes) ..."
  export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
  export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet PySide6 psutil watchdog sqlparse Pygments pyinstaller Pillow
fi

# ---------- 4. Generate icons if missing ----------
if [ ! -f "src/resources/icon.png" ]; then
  echo "[build] Generating icon ..."
  "$VENV_PY" scripts/make_icon.py || true
fi

# macOS app bundles want a .icns; build one from the PNG via native tools.
PYI_ICON_ARGS=()
OS_NAME="$(uname -s)"
if [ "$OS_NAME" = "Darwin" ]; then
  ICNS_PATH="src/resources/icon.icns"
  if [ ! -f "$ICNS_PATH" ] && [ -f "src/resources/icon.png" ] \
     && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
    echo "[build] Building .icns from icon.png ..."
    ICONSET="$(mktemp -d)/icon.iconset"
    mkdir -p "$ICONSET"
    for sz in 16 32 64 128 256 512; do
      sips -z $sz $sz "src/resources/icon.png" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null 2>&1 || true
      dbl=$((sz * 2))
      sips -z $dbl $dbl "src/resources/icon.png" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1 || true
    done
    iconutil -c icns "$ICONSET" -o "$ICNS_PATH" >/dev/null 2>&1 || true
  fi
  [ -f "$ICNS_PATH" ] && PYI_ICON_ARGS=(--icon "$ICNS_PATH")
fi

# ---------- 5. Clean old artifacts ----------
echo "[build] Cleaning old artifacts ..."
rm -rf pyinstaller-work dist mini-ide.spec
rm -rf ../mini-ide.app ../mini-ide

# ---------- 6. Build ----------
# 注意：PyInstaller 不支持交叉编译，必须在目标平台（这台机器）上构建。
# --add-data 在类 Unix 下用冒号分隔（Windows 用分号）。
echo "[build] Running PyInstaller ..."
if [ "$OS_NAME" = "Darwin" ]; then
  # macOS: --windowed 产出 .app 包
  "$VENV_PY" -m PyInstaller \
    --noconfirm --clean --onedir --windowed \
    --name mini-ide \
    "${PYI_ICON_ARGS[@]}" \
    --add-data "src:src" \
    --hidden-import psutil \
    --hidden-import watchdog \
    --hidden-import sqlparse \
    --hidden-import pygments \
    --workpath pyinstaller-work \
    --specpath . \
    main.py
  # 软链接放在项目根，目标相对路径要从项目根解析（真身在 _build/dist/ 下）。
  # 旧版写 "dist/mini-ide.app" 会断链（根目录无 dist/），Finder 报"找不到原始项目"。
  ln -snf "_build/dist/mini-ide.app" "../mini-ide.app"
  echo
  echo "[build] Done. Output: dist/mini-ide.app (symlinked at project root)"
  echo "[build] 首次打开被 Gatekeeper 拦截时，右键 → 打开，或运行："
  echo "        xattr -dr com.apple.quarantine ../mini-ide.app"
else
  # Linux: 单文件可执行
  "$VENV_PY" -m PyInstaller \
    --noconfirm --clean --onefile \
    --name mini-ide \
    --add-data "src:src" \
    --hidden-import psutil \
    --hidden-import watchdog \
    --hidden-import sqlparse \
    --hidden-import pygments \
    --distpath .. \
    --workpath pyinstaller-work \
    --specpath . \
    main.py
  echo
  echo "[build] Done. Output: mini-ide (project root)"
fi
