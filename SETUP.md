Here’s a cleaned and polished final version of `SETUP.md`.

# MC Host Manager — Setup Guide

Complete setup instructions for Linux and Windows.

---

# Requirements

Before running MC Host Manager, install the following dependencies.

## Required

* Python 3.10+
* Java (required for Minecraft server)
* Git (optional)

---

# Recommended Java Versions

* Java 17 → Minecraft 1.18+
* Java 21 → newer server builds

---

# Linux Setup

## 1. Clone Repository

```bash
git clone https://github.com/shivam238/mc-host-manager.git
```

```bash
cd mc-host-manager
```

---

## 2. Install Python Dependencies

```bash
python3 -m pip install -r requirements.txt
```

---

## 3. Make Launch Script Executable

```bash
chmod +x launch.sh
```

Optional:

```bash
chmod +x build.sh
```

---

## 4. Start Application

```bash
bash launch.sh
```

Open the dashboard in your browser:

```text
http://localhost:7842
```

---

# Windows Setup

## 1. Download Repository

Either:

* Clone using Git
* OR download ZIP from GitHub Releases

---

## 2. Install Python Dependencies

Open terminal inside project folder:

```bat
pip install -r requirements.txt
```

---

## 3. Start Application

Run:

```bat
launch.bat
```

You can also double-click `launch.bat` from File Explorer.

Open dashboard:

```text
http://localhost:7842
```

---

# Using Ready-to-use Package

Prebuilt packages are available in:

```text
release/
```

Example package:

```text
mc-host-manager-ready-to-use-*.zip
```

## Steps

1. Extract ZIP
2. Open extracted folder
3. Run:

   * `launch.sh` on Linux
   * `launch.bat` on Windows

Open dashboard:

```text
http://localhost:7842
```

---

# PyPI Installation

Install latest beta release:

```bash
pip install --pre mc-host-manager
```

Specific version:

```bash
pip install mc-host-manager==1.0.0b1
```

---

# Docker Setup

## Pull Image

```bash
docker pull ghcr.io/shivam238/mc-host-manager:v1.0.0b1
```

---

## Run Container

```bash
docker run -p 7842:7842 ghcr.io/shivam238/mc-host-manager:v1.0.0b1
```

Open dashboard:

```text
http://localhost:7842
```

---

# Minecraft Server Setup

Place your Minecraft server files inside your configured server directory.

Example files:

```text
server.jar
eula.txt
server.properties
world/
```

---

# First Launch

On first startup:

* Dashboard initializes runtime folders
* Lock system activates
* Backup system becomes available
* Monitoring services start automatically

---

# Remote Controls (LAN)

To control the server from another device:

1. Open dashboard on host machine
2. Open Settings
3. Copy `project_key`
4. Use the same key on remote device

## Firewall Note

If remote devices cannot access the dashboard:

* Allow port `7842` through firewall
* Ensure both devices are on the same LAN

---

# Build Instructions

## Linux

```bash
bash build.sh
```

## Windows

```bat
build.bat
```

---

# Troubleshooting

## Port Already In Use

If port `7842` is already occupied:

* Stop the conflicting application
* OR change the configured port

---

## Permission Issues (Linux)

If scripts fail to execute:

```bash
chmod +x launch.sh
chmod +x build.sh
```

---

## Docker Permission Error

Add user to docker group:

```bash
sudo usermod -aG docker $USER
```

Then logout/login again.

---

# Updating

Pull latest changes:

```bash
git pull
```

Reinstall dependencies if required:

```bash
python3 -m pip install -r requirements.txt
```

---

# Repository

## GitHub

[https://github.com/shivam238/mc-host-manager](https://github.com/shivam238/mc-host-manager)

## PyPI

[https://pypi.org/project/mc-host-manager/](https://pypi.org/project/mc-host-manager/)

## GitHub Container Registry

[https://github.com/shivam238/mc-host-manager/pkgs/container/mc-host-manager](https://github.com/shivam238/mc-host-manager/pkgs/container/mc-host-manager)

---

# License

MIT License

See `LICENSE.md` for full license text.
