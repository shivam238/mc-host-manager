from __future__ import annotations

import time
from typing import Any

try:
    import requests
except ImportError:
    requests = None


def _get_base_url(firebase_url: str) -> str:
    url = str(firebase_url).strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    return url


def upload_host_invite(firebase_url: str, server_id: str, payload: dict[str, Any]) -> tuple[bool, str]:
    if not firebase_url or not server_id or not payload or requests is None:
        return False, "Invalid parameters or requests missing"
    
    endpoint = f"{_get_base_url(firebase_url)}/matchmaking/{server_id.upper()}/host.json"
    try:
        data = dict(payload)
        data["updated_at"] = int(time.time())
        r = requests.put(endpoint, json=data, timeout=5.0)
        if r.status_code in (200, 204):
            return True, "Uploaded host invite"
        return False, f"Failed (HTTP {r.status_code})"
    except Exception as e:
        return False, f"Error: {e}"


def fetch_host_invite(firebase_url: str, server_id: str) -> tuple[bool, str, dict[str, Any]]:
    if not firebase_url or not server_id or requests is None:
        return False, "Invalid parameters", {}
        
    endpoint = f"{_get_base_url(firebase_url)}/matchmaking/{server_id.upper()}/host.json"
    try:
        r = requests.get(endpoint, timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, dict):
                if data.get("server_id") == server_id.upper():
                    return True, "Host invite fetched", data
            return False, "Host invite not found", {}
        return False, f"Failed (HTTP {r.status_code})", {}
    except Exception as e:
        return False, f"Error: {e}", {}


def upload_peer_invite(firebase_url: str, server_id: str, device_id: str, payload: dict[str, Any]) -> tuple[bool, str]:
    if not firebase_url or not server_id or not device_id or not payload or requests is None:
        return False, "Invalid parameters"
    
    # We use device_id as the key in the peers dictionary
    clean_did = device_id.replace("-", "").upper()
    endpoint = f"{_get_base_url(firebase_url)}/matchmaking/{server_id.upper()}/peers/{clean_did}.json"
    try:
        data = dict(payload)
        data["updated_at"] = int(time.time())
        r = requests.put(endpoint, json=data, timeout=5.0)
        if r.status_code in (200, 204):
            return True, "Uploaded peer invite"
        return False, f"Failed (HTTP {r.status_code})"
    except Exception as e:
        return False, f"Error: {e}"


def fetch_peer_invites(firebase_url: str, server_id: str) -> tuple[bool, str, list[dict[str, Any]]]:
    if not firebase_url or not server_id or requests is None:
        return False, "Invalid parameters", []
        
    endpoint = f"{_get_base_url(firebase_url)}/matchmaking/{server_id.upper()}/peers.json"
    try:
        r = requests.get(endpoint, timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, dict):
                peers = [p for p in data.values() if isinstance(p, dict)]
                return True, f"Found {len(peers)} peers", peers
            return True, "No peers found", []
        return False, f"Failed (HTTP {r.status_code})", []
    except Exception as e:
        return False, f"Error: {e}", []


# ── Presence (Real-time Global Members List) ─────────────────

def update_presence(firebase_url: str, server_id: str, node_id: str, user_data: dict[str, Any]) -> tuple[bool, str]:
    if not firebase_url or not server_id or not node_id or requests is None:
        return False, "Invalid parameters"
    
    clean_node = node_id.replace(".", "_").replace("-", "_").upper()
    endpoint = f"{_get_base_url(firebase_url)}/presence/{server_id.upper()}/{clean_node}.json"
    try:
        data = dict(user_data)
        data["last_seen"] = int(time.time())
        r = requests.put(endpoint, json=data, timeout=5.0)
        return (True, "OK") if r.status_code in (200, 204) else (False, f"HTTP {r.status_code}")
    except Exception as e:
        return False, str(e)


