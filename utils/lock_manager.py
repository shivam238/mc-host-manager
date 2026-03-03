import json
import socket
import os
from pathlib import Path
from datetime import datetime

LOCK_EXPIRE_MINUTES = 10


def get_lock_path(shared_dir):
    return Path(shared_dir) / "host.lock"

def get_status_path(shared_dir):
    return Path(shared_dir) / "current_host.txt"

def get_lock(shared_dir):
    p = get_lock_path(shared_dir)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        # Non-expiring lock mode is used while world sync is intentionally paused.
        if bool(data.get("no_expire", False)):
            data["expired"] = False
            return data
        # Expiry window is refreshed by heartbeat while host is active.
        t = datetime.fromisoformat(data["time"])
        age = (datetime.now() - t).total_seconds() / 60
        if age > LOCK_EXPIRE_MINUTES:
            data["expired"] = True
        else:
            data["expired"] = False
        return data
    except:
        return None

def _best_local_ip():
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
        if ip and ip != "127.0.0.1":
            return ip
    except Exception:
        pass
    return "127.0.0.1"

def _lock_payload(user_name, project_key="", no_expire=True):
    ip = _best_local_ip()
    ui_url = f"http://{ip}:7842"
    return {
        "host": user_name,
        "hostname": socket.gethostname(),
        "ip": ip,
        "ui_url": ui_url,
        "project_key": str(project_key or ""),
        "no_expire": bool(no_expire),
        "time": datetime.now().isoformat(),
    }


def _write_status(shared_dir, user_name, ui_url):
    status_p = get_status_path(shared_dir)
    status_p.parent.mkdir(parents=True, exist_ok=True)
    with open(status_p, "w") as f:
        f.write(f"{user_name} is hosting @ {ui_url}")


def create_lock(shared_dir, user_name, project_key="", no_expire=True):
    p = get_lock_path(shared_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _lock_payload(user_name, project_key, no_expire=no_expire)

    # Refuse overwrite of non-expired lock owned by someone else/project.
    existing = get_lock(shared_dir)
    if existing and not existing.get("expired", False):
        ex_host = str(existing.get("host", ""))
        ex_key = str(existing.get("project_key", "") or "")
        if ex_host != user_name:
            return False, f"Locked by {ex_host}"
        if ex_key and ex_key != str(project_key or ""):
            return False, "Lock belongs to another project key"

    # If stale lock exists, remove first, then atomically create.
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass
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


def refresh_lock(shared_dir, user_name, project_key=""):
    p = get_lock_path(shared_dir)
    existing = get_lock(shared_dir)
    if not existing:
        return False, "Lock missing"
    ex_host = str(existing.get("host", ""))
    ex_key = str(existing.get("project_key", "") or "")
    if ex_host != str(user_name):
        return False, "Lock owned by another host"
    if ex_key and ex_key != str(project_key or ""):
        return False, "Project key mismatch"
    data = _lock_payload(
        user_name,
        project_key or ex_key,
        no_expire=bool(existing.get("no_expire", False)),
    )
    try:
        with open(p, "w") as f:
            json.dump(data, f, indent=2)
        _write_status(shared_dir, user_name, data["ui_url"])
        return True, "lock refreshed"
    except Exception as e:
        return False, str(e)

def remove_lock(shared_dir):
    p = get_lock_path(shared_dir)
    if p.exists():
        p.unlink()
    
    status_p = get_status_path(shared_dir)
    status_p.parent.mkdir(parents=True, exist_ok=True)
    with open(status_p, "w") as f:
        f.write("Nobody is hosting")
