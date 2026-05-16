import time
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional, Dict

from utils import backup_manager, lock_manager, server_controller, sync_manager, matchmaker, members_registry
from utils.config import (
    load_config,
    save_config,
    load_user,
    get_node_id,
    ensure_project_key,
    ensure_shared_layout,
    normalize_path,
    get_syncthing_folder
)
from utils.app_state import task_status, clear_cache
from utils.host_policy import evaluate_start_gate
from utils.remote_lock import poll_remote_hosting
from utils.world_conflict import check_world_conflict

st_api = sync_manager.SyncManager()
mc_server = server_controller.ServerController()

def pick_best_host_ip(cfg=None):
    """Picks the best local IP for hosting."""
    from utils.config import get_local_ip
    return get_local_ip(), "local"

def auto_pull_world(cfg, cb):
    """Automatically pulls the latest world from the shared directory."""
    from utils.flow_manager import safe_copy_world_from_shared, ensure_shared_layout
    shared = ensure_shared_layout(cfg.get("shared_dir", ""))
    server = Path(cfg.get("server_dir", ""))
    safe_copy_world_from_shared(shared, server, cb)

def prepare_then_start(cfg, cb, start_fn, auto_pull=False, host_ip="", ack_world_overwrite=False):
    """Prepares the environment and then starts the server."""
    if auto_pull:
        cb(5, "Auto-pulling world...")
        auto_pull_world(cfg, cb)
    
    # Pass host_ip if needed or other flags
    start_fn(cfg, cb)

def wait_peer_stopped(ip: str, sid: str, timeout: int = 45):
    """Wait until the peer has released the lock in Firebase."""
    cfg = load_config()
    fb_url = cfg.get("firebase_url")
    if not fb_url: return True, ""
    
    end = time.time() + timeout
    while time.time() < end:
        is_ok, _, lock_data = matchmaker.get_lock_data(fb_url, sid)
        if is_ok:
            if not lock_data:
                # Lock is gone! Wait 2 more seconds for OS/Syncthing cleanup safety
                time.sleep(2)
                return True, ""
            
            # Check if lock is old/stale
            now = int(time.time())
            ls = lock_data.get("t", 0)
            if (now - ls) >= 45:
                # Stale lock! Wait 1 more second for safety
                time.sleep(1)
                return True, ""
        time.sleep(3)
    return False, "Timed out waiting for peer to release lock in Firebase."

def switch_host_flow(cfg: dict[str, Any], cb: Callable[[int, str], None], auto_start=True, start_fn=None, **kwargs) -> None:
    """Orchestrates the host role handover."""
    if not start_fn:
        from utils.flow_manager import start_flow
        start_fn = start_flow
    
    # Extract host_ip if passed
    ip_from_request = kwargs.get("host_ip")
    
    cb(2, "Scanning network for current host...")
    shared = normalize_path(cfg.get("shared_dir", ""))
    lock_info = lock_manager.get_lock(shared) if shared else None
    syn_h = st_api.get_health(get_syncthing_folder(cfg))
    
    remote_lock = poll_remote_hosting(cfg) if cfg.get("http_lock_enabled", True) else None
    
    gate = evaluate_start_gate(
        cfg,
        running=False,
        task_running=True,
        lock_info=lock_info,
        syn_h=syn_h,
        remote_lock=remote_lock,
    )

    remote = gate.get("remote_host")
    user = load_user()
    
    if remote:
        ip = str(remote.get("ip") or "").strip()
        remote_user = str(remote.get("user") or "")
        
        if not remote_user or remote_user.lower() != user.lower():
            try:
                cb(7, f"Stopping remote host ({ip or 'Global Signal'})...")
                remote_node_id = (remote.get("node_id") if isinstance(remote, dict) else None) or "ANY"
                matchmaker.send_signal(
                    cfg.get("firebase_url"), 
                    str(cfg.get("server_id")), 
                    "stop", 
                    target_node=remote_node_id,
                    sender_node=get_node_id()
                )
                if ip:
                    import requests
                    requests.post(f"http://{ip}:7842/host/stop", json={"ack_remote": True}, timeout=2.0)
            except Exception:
                pass
            
            cb(10, "Waiting for remote host to release world...")
            ok_wait, wmsg = wait_peer_stopped(ip, str(cfg.get("server_id", "") or ""))
            if not ok_wait:
                raise RuntimeError(wmsg)

    if not auto_start:
        cb(100, "Remote host stopped. You can now host manually.")
        return

    cb(50, "Switching role to host...")
    start_fn(cfg, cb)
