"""
MC Host Manager - Phase 2 (Modular & Reliable)
"""

import sys
from pathlib import Path

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# PROJECT ROOT INJECTION (fixes "Could not find import of utils")
# This must happen before we try to import our own modules
if getattr(sys, "frozen", False):
    # PyInstaller onefile: resources are unpacked in _MEIPASS, writable files should
    # live next to the executable.
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    RUNTIME_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).parent.resolve()
    RUNTIME_DIR = RESOURCE_DIR

PROJECT_DIR = RESOURCE_DIR
if str(RESOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(RESOURCE_DIR))

# Standard Libraries
import json
import threading
import socket
import os
import shutil
import subprocess
import urllib.request
import tarfile
import zipfile
import platform
import time
import tempfile
import secrets
import re
import atexit
import signal
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

try:
    import psutil
except Exception:
    psutil = None

# Modular Utils (Imported from the utils/ folder)
try:
    from utils import lock_manager, sync_manager, server_controller, backup_manager, tunnel_manager
except ImportError as e:
    print(f"[ERROR] Critical error: could not find 'utils' folder in {PROJECT_DIR}")
    print(f"[HINT] Ensure you are running the script from the correct folder. (Detail: {e})")
    sys.exit(1)

DEFAULT_CONFIG = {
    "project_name": "Minecraft Server",
    "server_dir": "",
    "shared_dir": "",
    "world_dir_override": "",
    "manual_server_dir": "",
    "manual_shared_dir": "",
    "manual_backups_dir": "",
    "manual_crash_dir": "",
    "project_key": "",
    "server_jar": "server.jar",
    "java_args": "-Xmx4G -Xms2G",
    "ram": "4G",
    "max_players": 20,
    "whitelist_enabled": False,
    "auto_backup": True,
    "backup_keep": 5,
    "wizard_completed": False,
}

LOCAL_DATA_DIR = Path.home() / ".mc-host"
USER_CONFIG_FILE = LOCAL_DATA_DIR / "user.json"


def get_user_data_root() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "mc-host-manager"


def _can_write_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def resolve_app_data_dir() -> Path:
    # Keep existing local progress first (legacy behavior).
    local = RUNTIME_DIR / "app_data"
    if local.exists():
        return local
    # For installed executables, prefer user profile location to avoid permission issues.
    if getattr(sys, "frozen", False):
        return get_user_data_root() / "app_data"
    return local


def resolve_bin_dir() -> Path:
    preferred = RUNTIME_DIR / "bin"
    if _can_write_directory(preferred):
        return preferred
    fallback = get_user_data_root() / "bin"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


APP_DATA_DIR = resolve_app_data_dir()
CONFIG_FILE = APP_DATA_DIR / "settings.json"
BIN_DIR = resolve_bin_dir()


def get_bin_dir() -> Path:
    return BIN_DIR


def bootstrap_bundled_bin_assets() -> None:
    """Copy bundled bin assets from PyInstaller resources to runtime bin dir."""
    src = RESOURCE_DIR / "bin"
    dst = get_bin_dir()
    if not src.exists() or not src.is_dir():
        return
    for item in src.iterdir():
        target = dst / item.name
        if target.exists():
            continue
        try:
            if item.is_file():
                shutil.copy2(item, target)
                if not target.suffix.lower() == ".exe":
                    try:
                        target.chmod(0o755)
                    except Exception:
                        pass
        except Exception:
            pass


def normalize_path_value(value: str | Path | None) -> str:
    if value is None:
        return ""
    txt = str(value).strip()
    if not txt:
        return ""
    return str(Path(txt).expanduser())


def ensure_shared_layout(shared_dir: str | Path) -> Path:
    shared = Path(shared_dir).expanduser()
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "backups").mkdir(parents=True, exist_ok=True)
    (shared / "world_latest").mkdir(parents=True, exist_ok=True)
    return shared


def _control_root(shared_dir: str | Path) -> Path:
    return Path(shared_dir).expanduser() / ".mc_control"


def _presence_dir(shared_dir: str | Path) -> Path:
    return _control_root(shared_dir) / "presence"


def _commands_dir(shared_dir: str | Path) -> Path:
    return _control_root(shared_dir) / "commands"


def _acks_dir(shared_dir: str | Path) -> Path:
    return _control_root(shared_dir) / "acks"


def _read_json_safe(path: Path) -> dict | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


LOCAL_NODE_ID_FILE = APP_DATA_DIR / "node_id.txt"
_local_node_id_cache = ""


def get_local_node_id() -> str:
    global _local_node_id_cache
    if _local_node_id_cache:
        return _local_node_id_cache
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        if LOCAL_NODE_ID_FILE.exists():
            node = LOCAL_NODE_ID_FILE.read_text(encoding="utf-8", errors="replace").strip()
            if node:
                _local_node_id_cache = node
                return node
        node = secrets.token_hex(8)
        LOCAL_NODE_ID_FILE.write_text(node, encoding="utf-8")
        _local_node_id_cache = node
        return node
    except Exception:
        # last-resort ephemeral id
        _local_node_id_cache = f"ephemeral-{secrets.token_hex(4)}"
        return _local_node_id_cache


def _best_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip:
            return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip:
            return ip
    except Exception:
        pass
    return "127.0.0.1"


PRESENCE_STALE_S = 20
ACK_RETENTION_S = 2 * 3600
COMMAND_RETENTION_S = 20 * 60
PRESENCE_RETENTION_S = 24 * 3600
CONTROL_TMP_RETENTION_S = 10 * 60
DOWNLOAD_TMP_RETENTION_S = 6 * 3600
HOUSEKEEPING_INTERVAL_S = 90


