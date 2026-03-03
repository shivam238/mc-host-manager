# MC Host Manager

Built and maintained by **Shivam Kumar Mahto**.

Minecraft server host dashboard with safe handover flow:
- one active host lock
- controlled start/stop
- backup + sync finalize
- player and log controls
- Linux/Windows executable build scripts

## Highlights
- Web UI on `http://localhost:7842`
- Start/Stop/Restart/Kill controls with progress
- Stop/finalize flow: save -> backup -> sync -> unlock
- Unexpected stop recovery monitor
- Manual folder override support (Access panel)
- Syncthing status + sync trigger
- Backup history + restore/download
- **Host on Another PC** (sidebar): select active machine and trigger start/stop there
- **Join Code onboarding** (`MC-HOST://...`): generate on host, paste on friend PC for auto setup apply
- Syncthing pending requests auto-accept (trusted-device guarded via project join flow)
- Enhanced **Auto Fix**: diagnostics-driven self-heal (paths, jar selection, sync setup)
- Stability hardening:
  - atomic config save/load with corruption fallback
  - thread-safe player tracking
  - atomic background task runner (prevents duplicate concurrent operations)
  - periodic auto-cleanup for stale temp/control artifacts

## Project Files
- `host_manager.py` - backend + orchestration
- `ui.html` - frontend dashboard
- `utils/` - lock, backup, server, sync, tunnel modules
- `launch.sh` / `launch.bat` - run app
- `build.sh` / `build.bat` - one command for full release (deps + build + package)

## Requirements
- Python 3.11+ (3.12 tested)
- Java in PATH
- Syncthing (app can try portable fallback)
- Optional: `psutil` for better system metrics

## Quick Start
1. Run app:
   - Linux/macOS: `bash launch.sh`
   - Windows: `launch.bat`
2. Open `http://localhost:7842`
3. Configure:
   - `Server Folder` (local server files)
   - `Shared Folder` (synced folder)
   - RAM
   - Optional: use `Join Code` to auto-apply project setup on another PC
4. Start host from dashboard.
5. Optional: use `Host on Another PC` from sidebar to trigger hosting on target machine.

## Dev/Test Without Rebuild
- You do not need to run build for normal testing.
- Use source launcher directly:
  - Linux/macOS: `bash launch.sh`
  - Windows: `launch.bat`

## Safe Usage Rules
- Always stop from UI Stop button for safe finalize.
- Use Restart button only after status becomes `RUNNING` (it performs safe stop -> backup/sync -> start).
- Avoid hard kill/power cut during finalize.
- Keep `server_dir` and `shared_dir` different.
- Keep Syncthing running on both machines for lock/control propagation.

## Build Executable

### Linux/macOS
```bash
bash build.sh
```
Outputs:
- `dist/mc-host-manager`
- `release/` package zip/tar + installer

### Windows
```bat
build.bat
```
Outputs:
- `dist\mc-host-manager.exe`
- `release\` package zip + installer

Note: build on target OS. Linux build cannot produce native Windows `.exe`.

## Share As App (No Source Folder Needed)
- Recommended one-click:
  - Linux/macOS: `bash build.sh`
  - Windows: `build.bat`
- Output goes to `release/` as a timestamped archive you can directly share.
- Also generates a single-file installer:
  - Windows: `release/mc-host-manager-installer-windows.bat`
  - Linux: `release/mc-host-manager-installer-linux.sh`
- User config is kept backward-compatible:
  - existing `app_data/` near old runs is reused automatically
  - installed executable can fall back to per-user config folder when install path is read-only

## Validation Done (this environment)
- `python3 -m py_compile host_manager.py utils/*.py` passed
- `ui.html` script syntax check passed
- Linux one-file build completed (`dist/mc-host-manager`)

Runtime HTTP smoke test could not be fully executed in this sandbox due bind restriction (`Errno 1 Operation not permitted`), but local machine run is expected to work.

## License
See [LICENSE.md](LICENSE.md).

## GitHub Publish
Use the steps in [SETUP.md](SETUP.md) under `Publish to GitHub`.
