#!/usr/bin/env bash
# Build one-file executable (Linux/macOS).
# Usage: bash build_executable.sh

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

echo "[1/5] Using Python: $("$PYTHON" -c 'import sys; print(sys.executable)')"

echo "[2/5] Verifying project files"
for p in host_manager.py ui.html utils; do
  if [[ ! -e "$p" ]]; then
    echo "Error: missing required path: $p"
    exit 1
  fi
done

echo "[3/5] Checking build dependencies (pyinstaller, requests)"
if ! "$PYTHON" -c "import PyInstaller, requests" >/dev/null 2>&1; then
  echo "Missing dependency detected. Attempting install..."
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install pyinstaller requests
fi
if ! "$PYTHON" -c "import PyInstaller, requests" >/dev/null 2>&1; then
  echo "Error: required packages not available (pyinstaller, requests)."
  echo "Install them manually and re-run this script."
  exit 1
fi

echo "[4/5] Cleaning old build artifacts"
rm -rf build dist
rm -f mc-host-manager.spec host_manager.spec

echo "[5/5] Building single executable"
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

echo
echo "Build complete:"
echo "  $ROOT_DIR/dist/mc-host-manager"
echo
echo "Run:"
echo "  ./dist/mc-host-manager"
