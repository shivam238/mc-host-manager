from __future__ import annotations
import threading
import time
from typing import Any

# Global state for hosting
host_lock = threading.Lock()
host_state: dict[str, Any] = {
    "active": False,
    "ready": False,
    "last_cfg": {},
    "last_sync": "",
    "last_error": "",
}

# Task tracking
task_lock = threading.Lock()
task_status: dict[str, Any] = {
    "running": False,
    "pct": 0,
    "msg": "",
    "error": "",
    "action": "",
}

# Status caching
status_cache_lock = threading.Lock()
status_cache: dict[str, tuple[float, Any]] = {}

def get_task() -> dict[str, Any]:
    with task_lock:
        return dict(task_status)

def is_task_running() -> bool:
    with task_lock:
        return bool(task_status.get("running"))

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
