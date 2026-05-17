from __future__ import annotations
import threading
import time
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from utils import backup_manager, lock_manager, server_controller, sync_manager
from utils.config import normalize_path, ensure_project_key, load_user, get_node_id, ensure_shared_layout, get_syncthing_folder
from utils.app_state import host_lock, host_state, task_lock, task_status, clear_cache

st_api = sync_manager.SyncManager()
mc_server = server_controller.ServerController()

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

def safe_copy_world_from_shared(shared_dir: Path, server_dir: Path, cb) -> None:
    src = shared_dir / "world_latest"
    if not src.exists() or not src.is_dir():
        return
    cb(30, "Syncing world from shared...")
    backup_manager.copy_world(src, server_dir, progress_cb=lambda p, m: cb(30 + int(p * 0.2), m))

def kill_port_process(port: int = 25565):
    """Forcefully kill any process occupying the Minecraft port."""
    try:
        import subprocess
        import os
        if os.name == "nt":
            # Windows
            cmd = f"netstat -ano | findstr :{port}"
            lines = subprocess.check_output(cmd, shell=True).decode().splitlines()
            for line in lines:
                parts = line.split()
                if len(parts) > 4:
                    pid = parts[-1]
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        else:
            # Linux/Mac
            cmd = f"lsof -t -i:{port}"
            try:
                pids = subprocess.check_output(cmd, shell=True).decode().splitlines()
                for pid in pids:
                    if pid.strip():
                        subprocess.run(["kill", "-9", pid.strip()], capture_output=True)
            except subprocess.CalledProcessError:
                pass # No process found
    except Exception:
        pass

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

    cb(95, "Releasing locks...")
    # 1. Release Local Lock
    lock_manager.remove_lock(shared)
    
    # 2. Release Global Lock (Firebase)
    fb_url = cfg.get("firebase_url", "")
    sid = cfg.get("server_id", "")
    if fb_url and sid:
        try:
            from utils import matchmaker
            matchmaker.release_lock(fb_url, sid, get_node_id())
        except Exception:
            pass

    try:
        st_api.scan_folder(get_syncthing_folder(cfg))
    except Exception:
        pass

    # Update presence immediately as offline
    try:
        from utils import members_registry
        members_registry.touch_presence(shared, server_id=cfg["server_id"], hosting=False)
        fb_url = cfg.get("firebase_url", "")
        sid = cfg.get("server_id", "")
        if fb_url and sid:
            from utils import matchmaker
            matchmaker.touch_presence(fb_url, sid, get_node_id(), hosting=False)
    except Exception:
        pass

    with host_lock:
        host_state["active"] = False
        host_state["ready"] = False
        host_state["last_sync"] = datetime.now().isoformat()

    with host_lock:
        host_state["last_error"] = ""
    cb(100, "Stopped safely")

def start_flow(cfg: dict[str, Any], cb) -> None:
    cb(5, "Clearing port 25565...")
    kill_port_process(25565)
    time.sleep(1.0)
    
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
        ok_start, msg_start = mc_server.start(server, str(cfg.get("server_jar", "server.jar")), ram_args, shared_dir=shared)
        if not ok_start:
            raise RuntimeError(msg_start)

        with host_lock:
            host_state["last_error"] = ""
            
        # Update presence immediately as hosting
        try:
            from utils import members_registry
            members_registry.touch_presence(shared, server_id=cfg["server_id"], hosting=True)
            fb_url = cfg.get("firebase_url", "")
            if fb_url:
                from utils import matchmaker
                matchmaker.touch_presence(fb_url, cfg["server_id"], get_node_id(), hosting=True)
        except Exception:
            pass

        cb(100, "Server started")
    except Exception:
        lock_manager.remove_lock(shared)
        fb_url = cfg.get("firebase_url", "")
        sid = cfg.get("server_id", "")
        if fb_url and sid:
            try:
                from utils import matchmaker
                matchmaker.release_lock(fb_url, sid, get_node_id())
            except Exception:
                pass
        with host_lock:
            host_state["active"] = False
            host_state["ready"] = False
        raise

def restart_flow(cfg: dict[str, Any], cb) -> None:
    cb(5, "Stopping for restart...")
    finalize_stop_flow(cfg, cb=lambda p, m: cb(5 + int(p * 0.45), m), reason="restart")
    cb(55, "Starting again...")
    start_flow(cfg, cb=lambda p, m: cb(55 + int(p * 0.45), m))

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
