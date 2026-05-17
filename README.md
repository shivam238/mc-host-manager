# MC Host Manager

Lightweight local Minecraft hosting dashboard with safe backup flow, real-time monitoring, and cross-platform support.

---

# Overview

Open: `http://localhost:7842`

## Ready-to-use package

A packaged ready-to-use app is available in `release/` as `mc-host-manager-ready-to-use-*.zip`.

Unzip it and run:

```bash
bash launch.sh
```

The package already includes the app files, UI assets, and docs.

## Controls From Another Screen/PC

If dashboard is opened from another device, control actions require matching `project_key`.

- Project key is shown in Settings
- Same key on both screens allows control

## Build

```bash
bash build.sh
```

**License:** MIT — see [LICENSE.md](LICENSE.md) for full text.

**Support:** For issues or questions, please open an issue on the project's GitHub repository.
This reduces corruption risks during shutdowns or crashes.

---

## Monitoring

Real-time resource monitoring:

* CPU usage
* RAM usage
* Disk I/O activity
* Server process status

---

## Backup System

* Automatic backup creation
* Backup listing
* Backup downloads
* Full server ZIP export

---

## Lightweight Architecture

Designed for low overhead and local hosting.

### Included

* Lightweight backend
* Fast polling system
* Cached status snapshots
* Minimal background workers
* Minimal API surface
* Local-first architecture

### Excluded

* Heavy orchestration layers
* Cloud dependency stack
* Large hosting panel frameworks

---

# Lock & Safety System

The application uses a lock + heartbeat system to prevent unsafe multi-instance hosting.

## Safety Logic

* Lock acquired before startup
* Heartbeat maintained while hosting
* Automatic finalize flow on crash
* Active lock protection
* Safe unlock handling
* Protected force-clear behavior

---

# Requirements

## Linux / Windows

* Python 3.10+
* Java installed
* Minecraft server files

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

<<<<<<< HEAD
=======
Open: `http://localhost:7842`

## Ready-to-use package

A packaged ready-to-use app is available in `release/` as `mc-host-manager-ready-to-use-*.zip`.

Unzip it and run:

```bash
bash launch.sh
```

The package already includes the app files, UI assets, and docs.

## Controls From Another Screen/PC

**License:** MIT — see [LICENSE.md](LICENSE.md) for full text.

**Support:** For issues or questions, please open an issue on the project's GitHub repository.

If dashboard is opened from another device, control actions require matching `project_key`.

- Project key is shown in Settings
- Same key on both screens allows control

## Build

```bash
bash build.sh
```

>>>>>>> 7276cd4 (docs: update README and SETUP quick-start; add requirements.txt)
Windows:

```bat
launch.bat
```

Open dashboard:

```text
http://localhost:7842
```

---

# Docker

Pull image:

```bash
docker pull ghcr.io/shivam238/mc-host-manager:v1.0.0b1
```

Run container:

```bash
docker run -p 7842:7842 ghcr.io/shivam238/mc-host-manager:v1.0.0b1
```

---

# Ready-to-use Package

Prebuilt packages are available in:

```text
release/
```

Example:

```text
mc-host-manager-ready-to-use-*.zip
```

The packaged build already includes:

* Application files
* UI assets
* Runtime configuration
* Documentation

Run packaged version:

Linux:

```bash
bash launch.sh
```

Windows:

```bat
launch.bat
```

---

# Remote Controls (LAN)

When opened from another device, control actions require a matching `project_key`.

## Security

* Project key shown in Settings
* Matching key required for remote control
* Prevents unauthorized actions from other devices

---

# Build

## Linux

```bash
bash build.sh
```

## Windows

```bat
build.bat
```

---

# Project Structure

```text
MC_HOSTER/
├── ui/
├── release/
├── backups/
├── host_manager.py
├── api_handler.py
├── launch.sh
├── launch.bat
├── build.sh
├── build.bat
└── pyproject.toml
```

---

# Release Status

Current release:

```text
v1.0.0b1 (Beta)
```

This is an early beta release and may contain unfinished features or bugs.

Feedback, testing, and bug reports are welcome.

---

# Roadmap

Planned improvements:

* Improved remote management
* Better multi-server support
* Enhanced backup scheduling
* Plugin/modpack presets
* WebSocket live updates
* Advanced metrics dashboard

---

# Repository

GitHub Repository:

[mc-host-manager GitHub Repository](https://github.com/shivam238/mc-host-manager?utm_source=chatgpt.com)

PyPI Package:

[mc-host-manager on PyPI](https://pypi.org/project/mc-host-manager/?utm_source=chatgpt.com)

GitHub Container Package:

[GitHub Container Registry Package](https://github.com/shivam238/mc-host-manager/pkgs/container/mc-host-manager?utm_source=chatgpt.com)

---

# License

This project is released under the project license included in the repository.
