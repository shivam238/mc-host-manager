from __future__ import annotations

import json
import os
import platform
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEVICE_ID_RE = re.compile(
    r"^[A-Z0-9]{7}-[A-Z0-9]{7}-[A-Z0-9]{7}-[A-Z0-9]{7}-[A-Z0-9]{7}-[A-Z0-9]{7}-[A-Z0-9]{7}-[A-Z0-9]{7}$"
)

try:
    import requests
except Exception:
    requests = None


class SyncManager:
    """Minimal Syncthing wrapper for lightweight dashboard mode."""

    def __init__(self, url: str = "http://localhost:8384"):
        self.url = url.rstrip("/")
        self.api_key = self._get_api_key()
        self._session = requests.Session() if requests is not None else None
        self._cache: dict[str, tuple[float, Any]] = {}

    def _cached(self, key: str, ttl: float, fn):
        now = time.time()
        hit = self._cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
        val = fn()
        self._cache[key] = (now, val)
        return val

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"X-API-Key": self.api_key}
        return {}

    def _request(self, method: str, path: str, timeout: float = 1.5, noauth: bool = False, **kwargs):
        if self._session is None:
            raise RuntimeError("requests is not available")
        headers = kwargs.pop("headers", {})
        if not noauth:
            h = self._headers()
            h.update(headers)
            headers = h
        return self._session.request(method, f"{self.url}{path}", headers=headers, timeout=timeout, **kwargs)

    def _get_api_key(self) -> str:
        paths: list[Path] = [Path.home() / ".config/syncthing/config.xml"]
        system = platform.system()
        if system == "Windows":
            appdata = os.environ.get("APPDATA")
            localapp = os.environ.get("LOCALAPPDATA")
            if appdata:
                paths.append(Path(appdata) / "Syncthing/config.xml")
            if localapp:
                paths.append(Path(localapp) / "Syncthing/config.xml")
        else:
            paths.extend([
                Path.home() / ".local/share/syncthing/config.xml",
                Path.home() / ".local/state/syncthing/config.xml",
            ])

        seen = set()
        for p in paths:
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            if not p.exists():
                continue
            try:
                root = ET.parse(p).getroot()
                el = root.find(".//apikey")
                if el is not None and el.text:
                    return el.text.strip()
            except Exception:
                continue
        return ""

    def refresh_api_key(self) -> bool:
        self.api_key = self._get_api_key()
        self._cache.clear()
        return bool(self.api_key)

    def is_running_noauth(self) -> bool:
        if self._session is None:
            return False
        try:
            r = self._request("GET", "/rest/noauth/health", timeout=1.0, noauth=True)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        try:
            r = self._request("GET", "/", timeout=1.0, noauth=True)
            return r.status_code in (200, 302, 401, 403)
        except Exception:
            return False

    def get_health(self, folder_id: str = "mc-shared") -> dict[str, Any]:
        def _load() -> dict[str, Any]:
            health: dict[str, Any] = {
                "api_key_ok": bool(self.api_key),
                "running": False,
                "connected_peers": 0,
                "folder_exists": False,
                "folder_paused": None,
            }
            if not self.is_running_noauth():
                return health
            health["running"] = True

            if not self.api_key:
                self.refresh_api_key()
                health["api_key_ok"] = bool(self.api_key)
            if not self.api_key:
                return health

            try:
                conn = self._request("GET", "/rest/system/connections", timeout=1.2)
                if conn.status_code == 200:
                    conns = conn.json().get("connections", {})
                    health["connected_peers"] = sum(1 for v in conns.values() if v.get("connected"))
            except Exception:
                pass

            try:
                cfg = self._request("GET", "/rest/config", timeout=1.2)
                if cfg.status_code == 200:
                    for f in cfg.json().get("folders", []):
                        if str(f.get("id", "")) == folder_id:
                            health["folder_exists"] = True
                            health["folder_paused"] = bool(f.get("paused", False))
                            break
            except Exception:
                pass
            return health

        return self._cached(f"health:{folder_id}", 2.0, _load)

    def scan_folder(self, folder_id: str = "mc-shared") -> bool:
        if not self.api_key:
            self.refresh_api_key()
        if not self.api_key:
            return False
        try:
            r = self._request("POST", f"/rest/db/scan?folder={folder_id}", timeout=2.0)
            self._cache.pop(f"pending:{folder_id}", None)
            return r.status_code in (200, 204)
        except Exception:
            return False

    def get_pending_count(self, folder_id: str = "mc-shared") -> int:
        def _load() -> int:
            if not self.api_key:
                self.refresh_api_key()
            if not self.api_key:
                return 0
            try:
                s = self._request("GET", f"/rest/db/status?folder={folder_id}", timeout=1.2)
                if s.status_code == 200:
                    d = s.json()
                    for k in ("needTotalItems", "needTotalFiles", "needFiles", "needItems"):
                        if k in d:
                            try:
                                return max(0, int(d.get(k, 0)))
                            except Exception:
                                pass
                r = self._request("GET", f"/rest/db/need?folder={folder_id}", timeout=1.8)
                if r.status_code == 200:
                    d2 = r.json()
                    return len(d2.get("progress", [])) + len(d2.get("queued", [])) + len(d2.get("rest", []))
            except Exception:
                return 0
            return 0

        return self._cached(f"pending:{folder_id}", 2.5, _load)

    def ensure_folder(self, folder_id: str, path: str) -> tuple[bool, str]:
        """Register Syncthing folder at path if missing."""
        from pathlib import Path as _Path

        folder_id = str(folder_id or "").strip()
        folder_path = str(_Path(path).expanduser().resolve())
        if not folder_id or not folder_path:
            return False, "Folder id/path missing"

        _Path(folder_path).mkdir(parents=True, exist_ok=True)

        if not self.api_key:
            self.refresh_api_key()
        if not self.api_key:
            return False, "Syncthing API key not found"

        health = self.get_health(folder_id)
        if health.get("folder_exists"):
            return True, "Folder already configured"

        try:
            cfg_resp = self._request("GET", "/rest/config", timeout=2.5)
            if cfg_resp.status_code != 200:
                return False, f"Syncthing config read failed ({cfg_resp.status_code})"
            cfg = cfg_resp.json()
            folders = list(cfg.get("folders") or [])
            for f in folders:
                if str(f.get("id", "")) == folder_id:
                    return True, "Folder already configured"
            folders.append(
                {
                    "id": folder_id,
                    "label": f"Minecraft {folder_id}",
                    "path": folder_path,
                    "type": "sendreceive",
                    "rescanIntervalS": 60,
                    "fsWatcherEnabled": True,
                    "fsWatcherDelayS": 10,
                    "versioning": {"type": "simple", "params": {"keep": "5"}},
                }
            )
            cfg["folders"] = folders
            put = self._request("PUT", "/rest/config", timeout=4.0, json=cfg)
            self._cache.clear()
            if put.status_code in (200, 202):
                return True, "Syncthing folder created"
            return False, f"Syncthing config update failed ({put.status_code})"
        except Exception as e:
            return False, str(e)

    def _require_api(self) -> tuple[bool, str]:
        if not self.is_running_noauth():
            return False, "Syncthing is not running"
        if not self.api_key:
            self.refresh_api_key()
        if not self.api_key:
            return False, "Syncthing API key not found"
        return True, ""

    def _load_config(self) -> tuple[dict[str, Any] | None, str]:
        ok, msg = self._require_api()
        if not ok:
            return None, msg
        try:
            r = self._request("GET", "/rest/config", timeout=2.5)
            if r.status_code != 200:
                return None, f"Config read failed ({r.status_code})"
            cfg = r.json()
            if isinstance(cfg, dict):
                return cfg, ""
            return None, "Invalid Syncthing config"
        except Exception as e:
            return None, str(e)

    def _save_config(self, cfg: dict[str, Any]) -> tuple[bool, str]:
        try:
            r = self._request("PUT", "/rest/config", timeout=5.0, json=cfg)
            self._cache.clear()
            if r.status_code in (200, 202):
                return True, "OK"
            return False, f"Config save failed ({r.status_code})"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def normalize_device_id(raw: str) -> str:
        s = str(raw or "").strip().upper().replace(" ", "")
        if DEVICE_ID_RE.match(s):
            return s
        compact = re.sub(r"[^A-Z0-9]", "", s)
        if len(compact) == 56:
            parts = [compact[i : i + 7] for i in range(0, 56, 7)]
            return "-".join(parts)
        return ""

    def get_local_device(self) -> dict[str, Any]:
        out = {"device_id": "", "name": "", "running": False}
        if not self.is_running_noauth():
            return out
        out["running"] = True
        ok, _ = self._require_api()
        if not ok:
            return out
        try:
            r = self._request("GET", "/rest/system/status", timeout=1.5)
            if r.status_code == 200:
                d = r.json()
                out["device_id"] = str(d.get("myID", "") or "")
                out["name"] = str(d.get("name", "") or "")
        except Exception:
            pass
        return out

    def list_peers(self) -> list[dict[str, Any]]:
        ok, _ = self._require_api()
        if not ok:
            return []
        names: dict[str, str] = {}
        connected: dict[str, bool] = {}
        try:
            cfg, _ = self._load_config()
            if cfg:
                for dev in cfg.get("devices") or []:
                    did = str(dev.get("deviceID", "") or "")
                    if did:
                        names[did] = str(dev.get("name", "") or did[:7])
            conn = self._request("GET", "/rest/system/connections", timeout=1.5)
            if conn.status_code == 200:
                for did, row in (conn.json().get("connections") or {}).items():
                    connected[str(did)] = bool((row or {}).get("connected"))
        except Exception:
            return []

        rows: list[dict[str, Any]] = []
        for did, name in names.items():
            local = self.get_local_device().get("device_id", "")
            if did and did == local:
                continue
            rows.append(
                {
                    "device_id": did,
                    "name": name,
                    "connected": bool(connected.get(did)),
                }
            )
        rows.sort(key=lambda r: (not r["connected"], r["name"].lower()))
        return rows

    def build_invite_payload(self, server_id: str, folder_id: str) -> dict[str, Any]:
        local = self.get_local_device()
        payload = {
            "t": "mc-host",
            "server_id": str(server_id or "").strip().upper(),
            "folder_id": str(folder_id or "").strip(),
            "device_id": str(local.get("device_id", "") or ""),
            "device_name": str(local.get("name", "") or ""),
        }
        return payload

    @staticmethod
    def invite_qr_url(invite_text: str, size: int = 200) -> str:
        data = quote(str(invite_text or ""), safe="")
        return f"https://api.qrserver.com/v1/create-qr-code/?size={size}x{size}&data={data}"

    def add_remote_device(
        self,
        device_id: str,
        *,
        name: str = "",
        folder_id: str = "",
    ) -> tuple[bool, str]:
        did = self.normalize_device_id(device_id)
        if not did:
            return False, "Invalid Syncthing device ID"

        local = self.get_local_device()
        if did == str(local.get("device_id", "") or ""):
            return False, "That is your own device ID"

        cfg, msg = self._load_config()
        if cfg is None:
            return False, msg

        devices = list(cfg.get("devices") or [])
        known = {str(d.get("deviceID", "")) for d in devices}
        label = str(name or "").strip() or f"Friend-{did[:7]}"

        if did not in known:
            devices.append(
                {
                    "deviceID": did,
                    "name": label,
                    "addresses": ["dynamic"],
                    "compression": "metadata",
                    "introducer": False,
                    "paused": False,
                    "allowedNetworks": [],
                    "autoAcceptFolders": False,
                    "maxSendKbps": 0,
                    "maxRecvKbps": 0,
                    "untrusted": False,
                }
            )
            cfg["devices"] = devices

        if folder_id:
            folders = list(cfg.get("folders") or [])
            touched = False
            for folder in folders:
                if str(folder.get("id", "")) != folder_id:
                    continue
                devs = list(folder.get("devices") or [])
                ids = {str(x.get("deviceID", "")) for x in devs}
                if did not in ids:
                    devs.append({"deviceID": did})
                    folder["devices"] = devs
                    touched = True
                break
            if not touched:
                return False, f'Syncthing folder "{folder_id}" not found. Save settings first.'
            cfg["folders"] = folders

        ok, save_msg = self._save_config(cfg)
        if not ok:
            return False, save_msg
        return True, f"Added {label}. Ask them to accept your device in Syncthing too."

    def apply_invite_payload(self, payload: dict[str, Any]) -> tuple[bool, str]:
        kind = str(payload.get("t", "") or "")
        if kind and kind != "mc-host":
            return False, "Not a valid MC Host invite"
        did = self.normalize_device_id(str(payload.get("device_id", "")))
        if not did:
            return False, "Invite missing device ID"
        name = str(payload.get("device_name", "") or "Friend")
        folder_id = str(payload.get("folder_id", "") or "")
        ok, msg = self.add_remote_device(did, name=name, folder_id=folder_id)
        if not ok:
            return ok, msg
        sid = str(payload.get("server_id", "") or "").strip().upper()
        extra = f" Server ID: {sid}" if sid else ""
        return True, msg + extra
