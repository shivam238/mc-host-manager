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
SYNCTHING_LOG = APP_DATA_DIR / "syncthing.log"
SYNCTHING_PORT = 8384

_install_lock = threading.Lock()
_state_lock = threading.Lock()
_bg_started = False
_bg_retries = 0
MAX_BG_RETRIES = 12
_install_thread_running = False


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


def _read_syncthing_log_tail(lines: int = 8) -> str:
    if not SYNCTHING_LOG.exists():
        return ""
    try:
        rows = SYNCTHING_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(rows[-lines:]).strip()
    except Exception:
        return ""


def is_syncthing_running() -> bool:
    if not _port_open(SYNCTHING_PORT):
        return False
    try:
        import requests

        r = requests.get(f"http://127.0.0.1:{SYNCTHING_PORT}/rest/noauth/health", timeout=1.0)
        if r.status_code == 200:
            return True
    except Exception:
        pass
    return _port_open(SYNCTHING_PORT)


def syncthing_api_ready() -> bool:
    """Port open AND we can read an API key from our or system config."""
    if not is_syncthing_running():
        return False
    for p in syncthing_config_paths():
        if not p.exists():
            continue
        try:
            import xml.etree.ElementTree as ET

            root = ET.parse(p).getroot()
            el = root.find(".//apikey")
            if el is not None and (el.text or "").strip():
                return True
        except Exception:
            continue
    return False


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
        try:
            # filter="data" was added in Python 3.12; graceful fallback for older versions
            tf.extractall(dest, filter="data")
        except TypeError:
            tf.extractall(dest)


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


def _ensure_syncthing_config(binary: str | Path) -> None:
    config_xml = SYNCTHING_HOME / "config.xml"
    if config_xml.exists():
        return
    SYNCTHING_HOME.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [str(binary), "generate", f"--home={SYNCTHING_HOME}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception:
        pass


def start_syncthing(*, wait_seconds: float = 60.0) -> tuple[bool, str]:
    if syncthing_api_ready():
        return True, "Syncthing already running"

    binary = resolve_syncthing_binary()
    if not binary:
        return False, "Syncthing binary not found — internet se download karo ya install karo."

    _ensure_syncthing_config(binary)

    if _port_open(SYNCTHING_PORT) and not (SYNCTHING_HOME / "config.xml").exists():
        msg = (
            "Port 8384 pe koi aur Syncthing chal raha hai. "
            "Us app ko band karo ya system Syncthing restart karo."
        )
        _save_state({"last_error": msg})
        return False, msg

    SYNCTHING_HOME.mkdir(parents=True, exist_ok=True)
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(SYNCTHING_LOG, "a", encoding="utf-8", errors="replace")
    log_fh.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_fh.flush()

    cmd = [
        str(binary),
        "serve",
        f"--home={SYNCTHING_HOME}",
        f"--gui-address=127.0.0.1:{SYNCTHING_PORT}",
        "--no-browser",
        "--no-restart",
    ]
    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        subprocess.Popen(
            cmd,
            cwd=str(SYNCTHING_HOME),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
        )
    except OSError as e:
        log_fh.close()
        return False, f"Failed to start Syncthing: {e}"

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if syncthing_api_ready():
            _save_state({"syncthing_running": True, "last_error": ""})
            log_fh.close()
            try:
                from utils.flow_manager import st_api

                st_api.refresh_api_key()
            except Exception:
                pass
            return True, "Syncthing started"
        time.sleep(0.5)

    tail = _read_syncthing_log_tail(6)
    err = "Syncthing start timeout. Internet / port 8384 check karo."
    if tail:
        err += f" Log: {tail[-200:]}"
    _save_state({"last_error": err})
    return False, err


def ensure_syncthing(*, install_if_missing: bool = True) -> tuple[bool, str]:
    if syncthing_api_ready():
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
    running = syncthing_api_ready()
    return {
        "syncthing_installed": bool(resolve_syncthing_binary()),
        "syncthing_running": running,
        "syncthing_port_open": _port_open(SYNCTHING_PORT),
        "syncthing_bundled": bool(bundled_syncthing_binary()),
        "syncthing_version": st.get("syncthing_version") or (SYNCTHING_VERSION if bundled_syncthing_binary() else ""),
        "syncthing_log_tail": _read_syncthing_log_tail(4),
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
    want_syncthing: bool = True,
    want_java: bool = True,
    want_requests: bool = True,
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

        if want_requests:
            step("requests", ensure_requests)

        if want_syncthing:
            if not resolve_syncthing_binary():
                step("syncthing_pkg", try_system_package_syncthing)
            step(
                "syncthing_install",
                lambda: install_syncthing() if not resolve_syncthing_binary() else (True, "Syncthing present"),
            )
            if start_sync:
                step("syncthing_start", lambda: ensure_syncthing(install_if_missing=False))

        if want_java:
            step("java", lambda: ensure_java(install_if_missing=True))

        try:
            from utils.flow_manager import st_api

            st_api.refresh_api_key()
        except Exception:
            pass

        snap = status_snapshot()
        if want_syncthing and not snap.get("syncthing_running"):
            results["ok"] = False
            if not _load_state().get("last_error"):
                _save_state({"last_error": "Syncthing start nahi hua — Install now dabao ya internet check karo."})

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


def start_install_async() -> dict[str, Any]:
    """Non-blocking install for HTTP — poll /deps/status."""
    global _install_thread_running

    def _worker() -> None:
        global _install_thread_running
        try:
            ensure_all_dependencies()
        except Exception as e:
            _save_state({"installing": False, "last_error": str(e)})
        finally:
            _install_thread_running = False

    with _install_lock:
        if _install_thread_running:
            return {"ok": True, "msg": "Install already running.", "status": status_snapshot()}
        _install_thread_running = True
        _save_state({"installing": True, "last_message": "Installing..."})
    threading.Thread(target=_worker, daemon=True, name="deps-install").start()
    return {"ok": True, "msg": "Install started — wait 1-2 min.", "status": status_snapshot()}


def try_system_package_syncthing() -> tuple[bool, str]:
    """Best-effort: apt/dnf install when available (may need user sudo)."""
    if system_syncthing_binary():
        return True, "Syncthing on PATH"
    if os.name == "nt":
        return False, "Use installer or bundled download on Windows"
    for cmd in (
        ["apt-get", "install", "-y", "syncthing"],
        ["dnf", "install", "-y", "syncthing"],
        ["pacman", "-S", "--noconfirm", "syncthing"],
    ):
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
            if r.returncode == 0 and system_syncthing_binary():
                return True, f"Installed via {cmd[0]}"
        except Exception:
            continue
    return False, "Package install skipped (needs sudo or not found)"


def ensure_dependencies_background() -> None:
    global _bg_started

    def _run() -> None:
        global _bg_retries
        while _bg_retries < MAX_BG_RETRIES:
            _bg_retries += 1
            try:
                result = ensure_all_dependencies()
                if result.get("ok") and result.get("status", {}).get("syncthing_running"):
                    break
            except Exception as e:
                _save_state({"installing": False, "last_error": str(e)})
            if _bg_retries < MAX_BG_RETRIES:
                time.sleep(30)
        _save_state({"installing": False})

    if _bg_started:
        return
    _bg_started = True
    threading.Thread(target=_run, daemon=True, name="deps-bootstrap").start()
