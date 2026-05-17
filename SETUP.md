README ke saath ye `SETUP.md` bhi useful rahega for first-time users:

# MC Host Manager — Setup Guide

Complete setup instructions for Linux and Windows.

---

# Requirements

Before running MC Host Manager, install the following:

## Required

* Python 3.10+
* Java (required for Minecraft server)
* Git (optional)

---

# Linux Setup

## 1. Clone Repository

```bash id="j5m3zy"
git clone https://github.com/shivam238/mc-host-manager.git
```

```bash id="m9g2dv"
cd mc-host-manager
```

---

## 2. Install Python Dependencies

```bash id="s4g9nu"
pip install -r requirements.txt
```

---

## 3. Make Launch Script Executable

```bash id="r4ztzw"
chmod +x launch.sh
```

---

## 4. Start Application

```bash id="a4my1s"
bash launch.sh
```
Open the dashboard in your browser at `http://localhost:7842`.

Optional: install Python dependencies (recommended):

```bash
python3 -m pip install -r requirements.txt
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

```bat id="jlwmie"
pip install -r requirements.txt
```

---

## 3. Start Application

Double click:

```text id="b0vvwb"
launch.bat
```

OR run:

```bat id="4x8o16"
launch.bat
```

Open dashboard:

```text id="v9spuj"
http://localhost:7842
```

---

# Using Ready-to-use Package

Prebuilt package location:

```text id="snr2iy"
release/
```

Example:

```text id="wrffsw"
mc-host-manager-ready-to-use-*.zip
```

## Steps

1. Extract ZIP
2. Open extracted folder
3. Run:

   * `launch.sh` on Linux
   * `launch.bat` on Windows

---

# PyPI Installation

Install latest beta release:

```bash id="zmh4lh"
pip install --pre mc-host-manager
```

Specific version:

```bash id="y74jcu"
pip install mc-host-manager==1.0.0b1
```

---

# Docker Setup

## Pull Image

```bash id="lmq5l7"
docker pull ghcr.io/shivam238/mc-host-manager:v1.0.0b1
```

---

## Run Container

```bash id="1pkx9l"
docker run -p 7842:7842 ghcr.io/shivam238/mc-host-manager:v1.0.0b1
```

Open dashboard:

```text id="qqn7zq"
http://localhost:7842
```

---

# Minecraft Server Setup

Place your Minecraft server files inside your configured server directory.

Example files:

```text id="uxpqav"
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

To control server from another device:

1. Open dashboard on host machine
2. Open Settings
3. Copy `project_key`
4. Use same key on remote device

---

# Build Instructions

## Linux

```bash id="cc9t54"
bash build.sh
```

## Windows

```bat id="8go8y4"
build.bat
```

---

# Troubleshooting

## Port Already In Use

If port `7842` is busy:

* Stop conflicting application
* OR change configured port

---

## Permission Issues (Linux)

If scripts fail:

```bash id="lyjlwm"
chmod +x launch.sh
chmod +x build.sh
```

---

## Docker Permission Error

Add user to docker group:

```bash id="68kvkr"
sudo usermod -aG docker $USER
```

Then logout/login again.

---

# Updating

Pull latest changes:

```bash id="jlwmhf"
git pull
```

Reinstall dependencies if needed:

```bash id="z4f1kt"
pip install -r requirements.txt
```

---

# Repository

GitHub:

[mc-host-manager Repository](https://github.com/shivam238/mc-host-manager?utm_source=chatgpt.com)

PyPI:

[mc-host-manager on PyPI](https://pypi.org/project/mc-host-manager/?utm_source=chatgpt.com)
