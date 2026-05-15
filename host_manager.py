"""
MC Host Manager - Lightweight Core Build
Fast, minimal, and deterministic host/start/stop logic.
"""

from __future__ import annotations

import atexit
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any

import sys

try:
    reconfig_out = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfig_out):
        reconfig_out(encoding="utf-8", errors="replace")
    reconfig_err = getattr(sys.stderr, "reconfigure", None)
    if callable(reconfig_err):
        reconfig_err(encoding="utf-8", errors="replace")
except Exception:
    pass

if getattr(sys, "frozen", False):
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    RUNTIME_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).parent.resolve()
    RUNTIME_DIR = RESOURCE_DIR

if str(RESOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(RESOURCE_DIR))

try:
    from utils import backup_manager, lock_manager, server_controller, sync_manager
except ImportError as e:
    print(f"[ERROR] Could not import utils modules: {e}")
    sys.exit(1)

try:
    import psutil  # optional
except Exception:
    psutil = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def get_user_data_root() -> Path:
    system = sys.platform
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif system == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "mc-host-manager"


APP_DATA_DIR = get_user_data_root() / "app_data"
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DATA_DIR / "settings.json"
USER_FILE = APP_DATA_DIR / "user.json"
NODE_FILE = APP_DATA_DIR / "node_id.txt"

DEFAULT_CONFIG: dict[str, Any] = {
    "project_name": "Minecraft Server",
    "server_dir": "",
    "shared_dir": "",
    "server_jar": "server.jar",
    "ram": "4G",
    "max_players": 20,
    "whitelist_enabled": False,
    "backup_keep": 5,
    "project_key": "",
    "allow_remote_stop": True,
}

config_lock = threading.Lock()
_config_cache: dict[str, Any] | None = None
_config_mtime: float = 0.0


def normalize_path(v: str | Path | None) -> str:
    if v is None:
        return ""
    s = str(v).strip().strip('"').strip("'")
    if not s:
        return ""
    # prevent cross-os bad path confusion
    is_win = bool(re.match(r"^[A-Za-z]:[\\/]", s))
    is_unix = s.startswith("/")
    if os.name == "nt" and is_unix:
        return ""
    if os.name != "nt" and is_win:
        return ""
    return str(Path(s).expanduser())