def _safe_unlink(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _cleanup_old_files(folder: Path, pattern: str, older_than_s: int) -> int:
    now = time.time()
    removed = 0
    try:
        if not folder.exists() or not folder.is_dir():
            return 0
        for f in folder.glob(pattern):
            try:
                if not f.is_file():
                    continue
                age = now - f.stat().st_mtime
                if age >= older_than_s and _safe_unlink(f):
                    removed += 1
            except Exception:
                continue
    except Exception:
        return removed
    return removed


def cleanup_runtime_artifacts(cfg) -> int:
    """Delete stale temp/control artifacts safely. Returns removed file count."""
    removed_total = 0
    shared = normalize_path_value(cfg.get("shared_dir", ""))
    if shared:
        try:
            removed_total += _cleanup_old_files(_acks_dir(shared), "*.json", ACK_RETENTION_S)
            removed_total += _cleanup_old_files(_commands_dir(shared), "*.json", COMMAND_RETENTION_S)
            removed_total += _cleanup_old_files(_presence_dir(shared), "*.json", PRESENCE_RETENTION_S)
            # Atomic write leftovers
            removed_total += _cleanup_old_files(_control_root(shared), "*.tmp", CONTROL_TMP_RETENTION_S)
            removed_total += _cleanup_old_files(_acks_dir(shared), "*.tmp", CONTROL_TMP_RETENTION_S)
            removed_total += _cleanup_old_files(_commands_dir(shared), "*.tmp", CONTROL_TMP_RETENTION_S)
            removed_total += _cleanup_old_files(_presence_dir(shared), "*.tmp", CONTROL_TMP_RETENTION_S)
        except Exception:
            pass

    try:
        # Remove stale server download temp archives from system temp dir.
        removed_total += _cleanup_old_files(Path(tempfile.gettempdir()), "mc_server_*.zip", DOWNLOAD_TMP_RETENTION_S)
    except Exception:
        pass

    try:
        # Keep only latest 5 corrupt-config snapshots.
        bads = sorted(
            [p for p in APP_DATA_DIR.glob("settings.corrupt.*.json") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in bads[5:]:
            if _safe_unlink(old):
                removed_total += 1
    except Exception:
        pass

    return removed_total


def publish_local_presence(cfg) -> None:
    shared = normalize_path_value(cfg.get("shared_dir", ""))
    if not shared:
        return
    try:
        ensure_shared_layout(shared)
        pkey = str(cfg.get("project_key", "") or "").strip() or ensure_project_key(cfg)
        now = time.time()
        payload = {
            "node_id": get_local_node_id(),
            "user": load_local_user(),
            "hostname": socket.gethostname(),
            "ip": _best_local_ip(),
            "ui_url": f"http://{_best_local_ip()}:7842",
            "project_key": pkey,
            "server_running": bool(mc_server.is_running()),
            "task_running": bool(is_task_running()),
            "server_state": "running" if mc_server.is_running() else "offline",
            "ts": now,
            "time": datetime.now().isoformat(),
        }
        _atomic_write_json(_presence_dir(shared) / f"{get_local_node_id()}.json", payload)
    except Exception:
        pass


def get_remote_nodes(cfg) -> list[dict]:
    shared = normalize_path_value(cfg.get("shared_dir", ""))
    if not shared:
        return []
    pkey = str(cfg.get("project_key", "") or "").strip()
    now = time.time()
    me = get_local_node_id()
    out: list[dict] = []
    try:
        pdir = _presence_dir(shared)
        if not pdir.exists():
            return []
        for f in pdir.glob("*.json"):
            row = _read_json_safe(f)
            if not isinstance(row, dict):
                continue
            row_key = str(row.get("project_key", "") or "").strip()
            if pkey and row_key and row_key != pkey:
                continue
            node_id = str(row.get("node_id", "") or "").strip()
            if not node_id:
                node_id = f.stem
            ts = float(row.get("ts", 0) or 0)
            online = (now - ts) <= PRESENCE_STALE_S if ts > 0 else False
            out.append(
                {
                    "node_id": node_id,
                    "user": str(row.get("user", "") or ""),
                    "hostname": str(row.get("hostname", "") or ""),
                    "ip": str(row.get("ip", "") or ""),
                    "ui_url": str(row.get("ui_url", "") or ""),
                    "server_running": bool(row.get("server_running")),
                    "task_running": bool(row.get("task_running")),
                    "server_state": str(row.get("server_state", "offline") or "offline"),
                    "online": bool(online),
                    "is_local": node_id == me,
                    "time": str(row.get("time", "") or ""),
                }
            )
    except Exception:
        return []
    out.sort(key=lambda r: (not r.get("online", False), not r.get("is_local", False), str(r.get("user", ""))))
    return out


def dispatch_remote_host_action(cfg, target_node_id: str, action: str) -> tuple[bool, str, str]:
    shared = normalize_path_value(cfg.get("shared_dir", ""))
    if not shared:
        return False, "Shared folder is not configured.", ""
    action = str(action or "").strip().lower()
    if action not in ("start", "stop"):
        return False, "Unsupported action.", ""
    target = str(target_node_id or "").strip()
    if not target:
        return False, "Target node is required.", ""
    if target == get_local_node_id():
        return False, "Target is this machine. Use local Start/Stop.", ""
    pkey = str(cfg.get("project_key", "") or "").strip() or ensure_project_key(cfg)
    req_id = secrets.token_hex(10)
    payload = {
        "request_id": req_id,
        "action": action,
        "target_node_id": target,
        "from_node_id": get_local_node_id(),
        "from_user": load_local_user(),
        "project_key": pkey,
        "time": datetime.now().isoformat(),
        "ts": time.time(),
    }
    try:
        _atomic_write_json(_commands_dir(shared) / f"{target}.json", payload)
        # Nudge syncthing to propagate command quickly.
        st_api.scan_folder("mc-shared")
        return True, f"{action.title()} request sent to selected PC.", req_id
    except Exception as e:
        return False, f"Failed to send remote request: {e}", ""


def post_local_host_action(action: str) -> tuple[bool, str]:
    action = str(action or "").strip().lower()
    if action not in ("start", "stop"):
        return False, "Unsupported action"
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:7842/host/{action}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw or "{}")
        ok = bool(data.get("ok"))
        msg = str(data.get("msg", "") or ("Accepted" if ok else "Rejected"))
        return ok, msg
    except Exception as e:
        return False, str(e)


def ensure_syncthing_binary() -> bool:
    """Make sure a syncthing executable is available either on PATH or in our bin folder.
    If not present we attempt to fetch a portable copy (same logic as launch.sh) and
    unpack it into ./bin. Returns True if we now have a usable binary.
    """
    # first check system path
    if shutil.which("syncthing"):
        return True
    bin_dir = get_bin_dir()
    syn_name = "syncthing.exe" if platform.system() == "Windows" else "syncthing"
    syn_path = bin_dir / syn_name
    if syn_path.exists():
        return True

    print("[INFO] Syncthing not found on PATH, attempting to download portable copy...")
    system = platform.system()
    arch = platform.machine().lower()
    url = None
    latest_tag = ""
    assets = []
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/syncthing/syncthing/releases/latest",
            headers={"User-Agent": "mc-host-manager"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            payload = json.loads(r.read().decode("utf-8", errors="replace"))
            latest_tag = str(payload.get("tag_name", "") or "").strip()
            assets = payload.get("assets", []) or []
    except Exception:
        latest_tag = ""

    def with_tag(prefix: str, ext: str) -> str | None:
        if latest_tag:
            return f"https://github.com/syncthing/syncthing/releases/download/{latest_tag}/{prefix}-{latest_tag}{ext}"
        return None

    if system == "Linux":
        if arch in ("x86_64", "amd64"):
            prefix, ext = "syncthing-linux-amd64", ".tar.gz"
        elif arch in ("aarch64", "arm64"):
            prefix, ext = "syncthing-linux-arm64", ".tar.gz"
        else:
            prefix, ext = "", ""
    elif system == "Darwin":
        if arch in ("aarch64", "arm64"):
            prefix, ext = "syncthing-macos-arm64", ".tar.gz"
        elif arch in ("x86_64", "amd64"):
            prefix, ext = "syncthing-macos-amd64", ".tar.gz"
        else:
            prefix, ext = "", ""
    elif system == "Windows":
        if arch in ("aarch64", "arm64"):
            prefix, ext = "syncthing-windows-arm64", ".zip"
        elif arch in ("x86_64", "amd64"):
            prefix, ext = "syncthing-windows-amd64", ".zip"
        else:
            prefix, ext = "", ""
    else:
        prefix, ext = "", ""

    if prefix:
        for asset in assets:
            name = str(asset.get("name", ""))
            dl = str(asset.get("browser_download_url", ""))
            if name.startswith(prefix) and name.endswith(ext) and dl:
                url = dl
                break
        if not url:
            url = with_tag(prefix, ext)

    if not url:
        print(f"[WARN] Unable to auto-download Syncthing for {system}/{arch}. Please install manually.")
        return False

    try:
        tmpfile = bin_dir / "syncthing_dl"
        urllib.request.urlretrieve(url, tmpfile)
        if url.endswith(".tar.gz"):
            with tarfile.open(tmpfile, "r:gz") as tar:
                for member in tar.getmembers():
                    # extract the executable only
                    if member.name.endswith("syncthing") and not member.isdir():
                        member.name = Path(member.name).name
                        tar.extract(member, path=bin_dir)
                        break
        elif url.endswith(".zip"):
            with zipfile.ZipFile(tmpfile, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(syn_name):
                        zf.extract(name, path=bin_dir)
                        extracted = bin_dir / name
                        try:
                            extracted.rename(syn_path)
                        except Exception:
                            pass
                        break
        try:
            tmpfile.unlink()
        except Exception:
            pass
        try:
            syn_path.chmod(0o755)
        except Exception:
            pass
        print(f"[OK] Syncthing binary available at {syn_path}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to download syncthing: {e}")
        return False

def load_local_user():
    if not LOCAL_DATA_DIR.exists():
        LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if USER_CONFIG_FILE.exists():
        try:
            with open(USER_CONFIG_FILE) as f:
                return json.load(f).get("user", "Player1")
        except: return "Player1"
    return "Player1"

def save_local_user(user_name):
    if not LOCAL_DATA_DIR.exists():
        LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(USER_CONFIG_FILE, "w") as f:
        json.dump({"user": user_name}, f, indent=2)

config_cache_lock = threading.Lock()
_config_cache_data: dict | None = None
_config_cache_ts: float = 0.0
CONFIG_CACHE_TTL_S = 0.8


def _normalize_config_dict(raw_cfg: dict | None) -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if isinstance(raw_cfg, dict):
        cfg.update(raw_cfg)

    # Migration: shared_folder -> shared_dir
    if cfg.get("shared_folder") and not cfg.get("shared_dir"):
        cfg["shared_dir"] = cfg.get("shared_folder", "")
    cfg.pop("shared_folder", None)

    for key in (
        "server_dir",
        "shared_dir",
        "world_dir_override",
        "manual_server_dir",
        "manual_shared_dir",
        "manual_backups_dir",
        "manual_crash_dir",
    ):
        cfg[key] = normalize_path_value(cfg.get(key, ""))
    try:
        cfg["max_players"] = int(cfg.get("max_players", 20))
    except Exception:
        cfg["max_players"] = 20
    cfg["max_players"] = max(1, min(500, cfg["max_players"]))
    cfg["whitelist_enabled"] = bool(cfg.get("whitelist_enabled", False))
    cfg["wizard_completed"] = bool(cfg.get("wizard_completed", False))
    return cfg


def _write_config_atomic(cfg: dict) -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def load_config(force: bool = False):
    global _config_cache_data, _config_cache_ts
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

    now = time.time()
    if not force:
        with config_cache_lock:
            if _config_cache_data is not None and (now - _config_cache_ts) < CONFIG_CACHE_TTL_S:
                return dict(_config_cache_data)

    raw: dict | None = None
    needs_rewrite = False
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            # Preserve broken config for manual inspection and continue with defaults.
            try:
                bad = CONFIG_FILE.with_name(f"settings.corrupt.{int(now)}.json")
                CONFIG_FILE.replace(bad)
            except Exception:
                pass
            raw = {}
            needs_rewrite = True
    else:
        raw = {}
        needs_rewrite = True

    cfg = _normalize_config_dict(raw)

    if needs_rewrite:
        try:
            _write_config_atomic(cfg)
        except Exception:
            pass

    with config_cache_lock:
        _config_cache_data = dict(cfg)
        _config_cache_ts = time.time()
    return dict(cfg)


def save_config(cfg):
    global _config_cache_data, _config_cache_ts
    clean = _normalize_config_dict(cfg if isinstance(cfg, dict) else {})
    _write_config_atomic(clean)
    with config_cache_lock:
        _config_cache_data = dict(clean)
        _config_cache_ts = time.time()


def parse_server_properties(server_dir: str | Path):
    props: dict[str, str] = {}
    p = Path(server_dir).expanduser() / "server.properties"
    if not p.exists() or not p.is_file():
        return props
    try:
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = ln.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            props[k.strip()] = v.strip()
    except Exception:
        pass
    return props


def update_server_properties(server_dir: str | Path, updates: dict[str, str | int | bool]):
    p = Path(server_dir).expanduser() / "server.properties"
    lines: list[str] = []
    existing: dict[str, int] = {}

    if p.exists() and p.is_file():
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            lines = []

    for idx, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if key:
            existing[key] = idx

    for key, val in updates.items():
        sval = str(val).lower() if isinstance(val, bool) else str(val)
        new_ln = f"{key}={sval}"
        if key in existing:
            lines[existing[key]] = new_ln
        else:
            lines.append(new_ln)

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

# Global instances
st_api = sync_manager.SyncManager()
mc_server = server_controller.ServerController()
t_manager = tunnel_manager.TunnelManager(get_bin_dir())
last_sync_time = None
state_lock = threading.Lock()
host_session = {
    "active": False,          # True after world copied + lock created
    "ready": False,           # True when MC server is fully ready
    "recovering": False,      # True when crash-recovery finalize is running
    "last_cfg": {},           # Last known server/shared config for recovery
}
HEARTBEAT_INTERVAL_S = 180
MAX_RECOVERY_RETRIES = 6
recovery_failures = 0
recovery_next_try = 0.0
runtime_health = {
    "last_recovery_reason": "",
    "last_recovery_time": "",
    "last_error": "",
    "last_backup_time": "",
    "last_finalize_result": "",
    "last_cleanup_time": "",
    "last_cleanup_removed": "",
}
last_player_poll = 0.0
last_player_stats_poll = 0.0
proc_metric_cache = {
    "pid": None,
    "io_bytes": 0,
    "io_time": 0.0,
    "proc_jiffies": 0,
    "sys_jiffies": 0,
}
status_cache_lock = threading.Lock()
status_cache: dict[str, dict] = {}
STATUS_TTL_SYN_HEALTH_S = 2.8
STATUS_TTL_SYNC_PENDING_S = 10.0
STATUS_TTL_SETUP_STATE_S = 12.0
STATUS_TTL_BACKUPS_S = 20.0
STATUS_TTL_LOCK_INFO_S = 1.2


def _cached_value(cache_key: str, ttl_s: float, loader):
    now = time.time()
    with status_cache_lock:
        entry = status_cache.get(cache_key)
        if entry and (now - float(entry.get("ts", 0.0))) < ttl_s:
            return entry.get("value")
    value = loader()
    with status_cache_lock:
        status_cache[cache_key] = {"ts": now, "value": value}
    return value


def _clear_status_cache(prefix: str | None = None) -> None:
    with status_cache_lock:
        if not prefix:
            status_cache.clear()
            return
        for key in list(status_cache.keys()):
            if key.startswith(prefix):
                status_cache.pop(key, None)

def parse_ram_to_mb(ram_value: str) -> int | None:
    try:
        s = str(ram_value).strip().upper()
        if s.endswith("G"):
            return int(float(s[:-1]) * 1024)
        if s.endswith("M"):
            return int(float(s[:-1]))
        if s.isdigit():
            return int(s)
    except Exception:
        return None
    return None

def get_system_metrics(server_dir: str = ""):
    if psutil is not None:
        try:
            target = Path(server_dir) if server_dir else Path("/")
            if not target.exists():
                target = Path("/")
            d = psutil.disk_usage(str(target))
            return {
                "cpu_pct": int(max(0, min(100, psutil.cpu_percent(interval=0.0)))),
                "mem_pct": int(max(0, min(100, psutil.virtual_memory().percent))),
                "disk_pct": int(max(0, min(100, d.percent))),
            }
        except Exception:
            pass

    cpu_pct = 0
    mem_pct = 0
    disk_pct = 0
    try:
        cpus = os.cpu_count() or 1
        loadavg_fn = getattr(os, "getloadavg", None)
        if platform.system() != "Windows" and callable(loadavg_fn):
            loads = loadavg_fn()
            load1 = float(loads[0]) if loads else 0.0
            cpu_pct = int(min(100, max(0, (load1 / cpus) * 100)))
        elif psutil is not None:
            cpu_pct = int(max(0, min(100, psutil.cpu_percent(interval=0.0))))
        else:
            cpu_pct = 0
    except Exception:
        cpu_pct = 0
    try:
        mem_total = 0
        mem_avail = 0
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("MemTotal:"):
                    mem_total = int(ln.split()[1])
                elif ln.startswith("MemAvailable:"):
                    mem_avail = int(ln.split()[1])
        if mem_total > 0:
            mem_pct = int(min(100, max(0, ((mem_total - mem_avail) / mem_total) * 100)))
    except Exception:
        mem_pct = 0
    try:
        target = Path(server_dir) if server_dir else Path("/")
        if not target.exists():
            target = Path("/")
        d = shutil.disk_usage(target)
        if d.total > 0:
            disk_pct = int(min(100, max(0, (d.used / d.total) * 100)))
    except Exception:
        disk_pct = 0
    return {"cpu_pct": cpu_pct, "mem_pct": mem_pct, "disk_pct": disk_pct}


def get_server_metrics(pid: int | None, ram_used_mb, ram_alloc_mb):
    """Server-process specific metrics for dashboard graphs."""
    cpu_pct = 0
    mem_pct = 0
    disk_pct = 0

    if ram_used_mb is not None and ram_alloc_mb and ram_alloc_mb > 0:
        try:
            mem_pct = int(max(0, min(100, round((float(ram_used_mb) / float(ram_alloc_mb)) * 100))))
        except Exception:
            mem_pct = 0

    if not pid:
        proc_metric_cache["pid"] = None
        proc_metric_cache["io_bytes"] = 0
        proc_metric_cache["io_time"] = 0.0
        proc_metric_cache["proc_jiffies"] = 0
        proc_metric_cache["sys_jiffies"] = 0
        return {"cpu_pct": cpu_pct, "mem_pct": mem_pct, "disk_pct": disk_pct}

    if psutil is None:
        # Linux /proc fallback (works without psutil)
        try:
            proc_stat = Path(f"/proc/{pid}/stat")
            cpu_stat = Path("/proc/stat")
            io_stat = Path(f"/proc/{pid}/io")
            if proc_stat.exists() and cpu_stat.exists():
                pvals = proc_stat.read_text(encoding="utf-8", errors="replace").split()
                proc_j = int(pvals[13]) + int(pvals[14])
                cvals = cpu_stat.read_text(encoding="utf-8", errors="replace").splitlines()[0].split()[1:]
                sys_j = sum(int(v) for v in cvals)
                prev_pid = proc_metric_cache["pid"]
                prev_proc_j = int(proc_metric_cache["proc_jiffies"])
                prev_sys_j = int(proc_metric_cache["sys_jiffies"])
                if prev_pid == pid and sys_j > prev_sys_j and proc_j >= prev_proc_j:
                    d_proc = proc_j - prev_proc_j
                    d_sys = sys_j - prev_sys_j
                    cpu_count = float(os.cpu_count() or 1)
                    cpu_pct = int(max(0, min(100, round((d_proc / d_sys) * 100 * cpu_count))))
                proc_metric_cache["proc_jiffies"] = proc_j
                proc_metric_cache["sys_jiffies"] = sys_j

            now = time.time()
            total_io = 0
            if io_stat.exists():
                rb = 0
                wb = 0
                for ln in io_stat.read_text(encoding="utf-8", errors="replace").splitlines():
                    if ln.startswith("read_bytes:"):
                        rb = int((ln.split(":")[1] or "0").strip())
                    elif ln.startswith("write_bytes:"):
                        wb = int((ln.split(":")[1] or "0").strip())
                total_io = rb + wb
            prev_pid = proc_metric_cache["pid"]
            prev_io = int(proc_metric_cache["io_bytes"])
            prev_t = float(proc_metric_cache["io_time"])
            if prev_pid == pid and now > prev_t and total_io >= prev_io:
                bps = (total_io - prev_io) / max(0.001, (now - prev_t))
                disk_pct = int(max(0, min(100, round((bps / (20 * 1024 * 1024)) * 100))))
            proc_metric_cache["io_bytes"] = total_io
            proc_metric_cache["io_time"] = now
            proc_metric_cache["pid"] = pid
        except Exception:
            pass
        return {"cpu_pct": cpu_pct, "mem_pct": mem_pct, "disk_pct": disk_pct}

    try:
        proc = psutil.Process(pid)

        # Process cpu_percent can exceed 100 on multi-core systems.
        raw_cpu = float(proc.cpu_percent(interval=None))
        cpu_count = float(psutil.cpu_count() or 1)
        cpu_pct = int(max(0, min(100, raw_cpu / cpu_count)))

        if mem_pct <= 0:
            mem_pct = int(max(0, min(100, round(float(proc.memory_percent())))))

        io = proc.io_counters()
        now = time.time()
        total_io = int(io.read_bytes + io.write_bytes)
        prev_pid = proc_metric_cache["pid"]
        prev_io = int(proc_metric_cache["io_bytes"])
        prev_t = float(proc_metric_cache["io_time"])

        if prev_pid == pid and now > prev_t and total_io >= prev_io:
            bps = (total_io - prev_io) / max(0.001, (now - prev_t))
            # 20 MB/s or more maps to 100%
            disk_pct = int(max(0, min(100, round((bps / (20 * 1024 * 1024)) * 100))))

        proc_metric_cache["pid"] = pid
        proc_metric_cache["io_bytes"] = total_io
        proc_metric_cache["io_time"] = now
    except Exception:
        pass

    return {"cpu_pct": cpu_pct, "mem_pct": mem_pct, "disk_pct": disk_pct}


def get_syncthing_executable():
    """Return usable syncthing executable path or None."""
    from_path = shutil.which("syncthing")
    if from_path:
        return from_path
    syn_name = "syncthing.exe" if platform.system() == "Windows" else "syncthing"
    local_bin = get_bin_dir() / syn_name
    if local_bin.exists():
        return str(local_bin)
    return None


def start_syncthing_background() -> bool:
    """Attempt to start Syncthing daemon in background."""
    exe = get_syncthing_executable()
    if not exe:
        return False
    try:
        cmd = [exe, "serve", "--no-browser"]
        if platform.system() == "Windows":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
        else:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False


def ensure_syncthing_running(timeout_s: float = 4.0) -> bool:
    """Best-effort: ensure Syncthing API is reachable."""
    # Running instance may be reachable without API key (noauth endpoint).
    try:
        if st_api.is_running_noauth():
            return True
    except Exception:
        pass

    # Already reachable
    if st_api.get_my_id():
        return True

    # Try to start service
    if not start_syncthing_background():
        return False

    end = time.time() + timeout_s
    while time.time() < end:
        st_api.refresh_api_key()
        if st_api.get_my_id():
            return True
        time.sleep(0.25)
    return False


def can_edit_server_files(cfg) -> bool:
    """Server files should not be edited while host actions are in progress or server is running."""
    if mc_server.is_running():
        return False
    if is_task_running():
        return False
    lock = lock_manager.get_lock(cfg.get("shared_dir", "")) if cfg.get("shared_dir") else None
    if lock and lock.get("host") == load_local_user() and not lock.get("expired"):
        return False
    return True


def validate_paths(cfg, require_server: bool = True, require_shared: bool = True) -> tuple[bool, str]:
    server_dir = normalize_path_value(cfg.get("server_dir", ""))
    shared_dir = normalize_path_value(cfg.get("shared_dir", ""))

    if require_server and not server_dir:
        return False, "Server folder is not configured."
    if require_shared and not shared_dir:
        return False, "Shared folder is not configured."

    if require_server:
        server = Path(server_dir)
        if platform.system() == "Windows":
            s = server_dir.replace("\\", "/").lower()
            if s.startswith("/home/") or s.startswith("home/"):
                return False, "Current Server folder looks like a Linux path. Please set a valid Windows folder in Options."
        if not server.exists() or not server.is_dir():
            return False, f"Server folder does not exist: {server}"
        run_script_exists = (server / "run.bat").exists() or (server / "start.bat").exists() or (server / "run.sh").exists()
        jar_name = str(cfg.get("server_jar", "server.jar")).strip() or "server.jar"
        if not run_script_exists:
            if not (server / jar_name).exists():
                jars = sorted([p.name for p in server.glob("*.jar") if p.is_file()])
                if not jars:
                    return False, f"No .jar file found in server folder: {server}"
                return False, f"Configured jar '{jar_name}' not found. Available jars: {', '.join(jars[:6])}"

    if require_shared:
        try:
            ensure_shared_layout(shared_dir)
        except Exception as e:
            return False, f"Shared folder is not accessible: {e}"

    return True, ""


def get_setup_state(cfg) -> tuple[bool, str]:
    server_dir = normalize_path_value(cfg.get("server_dir", ""))
    shared_dir = normalize_path_value(cfg.get("shared_dir", ""))
    if not server_dir or not shared_dir:
        return False, "Server and shared folders are not configured."

    if platform.system() == "Windows":
        s_server = server_dir.replace("\\", "/").lower()
        s_shared = shared_dir.replace("\\", "/").lower()
        if s_server.startswith("/home/") or s_server.startswith("home/"):
            return False, "Server folder is still set to a Linux path."
        if s_shared.startswith("/home/") or s_shared.startswith("home/"):
            return False, "Shared folder is still set to a Linux path."

    server = Path(server_dir)
    shared = Path(shared_dir)
    if not server.exists() or not server.is_dir():
        return False, f"Server folder does not exist: {server}"
    if not shared.exists() or not shared.is_dir():
        return False, f"Shared folder does not exist: {shared}"

    try:
        if server.resolve() == shared.resolve():
            return False, "Server and shared folders should be different."
    except Exception:
        pass

    run_script_exists = (server / "run.bat").exists() or (server / "start.bat").exists() or (server / "run.sh").exists()
    jar_name = str(cfg.get("server_jar", "server.jar")).strip() or "server.jar"
    if not run_script_exists and not (server / jar_name).exists():
        if not any(server.glob("*.jar")):
            return False, "No .jar file found in server folder."

    return True, ""


def build_connectivity_diagnostics(cfg):
    shared_dir = normalize_path_value(cfg.get("shared_dir", ""))
    server_dir = normalize_path_value(cfg.get("server_dir", ""))
    setup_ok, setup_msg = get_setup_state(cfg)
    pkey = ensure_project_key(cfg)
    shared_marker = Path(shared_dir) / ".mc_project_key" if shared_dir else None
    autoconfig = Path(shared_dir) / ".mc_autoconfig.json" if shared_dir else None
    lock = lock_manager.get_lock(shared_dir) if shared_dir else None
    syn_health = st_api.get_health("mc-shared")

    items = []

    def add(name, status, detail, fix=""):
        items.append({
            "name": name,
            "status": status,  # pass / warn / fail
            "detail": detail,
            "fix": fix,
        })

    if not shared_dir:
        add("Shared Folder", "fail", "Shared folder path is not configured.", "Set Shared Folder in setup/options.")
    elif not Path(shared_dir).exists():
        add("Shared Folder", "fail", f"Shared folder does not exist: {shared_dir}", "Set a valid synced folder path.")
    else:
        add("Shared Folder", "pass", f"Using: {shared_dir}")

    if not server_dir:
        add("Server Folder", "warn", "Server folder is not configured yet.", "Set Server Folder path on this PC.")
    elif not Path(server_dir).exists():
        add("Server Folder", "fail", f"Server folder does not exist: {server_dir}", "Set a valid local server path.")
    else:
        add("Server Folder", "pass", f"Using: {server_dir}")

    if pkey:
        add("Project Key", "pass", f"Key loaded: {pkey[:8]}...")
    else:
        add("Project Key", "fail", "Project key missing.", "Run Auto Fix or save settings again.")

    if shared_marker and shared_marker.exists():
        try:
            mk = shared_marker.read_text(encoding="utf-8", errors="replace").strip()
            if mk and mk == pkey:
                add("Shared Key Marker", "pass", ".mc_project_key is synced and matches.")
            else:
                add("Shared Key Marker", "warn", ".mc_project_key exists but mismatched/empty.", "Run Auto Fix on both PCs.")
        except Exception:
            add("Shared Key Marker", "warn", ".mc_project_key could not be read.", "Check folder permissions.")
    else:
        add("Shared Key Marker", "warn", ".mc_project_key not found in shared folder.", "Run Auto Fix to generate/sync it.")

    if autoconfig and autoconfig.exists():
        add("Shared Auto Config", "pass", ".mc_autoconfig.json present.")
    else:
        add("Shared Auto Config", "warn", ".mc_autoconfig.json missing.", "Save settings once or run Auto Fix.")

    if lock:
        ex = "expired" if lock.get("expired") else "active"
        lk = str(lock.get("project_key", "") or "").strip()
        if lk and lk != pkey:
            add("Host Lock", "warn", f"Lock exists ({ex}) but belongs to different project key.", "Use correct shared folder/project.")
        else:
            add("Host Lock", "pass" if not lock.get("expired") else "warn", f"Lock by {lock.get('host','?')} ({ex}).")
    else:
        add("Host Lock", "warn", "No active host lock found.", "Normal if no one is hosting.")

    if not syn_health.get("running"):
        add("Syncthing", "fail", "Syncthing is not running.", "Start Syncthing and accept pending requests.")
    else:
        peers = int(syn_health.get("connected_peers", 0) or 0)
        fexists = bool(syn_health.get("folder_exists"))
        paused = bool(syn_health.get("folder_paused"))
        if not fexists:
            add("Syncthing Folder", "warn", "mc-shared folder not ensured yet.", "Run Auto Fix or accept folder in Syncthing UI.")
        elif paused:
            add("Syncthing Folder", "warn", "Folder is paused.", "Resume folder in Syncthing.")
        else:
            add("Syncthing Folder", "pass", f"Folder active. Connected peers: {peers}.")

    if setup_ok:
        add("Setup State", "pass", "Setup looks valid on this machine.")
    else:
        add("Setup State", "warn", setup_msg or "Setup incomplete.", "Run wizard Auto Fix or complete missing paths.")

    counts = {
        "pass": sum(1 for i in items if i["status"] == "pass"),
        "warn": sum(1 for i in items if i["status"] == "warn"),
        "fail": sum(1 for i in items if i["status"] == "fail"),
    }
    return {
        "ok": counts["fail"] == 0,
        "counts": counts,
        "items": items,
    }


def _tail_text_lines(path: Path, max_lines: int = 300) -> list[str]:
    """Read last N lines from a text file with best-effort decoding."""
    if not path.exists() or not path.is_file():
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= max_lines:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
            text = data.decode("utf-8", errors="replace")
            lines = [ln.rstrip("\r") for ln in text.splitlines()]
            return lines[-max_lines:]
    except Exception:
        return []


def collect_log_details(server_dir: str) -> dict:
    """Return aternos-style log diagnostics: warnings, errors, crash reports."""
    p_err = re.compile(r"(error|exception|fatal|crash|traceback|caused by)", re.IGNORECASE)
    p_warn = re.compile(r"\bwarn(?:ing)?\b", re.IGNORECASE)
    p_ignore = re.compile(r"(launcher|deprecation warning)", re.IGNORECASE)
    server = Path(server_dir) if server_dir else None
    lines_pool: list[tuple[str, str]] = []

    # in-memory live logs
    for ln in mc_server.get_logs(280):
        lines_pool.append(("live", ln))

    # file-based logs from latest.log for deeper history
    if server and server.exists():
        latest_log = server / "logs" / "latest.log"
        for ln in _tail_text_lines(latest_log, 420):
            lines_pool.append(("latest.log", ln))

    seen = set()
    issues = []
    warn_count = 0
    err_count = 0
    for source, ln in lines_pool:
        if not ln or p_ignore.search(ln):
            continue
        level = ""
        if p_err.search(ln):
            level = "error"
            err_count += 1
        elif p_warn.search(ln):
            level = "warn"
            warn_count += 1
        if not level:
            continue
        key = (level, ln.strip())
        if key in seen:
            continue
        seen.add(key)
        issues.append({"level": level, "source": source, "line": ln[-500:]})
        if len(issues) >= 160:
            break

    crashes = []
    if server and server.exists():
        crash_dir = server / "crash-reports"
        if crash_dir.exists():
            files = sorted(
                [f for f in crash_dir.iterdir() if f.is_file()],
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )[:14]
            for f in files:
                headline = ""
                lines = _tail_text_lines(f, 220)
                for ln in lines:
                    l = ln.strip()
                    if l.startswith("Description:") or "Exception" in l or "Caused by:" in l:
                        headline = l[:220]
                        break
                if not headline and lines:
                    headline = lines[0][:220]
                crashes.append({
                    "name": f.name,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "headline": headline or "Crash report",
                })

    return {
        "ok": True,
        "warn_count": warn_count,
        "error_count": err_count,
        "crash_count": len(crashes),
        "issues": issues,
        "crash_reports": crashes,
    }


def ensure_project_key(cfg) -> str:
    """Ensure project key exists and is synced with shared folder marker file."""
    key = str(cfg.get("project_key", "") or "").strip()
    shared_dir = normalize_path_value(cfg.get("shared_dir", ""))
    marker = Path(shared_dir) / ".mc_project_key" if shared_dir else None

    if marker and marker.exists():
        try:
            marker_key = marker.read_text(encoding="utf-8").strip()
            if marker_key:
                if marker_key != key:
                    cfg["project_key"] = marker_key
                    save_config(cfg)
                return marker_key
        except Exception:
            pass

    if not key:
        key = secrets.token_hex(8)
        cfg["project_key"] = key
        save_config(cfg)

    if marker:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            if not marker.exists() or marker.read_text(encoding="utf-8").strip() != key:
                marker.write_text(key, encoding="utf-8")
        except Exception:
            pass
    return key


def _shared_autoconfig_path(shared_dir: str) -> Path | None:
    s = normalize_path_value(shared_dir)
    if not s:
        return None
    return Path(s) / ".mc_autoconfig.json"


def write_shared_autoconfig(cfg) -> None:
    shared_dir = normalize_path_value(cfg.get("shared_dir", ""))
    p = _shared_autoconfig_path(shared_dir)
    if not p:
        return
    payload = {
        "project_name": str(cfg.get("project_name", "") or "").strip() or "Minecraft Server",
        "project_key": str(cfg.get("project_key", "") or "").strip(),
        "server_jar": str(cfg.get("server_jar", "server.jar") or "server.jar").strip(),
        "ram": str(cfg.get("ram", "4G") or "4G").strip(),
        "max_players": int(cfg.get("max_players", 20) or 20),
        "whitelist_enabled": bool(cfg.get("whitelist_enabled", False)),
        "updated_at": datetime.now().isoformat(),
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_shared_autoconfig(shared_dir: str) -> dict:
    p = _shared_autoconfig_path(shared_dir)
    if not p or not p.exists() or not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace")) or {}
    except Exception:
        return {}


def _guess_local_server_dir(cfg) -> str:
    """Best-effort local server path guess for setup automation."""
    current = normalize_path_value(cfg.get("server_dir", ""))
    manual = normalize_path_value(cfg.get("manual_server_dir", ""))
    jar_name = str(cfg.get("server_jar", "server.jar") or "server.jar").strip() or "server.jar"

    def score_dir(p: Path) -> int:
        try:
            if not p.exists() or not p.is_dir():
                return -1
            s = 0
            name = p.name.lower()
            if (p / jar_name).exists():
                s += 140
            if (p / "run.sh").exists() or (p / "run.bat").exists() or (p / "start.bat").exists():
                s += 100
            jars = [j for j in p.glob("*.jar") if j.is_file()]
            if jars:
                s += 70 + min(25, len(jars) * 5)
            if (p / "server.properties").exists():
                s += 18
            if "server" in name or "minecraft" in name:
                s += 16
            if "pack" in name:
                s += 8
            return s
        except Exception:
            return -1

    candidates: list[Path] = []
    for raw in (manual, current):
        if raw:
            candidates.append(Path(raw))

    home = Path.home()
    candidates.extend(
        [
            home / "mc-server",
            home / "MinecraftServer",
            home / "minecraft-server",
            home / "Desktop",
            home / "Downloads",
            Path.cwd(),
        ]
    )

    prism_root = home / ".local/share/PrismLauncher/instances"
    if prism_root.exists() and prism_root.is_dir():
        try:
            for inst in prism_root.iterdir():
                if not inst.is_dir():
                    continue
                candidates.append(inst)
                try:
                    for child in inst.iterdir():
                        if child.is_dir():
                            candidates.append(child)
                except Exception:
                    pass
        except Exception:
            pass

    best_path = ""
    best_score = -1
    seen: set[str] = set()
    for c in candidates:
        try:
            k = str(c.resolve())
        except Exception:
            k = str(c)
        if k in seen:
            continue
        seen.add(k)
        s = score_dir(c)
        if s > best_score:
            best_score = s
            best_path = str(c)
    return best_path if best_score >= 70 else ""


def _select_best_server_jar(server_dir: str, preferred_jar: str) -> str:
    preferred = str(preferred_jar or "server.jar").strip() or "server.jar"
    sdir = Path(server_dir)
    if not sdir.exists() or not sdir.is_dir():
        return preferred
    if (sdir / preferred).exists():
        return preferred
    jars = sorted([p.name for p in sdir.glob("*.jar") if p.is_file()])
    if not jars:
        return preferred
    rank_words = ("server", "forge", "fabric", "paper", "purpur", "spigot", "minecraft")

    def score(name: str) -> int:
        n = name.lower()
        sc = 0
        if n == "server.jar":
            sc += 60
        if n.endswith(".jar"):
            sc += 8
        for idx, w in enumerate(rank_words):
            if w in n:
                sc += max(5, 25 - idx * 2)
        return sc

    jars.sort(key=lambda j: (score(j), -len(j)), reverse=True)
    return jars[0]


def resolve_world_folder(
    cfg,
    *,
    allow_server_fallback: bool = True,
    create_shared_world: bool = False,
) -> Path | None:
    """Resolve folder for world actions with safe preference to shared world_latest."""
    override = normalize_path_value(cfg.get("world_dir_override", ""))
    if override:
        p = Path(override)
        if p.exists() and p.is_dir():
            return p

    shared = normalize_path_value(cfg.get("shared_dir", ""))
    if shared:
        sp = Path(shared)
        world_latest = sp / "world_latest"
        world_classic = sp / "world"

        if world_latest.exists() and world_latest.is_dir():
            return world_latest
        if world_classic.exists() and world_classic.is_dir():
            return world_classic
        if create_shared_world:
            try:
                ensure_shared_layout(sp)
                return world_latest
            except Exception:
                pass
        # Shared folder exists but world dirs are absent.
        if sp.exists() and sp.is_dir():
            return sp

    if not allow_server_fallback:
        return None

    server = normalize_path_value(cfg.get("server_dir", ""))
    if server:
        sv = Path(server)
        for c in (sv / "world", sv / "world_latest"):
            if c.exists() and c.is_dir():
                return c
    return None


def _finalize_stop_flow(cfg, progress_cb=None, reason="normal"):
    global recovery_failures, recovery_next_try
    shared = Path(cfg["shared_dir"])
    server = Path(cfg["server_dir"])
    cb = progress_cb if progress_cb is not None else (lambda *_: None)

    cb(10, "Stopping Server & Tunnel...")
    mc_server.prepare_for_copy()
    mc_server.stop()
    t_manager.stop()

    cb(40, "Creating timestamped backup...")
    backup_manager.create_timestamped_backup(
        server,
        shared / "backups",
        cfg.get("backup_keep", 5),
        progress_cb=lambda p, m: cb(40 + int(p * 0.2), m),
    )
    runtime_health["last_backup_time"] = datetime.now().isoformat()

    cb(70, "Syncing world to shared...")
    backup_manager.copy_world(server, shared / "world_latest", progress_cb=lambda p, m: cb(70 + int(p * 0.2), m))

    cb(90, "Releasing lock & Resuming sync...")
    lock_manager.remove_lock(shared)
    st_api.set_paused("mc-shared", False)
    # Push lock removal/world updates to peers immediately after resume.
    st_api.scan_folder("mc-shared")

    global last_sync_time
    last_sync_time = datetime.now().isoformat()

    with state_lock:
        host_session["active"] = False
        host_session["ready"] = False
        host_session["recovering"] = False
    recovery_failures = 0
    recovery_next_try = 0.0
    runtime_health["last_recovery_reason"] = reason
    runtime_health["last_recovery_time"] = datetime.now().isoformat()
    runtime_health["last_finalize_result"] = "ok"
    cb(100, "Recovered and synced" if reason == "unexpected" else "Done!")


def monitor_unexpected_stop():
    """Detect external server closure and auto-run safe sync finalize."""
    global last_player_poll, last_player_stats_poll, recovery_failures, recovery_next_try
    while True:
        time.sleep(1.5)
        with state_lock:
            active = host_session["active"]
            recovering = host_session["recovering"]
            cfg = dict(host_session["last_cfg"]) if host_session["last_cfg"] else {}
        if not active or recovering:
            continue
        if mc_server.is_running():
            # Transition to ready once process confirms startup message.
            if mc_server.is_ready() or mc_server.get_uptime_seconds() >= 15:
                with state_lock:
                    host_session["ready"] = True
            # Periodically refresh "list" output so player roster stays accurate.
            if time.time() - last_player_poll >= 20:
                mc_server.send_command("list")
                last_player_poll = time.time()
            if time.time() - last_player_stats_poll >= 8:
                for pname in mc_server.get_online_players():
                    mc_server.send_command(f"data get entity {pname}")
                last_player_stats_poll = time.time()
            continue
        # Process died while session active -> recover flow.
        if cfg.get("server_dir") and cfg.get("shared_dir"):
            now = time.time()
            if recovery_failures >= MAX_RECOVERY_RETRIES:
                with state_lock:
                    host_session["active"] = False
                    host_session["recovering"] = False
                runtime_health["last_error"] = "Recovery retry limit reached. Manual intervention required."
                runtime_health["last_finalize_result"] = "error"
                continue
            if now < recovery_next_try:
                continue

            def task(progress_cb):
                global recovery_failures, recovery_next_try
                with state_lock:
                    host_session["recovering"] = True
                try:
                    _finalize_stop_flow(cfg, progress_cb=progress_cb, reason="unexpected")
                except Exception as e:
                    recovery_failures += 1
                    delay = min(300, 5 * (2 ** (recovery_failures - 1)))
                    recovery_next_try = time.time() + delay
                    runtime_health["last_error"] = f"Recovery failed ({recovery_failures}/{MAX_RECOVERY_RETRIES}): {e}"
                    runtime_health["last_finalize_result"] = "error"
                    with state_lock:
                        host_session["recovering"] = False
                    raise
            run_background_task(task, action="recovering")


def monitor_lock_heartbeat():
    """Refresh host lock while this node is actively hosting."""
    while True:
        time.sleep(HEARTBEAT_INTERVAL_S)
        with state_lock:
            active = bool(host_session["active"])
            cfg = dict(host_session["last_cfg"]) if host_session["last_cfg"] else {}
        if not active:
            continue
        if not cfg.get("shared_dir"):
            continue
        # Keep lock fresh only while server process is up (including startup).
        if not mc_server.is_running() and not is_task_running():
            continue
        pkey = ensure_project_key(cfg)
        ok, msg = lock_manager.refresh_lock(cfg["shared_dir"], load_local_user(), pkey)
        if not ok:
            runtime_health["last_error"] = f"Lock heartbeat failed: {msg}"


def monitor_node_presence():
    """Continuously publish this node's presence into the shared control folder."""
    while True:
        time.sleep(4.0)
        try:
            cfg = load_config()
            publish_local_presence(cfg)
        except Exception:
            pass


last_remote_command_id = ""


def monitor_remote_host_dispatch():
    """Consume remote host commands addressed to this node from shared control channel."""
    global last_remote_command_id
    while True:
        time.sleep(1.3)
        try:
            cfg = load_config()
            shared = normalize_path_value(cfg.get("shared_dir", ""))
            if not shared:
                continue
            cmd_file = _commands_dir(shared) / f"{get_local_node_id()}.json"
            cmd = _read_json_safe(cmd_file)
            if not isinstance(cmd, dict):
                continue
            req_id = str(cmd.get("request_id", "") or "").strip()
            if not req_id or req_id == last_remote_command_id:
                continue
            pkey = str(cfg.get("project_key", "") or "").strip() or ensure_project_key(cfg)
            cmd_key = str(cmd.get("project_key", "") or "").strip()
            if pkey and cmd_key and cmd_key != pkey:
                last_remote_command_id = req_id
                try:
                    cmd_file.unlink(missing_ok=True)
                except Exception:
                    pass
                continue

            action = str(cmd.get("action", "") or "").strip().lower()
            ok, msg = post_local_host_action(action)
            low_msg = str(msg or "").lower()
            transient_local_http_fail = (
                (not ok)
                and (
                    "connection refused" in low_msg
                    or "timed out" in low_msg
                    or "failed to establish" in low_msg
                    or "temporarily unavailable" in low_msg
                )
            )
            if transient_local_http_fail:
                # Keep command file; likely local dashboard HTTP just not ready yet.
                continue
            ack = {
                "request_id": req_id,
                "target_node_id": get_local_node_id(),
                "action": action,
                "ok": bool(ok),
                "msg": msg,
                "time": datetime.now().isoformat(),
                "ts": time.time(),
            }
            try:
                _atomic_write_json(_acks_dir(shared) / f"{req_id}.json", ack)
            except Exception:
                pass
            last_remote_command_id = req_id
            try:
                cmd_file.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                st_api.scan_folder("mc-shared")
            except Exception:
                pass
        except Exception:
            pass


def monitor_housekeeping():
    """Periodic cleanup for stale temp/control files."""
    while True:
        time.sleep(HOUSEKEEPING_INTERVAL_S)
        try:
            cfg = load_config()
            removed = cleanup_runtime_artifacts(cfg)
            if removed:
                runtime_health["last_cleanup_time"] = datetime.now().isoformat()
                runtime_health["last_cleanup_removed"] = str(removed)
        except Exception:
            pass

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def _client_ip(self) -> str:
        try:
            xff = str(self.headers.get("X-Forwarded-For", "") or "").split(",")[0].strip()
            if xff:
                return xff
        except Exception:
            pass
        return str(self.client_address[0] if self.client_address else "")

    def _is_local_request(self) -> bool:
        ip = self._client_ip()
        return ip in ("127.0.0.1", "::1", "localhost")

    def _is_safe_console_command(self, cmd: str) -> tuple[bool, str]:
        s = str(cmd or "").strip()
        if not s:
            return False, "Command is empty"
        if len(s) > 240:
            return False, "Command is too long"
        if any(ch in s for ch in ("\n", "\r", "\0")):
            return False, "Invalid command characters"
        dangerous = (
            "stop",
            "restart",
            "op ",
            "deop ",
            "ban ",
            "pardon ",
            "whitelist ",
            "save-off",
            "save-on",
        )
        low = s.lower()
        if low in dangerous or any(low.startswith(d) for d in dangerous):
            return False, "Use dedicated dashboard controls for this command"
        if not re.fullmatch(r"[A-Za-z0-9 _:\-./@,+='\"\\[\\]()]+", s):
            return False, "Command contains unsupported characters"
        return True, ""

    def do_GET(self):
        cfg = load_config()
        if self.path == "/": self._serve_ui()
        elif self.path == "/status": self._json(self._get_status(cfg))
        elif self.path == "/diagnostics":
            self._json(build_connectivity_diagnostics(cfg))
        elif self.path == "/backup/list":
            shared = cfg.get("shared_dir", "")
            backups = backup_manager.list_backups(Path(shared) / "backups") if shared else []
            self._json({"backups": backups})
        elif self.path.startswith("/backup/get"):
            from urllib.parse import urlparse, parse_qs
            query = parse_qs(urlparse(self.path).query)
            name = query.get("name", [""])[0]
            shared = normalize_path_value(cfg.get("shared_dir", ""))
            backup_root = Path(shared) / "backups" if shared else None
            path = (backup_root / name).resolve() if backup_root and name else None
            if path and backup_root and backup_root.resolve() in path.parents and path.exists() and path.is_file():
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", f"attachment; filename={name}")
                    self.send_header("Content-Length", str(path.stat().st_size))
                    self.end_headers()
                    with open(path, "rb") as f:
                        shutil.copyfileobj(f, self.wfile)
                except Exception:
                    pass
            else:
                self.send_response(404)
                self.end_headers()
        elif self.path.startswith("/server/download"):
            ok_paths, msg_paths = validate_paths(cfg, require_server=True, require_shared=False)
            if not ok_paths:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg_paths.encode("utf-8", errors="replace"))
                return
            if not can_edit_server_files(cfg):
                self.send_response(409)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Cannot download while server/sync task is active.")
                return
            server_path = Path(normalize_path_value(cfg.get("server_dir", "")))
            if not server_path.exists() or not server_path.is_dir():
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Configured server folder does not exist.")
                return

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_project = "".join(ch for ch in cfg.get("project_name", "minecraft_server") if ch.isalnum() or ch in ("-", "_")).strip() or "minecraft_server"
            out_name = f"{safe_project}_server_files_{stamp}.zip"
            tmp = None
            try:
                tmp = tempfile.NamedTemporaryFile(prefix="mc_server_", suffix=".zip", delete=False)
                tmp_path = Path(tmp.name)
                tmp.close()
                server_root_resolved = server_path.resolve()
                with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for root, dirs, files in os.walk(server_path, topdown=True, followlinks=False):
                        root_path = Path(root)
                        # Never recurse into symlinked directories.
                        dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]
                        for fn in files:
                            fpath = root_path / fn
                            try:
                                rel = fpath.relative_to(server_path)
                            except Exception:
                                continue
                            # Skip non-regular/unreadable files instead of failing whole download.
                            try:
                                if not fpath.is_file() or fpath.is_symlink():
                                    continue
                                real_f = fpath.resolve(strict=False)
                                if server_root_resolved not in real_f.parents and real_f != server_root_resolved:
                                    continue
                                zf.write(fpath, arcname=str(rel))
                            except Exception:
                                continue

                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{out_name}"')
                self.send_header("Content-Length", str(tmp_path.stat().st_size))
                self.end_headers()
                with open(tmp_path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"Download failed: {e}".encode("utf-8", errors="replace"))
            finally:
                try:
                    if tmp is not None:
                        Path(tmp.name).unlink(missing_ok=True)
                except Exception:
                    pass
        elif self.path == "/logs": self._json({"logs": mc_server.get_logs()})
        elif self.path == "/logs/details":
            self._json(collect_log_details(cfg.get("server_dir", "")))
        elif self.path.startswith("/open-folder"):
            # open a folder in system file manager
            from urllib.parse import urlparse, parse_qs
            query = parse_qs(urlparse(self.path).query)
            raw_target = query.get("target", [""])[0]
            target = raw_target.strip().lower()
            custom_path = query.get("path", [""])[0].strip()
            norm = re.sub(r"[^a-z0-9]", "", target)
            folder = None
            if target == "server" and not can_edit_server_files(cfg):
                self._json({"ok": False, "msg": "Cannot open server files while hosting/sync task is active."})
                return
            if target == "backups":
                manual_backups = normalize_path_value(cfg.get("manual_backups_dir", ""))
                if manual_backups:
                    folder = Path(manual_backups)
                else:
                    shared = normalize_path_value(cfg.get("manual_shared_dir", "")) or normalize_path_value(cfg.get("shared_dir", ""))
                    if shared:
                        folder = ensure_shared_layout(shared) / "backups"
            elif target == "crash-reports":
                manual_crash = normalize_path_value(cfg.get("manual_crash_dir", ""))
                if manual_crash:
                    folder = Path(manual_crash)
                else:
                    server = normalize_path_value(cfg.get("server_dir", ""))
                    folder = Path(server) / "crash-reports" if server else None
                if folder and not folder.exists():
                    folder.mkdir(parents=True, exist_ok=True)
            elif norm in (
                "worldlatest",
                "sharedworld",
                "sharedworldfolder",
                "world",
                "worlds",
                "downloadworld",
            ):
                folder = resolve_world_folder(
                    cfg,
                    allow_server_fallback=True,
                    create_shared_world=True,
                )
            elif target == "shared":
                shared = normalize_path_value(cfg.get("manual_shared_dir", "")) or normalize_path_value(cfg.get("shared_dir", ""))
                if shared:
                    folder = ensure_shared_layout(shared)
            elif target == "server":
                server = normalize_path_value(cfg.get("manual_server_dir", "")) or normalize_path_value(cfg.get("server_dir", ""))
                folder = Path(server) if server else None
            elif target == "custom" and custom_path:
                folder = Path(custom_path).expanduser()
                if not folder.exists() or not folder.is_dir():
                    self._json({"ok": False, "msg": f"Folder does not exist: {folder}"})
                    return
            if folder:
                try:
                    if not folder.exists():
                        folder.mkdir(parents=True, exist_ok=True)
                    if platform.system() == "Windows":
                        subprocess.Popen(["explorer", str(folder)])
                    elif platform.system() == "Darwin":
                        subprocess.Popen(["open", str(folder)])
                    else:
                        subprocess.Popen(["xdg-open", str(folder)])
                    self._json({"ok": True, "path": str(folder)})
                    return
                except Exception as e:
                    self._json({"ok": False, "msg": str(e)})
                    return
            self._json({
                "ok": False,
                "msg": f"Folder path is not configured. target={raw_target}",
                "target": raw_target,
                "server_dir": str(cfg.get("server_dir", "") or ""),
                "shared_dir": str(cfg.get("shared_dir", "") or ""),
                "world_dir_override": str(cfg.get("world_dir_override", "") or ""),
            })
        elif self.path.startswith("/setup/list-dirs"):
            from urllib.parse import urlparse, parse_qs
            query = parse_qs(urlparse(self.path).query)
            path = query.get("path", [str(Path.home())])[0]
            self._json(self._list_dirs(path))
        elif self.path == "/task": self._json(get_task_status_snapshot())
        elif self.path == "/sync/preview":
            self._json({"ok": True, "pending": st_api.get_pending_count("mc-shared")})
        else: self.send_response(404); self.end_headers()

    def do_POST(self):
        cfg = load_config()
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except:
            body = {}

        if self.path == "/config/save":
            if "user" in body:
                save_local_user(body["user"])
                body.pop("user", None) # Don't save to shared config
            for k in body:
                cfg[k] = body[k]
            for key in (
                "server_dir",
                "shared_dir",
                "world_dir_override",
                "manual_server_dir",
                "manual_shared_dir",
                "manual_backups_dir",
                "manual_crash_dir",
            ):
                if key in cfg:
                    cfg[key] = normalize_path_value(cfg.get(key))
            try:
                cfg["max_players"] = int(cfg.get("max_players", 20))
            except Exception:
                cfg["max_players"] = 20
            cfg["max_players"] = max(1, min(500, cfg["max_players"]))
            cfg["whitelist_enabled"] = bool(cfg.get("whitelist_enabled", False))
            cfg["wizard_completed"] = bool(cfg.get("wizard_completed", False))

            warn_messages: list[str] = []
            if cfg.get("server_dir"):
                try:
                    update_server_properties(
                        cfg["server_dir"],
                        {
                            "max-players": cfg["max_players"],
                            "white-list": cfg["whitelist_enabled"],
                        },
                    )
                except Exception as e:
                    warn_messages.append(f"Could not update server.properties automatically: {e}")
            # Apply whitelist mode immediately if server is online.
            if mc_server.is_running():
                if cfg["whitelist_enabled"]:
                    mc_server.send_command("whitelist on")
                else:
                    mc_server.send_command("whitelist off")
                mc_server.send_command("whitelist reload")

            save_config(cfg)
            ensure_project_key(cfg)
            write_shared_autoconfig(cfg)
            _clear_status_cache()
            warn = ""
            if cfg.get("server_dir") and cfg.get("shared_dir") and cfg.get("server_dir") == cfg.get("shared_dir"):
                warn_messages.append("Server folder and shared folder are the same path. Recommended: keep them different.")
            if warn_messages:
                warn = " | ".join(warn_messages)
            self._json({"ok": True, "warn": warn})

        elif self.path == "/host/start":
            if not self._is_local_request():
                self._json({"ok": False, "msg": "Remote host control blocked. Open this app on the machine that should host the server."})
                return
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            if mc_server.is_running():
                self._json({"ok": False, "msg": "Server already running."})
                return
            ok_paths, msg_paths = validate_paths(cfg, require_server=True, require_shared=True)
            if not ok_paths:
                self._json({"ok": False, "msg": msg_paths})
                return
            pkey = ensure_project_key(cfg)
            existing = lock_manager.get_lock(cfg.get("shared_dir", "")) if cfg.get("shared_dir") else None
            if existing and not existing.get("expired"):
                ex_key = str(existing.get("project_key", "") or "").strip()
                if not ex_key:
                    self._json({"ok": False, "msg": "Legacy lock without project key detected. Use Force Clear only after confirming host is offline."})
                    return
                if ex_key != pkey:
                    self._json({"ok": False, "msg": "This shared folder is locked by a different project key."})
                    return
            if not self._start_host_flow(cfg):
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            self._json({"ok": True, "msg": "Starting..."})

        elif self.path == "/host/stop":
            if not self._is_local_request():
                self._json({"ok": False, "msg": "Remote host control blocked. Open this app on the machine that should host the server."})
                return
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            ok_paths, msg_paths = validate_paths(cfg, require_server=True, require_shared=True)
            if not ok_paths:
                self._json({"ok": False, "msg": msg_paths})
                return
            # Prevent stopping during startup window to avoid partial/corrupt transitions.
            with state_lock:
                session_ready = host_session["ready"]
                session_active = host_session["active"]
            if not session_active and not mc_server.is_running():
                self._json({"ok": False, "msg": "Server is already offline."})
                return
            if session_active and not session_ready and mc_server.is_running():
                self._json({"ok": False, "msg": "Server is still starting. Wait until status shows RUNNING."})
                return
            if not self._stop_host_flow(cfg):
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            self._json({"ok": True, "msg": "Stopping..."})

        elif self.path == "/host/restart":
            if not self._is_local_request():
                self._json({"ok": False, "msg": "Remote host control blocked. Open this app on the machine that should host the server."})
                return
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            if not mc_server.is_running():
                self._json({"ok": False, "msg": "Server is not running."})
                return
            with state_lock:
                session_ready = bool(host_session["ready"])
                session_active = bool(host_session["active"])
            if not session_active:
                self._json({"ok": False, "msg": "Restart is allowed only for active host session. Use Start from this app first."})
                return
            if not session_ready:
                self._json({"ok": False, "msg": "Server is still starting. Wait until status shows RUNNING."})
                return
            ok_paths, msg_paths = validate_paths(cfg, require_server=True, require_shared=True)
            if not ok_paths:
                self._json({"ok": False, "msg": msg_paths})
                return
            if not self._restart_host_flow(cfg):
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            self._json({"ok": True, "msg": "Restarting..."})

        elif self.path == "/host/kill":
            if not self._is_local_request():
                self._json({"ok": False, "msg": "Remote host control blocked. Open this app on the machine that should host the server."})
                return
            if not mc_server.is_running():
                self._json({"ok": False, "msg": "Server is not running."})
                return
            try:
                proc = mc_server.proc
                if proc is not None:
                    proc.kill()
                self._json({"ok": True, "msg": "Kill signal sent."})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)})
                return

        elif self.path == "/host/force":
            if not self._is_local_request():
                self._json({"ok": False, "msg": "Remote host control blocked. Open this app on the machine that should host the server."})
                return
            shared = normalize_path_value(cfg.get("shared_dir", ""))
            if not shared:
                self._json({"ok": False, "msg": "Shared folder is not configured."})
                return
            if mc_server.is_running() or is_task_running():
                self._json({"ok": False, "msg": "Stop/finalize server first, then force clear lock if still stuck."})
                return
            lock_manager.remove_lock(shared)
            with state_lock:
                host_session["active"] = False
                host_session["ready"] = False
                host_session["recovering"] = False
            self._json({"ok": True, "msg": "Lock cleared"})

        elif self.path == "/host/dispatch":
            if not self._is_local_request():
                self._json({"ok": False, "msg": "Remote dispatch blocked. Open this app locally on your machine."})
                return
            action = str(body.get("action", "") or "").strip().lower()
            target_node_id = str(body.get("target_node_id", "") or "").strip()
            ok, msg, req_id = dispatch_remote_host_action(cfg, target_node_id, action)
            self._json({"ok": bool(ok), "msg": msg, "request_id": req_id})

        elif self.path == "/backup/now":
            if is_task_running():
                self._json({"ok": False, "msg": "Wait for current operation to finish."})
                return
            ok_paths, msg_paths = validate_paths(cfg, require_server=True, require_shared=True)
            if not ok_paths:
                self._json({"ok": False, "msg": msg_paths})
                return
            # Run a one-off backup as a background task so UI can track progress
            def task(progress_cb):
                progress_cb(10, "Preparing backup...")
                bkp = backup_manager.create_timestamped_backup(
                    Path(cfg["server_dir"]),
                    Path(cfg["shared_dir"]) / "backups",
                    cfg.get("backup_keep", 5),
                    progress_cb=progress_cb,
                )
                if bkp is None:
                    raise RuntimeError("No world folders found to back up.")
                progress_cb(100, "Backup complete")

            run_background_task(task, action="backup")
            self._json({"ok": True, "msg": "Backup started"})

        elif self.path == "/backup/restore":
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            if mc_server.is_running():
                self._json({"ok": False, "msg": "Stop server before restore."})
                return
            ok_paths, msg_paths = validate_paths(cfg, require_server=True, require_shared=True)
            if not ok_paths:
                self._json({"ok": False, "msg": msg_paths})
                return
            name = str(body.get("name", ""))
            shared = cfg.get("shared_dir", "")
            if not name or not shared:
                self._json({"ok": False, "msg": "Missing backup name/shared_dir"})
                return
            backup_root = (Path(shared) / "backups").resolve()
            bzip = (backup_root / name).resolve()
            if backup_root not in bzip.parents:
                self._json({"ok": False, "msg": "Invalid backup path."})
                return
            server = Path(cfg.get("server_dir", ""))
            def task(progress_cb):
                ok = backup_manager.restore_backup(bzip, server, progress_cb=progress_cb)
                if not ok:
                    raise RuntimeError("Restore failed. Backup may be invalid.")
            run_background_task(task, action="restore")
            self._json({"ok": True, "msg": "Restore started"})

        elif self.path == "/command":
            cmd = str(body.get("cmd", ""))
            if not self._is_local_request():
                self._json({"ok": False, "msg": "Remote console command blocked. Run console commands from host machine only."})
                return
            ok_cmd, msg_cmd = self._is_safe_console_command(cmd)
            if not ok_cmd:
                self._json({"ok": False, "msg": msg_cmd})
                return
            if mc_server.is_running():
                mc_server.send_command(cmd)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "msg": "Server is offline"})

        elif self.path == "/players/refresh":
            if not mc_server.is_running():
                self._json({"ok": False, "msg": "Server is offline"})
                return
            mc_server.send_command("list")
            for pname in mc_server.get_online_players():
                mc_server.send_command(f"data get entity {pname}")
            self._json({"ok": True})

        elif self.path == "/players/action":
            if not mc_server.is_running():
                self._json({"ok": False, "msg": "Server is offline"})
                return
            action = str(body.get("action", "")).strip().lower()
            player = str(body.get("player", "")).strip()
            if not action or not player:
                self._json({"ok": False, "msg": "Missing action/player"})
                return
            cmd_map = {
                "kick": f"kick {player}",
                "op": f"op {player}",
                "deop": f"deop {player}",
                "ban": f"ban {player}",
                "pardon": f"pardon {player}",
                "wl_add": f"whitelist add {player}",
                "wl_remove": f"whitelist remove {player}",
            }
            cmd = cmd_map.get(action)
            if not cmd:
                self._json({"ok": False, "msg": "Unknown action"})
                return
            ok = True
            if action in ("wl_add", "wl_remove"):
                ok = mc_server.send_command("whitelist on") and ok
                ok = mc_server.send_command("whitelist reload") and ok
            ok = mc_server.send_command(cmd) and ok
            self._json({"ok": bool(ok), "msg": "Command sent" if ok else "Failed to send command"})

        elif self.path == "/sync/now":
            ok_paths, msg_paths = validate_paths(cfg, require_server=False, require_shared=True)
            if not ok_paths:
                self._json({"ok": False, "msg": msg_paths})
                return
            running = ensure_syncthing_running()
            if not running:
                self._json({"ok": False, "msg": "Syncthing not running"})
                return
            ensured = st_api.ensure_folder(Path(cfg.get("shared_dir", "")))
            if not ensured:
                self._json({"ok": False, "msg": "Syncthing API not authorized or folder not accepted yet. Open Syncthing UI and accept folder/device."})
                return
            ok = st_api.scan_folder("mc-shared")
            _clear_status_cache("sync")
            self._json({"ok": bool(ok), "msg": "Sync scan triggered" if ok else "Failed to trigger sync"})

        elif self.path == "/setup/quickstart":
            # Minimal one-click setup: ensure binaries/services and default folders.
            if not cfg.get("server_dir"):
                cfg["server_dir"] = str(Path.home() / "mc-server")
            if not cfg.get("shared_dir"):
                cfg["shared_dir"] = str(Path.home() / "mc-shared")
            server = Path(cfg["server_dir"])
            shared = Path(cfg["shared_dir"])
            server.mkdir(parents=True, exist_ok=True)
            ensure_shared_layout(shared)
            ensure_syncthing_binary()
            running = ensure_syncthing_running()
            ensured = st_api.ensure_folder(shared) if running else False
            save_config(cfg)
            ensure_project_key(cfg)
            write_shared_autoconfig(cfg)
            _clear_status_cache()
            self._json({"ok": bool(running and ensured), "server_dir": str(server), "shared_dir": str(shared), "msg": "Quickstart completed" if running and ensured else "Quickstart partial"})

        elif self.path == "/setup/validate":
            path_val = str(body.get("path", ""))
            self._json(self._validate_folder(path_val))

        elif self.path == "/setup/auto-fix":
            # Aggressive self-heal for onboarding + cross-device issues.
            actions: list[str] = []
            warn_messages: list[str] = []

            shared_in = normalize_path_value(body.get("shared_dir", cfg.get("shared_dir", "")))
            manual_shared = normalize_path_value(cfg.get("manual_shared_dir", ""))
            if not shared_in and manual_shared:
                shared_in = manual_shared
                actions.append("Used manual shared path from Access settings.")
            if shared_in:
                if normalize_path_value(cfg.get("shared_dir", "")) != shared_in:
                    actions.append("Updated shared folder path.")
                cfg["shared_dir"] = shared_in
            if not normalize_path_value(cfg.get("shared_dir", "")):
                self._json({"ok": False, "msg": "Shared folder is required for auto-fix."})
                return

            # Normalize config values first.
            for key in (
                "server_dir",
                "shared_dir",
                "world_dir_override",
                "manual_server_dir",
                "manual_shared_dir",
                "manual_backups_dir",
                "manual_crash_dir",
            ):
                cfg[key] = normalize_path_value(cfg.get(key))
            try:
                cfg["max_players"] = max(1, min(500, int(cfg.get("max_players", 20))))
            except Exception:
                cfg["max_players"] = 20
                actions.append("Reset max players to default (20).")
            cfg["whitelist_enabled"] = bool(cfg.get("whitelist_enabled", False))

            ram_mb = parse_ram_to_mb(str(cfg.get("ram", "") or ""))
            if not ram_mb or ram_mb < 512:
                cfg["ram"] = "4G"
                actions.append("Reset invalid RAM setting to 4G.")

            try:
                ensure_shared_layout(cfg["shared_dir"])
                actions.append("Verified shared folder layout (backups/world_latest).")
            except Exception as e:
                self._json({"ok": False, "msg": f"Cannot access shared folder: {e}"})
                return

            # Pull synced project defaults and converge locally.
            shared_meta = load_shared_autoconfig(cfg["shared_dir"])
            if shared_meta:
                for k in ("project_name", "server_jar", "ram"):
                    v = str(shared_meta.get(k, "") or "").strip()
                    if not v:
                        continue
                    cur = str(cfg.get(k, "") or "").strip()
                    if not cur or (k == "server_jar" and cur == "server.jar"):
                        cfg[k] = v
                        actions.append(f"Imported {k} from shared config.")
                try:
                    mp = int(shared_meta.get("max_players", cfg.get("max_players", 20)))
                    mp = max(1, min(500, mp))
                    if mp != int(cfg.get("max_players", 20)):
                        cfg["max_players"] = mp
                        actions.append("Imported max players from shared config.")
                except Exception:
                    pass
                if "whitelist_enabled" in shared_meta:
                    we = bool(shared_meta.get("whitelist_enabled"))
                    if we != bool(cfg.get("whitelist_enabled")):
                        cfg["whitelist_enabled"] = we
                        actions.append("Imported whitelist mode from shared config.")

            # Ensure project key and sync markers.
            prev_key = str(cfg.get("project_key", "") or "")
            pkey = ensure_project_key(cfg)
            if pkey and pkey != prev_key:
                actions.append("Synchronized project key.")

            # Repair/guess local server path.
            server_dir = normalize_path_value(cfg.get("server_dir", ""))
            server_ok = bool(server_dir and Path(server_dir).exists() and Path(server_dir).is_dir())
            if not server_ok:
                manual_server = normalize_path_value(cfg.get("manual_server_dir", ""))
                if manual_server and Path(manual_server).exists() and Path(manual_server).is_dir():
                    cfg["server_dir"] = manual_server
                    actions.append("Used manual server path from Access settings.")
                else:
                    guess = _guess_local_server_dir(cfg)
                    if guess:
                        cfg["server_dir"] = guess
                        actions.append("Auto-detected local server folder.")
                    else:
                        warn_messages.append("Could not auto-detect local server folder. Set server path manually in Options/Access.")

            # Jar auto-repair and server.properties sync.
            server_dir = normalize_path_value(cfg.get("server_dir", ""))
            if server_dir and Path(server_dir).exists() and Path(server_dir).is_dir():
                picked_jar = _select_best_server_jar(server_dir, str(cfg.get("server_jar", "server.jar")))
                if picked_jar and picked_jar != str(cfg.get("server_jar", "server.jar")):
                    cfg["server_jar"] = picked_jar
                    actions.append(f"Auto-selected server jar: {picked_jar}")
                try:
                    update_server_properties(
                        server_dir,
                        {
                            "max-players": int(cfg.get("max_players", 20)),
                            "white-list": bool(cfg.get("whitelist_enabled", False)),
                        },
                    )
                    actions.append("Updated server.properties (max-players/white-list).")
                except Exception as e:
                    warn_messages.append(f"Could not update server.properties automatically: {e}")

            # Keep marker/config in shared folder up to date.
            write_shared_autoconfig(cfg)
            actions.append("Updated shared auto-config marker.")

            # Syncthing self-heal with retry and immediate scan.
            ensure_syncthing_binary()
            running = ensure_syncthing_running(timeout_s=5.0)
            ensured = False
            if running:
                try:
                    st_api.refresh_api_key()
                except Exception:
                    pass
                for _ in range(3):
                    ensured = st_api.ensure_folder(Path(cfg["shared_dir"]))
                    if ensured:
                        break
                    time.sleep(0.6)
                    try:
                        st_api.refresh_api_key()
                    except Exception:
                        pass
                if ensured:
                    try:
                        st_api.scan_folder("mc-shared")
                        actions.append("Triggered Syncthing scan for shared folder.")
                    except Exception:
                        pass
                else:
                    warn_messages.append("Syncthing folder not ensured yet. Accept folder/device request in Syncthing UI.")
            else:
                warn_messages.append("Syncthing is not running. Start it once and keep it in background.")

            save_config(cfg)
            _clear_status_cache()
            publish_local_presence(cfg)
            setup_ok, setup_msg = get_setup_state(cfg)
            diag = build_connectivity_diagnostics(cfg)
            counts = diag.get("counts", {}) if isinstance(diag, dict) else {}
            diag_fail = int(counts.get("fail", 0) or 0)
            diag_warn = int(counts.get("warn", 0) or 0)

            if not setup_ok and setup_msg:
                warn_messages.append(setup_msg)
            if diag_fail > 0:
                warn_messages.append(f"{diag_fail} critical diagnostic issue(s) still remain.")

            self._json({
                "ok": bool(diag_fail == 0 and setup_ok),
                "setup_ok": bool(setup_ok),
                "server_dir": cfg.get("server_dir", ""),
                "shared_dir": cfg.get("shared_dir", ""),
                "project_key": cfg.get("project_key", ""),
                "server_jar": cfg.get("server_jar", "server.jar"),
                "syncthing_running": bool(running),
                "folder_ensured": bool(ensured),
                "actions": actions[:40],
                "diag_counts": {"fail": diag_fail, "warn": diag_warn, "pass": int(counts.get("pass", 0) or 0)},
                "warn": " | ".join([w for w in warn_messages if w]) if warn_messages else "",
                "msg": "Auto-fix completed." if (diag_fail == 0 and setup_ok) else "Auto-fix applied with remaining issues.",
            })

        elif self.path == "/setup/ensure-sync":
            shared_txt = normalize_path_value(body.get("shared_dir", cfg["shared_dir"]))
            if not shared_txt:
                self._json({"ok": False, "msg": "Shared folder path is missing.", "syncthing_running": False, "folder_ensured": False, "degraded": True})
                return
            shared = Path(shared_txt)
            # make sure physical folders exist (world_latest, backups)
            try:
                ensure_shared_layout(shared)
            except Exception:
                self._json({"ok": False, "msg": f"Cannot create shared folder layout at: {shared}", "syncthing_running": False, "folder_ensured": False, "degraded": True})
                return
            running = ensure_syncthing_running()
            ensured = st_api.ensure_folder(shared) if running else False
            _clear_status_cache()
            if not running:
                self._json({
                    "ok": True,
                    "degraded": True,
                    "msg": "Syncthing is not running right now. Setup saved; you can continue and start sync later.",
                    "syncthing_running": False,
                    "folder_ensured": False,
                })
                return
            if not ensured:
                self._json({
                    "ok": True,
                    "degraded": True,
                    "msg": "Syncthing is running but folder setup is pending. Accept folder/device in Syncthing UI.",
                    "syncthing_running": True,
                    "folder_ensured": False,
                })
                return
            self._json({"ok": True, "syncthing_running": True, "folder_ensured": True, "msg": "Sync setup verified."})
        else:
            self.send_response(404)
            self.end_headers()

    def _get_status(self, cfg):
        shared = normalize_path_value(cfg.get("shared_dir", ""))
        server_dir = normalize_path_value(cfg.get("server_dir", ""))
        publish_local_presence(cfg)
        project_key = _cached_value(
            f"project_key:{shared}",
            2.0,
            lambda: ensure_project_key(cfg),
        )
        def _resolve_local_ip():
            try:
                return socket.gethostbyname(socket.gethostname()) or "127.0.0.1"
            except Exception:
                return "127.0.0.1"

        local_ip = _cached_value("local_ip", 30.0, _resolve_local_ip)

        # suggestion for first-run server directory
        suggest = ""
        if not server_dir:
            # look in cwd for any .jar files
            def _suggest_from_cwd():
                cwd = Path.cwd()
                try:
                    if any(cwd.glob("*.jar")):
                        return str(cwd)
                except Exception:
                    pass
                return ""
            suggest = _cached_value("suggest_server_cwd", 8.0, _suggest_from_cwd)

        # syncthing installation check + runtime health
        syn_ok = _cached_value(
            "syn:binary_ok",
            20.0,
            lambda: bool(
                shutil.which("syncthing")
                or (get_bin_dir() / ("syncthing.exe" if platform.system() == "Windows" else "syncthing")).exists()
            ),
        )
        syn_health = _cached_value("syn:health", STATUS_TTL_SYN_HEALTH_S, lambda: st_api.get_health("mc-shared"))
        syn_status = "missing"
        if syn_ok and not syn_health.get("running"):
            syn_status = "stopped"
        if syn_health.get("running"):
            syn_status = "running"
            if syn_health.get("connected_peers", 0) > 0:
                syn_status = "connected"
        if syn_health.get("folder_paused") is True:
            syn_status = "paused"

        with state_lock:
            session_active = host_session["active"]
            session_ready = host_session["ready"]
            recovering = host_session["recovering"]

        running = mc_server.is_running()
        uptime_s = mc_server.get_uptime_seconds()
        ram_used_mb = mc_server.get_ram_mb()
        ram_alloc_mb = parse_ram_to_mb(cfg.get("ram", ""))
        ram_free_pct = None
        players_online = mc_server.get_online_players()
        players_info = mc_server.get_player_stats()
        serverm = get_server_metrics(mc_server.get_pid(), ram_used_mb, ram_alloc_mb)
        sysm = _cached_value(
            f"metrics:system:{server_dir}",
            1.5,
            lambda: get_system_metrics(server_dir),
        )
        sync_pending = _cached_value(
            "sync:pending",
            STATUS_TTL_SYNC_PENDING_S,
            lambda: st_api.get_pending_count("mc-shared") if syn_ok else 0,
        )
        props = _cached_value(
            f"server_props:{server_dir}",
            3.0,
            lambda: parse_server_properties(server_dir) if server_dir else {},
        )
        raw_max_players = props.get("max-players")
        if raw_max_players is None or str(raw_max_players).strip() == "":
            raw_max_players = str(cfg.get("max_players", 20))
        try:
            max_players_effective = int(str(raw_max_players).strip())
        except Exception:
            max_players_effective = int(cfg.get("max_players", 20))
        max_players_effective = max(1, min(500, max_players_effective))
        wl_raw = str(props.get("white-list", str(cfg.get("whitelist_enabled", False)))).strip().lower()
        whitelist_effective = wl_raw in ("true", "1", "yes", "on")
        if ram_used_mb is not None and ram_alloc_mb and ram_alloc_mb > 0:
            ram_free_pct = max(0, min(100, round((1 - (ram_used_mb / ram_alloc_mb)) * 100)))
        if running and not session_ready and uptime_s >= 15:
            # Fallback for servers where "Done..." marker is missing/changed.
            with state_lock:
                host_session["ready"] = True
                session_ready = True
        task_snapshot = get_task_status_snapshot()
        server_state = "offline"
        if task_snapshot.get("running") and task_snapshot.get("action") == "starting":
            server_state = "starting"
        elif task_snapshot.get("running") and task_snapshot.get("action") in ("stopping", "recovering"):
            server_state = "stopping"
        elif recovering:
            server_state = "recovering"
        elif running and not (session_ready or mc_server.is_ready()):
            server_state = "starting"
        elif running:
            server_state = "running"

        lock_info = _cached_value(
            f"lock_info:{shared}",
            STATUS_TTL_LOCK_INFO_S,
            lambda: lock_manager.get_lock(shared) if shared else None,
        )
        me = load_local_user()
        lock_key = str(lock_info.get("project_key", "")).strip() if lock_info else ""
        lock_project_mismatch = bool(lock_info and (not lock_key or lock_key != project_key))
        connect_hint = ""
        if lock_info and not lock_info.get("expired") and lock_info.get("host") != me and not lock_project_mismatch:
            connect_hint = str(lock_info.get("ui_url", "")).strip()

        setup_ok, setup_msg = _cached_value(
            f"setup_state:{server_dir}:{shared}:{cfg.get('server_jar', '')}",
            STATUS_TTL_SETUP_STATE_S,
            lambda: get_setup_state(cfg),
        )
        backups = _cached_value(
            f"backups:{shared}",
            STATUS_TTL_BACKUPS_S,
            lambda: backup_manager.list_backups(Path(shared) / "backups") if shared else [],
        )
        world_dir_effective = _cached_value(
            f"world_dir_effective:{shared}:{server_dir}:{cfg.get('world_dir_override', '')}:{cfg.get('manual_shared_dir', '')}:{cfg.get('manual_server_dir', '')}",
            2.5,
            lambda: str(resolve_world_folder(cfg) or ""),
        )
        can_open_server = _cached_value(
            f"can_open_server_files:{shared}:{server_dir}",
            1.0,
            lambda: can_edit_server_files(cfg),
        )
        return {
            "project_name": cfg.get("project_name", "Minecraft Server"),
            "running": running,
            "server_state": server_state,
            "server_ready": bool(session_ready or mc_server.is_ready()),
            "lock": lock_info,
            "user": me,
            "ram": cfg["ram"],
            "max_players": int(cfg.get("max_players", 20)),
            "max_players_effective": max_players_effective,
            "whitelist_enabled": bool(cfg.get("whitelist_enabled", False)),
            "whitelist_effective": whitelist_effective,
            "tunnel": cfg.get("tunnel", False),
            "st_id": syn_health.get("my_id"),
            "local_ip": local_ip,
            "tunnel_addr": t_manager.get_address(),
            "last_sync": {"time": last_sync_time} if last_sync_time else None,
            "setup_ok": bool(setup_ok),
            "setup_msg": setup_msg,
            "force_wizard": not bool(cfg.get("wizard_completed", False)),
            "backups": backups,
            # Extra fields so settings form can be pre-filled correctly
            "server_dir": cfg.get("server_dir", ""),
            "shared_dir": cfg.get("shared_dir", ""),
            "world_dir_override": cfg.get("world_dir_override", ""),
            "manual_server_dir": cfg.get("manual_server_dir", ""),
            "manual_shared_dir": cfg.get("manual_shared_dir", ""),
            "manual_backups_dir": cfg.get("manual_backups_dir", ""),
            "manual_crash_dir": cfg.get("manual_crash_dir", ""),
            "world_dir_effective": world_dir_effective,
            "server_jar": cfg.get("server_jar", "server.jar"),
            "suggest_server": suggest,
            "syncthing_ok": syn_ok,
            "syncthing_running": syn_health.get("running", False),
            "syncthing_connected_peers": syn_health.get("connected_peers", 0),
            "syncthing_folder_exists": syn_health.get("folder_exists", False),
            "syncthing_folder_paused": syn_health.get("folder_paused"),
            "syncthing_status": syn_status,
            "task": task_snapshot,
            "server_pid": mc_server.get_pid(),
            "server_uptime_s": uptime_s,
            "server_ram_mb": ram_used_mb,
            "server_ram_alloc_mb": ram_alloc_mb,
            "server_ram_free_pct": ram_free_pct,
            "players_online": players_online,
            "players_count": len(players_online),
            "players_info": players_info,
            "sync_pending_count": sync_pending,
            # Keep legacy keys but feed them with server-centric values for the main graphs.
            "cpu_pct": serverm["cpu_pct"],
            "mem_pct": serverm["mem_pct"],
            "disk_pct": serverm["disk_pct"],
            "server_cpu_pct": serverm["cpu_pct"],
            "server_mem_pct": serverm["mem_pct"],
            "server_disk_pct": serverm["disk_pct"],
            # Optional system-wide values (for diagnostics/advanced panels).
            "system_cpu_pct": sysm["cpu_pct"],
            "system_mem_pct": sysm["mem_pct"],
            "system_disk_pct": sysm["disk_pct"],
            "can_open_server_files": can_open_server,
            "health": dict(runtime_health),
            "connect_hint_url": connect_hint,
            "project_key": project_key,
            "lock_project_mismatch": lock_project_mismatch,
            "local_node_id": get_local_node_id(),
            "remote_nodes": _cached_value(
                f"remote_nodes:{shared}:{project_key}",
                2.0,
                lambda: get_remote_nodes(cfg),
            ),
        }

    def _list_dirs(self, path_str):
        p = Path(path_str).expanduser()
        if not p.exists() or not p.is_dir():
            p = Path.home()
        
        try:
            items = []
            # Parent dir
            items.append({"name": "..", "path": str(p.parent), "is_dir": True})
            
            for item in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                if item.is_dir():
                    items.append({"name": item.name, "path": str(item), "is_dir": True})
            return {"current": str(p), "items": items}
        except Exception as e:
            return {"error": str(e), "current": str(p), "items": []}

    def _validate_folder(self, path_str):
        p = Path(path_str).expanduser()
        if not p.exists() or not p.is_dir():
            return {"ok": False, "msg": "Not a directory"}
        
        jars = list(p.glob("*.jar"))
        eula = (p / "eula.txt").exists()
        
        return {
            "ok": True,
            "has_jar": len(jars) > 0,
            "jars": [j.name for j in jars],
            "has_eula": eula,
            "msg": "Valid server folder" if len(jars) > 0 else "No .jar files found"
        }

    def _start_host_flow(self, cfg):
        def task(progress_cb):
            shared = Path(cfg["shared_dir"])
            server = Path(cfg["server_dir"])
            pkey = ensure_project_key(cfg)
            lock_acquired = False
            tunnel_started = False
            with state_lock:
                host_session["last_cfg"] = dict(cfg)
                host_session["ready"] = False
            
            try:
                progress_cb(10, "Configuring Syncthing...")
                ensure_syncthing_running()
                st_api.ensure_folder(shared)

                progress_cb(20, "Acquiring host lock...")
                ok_lock, msg_lock = lock_manager.create_lock(shared, load_local_user(), pkey, no_expire=True)
                if not ok_lock:
                    raise RuntimeError(f"Failed to acquire host lock: {msg_lock}")
                lock_acquired = True
                with state_lock:
                    host_session["active"] = True
                # Ensure peers receive lock before folder is paused.
                st_api.scan_folder("mc-shared")
                time.sleep(1.2)
                st_api.set_paused("mc-shared", True)  # Pause world sync during run

                progress_cb(40, "Syncing world from shared...")
                backup_manager.copy_world(shared / "world_latest", server, progress_cb=lambda p,m: progress_cb(40+int(p*0.25), m))

                progress_cb(70, "Starting Tunnel & Server...")
                t_manager.start()
                tunnel_started = True
                update_server_properties(
                    server,
                    {
                        "max-players": int(cfg.get("max_players", 20)),
                        "white-list": bool(cfg.get("whitelist_enabled", False)),
                    },
                )

                # Use RAM setting for Java args
                ram_args = f"-Xmx{cfg['ram']} -Xms2G"
                ok, msg = mc_server.start(server, cfg["server_jar"], ram_args)
                if not ok:
                    raise RuntimeError(f"Server start failed: {msg}")

                global last_sync_time
                last_sync_time = datetime.now().isoformat()
                progress_cb(100, "Server process started. Waiting for READY...")
            except Exception:
                if tunnel_started:
                    try:
                        t_manager.stop()
                    except Exception:
                        pass
                if lock_acquired:
                    try:
                        lock_manager.remove_lock(shared)
                    except Exception:
                        pass
                st_api.set_paused("mc-shared", False)
                with state_lock:
                    host_session["active"] = False
                    host_session["ready"] = False
                raise

        return run_background_task(task, action="starting")

    def _stop_host_flow(self, cfg):
        def task(progress_cb):
            _finalize_stop_flow(cfg, progress_cb=progress_cb, reason="normal")
        return run_background_task(task, action="stopping")

    def _restart_host_flow(self, cfg):
        def task(progress_cb):
            progress_cb(5, "Stopping for restart...")
            _finalize_stop_flow(cfg, progress_cb=lambda p, m: progress_cb(5 + int(p * 0.55), m), reason="restart")
            progress_cb(65, "Starting server again...")

            shared = Path(cfg["shared_dir"])
            server = Path(cfg["server_dir"])
            pkey = ensure_project_key(cfg)
            lock_acquired = False
            tunnel_started = False
            with state_lock:
                host_session["last_cfg"] = dict(cfg)
                host_session["ready"] = False
                host_session["active"] = False

            try:
                ensure_syncthing_running()
                st_api.ensure_folder(shared)
                ok_lock, msg_lock = lock_manager.create_lock(shared, load_local_user(), pkey, no_expire=True)
                if not ok_lock:
                    raise RuntimeError(f"Failed to acquire host lock for restart: {msg_lock}")
                lock_acquired = True
                with state_lock:
                    host_session["active"] = True
                # Ensure peers receive lock before folder is paused.
                st_api.scan_folder("mc-shared")
                time.sleep(1.2)
                st_api.set_paused("mc-shared", True)
                backup_manager.copy_world(shared / "world_latest", server, progress_cb=lambda p, m: progress_cb(65 + int(p * 0.2), m))
                t_manager.start()
                tunnel_started = True
                update_server_properties(
                    server,
                    {
                        "max-players": int(cfg.get("max_players", 20)),
                        "white-list": bool(cfg.get("whitelist_enabled", False)),
                    },
                )
                ram_args = f"-Xmx{cfg['ram']} -Xms2G"
                ok, msg = mc_server.start(server, cfg["server_jar"], ram_args)
                if not ok:
                    raise RuntimeError(f"Restart start failed: {msg}")
                progress_cb(100, "Restarted.")
            except Exception:
                if tunnel_started:
                    try:
                        t_manager.stop()
                    except Exception:
                        pass
                if lock_acquired:
                    try:
                        lock_manager.remove_lock(shared)
                    except Exception:
                        pass
                st_api.set_paused("mc-shared", False)
                with state_lock:
                    host_session["active"] = False
                    host_session["ready"] = False
                raise

        return run_background_task(task, action="restart")

    def _json(self, data):
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_ui(self):
        ui_path = RESOURCE_DIR / "ui.html"
        if ui_path.exists():
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(ui_path.read_bytes())

