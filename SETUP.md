# Setup Guide (Lightweight)

## 1. Prepare folders

You need 2 folders:

1. **Server Folder** (contains `server.jar` / `run.sh`)
2. **Shared Folder** (Syncthing folder used for backups/world sync)

Shared folder auto-uses:

- `backups/`
- `world_latest/`
- `host.lock`

## 2. Start app

```bash
bash launch.sh
```

Open `http://localhost:7842`

## 3. Save settings

In dashboard Settings:

- User Name
- Project Name
- Server Folder path
- Shared Folder path
- RAM
- Max Players
- Server JAR
- Whitelist toggle

Click **Save**.

## 4. Start hosting

Click **START**.

Flow:

- lock check
- lock acquire
- world copy from `shared/world_latest` (if exists)
- server start

## 5. Stop hosting safely

Click **STOP**.

Flow:

- save + stop server
- backup ZIP in `shared/backups`
- sync world to `shared/world_latest`
- lock release

## 6. Multi-screen control

To control from another screen/device:

- open same dashboard URL
- use same **Project Key** shown in Settings

If key mismatches, control actions are blocked.

## 7. Syncthing

- App does not force heavy Syncthing automation
- `Sync Now` triggers scan only
- Keep Syncthing running externally for cross-device file sync

## Troubleshooting

- **Locked by another host**: stop on host machine first
- **Project key mismatch**: use same key on both screens
- **No JAR found**: fix `Server JAR` in Settings
- **Download failed**: stop server first, then retry