def _normalize_cfg(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        for k in DEFAULT_CONFIG.keys():
            if k in raw:
                cfg[k] = raw[k]
    for key in ("server_dir", "shared_dir"):
        cfg[key] = normalize_path(cfg.get(key, ""))
    try:
        cfg["max_players"] = int(cfg.get("max_players", 20))
    except Exception:
        cfg["max_players"] = 20
    cfg["max_players"] = max(1, min(500, cfg["max_players"]))
    cfg["whitelist_enabled"] = bool(cfg.get("whitelist_enabled", False))
    cfg["allow_remote_stop"] = bool(cfg.get("allow_remote_stop", True))
    try:
        keep = int(cfg.get("backup_keep", 5))
    except Exception:
        keep = 5
    cfg["backup_keep"] = max(1, min(50, keep))
    cfg["server_jar"] = str(cfg.get("server_jar", "server.jar") or "server.jar").strip()
    cfg["ram"] = str(cfg.get("ram", "4G") or "4G").strip()
    cfg["project_name"] = str(cfg.get("project_name", "Minecraft Server") or "Minecraft Server").strip()
    cfg["project_key"] = str(cfg.get("project_key", "") or "").strip()
    return cfg


def load_config(force: bool = False) -> dict[str, Any]:
    global _config_cache, _config_mtime
    with config_lock:
        try:
            mtime = CONFIG_FILE.stat().st_mtime if CONFIG_FILE.exists() else 0.0
        except Exception:
            mtime = 0.0
        if not force and _config_cache is not None and mtime == _config_mtime:
            return dict(_config_cache)

        data: dict[str, Any] = {}
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                data = {}
        cfg = _normalize_cfg(data)
        _config_cache = dict(cfg)
        _config_mtime = mtime
        return cfg


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    global _config_cache, _config_mtime
    ncfg = _normalize_cfg(cfg)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(ncfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)
    try:
        _config_mtime = CONFIG_FILE.stat().st_mtime
    except Exception:
        _config_mtime = time.time()
    _config_cache = dict(ncfg)
    ensure_project_key(ncfg)
    return ncfg


def load_user() -> str:
    if USER_FILE.exists():
        try:
            d = json.loads(USER_FILE.read_text(encoding="utf-8", errors="replace"))
            u = str(d.get("user", "") or "").strip()
            if u:
                return u
        except Exception:
            pass
    return socket.gethostname()


def save_user(name: str) -> str:
    nm = str(name or "").strip() or socket.gethostname()
    USER_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_FILE.write_text(json.dumps({"user": nm}, ensure_ascii=False, indent=2), encoding="utf-8")
    return nm


def get_node_id() -> str:
    try:
        if NODE_FILE.exists():
            val = NODE_FILE.read_text(encoding="utf-8", errors="replace").strip()
            if val:
                return val
        import secrets
        nid = secrets.token_hex(8)
        NODE_FILE.write_text(nid, encoding="utf-8")
        return nid
    except Exception:
        return "local-node"


def ensure_project_key(cfg: dict[str, Any]) -> str:
    key = str(cfg.get("project_key", "") or "").strip()
    if not key:
        import secrets
        key = secrets.token_hex(8)
        cfg["project_key"] = key
        # write back once
        with config_lock:
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_normalize_cfg(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(CONFIG_FILE)
    shared = normalize_path(cfg.get("shared_dir", ""))
    if shared:
        try:
            marker = Path(shared) / ".mc_project_key"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(key, encoding="utf-8")
        except Exception:
            pass
    return key


def ensure_shared_layout(shared_dir: str | Path) -> Path:
    p = Path(shared_dir).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    (p / "backups").mkdir(parents=True, exist_ok=True)
    (p / "world_latest").mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

st_api = sync_manager.SyncManager()
mc_server = server_controller.ServerController()

host_lock = threading.Lock()
host_state: dict[str, Any] = {
    "active": False,
    "ready": False,
    "last_cfg": {},
    "last_sync": "",
    "last_error": "",
}

status_cache_lock = threading.Lock()
status_cache: dict[str, tuple[float, Any]] = {}


def cache_get(key: str, ttl: float, fn):
    now = time.time()
    with status_cache_lock:
        item = status_cache.get(key)
        if item and (now - item[0]) < ttl:
            return item[1]
    val = fn()
    with status_cache_lock:
        status_cache[key] = (now, val)
    return val


def clear_cache(prefix: str = "") -> None:
    with status_cache_lock:
        if not prefix:
            status_cache.clear()
            return
        for k in list(status_cache.keys()):
            if k.startswith(prefix):
                status_cache.pop(k, None)


task_lock = threading.Lock()
task_status: dict[str, Any] = {
    "running": False,
    "pct": 0,
    "msg": "",
    "error": "",
    "action": "",
}


def get_task() -> dict[str, Any]:
    with task_lock:
        return dict(task_status)


def is_task_running() -> bool:
    with task_lock:
        return bool(task_status.get("running"))


def run_task(action: str, fn) -> bool:
    with task_lock:
        if task_status.get("running"):
            return False
        task_status.update({"running": True, "pct": 0, "msg": "", "error": "", "action": action})

    def worker():
        try:
            def cb(pct: int, msg: str):
                with task_lock:
                    task_status["pct"] = int(max(0, min(100, pct)))
                    task_status["msg"] = str(msg or "")
            fn(cb)
        except Exception as e:
            with task_lock:
                task_status["error"] = str(e)
                task_status["msg"] = f"Failed: {e}"
            with host_lock:
                host_state["last_error"] = str(e)
        finally:
            with task_lock:
                task_status["running"] = False
                task_status["action"] = ""
            clear_cache("status")
    threading.Thread(target=worker, daemon=True).start()
    return True


def parse_ram_to_mb(raw: str) -> int | None:
    try:
        s = str(raw or "").strip().upper()
        if s.endswith("G"):
            return int(float(s[:-1]) * 1024)
        if s.endswith("M"):
            return int(float(s[:-1]))
        if s.isdigit():
            return int(s)
    except Exception:
        return None
    return None


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip or "127.0.0.1"
    except Exception:
        return "127.0.0.1"


def update_server_properties(server_dir: str, max_players: int, whitelist_enabled: bool) -> None:
    p = Path(server_dir) / "server.properties"
    lines: list[str] = []
    idx: dict[str, int] = {}
    if p.exists():
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            lines = []
    for i, ln in enumerate(lines):
        if "=" not in ln or ln.strip().startswith("#"):
            continue
        k = ln.split("=", 1)[0].strip()
        if k:
            idx[k] = i

    def setprop(key: str, val: str):
        ln = f"{key}={val}"
        if key in idx:
            lines[idx[key]] = ln
        else:
            lines.append(ln)

    setprop("max-players", str(max_players))
    setprop("white-list", "true" if whitelist_enabled else "false")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def can_control_request(handler: BaseHTTPRequestHandler, cfg: dict[str, Any], body: dict[str, Any] | None = None) -> bool:
    ip = str(handler.client_address[0] if handler.client_address else "")
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    expected = ensure_project_key(cfg)
    sent = str(handler.headers.get("X-MC-Project-Key", "") or "").strip()
    if not sent and isinstance(body, dict):
        sent = str(body.get("project_key", "") or "").strip()
    return bool(expected and sent and sent == expected)


def validate_paths(cfg: dict[str, Any], require_server: bool = True, require_shared: bool = True) -> tuple[bool, str]:
    server = normalize_path(cfg.get("server_dir", ""))
    shared = normalize_path(cfg.get("shared_dir", ""))
    if require_server and not server:
        return False, "Server folder not configured"
    if require_shared and not shared:
        return False, "Shared folder not configured"
    if require_server:
        p = Path(server)
        if not p.exists() or not p.is_dir():
            return False, f"Server folder does not exist: {p}"
    if require_shared:
        p = Path(shared)
        if not p.exists() or not p.is_dir():
            return False, f"Shared folder does not exist: {p}"
    return True, ""


def safe_copy_world_from_shared(shared_dir: Path, server_dir: Path, cb) -> None:
    src = shared_dir / "world_latest"
    if not src.exists() or not src.is_dir():
        return
    cb(30, "Syncing world from shared...")
    backup_manager.copy_world(src, server_dir, progress_cb=lambda p, m: cb(30 + int(p * 0.2), m))


def finalize_stop_flow(cfg: dict[str, Any], cb, reason: str = "normal") -> None:
    shared = ensure_shared_layout(cfg["shared_dir"])
    server = Path(cfg["server_dir"])

    cb(10, "Stopping server...")
    try:
        mc_server.prepare_for_copy()
    except Exception:
        pass
    try:
        mc_server.stop()
    except Exception:
        pass

    cb(35, "Creating backup...")
    backup_manager.create_timestamped_backup(
        server,
        shared / "backups",
        int(cfg.get("backup_keep", 5)),
        progress_cb=lambda p, m: cb(35 + int(p * 0.3), m),
    )

    cb(70, "Syncing world to shared...")
    backup_manager.copy_world(server, shared / "world_latest", progress_cb=lambda p, m: cb(70 + int(p * 0.2), m))

    cb(92, "Releasing lock...")
    lock_manager.remove_lock(shared)
    try:
        st_api.scan_folder("mc-shared")
    except Exception:
        pass

    with host_lock:
        host_state["active"] = False
        host_state["ready"] = False
        host_state["last_sync"] = datetime.now().isoformat()

    cb(100, "Stopped safely")


def start_flow(cfg: dict[str, Any], cb) -> None:
    ok, msg = validate_paths(cfg, True, True)
    if not ok:
        raise RuntimeError(msg)

    shared = ensure_shared_layout(cfg["shared_dir"])
    server = Path(cfg["server_dir"])
    pkey = ensure_project_key(cfg)
    user = load_user()

    cb(10, "Checking lock...")
    existing = lock_manager.get_lock(shared)
    if existing:
        ex_host = str(existing.get("host", "") or "")
        expired = bool(existing.get("expired"))
        if expired:
            lock_manager.remove_lock(shared)
        elif ex_host != user:
            raise RuntimeError(f"Locked by {ex_host or 'another host'}")
        elif mc_server.is_running():
            raise RuntimeError("Server already running")
        else:
            # stale same-user lock
            lock_manager.remove_lock(shared)

    cb(20, "Acquiring lock...")
    ok_lock, msg_lock = lock_manager.create_lock(
        shared,
        user,
        pkey,
        owner_node_id=get_node_id(),
    )
    if not ok_lock:
        raise RuntimeError(msg_lock)

    try:
        with host_lock:
            host_state["active"] = True
            host_state["ready"] = False
            host_state["last_cfg"] = dict(cfg)

        safe_copy_world_from_shared(shared, server, cb)

        cb(60, "Applying server settings...")
        try:
            update_server_properties(server.as_posix(), int(cfg.get("max_players", 20)), bool(cfg.get("whitelist_enabled", False)))
        except Exception:
            pass

        cb(72, "Starting server...")
        ram_args = f"-Xmx{cfg['ram']} -Xms2G"
        ok_start, msg_start = mc_server.start(server, str(cfg.get("server_jar", "server.jar")), ram_args)
        if not ok_start:
            raise RuntimeError(msg_start)

        cb(100, "Server started")
    except Exception:
        lock_manager.remove_lock(shared)
        with host_lock:
            host_state["active"] = False
            host_state["ready"] = False
        raise


def restart_flow(cfg: dict[str, Any], cb) -> None:
    cb(5, "Stopping for restart...")
    finalize_stop_flow(cfg, cb=lambda p, m: cb(5 + int(p * 0.45), m), reason="restart")
    cb(55, "Starting again...")
    start_flow(cfg, cb=lambda p, m: cb(55 + int(p * 0.45), m))


# ---------------------------------------------------------------------------
# Monitors (very small)
# ---------------------------------------------------------------------------

last_list_poll = 0.0


def monitor_ready_and_recover() -> None:
    global last_list_poll
    while True:
        time.sleep(1.4)
        with host_lock:
            active = bool(host_state["active"])
            cfg = dict(host_state.get("last_cfg") or {})
        if not active:
            continue

        if mc_server.is_running():
            if mc_server.is_ready() or mc_server.get_uptime_seconds() >= 12:
                with host_lock:
                    host_state["ready"] = True
            if time.time() - last_list_poll >= 16:
                mc_server.send_command("list")
                last_list_poll = time.time()
            continue

        if is_task_running() or not cfg.get("server_dir") or not cfg.get("shared_dir"):
            continue

        def crash_task(cb):
            finalize_stop_flow(cfg, cb, reason="unexpected")

        run_task("recovering", crash_task)


def monitor_lock_heartbeat() -> None:
    while True:
        time.sleep(8.0)
        with host_lock:
            active = bool(host_state["active"])
            cfg = dict(host_state.get("last_cfg") or {})
        if not active:
            continue
        if not mc_server.is_running() and not is_task_running():
            continue
        shared = normalize_path(cfg.get("shared_dir", ""))
        if not shared:
            continue
        lock_manager.refresh_lock(
            shared,
            load_user(),
            ensure_project_key(cfg),
            owner_node_id=get_node_id(),
        )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_server_metrics(pid: int | None, ram_used_mb: float | None, ram_alloc_mb: int | None) -> dict[str, int]:
    cpu_pct = 0
    mem_pct = 0
    disk_pct = 0

    if ram_used_mb is not None and ram_alloc_mb and ram_alloc_mb > 0:
        try:
            mem_pct = int(max(0, min(100, round((float(ram_used_mb) / float(ram_alloc_mb)) * 100))))
        except Exception:
            mem_pct = 0

    if not pid:
        return {"cpu_pct": cpu_pct, "mem_pct": mem_pct, "disk_pct": disk_pct}

    try:
        if psutil is not None:
            proc = psutil.Process(pid)
            raw_cpu = float(proc.cpu_percent(interval=None))
            cpu_count = float(psutil.cpu_count() or 1)
            cpu_pct = int(max(0, min(100, raw_cpu / max(1.0, cpu_count))))
            if mem_pct <= 0:
                mem_pct = int(max(0, min(100, round(float(proc.memory_percent())))))
            io_c = proc.io_counters()
            bps = int(io_c.read_bytes + io_c.write_bytes)
            # visual scale only
            disk_pct = int(max(0, min(100, round((bps / (40 * 1024 * 1024)) * 100))))
    except Exception:
        pass

    return {"cpu_pct": cpu_pct, "mem_pct": mem_pct, "disk_pct": disk_pct}


def get_status(cfg: dict[str, Any]) -> dict[str, Any]:
    running = mc_server.is_running()
    task = get_task()

    with host_lock:
        active = bool(host_state["active"])
        ready = bool(host_state["ready"])
        last_sync = str(host_state.get("last_sync", "") or "")
        last_error = str(host_state.get("last_error", "") or "")

    server_state = "offline"
    if task.get("running"):
        act = str(task.get("action", "") or "")
        if act in ("starting", "restart"):
            server_state = "starting"
        elif act in ("stopping", "recovering"):
            server_state = "stopping"
        else:
            server_state = "working"
    elif running and not (ready or mc_server.is_ready()):
        server_state = "starting"
    elif running:
        server_state = "running"

    shared = normalize_path(cfg.get("shared_dir", ""))
    project_key = ensure_project_key(cfg)
    lock_info = lock_manager.get_lock(shared) if shared else None

    syn_h = cache_get("syn_health", 2.0, lambda: st_api.get_health("mc-shared"))
    syn_status = "missing"
    if syn_h.get("running"):
        syn_status = "connected" if (syn_h.get("connected_peers", 0) or 0) > 0 else "running"
    elif syn_h.get("api_key_ok"):
        syn_status = "stopped"

    ram_used = mc_server.get_ram_mb()
    ram_alloc = parse_ram_to_mb(cfg.get("ram", ""))
    pid = mc_server.get_pid()
    m = cache_get(f"server_metrics:{pid}:{ram_used}:{ram_alloc}", 0.6 if running else 1.2, lambda: get_server_metrics(pid, ram_used, ram_alloc))

    players = cache_get("players_online", 1.0 if running else 2.5, mc_server.get_online_players)
    pinfo = cache_get("players_info", 1.4 if running else 3.0, mc_server.get_player_stats)

    return {
        "project_name": cfg.get("project_name", "Minecraft Server"),
        "user": load_user(),
        "project_key": project_key,
        "running": running,
        "server_state": server_state,
        "server_ready": bool(ready or mc_server.is_ready()),
        "lock": lock_info,
        "local_ip": get_local_ip(),
        "ram": cfg.get("ram", "4G"),
        "max_players": int(cfg.get("max_players", 20)),
        "whitelist_enabled": bool(cfg.get("whitelist_enabled", False)),
        "allow_remote_stop": bool(cfg.get("allow_remote_stop", True)),
        "task": task,
        "last_sync": {"time": last_sync} if last_sync else None,
        "last_error": last_error,
        "server_dir": cfg.get("server_dir", ""),
        "shared_dir": cfg.get("shared_dir", ""),
        "server_jar": cfg.get("server_jar", "server.jar"),
        "syncthing_status": syn_status,
        "syncthing_connected_peers": int(syn_h.get("connected_peers", 0) or 0),
        "server_pid": pid,
        "server_uptime_s": mc_server.get_uptime_seconds(),
        "server_ram_mb": ram_used,
        "players_online": players,
        "players_count": len(players),
        "players_info": pinfo,
        "sync_pending_count": cache_get("sync_pending", 2.5, lambda: st_api.get_pending_count("mc-shared")),
        "server_cpu_pct": int(m.get("cpu_pct", 0)),
        "server_mem_pct": int(m.get("mem_pct", 0)),
        "server_disk_pct": int(m.get("disk_pct", 0)),
        "can_open_server_files": (not running and not is_task_running()),
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if length <= 0:
        return {}
    try:
        return json.loads(handler.rfile.read(length) or b"{}")
    except Exception:
        return {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_ui(self) -> None:
        p = RESOURCE_DIR / "ui.html"
        if not p.exists():
            self.send_response(404)
            self.end_headers()
            return
        raw = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MC-Project-Key")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        cfg = load_config()

        if self.path == "/":
            self._serve_ui()
            return

        if self.path == "/status":
            ttl = 0.25 if (mc_server.is_running() or is_task_running()) else 0.9
            self._json(cache_get("status:snapshot", ttl, lambda: get_status(cfg)))
            return

        if self.path.startswith("/logs") and self.path != "/logs/details":
            self._json(cache_get("logs:tail", 0.8, lambda: {"logs": mc_server.get_logs()}))
            return

        if self.path == "/logs/details":
            self._json({"summary": "Use live logs below for lightweight mode.", "errors": [], "warnings": [], "issues": []})
            return

        if self.path == "/task":
            self._json(get_task())
            return

        if self.path == "/backup/list":
            shared = normalize_path(cfg.get("shared_dir", ""))
            backups = backup_manager.list_backups(Path(shared) / "backups") if shared else []
            self._json({"backups": backups})
            return

        if self.path.startswith("/backup/get"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            name = str((q.get("name") or [""])[0])
            shared = normalize_path(cfg.get("shared_dir", ""))
            if not shared or not name:
                self.send_response(404)
                self.end_headers()
                return
            root = (Path(shared) / "backups").resolve()
            f = (root / name).resolve()
            if root not in f.parents or not f.exists() or not f.is_file():
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f"attachment; filename={f.name}")
            self.send_header("Content-Length", str(f.stat().st_size))
            self.end_headers()
            with open(f, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile)
            return

        if self.path.startswith("/server/download"):
            ok, msg = validate_paths(cfg, True, False)
            if not ok:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg.encode("utf-8", errors="replace"))
                return
            if mc_server.is_running() or is_task_running():
                self.send_response(409)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Stop server first before downloading files.")
                return
            server_root = Path(normalize_path(cfg.get("server_dir", ""))).resolve()
            tmp = tempfile.NamedTemporaryFile(prefix="mc_server_", suffix=".zip", delete=False)
            tmp_path = Path(tmp.name)
            tmp.close()
            try:
                with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, dirs, files in os.walk(server_root, topdown=True, followlinks=False):
                        rootp = Path(root)
                        dirs[:] = [d for d in dirs if not (rootp / d).is_symlink()]
                        for fn in files:
                            f = rootp / fn
                            if f.is_symlink() or not f.is_file():
                                continue
                            rel = f.relative_to(server_root)
                            zf.write(f, rel.as_posix())
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f"attachment; filename={server_root.name}_files.zip")
                self.send_header("Content-Length", str(tmp_path.stat().st_size))
                self.end_headers()
                with open(tmp_path, "rb") as fh:
                    shutil.copyfileobj(fh, self.wfile)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"Download failed: {e}".encode("utf-8", errors="replace"))
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            return

        if self.path.startswith("/open-folder"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            target = str((q.get("target") or [""])[0]).strip().lower()
            custom = str((q.get("path") or [""])[0]).strip()
            folder: Path | None = None
            if target == "server":
                folder = Path(normalize_path(cfg.get("server_dir", ""))) if cfg.get("server_dir") else None
            elif target == "shared":
                folder = Path(normalize_path(cfg.get("shared_dir", ""))) if cfg.get("shared_dir") else None
            elif target == "backups":
                sd = normalize_path(cfg.get("shared_dir", ""))
                folder = (Path(sd) / "backups") if sd else None
            elif target == "custom" and custom:
                folder = Path(normalize_path(custom)) if normalize_path(custom) else None
            if folder is None:
                self._json({"ok": False, "msg": "Folder path not configured."})
                return
            try:
                folder = folder.expanduser()
                folder.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    subprocess.Popen(["explorer", str(folder)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(folder)])
                else:
                    subprocess.Popen(["xdg-open", str(folder)])
                self._json({"ok": True, "path": str(folder)})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        cfg = load_config()
        body = read_json(self)

        if self.path == "/config/save":
            if "user" in body:
                save_user(str(body.get("user", "")))
                body.pop("user", None)
            cfg.update(body)
            saved = save_config(cfg)
            try:
                if normalize_path(saved.get("server_dir", "")):
                    update_server_properties(
                        normalize_path(saved.get("server_dir", "")),
                        int(saved.get("max_players", 20)),
                        bool(saved.get("whitelist_enabled", False)),
                    )
            except Exception:
                pass
            clear_cache()
            self._json({"ok": True})
            return

        if self.path == "/host/start":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            if mc_server.is_running():
                self._json({"ok": False, "msg": "Server already running."})
                return
            ok = run_task("starting", lambda cb: start_flow(load_config(force=True), cb))
            self._json({"ok": ok, "msg": "Starting..." if ok else "Another operation is running."})
            return

        if self.path == "/host/stop":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            if not mc_server.is_running() and not host_state.get("active"):
                self._json({"ok": False, "msg": "Server already offline."})
                return
            ok = run_task("stopping", lambda cb: finalize_stop_flow(load_config(force=True), cb))
            self._json({"ok": ok, "msg": "Stopping..." if ok else "Another operation is running."})
            return

        if self.path == "/host/restart":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            ok = run_task("restart", lambda cb: restart_flow(load_config(force=True), cb))
            self._json({"ok": ok, "msg": "Restarting..." if ok else "Another operation is running."})
            return

        if self.path == "/host/kill":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            if not mc_server.is_running() or mc_server.proc is None:
                self._json({"ok": False, "msg": "Server is not running."})
                return
            try:
                mc_server.proc.kill()
                self._json({"ok": True, "msg": "Kill signal sent."})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)})
            return

        if self.path == "/host/force":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            if mc_server.is_running() or is_task_running():
                self._json({"ok": False, "msg": "Stop server first."})
                return
            shared = normalize_path(cfg.get("shared_dir", ""))
            if not shared:
                self._json({"ok": False, "msg": "Shared folder not configured."})
                return
            lk = lock_manager.get_lock(shared)
            if lk and not lk.get("expired"):
                self._json({"ok": False, "msg": "Active lock exists. Use normal stop on host."})
                return
            lock_manager.remove_lock(shared)
            with host_lock:
                host_state["active"] = False
                host_state["ready"] = False
            self._json({"ok": True, "msg": "Lock cleared."})
            return

        if self.path == "/backup/now":
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            ok = run_task("backup", lambda cb: backup_now(load_config(force=True), cb))
            self._json({"ok": ok, "msg": "Backup started." if ok else "Another operation is running."})
            return

        if self.path == "/backup/restore":
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            name = str(body.get("name", "") or "").strip()
            ok = run_task("restore", lambda cb: restore_backup(load_config(force=True), name, cb))
            self._json({"ok": ok, "msg": "Restore started." if ok else "Another operation is running."})
            return

        if self.path == "/sync/now":
            try:
                done = st_api.scan_folder("mc-shared")
                self._json({"ok": bool(done), "msg": "Sync scan triggered." if done else "Sync trigger failed."})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)})
            return

        if self.path == "/command":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            cmd = str(body.get("cmd", "") or "").strip()
            if not cmd:
                self._json({"ok": False, "msg": "Empty command."})
                return
            if len(cmd) > 240 or any(c in cmd for c in ("\n", "\r", "\0")):
                self._json({"ok": False, "msg": "Invalid command."})
                return
            if not mc_server.is_running():
                self._json({"ok": False, "msg": "Server is offline."})
                return
            ok = mc_server.send_command(cmd)
            self._json({"ok": bool(ok), "msg": "Sent" if ok else "Failed"})
            return

        if self.path == "/players/refresh":
            if mc_server.is_running():
                mc_server.send_command("list")
                for p in mc_server.get_online_players():
                    mc_server.send_command(f"data get entity {p}")
                self._json({"ok": True})
            else:
                self._json({"ok": False, "msg": "Server offline"})
            return

        self.send_response(404)
        self.end_headers()


