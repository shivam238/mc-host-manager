from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from utils.config import APP_DATA_DIR

SYNCTHING_VERSION = "1.29.4"
JAVA_MAJOR = 21
DEPS_DIR = APP_DATA_DIR / "deps"
SYNCTHING_HOME = APP_DATA_DIR / "syncthing"
SYNCTHING_INSTALL_DIR = DEPS_DIR / "syncthing"
JAVA_INSTALL_DIR = DEPS_DIR / "java"
STATE_FILE = APP_DATA_DIR / "deps_state.json"
SYNCTHING_PORT = 8384

_install_lock = threading.Lock()
_state_lock = threading.Lock()
_bg_started = False


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(patch: dict[str, Any]) -> None:
    with _state_lock:
        state = _load_state()
        state.update(patch)
        try:
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def _log(msg: str) -> None:
    print(f"[deps] {msg}", flush=True)


def _platform_tuple() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64", "x64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine in ("i386", "i686", "x86"):
        arch = "386"
    else:
        arch = "amd64"
    if system.startswith("win"):
        os_name = "windows"
    elif system == "darwin":
        os_name = "darwin"
    else:
        os_name = "linux"
    return os_name, arch


def syncthing_config_paths() -> list[Path]:
    paths: list[Path] = []
    bundled = SYNCTHING_HOME / "config.xml"
    if bundled.exists():
        paths.append(bundled)
    paths.append(Path.home() / ".config/syncthing/config.xml")
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        localapp = os.environ.get("LOCALAPPDATA")
        if appdata:
            paths.append(Path(appdata) / "Syncthing/config.xml")
        if localapp:
            paths.append(Path(localapp) / "Syncthing/config.xml")
    else:
        paths.extend(
            [
                Path.home() / ".local/share/syncthing/config.xml",
                Path.home() / ".local/state/syncthing/config.xml",
            ]
        )
    return paths


def bundled_syncthing_binary() -> Path | None:
    os_name, arch = _platform_tuple()
    exe = "syncthing.exe" if os_name == "windows" else "syncthing"
    candidates = [
        SYNCTHING_INSTALL_DIR / exe,
        SYNCTHING_INSTALL_DIR / f"syncthing-{os_name}-{arch}-v{SYNCTHING_VERSION}" / exe,
    ]
    for p in candidates:
        if p.is_file():
            return p
    for p in SYNCTHING_INSTALL_DIR.rglob(exe):
        if p.is_file():
            return p
    return None


def system_syncthing_binary() -> str | None:
    return shutil.which("syncthing")


def resolve_syncthing_binary() -> Path | str | None:
    bundled = bundled_syncthing_binary()
    if bundled:
        return bundled
    return system_syncthing_binary()


def bundled_java_binary() -> Path | None:
    os_name, _ = _platform_tuple()
    exe = "java.exe" if os_name == "windows" else "java"
    if not JAVA_INSTALL_DIR.exists():
        return None
    for p in JAVA_INSTALL_DIR.rglob(exe):
        parts = {x.lower() for x in p.parts}
        if "bin" in parts and p.is_file():
            return p
    return None


def resolve_java_binary() -> str | None:
    bundled = bundled_java_binary()
    if bundled:
        return str(bundled)
    return shutil.which("java")


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.35):
            return True
    except OSError:
        return False


def is_syncthing_running() -> bool:
    if not _port_open(SYNCTHING_PORT):
        return False
    try:
        import requests

        r = requests.get(f"http://127.0.0.1:{SYNCTHING_PORT}/rest/noauth/health", timeout=1.0)
        return r.status_code == 200
    except Exception:
        return _port_open(SYNCTHING_PORT)


