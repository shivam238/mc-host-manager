from __future__ import annotations

import json
import socket
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.config import get_local_ip, get_node_id, load_user, normalize_path

MEMBERS_FILE = "members.json"
ONLINE_SECONDS = 45
_file_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _registry_lock:
        if key not in _file_locks:
            _file_locks[key] = threading.Lock()
        return _file_locks[key]


def _members_path(shared_dir: str | Path) -> Path:
    return Path(shared_dir).expanduser() / MEMBERS_FILE


def _now_iso() -> str:
    return datetime.now().isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).strip())
    except Exception:
        return None


def _is_online(last_seen: str | None) -> bool:
    t = _parse_iso(last_seen)
    if t is None:
        return False
    return (datetime.now() - t).total_seconds() <= ONLINE_SECONDS


def load_registry(shared_dir: str | Path) -> dict[str, Any]:
    p = _members_path(shared_dir)
    if not p.is_file():
        return {"server_id": "", "members": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            return {"server_id": "", "members": {}}
        members = data.get("members")
        if isinstance(members, list):
            conv: dict[str, Any] = {}
            for row in members:
                if isinstance(row, dict) and row.get("node_id"):
                    conv[str(row["node_id"])] = row
            data["members"] = conv
        elif not isinstance(members, dict):
            data["members"] = {}
        return data
    except Exception:
        return {"server_id": "", "members": {}}


def save_registry(shared_dir: str | Path, data: dict[str, Any]) -> None:
    p = _members_path(shared_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def touch_presence(
    shared_dir: str | Path,
    *,
    server_id: str,
    hosting: bool = False,
) -> None:
    shared = normalize_path(shared_dir)
    if not shared:
        return
    p = _members_path(shared)
    lock = _lock_for(p)
    user = load_user()
    node = get_node_id()

    with lock:
        data = load_registry(shared)
        data["server_id"] = str(server_id or data.get("server_id") or "")
        data["updated_at"] = _now_iso()
        members = data.get("members")
        if not isinstance(members, dict):
            members = {}
        members[node] = {
            "node_id": node,
            "user": user,
            "hostname": socket.gethostname(),
            "ip": get_local_ip(),
            "last_seen": _now_iso(),
            "hosting": bool(hosting),
        }
        data["members"] = members
        save_registry(shared, data)


def list_members(shared_dir: str | Path, *, lock_host: str = "") -> list[dict[str, Any]]:
    shared = normalize_path(shared_dir)
    if not shared:
        return []
    data = load_registry(shared)
    members = data.get("members")
    if not isinstance(members, dict):
        return []

    rows: list[dict[str, Any]] = []
    for node_id, row in members.items():
        if not isinstance(row, dict):
            continue
        user = str(row.get("user", "") or "Unknown").strip()
        last_seen = str(row.get("last_seen", "") or "")
        hosting = bool(row.get("hosting")) or (
            bool(lock_host) and lock_host.lower() == user.lower()
        )
        online = _is_online(last_seen)
        rows.append(
            {
                "node_id": str(node_id),
                "user": user,
                "hostname": str(row.get("hostname", "") or ""),
                "ip": str(row.get("ip", "") or ""),
                "last_seen": last_seen,
                "online": online,
                "hosting": hosting,
            }
        )

    rows.sort(key=lambda r: (not r["hosting"], not r["online"], r["user"].lower()))
    return rows


def members_summary(shared_dir: str | Path, *, lock_host: str = "") -> dict[str, Any]:
    rows = list_members(shared_dir, lock_host=lock_host)
    online = [r for r in rows if r.get("online")]
    hosting = [r for r in rows if r.get("hosting")]
    return {
        "members": rows,
        "members_online": len(online),
        "members_total": len(rows),
        "hosting_user": hosting[0]["user"] if hosting else (lock_host or ""),
    }
