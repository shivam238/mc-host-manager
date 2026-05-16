from __future__ import annotations
import os
import sys
import json
import socket
import re
import threading
import time
from pathlib import Path
from typing import Any

if getattr(sys, "frozen", False):
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    RUNTIME_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).parent.parent.resolve()
    RUNTIME_DIR = RESOURCE_DIR

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
    "server_id": "",
    "server_jar": "server.jar",
    "ram": "4G",
    "max_players": 20,
    "whitelist_enabled": False,
    "backup_keep": 5,
    "project_key": "",
    "allow_remote_stop": True,
    "strict_sync_gate": False,
    "auto_world_before_start": True,
    "http_lock_enabled": True,
    "firebase_url": "",
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
    cfg["strict_sync_gate"] = bool(cfg.get("strict_sync_gate", False))
    cfg["auto_world_before_start"] = bool(cfg.get("auto_world_before_start", True))
    cfg["http_lock_enabled"] = bool(cfg.get("http_lock_enabled", True))
    try:
        keep = int(cfg.get("backup_keep", 5))
    except Exception:
        keep = 5
    cfg["backup_keep"] = max(1, min(50, keep))
    cfg["server_jar"] = str(cfg.get("server_jar", "server.jar") or "server.jar").strip()
    cfg["ram"] = str(cfg.get("ram", "4G") or "4G").strip()
    cfg["project_name"] = str(cfg.get("project_name", "Minecraft Server") or "Minecraft Server").strip()
    cfg["project_key"] = str(cfg.get("project_key", "") or "").strip()
    cfg["server_id"] = str(cfg.get("server_id", "") or "").strip().upper()
    cfg["firebase_url"] = str(cfg.get("firebase_url", "") or "").strip()
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
        try:
            from utils.server_layout import resolve_layout

            cfg = resolve_layout(cfg, create_shared=False)
        except Exception:
            pass
        _config_cache = dict(cfg)
        _config_mtime = mtime
        return cfg

def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    global _config_cache, _config_mtime
    ncfg = _normalize_cfg(cfg)
    try:
        from utils.server_layout import resolve_layout

        ncfg = resolve_layout(ncfg, create_shared=True)
    except Exception:
        pass
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
    sid = str(cfg.get("server_id", "") or "").strip()
    if sid and shared:
        try:
            from utils.server_layout import write_server_id_file

            write_server_id_file(shared, sid)
        except Exception:
            pass
    return key


def get_syncthing_folder(cfg: dict[str, Any]) -> str:
    sid = str(cfg.get("server_id", "") or "").strip()
    if sid:
        try:
            from utils.server_layout import syncthing_folder_id

            return syncthing_folder_id(sid)
        except Exception:
            pass
    return "mc-shared"


def ensure_shared_layout(shared_dir: str | Path) -> Path:
    p = Path(shared_dir).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    (p / "backups").mkdir(parents=True, exist_ok=True)
    (p / "world_latest").mkdir(parents=True, exist_ok=True)
    return p


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip or "127.0.0.1"
    except Exception:
        return "127.0.0.1"
