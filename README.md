# MC Host Manager (Lightweight)

Fast local Minecraft host dashboard with safe lock + backup flow.

## Features

* Start / Stop / Restart Minecraft server
* Safe stop flow:

  * Save world
  * Stop server
  * Create backup
  * Sync world files
  * Release lock safely
* Live server status + console logs
* Real-time CPU / RAM / I/O monitoring
* Backup management + downloads
* Download full current server files as ZIP
* Open server/shared/backups folders directly from UI
* Lightweight architecture focused on stability and low overhead

---

## Lightweight Design

This build intentionally removes heavy multi-layer automation and keeps only core stable logic.

### Included

* Lightweight backend
* Fast polling
* Cached status snapshots
* Minimal background threads
* Minimal API endpoints
* Stable local-first architecture

### Excluded

* Heavy orchestration layers
* Large control panel stack
* Cloud dependency requirements

---

## Safety & Lock System

* Lock is acquired **before** server startup
* Lock heartbeat runs while hosting
* Auto finalize flow runs on unexpected crash/shutdown
* Force clear is blocked while active lock exists
* Safe backup flow protects world consistency

---

## Requirements

* Python 3.10+
* Java installed
* Linux or Windows

---

# Installation

## Install from PyPI (Beta)

```bash
pip install --pre mc-host-manager
```

Specific version:

```bash
pip install mc-host-manager==1.0.0b1
```

---

# Run From Source

Linux:

```bash
bash launch.sh
```

Open in browser:

```text
http://localhost:7842
```

---

# Ready-to-use Package

A packaged ready-to-use app is available in:

```text
release/
```

Example package:

```text
mc-host-manager-ready-to-use-*.zip
```

## Run packaged build

Linux:

```bash
bash launch.sh
```

Windows:

```bat
launch.bat
```

The package already includes:

* App files
* UI assets
* Documentation
* Runtime configuration

---

# Remote Controls (LAN / Another Device)

If dashboard is opened from another device, control actions require matching `project_key`.

## Security Logic

* Project key is shown in Settings
* Same key on both screens allows control
* Prevents unauthorized remote control requests

---

# Build

Linux:

```bash
bash build.sh
```

Windows:

```bat
build.bat
```

---

# Release Notes

Current release:

```text
v1.0.0b1 (Beta)
```

This is an early beta release and may contain unfinished features or bugs.

Feedback and bug reports are welcome.

---

# Repository

GitHub Repository:

[mc-host-manager GitHub Repository](https://github.com/shivam238/mc-host-manager?utm_source=chatgpt.com)

PyPI Package:

[mc-host-manager on PyPI](https://pypi.org/project/mc-host-manager/?utm_source=chatgpt.com)
