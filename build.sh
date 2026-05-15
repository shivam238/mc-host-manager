#!/usr/bin/env bash
# One command full pipeline (deps + build + package).
# Usage: bash build.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -x "./venv/bin/python" ]]; then
  PYTHON="./venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "Error: python3 not found. Install Python 3.11+ and retry."
  exit 1
fi

echo "[1/6] Python: $("$PYTHON" -c 'import sys; print(sys.executable)')"

echo "[2/6] Ensuring build dependencies (pyinstaller, requests)"
"$PYTHON" -m pip install --upgrade pip >/dev/null
"$PYTHON" -m pip install pyinstaller requests >/dev/null

echo "[3/6] Cleaning old build artifacts"
rm -rf build dist
rm -f mc-host-manager.spec host_manager.spec

echo "[4/6] Building single executable"
EXTRA_DATA=()
if [[ -d "bin" ]]; then
  EXTRA_DATA+=(--add-data "bin:bin")
fi
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name mc-host-manager \
  --add-data "ui.html:." \
  --add-data "utils:utils" \
  "${EXTRA_DATA[@]}" \
  --hidden-import zipfile \
  host_manager.py

echo "[5/6] Creating release package"
mkdir -p release
VERSION="$(date +%Y%m%d-%H%M%S)"
PKG_DIR="release/mc-host-manager-linux-${VERSION}"
rm -rf "$PKG_DIR"
mkdir -p "$PKG_DIR"
cp dist/mc-host-manager "$PKG_DIR/"
cp README.md SETUP.md LICENSE.md "$PKG_DIR/"

ARCHIVE="release/mc-host-manager-linux-${VERSION}.zip"
if command -v zip >/dev/null 2>&1; then
  (cd release && zip -r "$(basename "$ARCHIVE")" "$(basename "$PKG_DIR")" >/dev/null)
else
  ARCHIVE="release/mc-host-manager-linux-${VERSION}.tar.gz"
  (cd release && tar -czf "$(basename "$ARCHIVE")" "$(basename "$PKG_DIR")")
fi

echo "[6/6] Creating installer"
"$PYTHON" "$ROOT_DIR/make_single_file_installers.py" \
  --linux-bin "$ROOT_DIR/dist/mc-host-manager" \
  --output "$ROOT_DIR/release" || echo "Warning: installer generation failed."

echo
echo "Build complete:"
echo "  Executable: $ROOT_DIR/dist/mc-host-manager"
echo "  Package:    $ROOT_DIR/$ARCHIVE"
echo "  Installer:  $ROOT_DIR/release/mc-host-manager-installer-linux.sh"
