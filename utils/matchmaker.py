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
