# Setup Guide (User Friendly)

Project Owner: **Shivam Kumar Mahto**

## Step 1: Install Dependencies

### Required
- Java (`java -version` should work)
- Python 3.11+ (`python3 --version` on Linux/macOS, `python --version` on Windows)

### Recommended
- Syncthing installed and running

## Step 2: Prepare Folders
- Create a local server folder, example: `~/mc-server`
- Create a shared sync folder, example: `~/mc-shared`
- Keep these two folders different.

## Step 3: Launch App

### Linux/macOS
```bash
bash launch.sh
```

### Windows
```bat
launch.bat
```

Open: `http://localhost:7842`

## Step 4: Initial Dashboard Setup
In setup/options panel fill:
- User name
- Server Folder (local server files path)
- Shared Folder (Syncthing shared path)
- RAM (2G/4G/6G/8G or custom MiB)

Save settings.

Tip:
- If anything looks wrong, open Setup Wizard and click **Auto Fix**.
- Auto Fix now repairs common issues automatically:
  - shared/server path recovery
  - project key marker sync
  - server jar auto-selection
  - max players / whitelist sync to `server.properties`
  - Syncthing ensure + scan trigger

## Step 5: Syncthing Pairing (Multi-PC)
1. Open Syncthing UI: `http://localhost:8384`
2. Add/accept remote device
3. Share the same folder used as `Shared Folder` in app
4. Accept incoming folder on other PC

Project key is auto-managed by app (`.mc_project_key`), manual key entry not needed.

## Step 6: Daily Safe Flow
- Start host from dashboard Start button
- Stop host from dashboard Stop button
- Restart only when server shows `RUNNING` (safe sequence is automatic)
- Wait for finalize completion (backup + sync)

Do not hard-kill during finalize unless emergency.

## Multi-PC Friendly Control
- Sidebar -> **Host on Another PC**
- You can select an active synced machine and trigger:
  - `Start There`
  - `Stop There`
- Requirement: both machines must run this app + Syncthing with same shared folder and project key.

## If Folder Open Fails
Go to **Access** panel and set optional manual paths:
- Manual Server Folder
- Manual Shared/World Folder
- Manual Backups Folder
- Manual Crash Reports Folder

Click **Save Manual Paths** and retry folder action.

## Build Executables

### Linux/macOS
```bash
bash build.sh
```

### Windows
```bat
build.bat
```

## Create Shareable App Package
- One-click (recommended), Linux/macOS:
```bash
bash release.sh
```
- One-click (recommended), Windows:
```bat
release.bat
```

- Manual (advanced), Linux/macOS:
```bash
bash package_release.sh
```
- Manual (advanced), Windows:
```bat
package_release.bat
```

This generates a ready-to-share archive in `release/` so you do not need to send full source code every time.
It also generates a single-file installer:
- Windows: `release/mc-host-manager-installer-windows.bat`
- Linux: `release/mc-host-manager-installer-linux.sh`

## Common Issues
- `Folder path is not configured`:
  - Save correct paths in Options/Access
- `Server start failed`:
  - Verify Java and server jar path
- `Syncthing not connected`:
  - Check `http://localhost:8384` and pending device/folder approvals

## Auto Cleanup
- App now auto-cleans stale temp/control files in background.
- It does **not** delete active server data/world files.

## Publish to GitHub
If you want this project on GitHub as a standalone repo:

1. Create a new empty repository on GitHub (no README/license in that repo).
2. Run these commands inside this project folder:

```bash
cd /home/shivam/programming/Projects/mc-launcher
git init
git add .
git commit -m "Initial stable release"
git branch -M main
git remote add origin <YOUR_GITHUB_REPO_URL>
git push -u origin main
```

If your GitHub account uses 2FA, use a Personal Access Token when git asks for password.