def fetch_presence(firebase_url: str, server_id: str) -> tuple[bool, str, list[dict[str, Any]]]:
    if not firebase_url or not server_id or requests is None:
        return False, "Invalid parameters", []
        
    endpoint = f"{_get_base_url(firebase_url)}/presence/{server_id.upper()}.json"
    try:
        r = requests.get(endpoint, timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, dict):
                now = time.time()
                members = []
                for node_id, row in data.items():
                    if not isinstance(row, dict): continue
                    # Only show members active in last 120 seconds
                    ls = row.get("last_seen", 0)
                    if (now - ls) < 120:
                        row["node_id"] = node_id
                        row["online"] = True
                        members.append(row)
                return True, f"Found {len(members)} active members", members
            return True, "No one online", []
        return False, f"HTTP {r.status_code}", []
    except Exception as e:
        return False, str(e), []
# ── Global Lock (Prevents simultaneous hosting) ─────────────

def acquire_lock(firebase_url: str, server_id: str, user_data: dict[str, Any]) -> tuple[bool, str]:
    """Try to claim the host role on Firebase."""
    if not firebase_url or not server_id or requests is None:
        return False, "Invalid params"
    
    endpoint = f"{_get_base_url(firebase_url)}/locks/{server_id.upper()}.json"
    try:
        # 1. Check if existing lock is active
        r = requests.get(endpoint, timeout=3.0)
        now = int(time.time())
        if r.status_code == 200:
            existing = r.json()
            if existing and isinstance(existing, dict):
                ls = existing.get("t", 0)
                owner = existing.get("user", "Someone")
                # If lock is less than 45s old, it is ACTIVE
                if (now - ls) < 45:
                    if existing.get("node_id") != user_data.get("node_id"):
                        return False, f"Already hosted by {owner}"

        # 2. Claim it
        data = dict(user_data)
        data["t"] = now
        r_put = requests.put(endpoint, json=data, timeout=3.0)
        return (True, "Lock acquired") if r_put.status_code in (200, 204) else (False, f"HTTP {r_put.status_code}")
    except Exception as e:
        return False, str(e)


def release_lock(firebase_url: str, server_id: str, node_id: str) -> None:
    """Release the host role."""
    if not firebase_url or not server_id or requests is None: return
    endpoint = f"{_get_base_url(firebase_url)}/locks/{server_id.upper()}.json"
    try:
        # Only delete if it belongs to us
        r = requests.get(endpoint, timeout=2.0)
        if r.status_code == 200:
            data = r.json()
            if data and data.get("node_id") == node_id:
                requests.delete(endpoint, timeout=2.0)
    except Exception:
        pass

def get_lock_data(fb_url, server_id):
    """Retrieve the current lock data from Firebase."""
    if not fb_url or not server_id: return False, "Missing URL/SID", None
    try:
        url = f"{_get_base_url(fb_url)}/locks/{server_id.upper()}.json"
        # Disable cache
        url += f"?cache_bust={int(time.time())}"
        r = requests.get(url, timeout=5).json()
        if r and isinstance(r, dict):
            return True, "OK", r
        return True, "No lock", None
    except Exception as e:
        return False, str(e), None

def send_signal(fb_url, server_id, cmd, target_node=None, sender_node=None):
    """Send a command signal to a specific node or the whole group."""
    if not fb_url or not server_id: return False
    try:
        url = f"{_get_base_url(fb_url)}/servers/{server_id.upper()}/signal.json"
        payload = {"cmd": cmd, "t": int(time.time())}
        if target_node: payload["target"] = target_node
        if sender_node: payload["sender"] = sender_node
        requests.put(url, json=payload, timeout=5)
        return True
    except Exception:
        return False

def check_signal(fb_url, server_id):
    """Check if there is a pending signal for us."""
    if not fb_url or not server_id: return None
    try:
        url = f"{_get_base_url(fb_url)}/servers/{server_id.upper()}/signal.json"
        # Disable cache with timestamp
        url += f"?cache_bust={int(time.time())}"
        r = requests.get(url, timeout=3).json()
        if r and isinstance(r, dict):
            return r
    except Exception:
        pass
    return None

def clear_signal(fb_url, server_id):
    """Clear the signal after processing."""
    if not fb_url or not server_id: return
    try:
        url = f"{_get_base_url(fb_url)}/servers/{server_id.upper()}/signal.json"
        requests.delete(url, timeout=3)
    except Exception:
        pass