def _download(url: str, dest: Path, timeout: float = 600.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = Request(url, headers={"User-Agent": "MC-Host-Manager/1.0"})
    with urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as out:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(dest)


def _syncthing_release_url() -> str:
    os_name, arch = _platform_tuple()
    ver = SYNCTHING_VERSION
    base = f"https://github.com/syncthing/syncthing/releases/download/v{ver}"
    if os_name == "windows":
        name = f"syncthing-windows-{arch}-v{ver}.zip"
    elif os_name == "darwin":
        name = f"syncthing-macos-{arch}-v{ver}.tar.gz"
    else:
        name = f"syncthing-linux-{arch}-v{ver}.tar.gz"
    return f"{base}/{name}"


def _extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(dest)
        return
    with tarfile.open(archive, "r:*") as tf:
        tf.extractall(dest, filter="data")


def install_syncthing(*, force: bool = False) -> tuple[bool, str]:
    if not force and (bundled_syncthing_binary() or system_syncthing_binary()):
        return True, "Syncthing already available."
    os_name, _ = _platform_tuple()
    url = _syncthing_release_url()
    archive = DEPS_DIR / Path(url).name
    try:
        _log(f"Downloading Syncthing {SYNCTHING_VERSION}...")
        _download(url, archive)
        if SYNCTHING_INSTALL_DIR.exists():
            shutil.rmtree(SYNCTHING_INSTALL_DIR, ignore_errors=True)
        SYNCTHING_INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        _extract_archive(archive, SYNCTHING_INSTALL_DIR)
        archive.unlink(missing_ok=True)
        binary = bundled_syncthing_binary()
        if not binary:
            return False, "Syncthing download finished but binary not found."
        if os_name != "windows":
            binary.chmod(0o755)
        _save_state({"syncthing_version": SYNCTHING_VERSION, "syncthing_installed": True})
        return True, f"Syncthing {SYNCTHING_VERSION} installed."
    except (URLError, OSError, tarfile.TarError, zipfile.BadZipFile) as e:
        return False, f"Syncthing install failed: {e}"


def _java_download_url() -> str:
    os_name, arch = _platform_tuple()
    os_map = {"linux": "linux", "windows": "windows", "darwin": "mac"}
    arch_map = {"amd64": "x64", "arm64": "aarch64", "386": "x86"}
    return (
        "https://api.adoptium.net/v3/binary/latest/"
        f"{JAVA_MAJOR}/ga/{os_map[os_name]}/{arch_map.get(arch, 'x64')}/jre/hotspot/normal/eclipse"
    )


def install_java(*, force: bool = False) -> tuple[bool, str]:
    if not force and bundled_java_binary():
        return True, "Java already installed (bundled)."
    url = _java_download_url()
    os_name, _ = _platform_tuple()
    ext = ".zip" if os_name == "windows" else ".tar.gz"
    archive = DEPS_DIR / f"temurin-jre-{JAVA_MAJOR}{ext}"
    try:
        _log(f"Downloading Java {JAVA_MAJOR} JRE...")
        _download(url, archive, timeout=900.0)
        if JAVA_INSTALL_DIR.exists():
            shutil.rmtree(JAVA_INSTALL_DIR, ignore_errors=True)
        JAVA_INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        _extract_archive(archive, JAVA_INSTALL_DIR)
        archive.unlink(missing_ok=True)
        if not bundled_java_binary():
            return False, "Java download finished but java binary not found."
        _save_state({"java_version": JAVA_MAJOR, "java_installed": True})
        return True, f"Java {JAVA_MAJOR} JRE installed."
    except (URLError, OSError, tarfile.TarError, zipfile.BadZipFile) as e:
        return False, f"Java install failed: {e}"


def is_java_usable() -> bool:
    java = resolve_java_binary()
    if not java:
        return False
    try:
        r = subprocess.run(
            [java, "-version"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        out = (r.stderr or "") + (r.stdout or "")
        return r.returncode == 0 or "version" in out.lower()
    except Exception:
        return False


def ensure_requests() -> tuple[bool, str]:
    try:
        import requests  # noqa: F401

        return True, "requests OK"
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        return False, "requests missing in frozen build"
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "requests"],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        import requests  # noqa: F401

        return True, "requests installed"
    except Exception as e:
        return False, f"pip install requests failed: {e}"


def start_syncthing(*, wait_seconds: float = 45.0) -> tuple[bool, str]:
    if is_syncthing_running():
        return True, "Syncthing already running"

    binary = resolve_syncthing_binary()
    if not binary:
        return False, "Syncthing binary not found"

    SYNCTHING_HOME.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(binary),
        "serve",
        f"--home={SYNCTHING_HOME}",
        f"--gui-address=127.0.0.1:{SYNCTHING_PORT}",
        "--no-browser",
        "--no-restart",
        "--logflags=0",
    ]
    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        subprocess.Popen(
            cmd,
            cwd=str(SYNCTHING_HOME),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
        )
    except OSError as e:
        return False, f"Failed to start Syncthing: {e}"

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if is_syncthing_running():
            _save_state({"syncthing_running": True})
            return True, "Syncthing started"
        time.sleep(0.4)
    return False, "Syncthing start timed out (check port 8384)"


def ensure_syncthing(*, install_if_missing: bool = True) -> tuple[bool, str]:
    if is_syncthing_running():
        return True, "Syncthing running"

    if not resolve_syncthing_binary():
        if not install_if_missing:
            return False, "Syncthing not installed"
        ok, msg = install_syncthing()
        if not ok:
            return False, msg

    return start_syncthing()


def ensure_java(*, install_if_missing: bool = True) -> tuple[bool, str]:
    if is_java_usable():
        return True, "Java OK"
    if not install_if_missing:
        return False, "Java not found"
    ok, msg = install_java()
    if not ok:
        return False, msg
    if is_java_usable():
        return True, msg
    return False, "Java installed but not working"


def status_snapshot() -> dict[str, Any]:
    st = _load_state()
    return {
        "syncthing_installed": bool(resolve_syncthing_binary()),
        "syncthing_running": is_syncthing_running(),
        "syncthing_bundled": bool(bundled_syncthing_binary()),
        "syncthing_version": st.get("syncthing_version") or (SYNCTHING_VERSION if bundled_syncthing_binary() else ""),
        "java_installed": is_java_usable(),
        "java_bundled": bool(bundled_java_binary()),
        "java_path": resolve_java_binary() or "",
        "requests_ok": _has_requests(),
        "installing": bool(st.get("installing")),
        "last_message": str(st.get("last_message") or ""),
        "last_error": str(st.get("last_error") or ""),
    }


def _has_requests() -> bool:
    try:
        import requests  # noqa: F401

        return True
    except Exception:
        return False


def ensure_all_dependencies(
    *,
    install_syncthing: bool = True,
    install_java: bool = True,
    install_requests: bool = True,
    start_sync: bool = True,
) -> dict[str, Any]:
    """Install and start missing dependencies. Safe to call multiple times."""
    with _install_lock:
        _save_state({"installing": True, "last_message": "Checking dependencies..."})
        results: dict[str, Any] = {"ok": True, "steps": []}

        def step(name: str, fn: Callable[[], tuple[bool, str]]) -> None:
            ok, msg = fn()
            results["steps"].append({"name": name, "ok": ok, "msg": msg})
            _log(f"{name}: {msg}")
            if not ok:
                results["ok"] = False
                _save_state({"last_error": msg, "last_message": msg})

        if install_requests:
            step("requests", ensure_requests)

        if install_syncthing:
            step("syncthing_install", lambda: install_syncthing() if not resolve_syncthing_binary() else (True, "Syncthing present"))
            if start_sync:
                step("syncthing_start", lambda: ensure_syncthing(install_if_missing=False))

        if install_java:
            step("java", lambda: ensure_java(install_if_missing=True))

        try:
            from utils.flow_manager import st_api

            st_api.refresh_api_key()
        except Exception:
            pass

        summary = "; ".join(s["msg"] for s in results["steps"] if s.get("msg"))
        _save_state(
            {
                "installing": False,
                "last_message": summary or "Dependencies ready",
                "last_error": "" if results["ok"] else _load_state().get("last_error", ""),
            }
        )
        results["status"] = status_snapshot()
        return results


def ensure_dependencies_background() -> None:
    global _bg_started
    if _bg_started:
        return
    _bg_started = True

    def _run() -> None:
        try:
            ensure_all_dependencies()
        except Exception as e:
            _save_state({"installing": False, "last_error": str(e)})

    threading.Thread(target=_run, daemon=True, name="deps-bootstrap").start()
