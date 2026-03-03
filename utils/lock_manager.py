from __future__ import annotations

import json
import os
import socket
from datetime import datetime
from pathlib import Path

LOCK_LEASE_SECONDS = 45


def get_lock_path(shared_dir: str | Path) -> Path:
    return Path(shared_dir).expanduser() / "host.lock"


def get_status_path(shared_dir: str | Path) -> Path:
    return Path(shared_dir).expanduser() / "current_host.txt"


def _now_iso() -> str:
    return datetime.now().isoformat()


def _parse_iso(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).strip())
    except Exception:
        return None


def _best_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip or "127.0.0.1"
    except Exception:
        return "127.0.0.1"


def _payload(user: str, project_key: str = "", owner_node_id: str = "", created_at: str = "") -> dict:
    created = str(created_at or "").strip() or _now_iso()
    now = _now_iso()
    ip = _best_ip()
    return {
        "host": str(user or "").strip(),
        "hostname": socket.gethostname(),
        "owner_node_id": str(owner_node_id or "").strip(),
        "ip": ip,
        "ui_url": f"http://{ip}:7842",
        "project_key": str(project_key or "").strip(),
        "time": created,
        "created_at": created,
        "updated_at": now,
        "lease_seconds": LOCK_LEASE_SECONDS,
        "state": "active",
    }


def _age_seconds(data: dict) -> float:
    t = _parse_iso(str(data.get("updated_at") or data.get("time") or ""))
    if t is None:
        return 10**9
    return max(0.0, (datetime.now() - t).total_seconds())


def get_lock(shared_dir: str | Path):
    p = get_lock_path(shared_dir)
    if not p.exists() or not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(d, dict):
            return None
    except Exception:
        return None

    lease = int(d.get("lease_seconds", LOCK_LEASE_SECONDS) or LOCK_LEASE_SECONDS)
    lease = max(10, min(3600, lease))
    age = _age_seconds(d)
    d["age_seconds"] = round(age, 2)
    d["ttl_seconds"] = lease
    d["expired"] = bool(age > lease)
    return d


def _write_status(shared_dir: str | Path, user: str, ui_url: str):
    p = get_status_path(shared_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(f"{user} is hosting @ {ui_url}", encoding="utf-8")
    except Exception:
        pass


def create_lock(shared_dir: str | Path, user_name: str, project_key: str = "", no_expire=False, owner_node_id: str = ""):
    p = get_lock_path(shared_dir)
    p.parent.mkdir(parents=True, exist_ok=True)

    current = get_lock(shared_dir)
    if current and not current.get("expired"):
        who = str(current.get("host", "") or "another host")
        key = str(current.get("project_key", "") or "")
        if key and project_key and key != project_key:
            return False, "Lock belongs to another project key"
        return False, f"Locked by {who}"

    # clear stale lock
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass

    data = _payload(user_name, project_key, owner_node_id=owner_node_id)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        _write_status(shared_dir, user_name, data["ui_url"])
        return True, "lock acquired"
    except FileExistsError:
        return False, "Lock already exists"
    except Exception as e:
        return False, str(e)


def refresh_lock(shared_dir: str | Path, user_name: str, project_key: str = "", owner_node_id: str = ""):
    p = get_lock_path(shared_dir)
    current = get_lock(shared_dir)
    if not current:
        return False, "Lock missing"
    if str(current.get("host", "")) != str(user_name):
        return False, "Lock owned by another host"
    ex_key = str(current.get("project_key", "") or "")
    if ex_key and project_key and ex_key != project_key:
        return False, "Project key mismatch"

    data = _payload(
        user_name,
        project_key or ex_key,
        owner_node_id=owner_node_id or str(current.get("owner_node_id", "") or ""),
        created_at=str(current.get("created_at") or current.get("time") or ""),
    )

    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(p)
        _write_status(shared_dir, user_name, data["ui_url"])
        return True, "lock refreshed"
    except Exception as e:
        return False, str(e)


def remove_lock(shared_dir: str | Path):
    p = get_lock_path(shared_dir)
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass
    s = get_status_path(shared_dir)
    s.parent.mkdir(parents=True, exist_ok=True)
    try:
        s.write_text("Nobody is hosting", encoding="utf-8")
    except Exception:
        pass