def backup_now(cfg: dict[str, Any], cb) -> None:
    ok, msg = validate_paths(cfg, True, True)
    if not ok:
        raise RuntimeError(msg)
    server = Path(cfg["server_dir"])
    shared = ensure_shared_layout(cfg["shared_dir"])
    cb(10, "Preparing backup...")
    b = backup_manager.create_timestamped_backup(
        server,
        shared / "backups",
        int(cfg.get("backup_keep", 5)),
        progress_cb=lambda p, m: cb(10 + int(p * 0.8), m),
    )
    if b is None:
        raise RuntimeError("No world folders found to back up.")
    cb(100, "Backup complete")


def restore_backup(cfg: dict[str, Any], name: str, cb) -> None:
    ok, msg = validate_paths(cfg, True, True)
    if not ok:
        raise RuntimeError(msg)
    if mc_server.is_running():
        raise RuntimeError("Stop server before restore")
    if not name:
        raise RuntimeError("Backup name missing")
    root = (Path(cfg["shared_dir"]) / "backups").resolve()
    b = (root / name).resolve()
    if root not in b.parents or not b.exists():
        raise RuntimeError("Invalid backup path")
    ok_restore = backup_manager.restore_backup(b, Path(cfg["server_dir"]), progress_cb=cb)
    if not ok_restore:
        raise RuntimeError("Restore failed")


