from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from utils.config import get_local_ip, get_node_id, load_user, normalize_path
from utils import members_registry, world_transfer
from utils.remote_lock import (
    candidate_peer_ips,
    fetch_peer_lock,
    poll_remote_hosting,
    wait_peer_stopped,
)
from utils.server_layout import normalize_server_id
from utils.world_conflict import check_world_conflict

ProgressCb = Callable[[int, str], None]


def pick_best_host_ip(cfg: dict[str, Any], *, prefer_hosting: bool = True) -> tuple[str, str]:
    shared = normalize_path(cfg.get("shared_dir", ""))
    members = members_registry.list_members(shared) if shared else []
    ips = candidate_peer_ips(members, local_ip=get_local_ip(), local_node=get_node_id())
    if not ips:
        return "", "Koi friend IP nahi mili — members list khali ya offline."

    sid = normalize_server_id(str(cfg.get("server_id", "") or ""))
    for ip in ips:
        snap = fetch_peer_lock(ip, sid)
        if snap is None:
            continue
        if prefer_hosting:
            if snap.get("hosting") or snap.get("running"):
                return ip, ""
        else:
            return ip, ""
    if ips:
        return ips[0], "Peer reachable — world download try karenge."
    return "", "Koi reachable host IP nahi — same WiFi / firewall check karo."


def auto_pull_world(
    cfg: dict[str, Any],
    cb: ProgressCb | None = None,
    *,
    host_ip: str = "",
    wait_host_stop: bool = True,
    wait_timeout: float = 180.0,
) -> tuple[bool, str]:
    def report(pct: int, msg: str) -> None:
        if cb:
            cb(pct, msg)

    if cb:
        cb(5, "Host dhoondh rahe hain...")

    ip = str(host_ip or "").strip()
    hint = ""
    if not ip:
        ip, hint = pick_best_host_ip(cfg)
    if not ip:
        return False, hint or "Host IP missing."

    sid = normalize_server_id(str(cfg.get("server_id", "") or ""))
    server_dir = normalize_path(cfg.get("server_dir", ""))
    if not server_dir:
        return False, "Server folder set karo."

    snap = fetch_peer_lock(ip, sid)
    if snap is None:
        return False, f"{ip} par manager nahi mila — port 7842 / firewall check karo."

    if wait_host_stop and (snap.get("running") or snap.get("hosting")):
        report(15, f"{ip} par server band hone ka wait...")
        ok_wait, wmsg = wait_peer_stopped(ip, sid, timeout=wait_timeout)
        if not ok_wait:
            return False, wmsg

    report(40, f"World download from {ip}...")
    ok, msg = world_transfer.pull_world_from_host(ip, sid, server_dir)
    if ok:
        report(90, msg)
    return ok, msg


def prepare_then_start(
    cfg: dict[str, Any],
    cb: ProgressCb,
    *,
    start_fn: Callable[[dict[str, Any], ProgressCb], None],
    auto_pull: bool | None = None,
    host_ip: str = "",
    ack_world_overwrite: bool = False,
) -> None:
    """Sync world (Syncthing folder or LAN), then call start_fn."""
    conflict = check_world_conflict(cfg)
    if conflict.get("has_conflict") and not ack_world_overwrite:
        raise RuntimeError(str(conflict.get("message") or "World conflict — confirm overwrite."))

    do_pull = auto_pull if auto_pull is not None else bool(cfg.get("auto_world_before_start", True))
    isolated = True
    try:
        from utils.flow_manager import st_api
        from utils.config import get_syncthing_folder

        syn_h = st_api.get_health(get_syncthing_folder(cfg))
        if syn_h.get("running") and int(syn_h.get("connected_peers", 0) or 0) > 0:
            isolated = False
    except Exception:
        pass

    if do_pull and isolated:
        cb(8, "Syncthing peers nahi — LAN se world sync...")
        ok, msg = auto_pull_world(cfg, cb, host_ip=host_ip, wait_host_stop=True)
        if not ok:
            raise RuntimeError(msg)

    shared = Path(normalize_path(cfg.get("shared_dir", "")))
    latest = shared / "world_latest"
    if latest.is_dir() and any(latest.iterdir()):
        cb(12, "Shared world copy ready.")

    start_fn(cfg, lambda p, m: cb(15 + int(p * 0.85), m))


def switch_host_flow(
    cfg: dict[str, Any],
    cb: ProgressCb,
    *,
    start_fn: Callable[[dict[str, Any], ProgressCb], None],
    host_ip: str = "",
    auto_start: bool = True,
    ack_world_overwrite: bool = False,
) -> None:
    user = load_user()
    cb(3, "Remote host check...")

    remote = poll_remote_hosting(cfg)
    ip = str(host_ip or "").strip()
    if not ip and remote:
        ip = str(remote.get("peer_ip", "") or "")

    # Proactive Stop: Tell the remote host to stop before we wait
    if ip:
        remote_user = ""
        if remote:
            remote_user = str((remote.get("lock") or {}).get("host", "") or remote.get("user", "") or "")
        
        if not remote_user or remote_user.lower() != user.lower():
            try:
                cb(7, f"Stopping remote host ({ip})...")
                import requests
                # Send stop command to remote manager
                requests.post(f"http://{ip}:7842/host/stop", json={"ack_remote": True}, timeout=2.0)
            except Exception:
                pass
            
            cb(10, "Waiting for remote host to release world...")
            ok_wait, wmsg = wait_peer_stopped(ip, str(cfg.get("server_id", "") or ""))
            if not ok_wait:
                raise RuntimeError(wmsg)
    elif remote:
        # Fallback if we have remote info but no IP
        remote_user = str((remote.get("lock") or {}).get("host", "") or remote.get("user", "") or "")
        if remote_user and remote_user.lower() != user.lower():
             cb(10, f"Remote host {remote_user} is active. Use manual STOP first.")

    if not auto_start:
        ok, msg = auto_pull_world(cfg, cb, host_ip=ip, wait_host_stop=True)
        if not ok:
            raise RuntimeError(msg)
        cb(100, msg)
        return

    prepare_then_start(
        cfg,
        cb,
        start_fn=start_fn,
        auto_pull=True,
        host_ip=ip,
        ack_world_overwrite=ack_world_overwrite,
    )