# Task Runner
global_task_status = {"running": False, "pct": 0, "msg": "", "action": "", "error": ""}
task_status_lock = threading.Lock()


def is_task_running() -> bool:
    with task_status_lock:
        return bool(global_task_status.get("running"))


def _set_task_status(**kwargs):
    with task_status_lock:
        global_task_status.update(kwargs)


def get_task_status_snapshot() -> dict:
    with task_status_lock:
        return dict(global_task_status)


def run_background_task(target_fn, action="task"):
    with task_status_lock:
        if global_task_status.get("running"):
            return False
        global_task_status["running"] = True
        global_task_status["action"] = action
        global_task_status["error"] = ""
        global_task_status["pct"] = 0
        global_task_status["msg"] = ""

    def wrapper():
        runtime_health["last_error"] = ""
        try:
            def update_progress(p, m):
                _set_task_status(pct=p, msg=m)
            target_fn(update_progress)
        except Exception as e:
            _set_task_status(error=str(e), msg=f"Failed: {e}")
            runtime_health["last_error"] = str(e)
            runtime_health["last_finalize_result"] = "error"
        finally:
            _set_task_status(running=False, action="")
    threading.Thread(target=wrapper, daemon=True).start()
    return True


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a separate thread so UI polling doesn't block."""
    daemon_threads = True
    allow_reuse_address = (os.name != "nt")

    def server_bind(self):
        # On Windows, SO_REUSEADDR can allow multiple listeners on the same port.
        # Force exclusive bind so only one dashboard instance serves requests.
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except Exception:
                pass
        super().server_bind()