# ---------------------------------------------------------------------------
# Server main
# ---------------------------------------------------------------------------

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = (os.name != "nt")


shutdown_lock = threading.Lock()
shutdown_started = False


def safe_shutdown(reason: str = "exit") -> None:
    global shutdown_started
    with shutdown_lock:
        if shutdown_started:
            return
        shutdown_started = True

    # wait for in-flight task briefly
    end = time.time() + 12
    while is_task_running() and time.time() < end:
        time.sleep(0.2)

    with host_lock:
        active = bool(host_state["active"])
        cfg = dict(host_state.get("last_cfg") or {})

    if not cfg:
        cfg = load_config(force=True)

    if active or mc_server.is_running():
        try:
            finalize_stop_flow(cfg, cb=lambda *_: None, reason=reason)
        except Exception:
            try:
                mc_server.stop()
            except Exception:
                pass


def _signal(signum, _frame):
    safe_shutdown(reason=f"signal-{signum}")
    raise SystemExit(0)


if __name__ == "__main__":
    atexit.register(safe_shutdown)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal)

    cfg = load_config(force=True)
    if not cfg.get("project_key"):
        ensure_project_key(cfg)

    threading.Thread(target=monitor_ready_and_recover, daemon=True).start()
    threading.Thread(target=monitor_lock_heartbeat, daemon=True).start()

    PORT = 7842
    print(f"[INFO] MC Host Manager (lightweight) running on http://localhost:{PORT}")

    server = None
    try:
        server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
        server.serve_forever()
    except OSError as e:
        print(f"[ERROR] Server error: {e}")
    except KeyboardInterrupt:
        print("[INFO] Shutting down...")
    finally:
        try:
            safe_shutdown(reason="app-exit")
        finally:
            if server is not None:
                try:
                    server.server_close()
                except Exception:
                    pass
