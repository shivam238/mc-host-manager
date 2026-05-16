from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from utils.config import get_node_id, load_user, normalize_path
from utils import lock_manager, members_registry
from utils.server_layout import normalize_server_id

MANAGER_PORT = 7842
HTTP_TIMEOUT = 2.5


def local_lock_snapshot(cfg: dict[str, Any], *, running: bool) -> dict[str, Any]:
    shared = normalize_path(cfg.get("shared_dir", ""))
    sid = normalize_server_id(str(cfg.get("server_id", "") or ""))
    lock_info = lock_manager.get_lock(shared) if shared else None
    hosting = bool(
        running
        or (
            lock_info
            and not lock_info.get("expired")
            and str(lock_info.get("host", "") or "").strip()
        )
    )
    return {
        "ok": True,
        "server_id": sid,
        "running": bool(running),
        "hosting": hosting,
        "user": load_user(),
        "node_id": get_node_id(),
        "lock": lock_info,
    }


def fetch_peer_lock(host_ip: str, server_id: str, *, port: int = MANAGER_PORT) -> dict[str, Any] | None:
    host = str(host_ip or "").strip().strip("[]")
    sid = normalize_server_id(server_id)
    if not host or not sid:
        return None
    q = urlencode({"server_id": sid})
    url = f"http://{host}:{port}/host/lock?{q}"
    try:
        import requests

        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def candidate_peer_ips(
    members: list[dict[str, Any]],
    *,
    local_ip: str = "",
    local_node: str = "",
) -> list[str]:
    local_ip = str(local_ip or "").strip()
    local_node = str(local_node or "").strip()
    seen: set[str] = set()
    ordered: list[tuple[int, str]] = []

    def score(row: dict[str, Any]) -> int:
        s = 0
        if row.get("hosting"):
            s += 100
        if row.get("online"):
            s += 50
        ip = str(row.get("ip", "") or "").strip()
        if ip.startswith("192.168.0.") or ip.startswith("10."):
            s += 10
        return s

    for row in members:
        if not isinstance(row, dict):
            continue
        ip = str(row.get("ip", "") or "").strip()
        node = str(row.get("node_id", "") or "").strip()
        if not ip or ip in seen:
            continue
        if node and node == local_node:
            continue
        if local_ip and ip == local_ip:
            continue
        seen.add(ip)
        ordered.append((score(row), ip))

    ordered.sort(key=lambda x: (-x[0], x[1]))
    return [ip for _, ip in ordered]


def poll_remote_hosting(
    cfg: dict[str, Any],
    *,
    members: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not cfg.get("http_lock_enabled", True):
        return None
    sid = normalize_server_id(str(cfg.get("server_id", "") or ""))
    if not sid:
        return None

    shared = normalize_path(cfg.get("shared_dir", ""))
    if members is None and shared:
        members = members_registry.list_members(shared)

    from utils.config import get_local_ip

    ips = candidate_peer_ips(
        members or [],
        local_ip=get_local_ip(),
        local_node=get_node_id(),
    )
    for ip in ips:
        snap = fetch_peer_lock(ip, sid)
        if not snap:
            continue
        lock = snap.get("lock") if isinstance(snap.get("lock"), dict) else None
        hosting = bool(snap.get("hosting")) or bool(snap.get("running"))
        if lock and lock.get("expired"):
            hosting = False
        if hosting:
            snap["peer_ip"] = ip
            return snap
    return None


def wait_peer_stopped(
    host_ip: str,
    server_id: str,
    *,
    timeout: float = 180.0,
    poll_interval: float = 3.0,
) -> tuple[bool, str]:
    import time

    deadline = time.time() + max(10.0, timeout)
    last_msg = ""
    while time.time() < deadline:
        snap = fetch_peer_lock(host_ip, server_id)
        if snap is None:
            last_msg = f"{host_ip} reachable nahi — IP / firewall check karo."
            time.sleep(poll_interval)
            continue
        running = bool(snap.get("running"))
        lock = snap.get("lock") if isinstance(snap.get("lock"), dict) else None
        hosting = bool(snap.get("hosting")) or running
        if lock and lock.get("expired"):
            hosting = bool(running)
        if not hosting and not running:
            who = str((lock or {}).get("host", "") or snap.get("user", "") or host_ip)
            return True, f"{who} ne server band kar diya."
        who = str((lock or {}).get("host", "") or snap.get("user", "") or "Host")
        last_msg = f"{who} abhi bhi hosting kar raha hai — STOP ka wait..."
        time.sleep(poll_interval)
    return False, last_msg or "Host STOP hone ka wait timeout."
