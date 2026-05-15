#!/usr/bin/env python3
"""
Generate single-file installer scripts that embed the built executable payload.

Windows output: .bat installer (self-extracting)
Linux output:   .sh installer (self-extracting)
"""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
from datetime import datetime


def _read_b64_lines(binary_path: Path, width: int = 120) -> list[str]:
    raw = binary_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return [b64[i:i + width] for i in range(0, len(b64), width)]


def _build_windows_installer(output_path: Path, payload_lines: list[str]) -> None:
    lines: list[str] = [
        "@echo off",
        "setlocal EnableExtensions",
        "chcp 65001 >nul",
        'set "APP_NAME=MC Host Manager"',
        'set "INSTALL_DIR=%LOCALAPPDATA%\\MC-Host-Manager"',
        'set "EXE_PATH=%INSTALL_DIR%\\mc-host-manager.exe"',
        'set "TMP_B64=%TEMP%\\mc_host_manager_payload.b64"',
        'set "TMP_EXE=%TEMP%\\mc_host_manager_payload.exe"',
        "echo [INFO] Installing %APP_NAME%...",
        'if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"',
        '> "%TMP_B64%" (',
    ]
    lines.extend([f"  echo {chunk}" for chunk in payload_lines])
    lines.extend(
        [
            ")",
            (
                'powershell -NoProfile -ExecutionPolicy Bypass -Command '
                '"$raw = Get-Content -Raw -Path $env:TMP_B64; '
                '[IO.File]::WriteAllBytes($env:TMP_EXE, [Convert]::FromBase64String($raw))"'
            ),
            "if errorlevel 1 (",
            "  echo [ERROR] Failed to decode installer payload.",
            '  del /q "%TMP_B64%" >nul 2>&1',
            "  pause",
            "  exit /b 1",
            ")",
            'copy /y "%TMP_EXE%" "%EXE_PATH%" >nul',
            "if errorlevel 1 (",
            "  echo [ERROR] Failed to copy executable into install folder.",
            '  del /q "%TMP_B64%" >nul 2>&1',
            '  del /q "%TMP_EXE%" >nul 2>&1',
            "  pause",
            "  exit /b 1",
            ")",
            'del /q "%TMP_B64%" >nul 2>&1',
            'del /q "%TMP_EXE%" >nul 2>&1',
            (
                'powershell -NoProfile -ExecutionPolicy Bypass -Command '
                '"$ws=New-Object -ComObject WScript.Shell; '
                "$lnk=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\\\\MC Host Manager.lnk'); "
                '$lnk.TargetPath=$env:EXE_PATH; '
                '$lnk.WorkingDirectory=$env:INSTALL_DIR; '
                "$lnk.IconLocation=$env:EXE_PATH + ',0'; "
                '$lnk.Save()" >nul 2>&1'
            ),
            'setx MC_HOST_MANAGER_EXE "%EXE_PATH%" >nul 2>&1',
            "echo [OK] Install complete.",
            "echo [INFO] Launching app...",
            'start "" "%EXE_PATH%"',
            "echo [INFO] Dashboard URL: http://localhost:7842",
            "pause",
        ]
    )
    output_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def _build_linux_installer(output_path: Path, payload_lines: list[str]) -> None:
    lines: list[str] = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'APP_NAME="MC Host Manager"',
        'INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/mc-host-manager"',
        'BIN_PATH="$INSTALL_DIR/mc-host-manager"',
        'LOCAL_BIN_DIR="$HOME/.local/bin"',
        'LAUNCHER_PATH="$LOCAL_BIN_DIR/mc-host-manager"',
        'DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"',
        'DESKTOP_FILE="$DESKTOP_DIR/mc-host-manager.desktop"',
        'PAYLOAD_B64="$INSTALL_DIR/.mc_host_manager_payload.b64"',
        "",
        'echo "[INFO] Installing ${APP_NAME}..."',
        'mkdir -p "$INSTALL_DIR" "$LOCAL_BIN_DIR" "$DESKTOP_DIR"',
        "",
        'cat > "$PAYLOAD_B64" <<\'__MC_HOST_MANAGER_PAYLOAD__\'',
    ]
    lines.extend(payload_lines)
    lines.extend(
        [
            "__MC_HOST_MANAGER_PAYLOAD__",
            "",
            "if command -v base64 >/dev/null 2>&1; then",
            '  if ! base64 --decode "$PAYLOAD_B64" > "$BIN_PATH" 2>/dev/null; then',
            '    base64 -d "$PAYLOAD_B64" > "$BIN_PATH"',
            "  fi",
            "else",
            "  python3 - \"$PAYLOAD_B64\" \"$BIN_PATH\" <<'PY'",
            "import base64",
            "import pathlib",
            "import sys",
            "src = pathlib.Path(sys.argv[1])",
            "dst = pathlib.Path(sys.argv[2])",
            "dst.write_bytes(base64.b64decode(src.read_text(encoding='utf-8')))",
            "PY",
            "fi",
            'chmod +x "$BIN_PATH"',
            'rm -f "$PAYLOAD_B64"',
            "",
            'cat > "$LAUNCHER_PATH" <<EOF',
            "#!/usr/bin/env bash",
            'exec "$BIN_PATH" "$@"',
            "EOF",
            'chmod +x "$LAUNCHER_PATH"',
            "",
            'cat > "$DESKTOP_FILE" <<EOF',
            "[Desktop Entry]",
            "Type=Application",
            "Name=MC Host Manager",
            "Comment=Minecraft host manager dashboard",
            'Exec=$BIN_PATH',
            "Icon=utilities-terminal",
            "Terminal=false",
            "Categories=Game;Utility;",
            "EOF",
            "",
            'echo "[OK] Install complete."',
            'echo "[INFO] Binary: $BIN_PATH"',
            'echo "[INFO] Launcher: $LAUNCHER_PATH"',
            'echo "[INFO] Dashboard URL: http://localhost:7842"',
            'nohup "$BIN_PATH" >/dev/null 2>&1 &',
        ]
    )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output_path.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create single-file installers.")
    parser.add_argument("--windows-bin", type=Path, help="Path to built Windows exe.")
    parser.add_argument("--linux-bin", type=Path, help="Path to built Linux binary.")
    parser.add_argument("--output", type=Path, default=Path("release"), help="Output folder for installers.")
    parser.add_argument("--timestamp", action="store_true", help="Append timestamp to installer filenames.")
    args = parser.parse_args()

    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") if args.timestamp else ""

    produced: list[Path] = []

    if args.windows_bin:
        windows_bin = args.windows_bin.resolve()
        if not windows_bin.exists():
            raise FileNotFoundError(f"Windows binary not found: {windows_bin}")
        name = "mc-host-manager-installer-windows.bat"
        if stamp:
            name = f"mc-host-manager-installer-windows-{stamp}.bat"
        out = output_dir / name
        _build_windows_installer(out, _read_b64_lines(windows_bin))
        produced.append(out)

    if args.linux_bin:
        linux_bin = args.linux_bin.resolve()
        if not linux_bin.exists():
            raise FileNotFoundError(f"Linux binary not found: {linux_bin}")
        name = "mc-host-manager-installer-linux.sh"
        if stamp:
            name = f"mc-host-manager-installer-linux-{stamp}.sh"
        out = output_dir / name
        _build_linux_installer(out, _read_b64_lines(linux_bin))
        produced.append(out)

    if not produced:
        raise SystemExit("No installer generated. Pass --windows-bin and/or --linux-bin.")

    for p in produced:
        print(f"[OK] Created installer: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
