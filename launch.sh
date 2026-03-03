#!/usr/bin/env bash
# MC Host Manager - Source launcher (Linux/macOS)
# Usage: bash launch.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo
echo "  MC Host Manager"
echo "  ============================="

# Prefer project venv for predictable behavior.
if [[ -x "$SCRIPT_DIR/venv/bin/python" ]]; then
  PYTHON="$SCRIPT_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "  [ERROR] python3 not found. Install Python 3.11+ and retry."
  exit 1
fi

echo "  [INFO] Python: $("$PYTHON" -c 'import sys; print(sys.executable)')"

# Ensure required runtime dependency.
if ! "$PYTHON" -c "import requests" >/dev/null 2>&1; then
  echo "  [INFO] Installing missing dependency: requests"
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install requests
fi

PORT=7842
if command -v curl >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:${PORT}/status" >/dev/null 2>&1; then
  echo "  [INFO] App already running on http://localhost:${PORT}"
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:${PORT}" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "http://localhost:${PORT}" >/dev/null 2>&1 || true
  fi
  exit 0
fi

echo "  [OK] Starting on http://localhost:${PORT}"
echo

exec "$PYTHON" "$SCRIPT_DIR/host_manager.py"
