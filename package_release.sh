#!/usr/bin/env bash
# Build and package a Linux/macOS release archive.
# Usage: bash package_release.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

bash "$ROOT_DIR/build.sh"

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

echo "Release package created: $ROOT_DIR/$ARCHIVE"
python3 "$ROOT_DIR/make_single_file_installers.py" --linux-bin "$ROOT_DIR/dist/mc-host-manager" --output "$ROOT_DIR/release" || \
  echo "Warning: installer generation failed."
echo "Single-file installer: $ROOT_DIR/release/mc-host-manager-installer-linux.sh"
