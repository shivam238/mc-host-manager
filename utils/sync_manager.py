from __future__ import annotations

import os
import platform
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

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
