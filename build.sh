#!/usr/bin/env bash
# Short alias for build_executable.sh
# Usage: bash build.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT_DIR/build_executable.sh" "$@"

