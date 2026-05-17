Here’s a cleaned, polished README you can use directly.

# MC Host Manager

Lightweight local Minecraft hosting dashboard with safe backup flow, real-time monitoring, and cross-platform support.

---

# Overview

MC Host Manager is a lightweight local-first Minecraft server hosting dashboard designed for simplicity, safety, and low system overhead.

The application provides:

* Safe server lifecycle management
* Automatic backup handling
* Real-time resource monitoring
* Remote LAN controls with protection
* Cross-platform support
* Docker support
* Lightweight architecture without heavy orchestration layers

Open dashboard:

```text
http://localhost:7842
```

---

# Features

## Real-Time Monitoring

Monitor server activity and system resources in real time:

* CPU usage
* RAM usage
* Disk I/O activity
* Server process status

---

## Backup System

Built-in backup management includes:

* Automatic backup creation
* Backup listing
* Backup downloads
* Full server ZIP export

Designed to reduce corruption risks during crashes or unsafe shutdowns.

---

## Lock & Safety System

MC Host Manager uses a lock + heartbeat system to prevent unsafe multi-instance hosting.

### Safety Logic

* Lock acquired before startup
* Heartbeat maintained while hosting
* Automatic finalize flow on crash
* Active lock protection
* Safe unlock handling
* Protected force-clear behavior

---

## Lightweight Architecture

Designed for low-overhead local hosting.

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

# Requirements

## Supported Platforms

* Linux
* Windows

## Required Software

* Python 3.10+
* Java installed
* Minecraft server files

---

# Quick Start

## Ready-to-use Package

Prebuilt packages are available in:

```text
release/
```

Example package:

```text
mc-host-manager-ready-to-use-*.zip
```

Unzip the package and run:

### Linux

```bash
bash launch.sh
```

### Windows

```bat
launch.bat
```

Then open:

```text
http://localhost:7842
```

The packaged build already includes:

* Application files
* UI assets
* Runtime configuration
* Documentation

---

# Installation

## Install from PyPI (Beta)

Latest beta:

```bash
pip install --pre mc-host-manager
```

Specific version:

```bash
pip install mc-host-manager==1.0.0b1
```

---

# Run From Source

## Linux

```bash
bash launch.sh
```

## Windows

```bat
launch.bat
```

Open dashboard:

```text
http://localhost:7842
```

---

# Docker

## Pull Image

```bash
docker pull ghcr.io/shivam238/mc-host-manager:v1.0.0b1
```

## Run Container

```bash
docker run -p 7842:7842 ghcr.io/shivam238/mc-host-manager:v1.0.0b1
```

---

# Remote Controls (LAN)

When the dashboard is opened from another device, control actions require a matching `project_key`.

## Security

* Project key shown in Settings
* Matching key required for remote control
* Prevents unauthorized actions from other devices

> This project is designed primarily for trusted local/LAN environments.

---

# Minecraft Compatibility

Compatible with most standard Minecraft server types, including:

* Vanilla
* Paper
* Spigot
* Purpur
* Fabric
* Forge

Compatibility may vary depending on custom launch configurations or modpacks.

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
MC_HOST_MANAGER/
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

## GitHub Repository

[https://github.com/shivam238/mc-host-manager](https://github.com/shivam238/mc-host-manager)

## PyPI Package

[https://pypi.org/project/mc-host-manager/](https://pypi.org/project/mc-host-manager/)

## GitHub Container Registry

[https://github.com/shivam238/mc-host-manager/pkgs/container/mc-host-manager](https://github.com/shivam238/mc-host-manager/pkgs/container/mc-host-manager)

---

# License

MIT License

See `LICENSE.md` for full license text.

---

# Support

For issues, bug reports, or feature requests, please open an issue on the GitHub repository.
