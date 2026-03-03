#!/usr/bin/env bash
# One-click full pipeline: dependency check + build + release package.
# Usage: bash release.sh

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

echo "[release 1/3] Python: $("$PYTHON" -c 'import sys; print(sys.executable)')"
echo "[release 2/3] Ensuring packaging dependencies"
"$PYTHON" -m pip install --upgrade pip >/dev/null
"$PYTHON" -m pip install pyinstaller requests >/dev/null

echo "[release 3/3] Building and packaging"
exec bash "$ROOT_DIR/package_release.sh" "$@"
