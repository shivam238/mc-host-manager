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
from utils.remote_lock import fetch_peer_lock, poll_remote_hosting, wait_peer_stopped as wait_lan_peer_stopped
from utils.world_conflict import check_world_conflict

st_api = sync_manager.SyncManager()
mc_server = server_controller.ServerController()

def pick_best_host_ip(cfg=None):
    """Picks the best local IP for hosting."""
    from utils.config import get_local_ip
    return get_local_ip(), "local"

def auto_pull_world(cfg, cb, host_ip: str = "", wait_host_stop: bool = True):
    """Pull the latest world either over LAN from a stopped host or from shared sync."""
    host_ip = str(host_ip or "").strip()
    sid = str(cfg.get("server_id", "") or "").strip()
    server_dir = normalize_path(cfg.get("server_dir", ""))

    if host_ip:
        from utils.config import get_local_ip
        from utils import world_transfer

        if host_ip not in ("127.0.0.1", "localhost", get_local_ip()):
            if wait_host_stop and sid:
                cb(10, f"Waiting for host {host_ip} to stop...")
                ok_wait, wait_msg = wait_lan_peer_stopped(host_ip, sid, timeout=180.0)
                if not ok_wait:
                    return False, wait_msg

            cb(25, f"Downloading world from {host_ip}...")
            ok_pull, pull_msg = world_transfer.pull_world_from_host(host_ip, sid, server_dir)
            return ok_pull, pull_msg

    from utils.flow_manager import safe_copy_world_from_shared, ensure_shared_layout
    shared = ensure_shared_layout(cfg.get("shared_dir", ""))
    server = Path(cfg.get("server_dir", ""))
    safe_copy_world_from_shared(shared, server, cb)
    return True, "World synced from shared folder."

def prepare_then_start(cfg, cb, start_fn, auto_pull=False, host_ip="", ack_world_overwrite=False):
    """Prepares the environment and then starts the server."""
    start_cfg = dict(cfg)
    if auto_pull and str(host_ip or "").strip():
        cb(5, "Auto-pulling world over LAN...")
        ok_pull, pull_msg = auto_pull_world(cfg, cb, host_ip=host_ip, wait_host_stop=True)
        if not ok_pull:
            raise RuntimeError(pull_msg)
        start_cfg["_skip_shared_world_pull"] = True

    start_fn(start_cfg, cb)

def wait_peer_stopped(ip: str, sid: str, timeout: int = 45):
    """Wait until the peer has released the lock (supports Firebase and local Syncthing file lock)."""
    cfg = load_config()
    fb_url = cfg.get("firebase_url")
    shared = normalize_path(cfg.get("shared_dir", ""))

    if not fb_url and not shared:
        return True, ""

    end = time.time() + timeout
    while time.time() < end:
        # 1. Check Firebase if configured
        if fb_url:
            is_ok, _, lock_data = matchmaker.get_lock_data(fb_url, sid)
            if is_ok:
                if not lock_data:
                    # Lock is gone! Wait 2 more seconds for OS/Syncthing cleanup safety
                    time.sleep(2)
                    return True, ""

                # Check if lock is old/stale
                now = int(time.time())
                ls = lock_data.get("t", 0)

                is_stale = False
                if (now - ls) >= 45 or (now - ls) <= -45:
                    # Potential clock drift or actual expiration! Verify using presence liveness
                    is_stale = True
                    ok_p, _, fb_members = matchmaker.fetch_presence(fb_url, sid)
                    if ok_p and fb_members:
                        lock_node = str(lock_data.get("node_id") or "").strip().upper().replace(".", "_").replace("-", "_")
                        for fbm in fb_members:
                            fbm_node = str(fbm.get("node_id") or "").strip().upper().replace(".", "_").replace("-", "_")
                            if fbm_node == lock_node and fbm.get("hosting"):
                                # Owner is online and actively hosting, lock is NOT stale!
                                is_stale = False
                                break
                else:
                    is_stale = False

                if is_stale:
                    # Stale lock! Wait 1 more second for safety
                    time.sleep(1)
                    return True, ""

        # 2. Check local file lock (crucial for local/LAN setups where Firebase is not configured)
        if shared:
            existing = lock_manager.get_lock(shared)
            if not existing or existing.get("expired"):
                # Syncthing has successfully synced the lock file deletion from the stopped peer!
                time.sleep(2.5) # Extra delay for final file system/Syncthing propagation stability
                return True, ""

        time.sleep(3)
    return False, "Timed out waiting for peer to release lock."


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
        task_running=False,
        lock_info=lock_info,
        syn_h=syn_h,
        remote_lock=remote_lock,
    )

    remote = gate.get("remote_host")
    user = load_user()
    ip_for_pull = str(ip_from_request or "").strip()

    if isinstance(remote, dict) and remote:
        ip = str(remote.get("ip") or "").strip()
        if ip:
            ip_for_pull = ip
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
                    # Pass the project key so the remote host accepts the control command over LAN
                    project_key = cfg.get("project_key", "")
                    requests.post(
                        f"http://{ip}:7842/host/stop",
                        json={
                            "ack_remote": True,
                            "project_key": project_key
                        },
                        headers={"X-MC-Project-Key": project_key},
                        timeout=2.0
                    )
            except Exception:
                pass

            cb(10, "Waiting for remote host to release world...")
            ok_wait, wmsg = wait_peer_stopped(ip, str(cfg.get("server_id", "") or ""))
            if not ok_wait:
                raise RuntimeError(wmsg)
    elif ip_for_pull:
        snap = fetch_peer_lock(ip_for_pull, str(cfg.get("server_id", "") or ""))
        if snap and (snap.get("hosting") or snap.get("running")):
            cb(10, f"Waiting for host {ip_for_pull} to stop...")
            ok_wait, wmsg = wait_lan_peer_stopped(ip_for_pull, str(cfg.get("server_id", "") or ""), timeout=180.0)
            if not ok_wait:
                raise RuntimeError(wmsg)

    if not auto_start:
        cb(100, "Remote host stopped. You can now host manually.")
        return

    # Wait for Syncthing sync to complete before starting
    syn_folder = get_syncthing_folder(cfg)
    if syn_folder:
        cb(40, "Syncing latest world files from previous host...")
        try:
            st_api.scan_folder(syn_folder)
        except Exception:
            pass

        # Wait up to 30 seconds for pending items to clear
        end_sync = time.time() + 30
        while time.time() < end_sync:
            try:
                pending = st_api.get_pending_count(syn_folder)
            except Exception:
                pending = 0

            if pending <= 0:
                break

            cb(40 + int((30 - (end_sync - time.time())) * 0.3), f"Syncing latest world files ({pending} files remaining)...")
            time.sleep(2.0)

    start_cfg = dict(cfg)
    if ip_for_pull:
        cb(50, "Pulling latest world over LAN...")
        ok_pull, pull_msg = auto_pull_world(cfg, cb, host_ip=ip_for_pull, wait_host_stop=False)
        if not ok_pull:
            raise RuntimeError(pull_msg)
        start_cfg["_skip_shared_world_pull"] = True

    cb(55, "Switching role to host...")
    start_fn(start_cfg, cb)