shutdown_lock = threading.Lock()
shutdown_started = False


def safe_shutdown_cleanup(reason: str = "app-exit"):
    """Best-effort finalize when app process is closed/killed gracefully."""
    global shutdown_started
    with shutdown_lock:
        if shutdown_started:
            return
        shutdown_started = True

    # If a stop/finalize task is already running, give it a chance to finish.
    wait_deadline = time.time() + 18
    while is_task_running() and time.time() < wait_deadline:
        time.sleep(0.25)

    with state_lock:
        active = bool(host_session.get("active"))
        recovering = bool(host_session.get("recovering"))
        last_cfg = dict(host_session.get("last_cfg") or {})

    running = mc_server.is_running()
    if recovering:
        return
    if not active and not running:
        return

    cfg = dict(load_config())
    cfg.update({k: v for k, v in last_cfg.items() if v})
    if not cfg.get("server_dir") or not cfg.get("shared_dir"):
        # If we can't safely finalize, at least stop child processes.
        try:
            mc_server.stop()
        except Exception:
            pass
        try:
            t_manager.stop()
        except Exception:
            pass
        return

    try:
        _finalize_stop_flow(cfg, reason=reason)
    except Exception as e:
        runtime_health["last_error"] = f"shutdown cleanup failed: {e}"
        runtime_health["last_finalize_result"] = "error"


