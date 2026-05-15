# MC Host Manager (Lightweight)

Fast local Minecraft host dashboard with safe lock + backup flow.

## What It Does

- Start / Stop / Restart server
- Safe stop flow: `save -> stop -> backup -> sync world -> unlock`
- Live status + console logs
- Real-time CPU/RAM/I/O bars (server process based)
- Backup list + download
- Download full current server files (ZIP)
- Open server/shared/backups folders from UI

## Lightweight Design

This build intentionally removes heavy multi-layer automation and keeps only core stable logic.

- Minimal background threads
- Minimal endpoints
- No large control-panel stack
- Fast polling with lightweight cached status snapshots

## Key Logic

- Lock is acquired **before** start sequence
- Lock heartbeat runs while hosting
- On crash while hosting, auto finalize flow runs to release lock safely
- Force clear is blocked if active lock exists

## Run (source)

```bash
bash launch.sh
```

Open: `http://localhost:7842`

## Controls From Another Screen/PC

If dashboard is opened from another device, control actions require matching `project_key`.

- Project key is shown in Settings
- Same key on both screens allows control

## Build

```bash
bash build.sh
```

Windows:

```bat
build.bat
```