def _signal_handler(signum, _frame):
    try:
        safe_shutdown_cleanup(reason=f"signal-{signum}")
    finally:
        raise SystemExit(0)


def _open_dashboard_later(port: int) -> None:
    if os.environ.get("MC_NO_BROWSER"):
        return
    def _opener():
        time.sleep(1.5)
        try:
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass
    threading.Thread(target=_opener, daemon=True).start()


if __name__ == "__main__":
    atexit.register(safe_shutdown_cleanup)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    bootstrap_bundled_bin_assets()
    # make sure syncthing binary is available (download if missing)
    ensure_syncthing_binary()
    # best-effort auto start when app starts
    ensure_syncthing_running()
    try:
        cleanup_runtime_artifacts(load_config(force=True))
    except Exception:
        pass
    threading.Thread(target=monitor_unexpected_stop, daemon=True).start()
    threading.Thread(target=monitor_lock_heartbeat, daemon=True).start()
    threading.Thread(target=monitor_node_presence, daemon=True).start()
    threading.Thread(target=monitor_remote_host_dispatch, daemon=True).start()
    threading.Thread(target=monitor_housekeeping, daemon=True).start()

    PORT = 7842
    print(f"[INFO] MC Host Manager (Phase 2) running on http://localhost:{PORT}")
    server = None
    try:
        server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
        _open_dashboard_later(PORT)
        server.serve_forever()
    except OSError as e:
        in_use = e.errno in (48, 98) or getattr(e, "winerror", None) == 10048
        if in_use:
            print(f"[ERROR] Port {PORT} is already in use!")
            if platform.system() == "Windows":
                print("[HINT] Another instance may already be running. Close it and retry.")
            else:
                print(f"[HINT] Another instance may already be running. You can stop it with: 'fuser -k {PORT}/tcp'")
        else:
            print(f"[ERROR] Server error: {e}")
    except KeyboardInterrupt:
        print("\n[INFO] Shutting down...")
    finally:
        try:
            safe_shutdown_cleanup(reason="app-exit")
        finally:
            if server is not None:
                try:
                    server.server_close()
                except Exception:
                    pass
