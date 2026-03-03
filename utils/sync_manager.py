try:
    import requests
except ImportError:
    print("❌ Error: 'requests' library is not installed.")
    print("👉 Fix: Run 'pip3 install requests'")
    import sys
    sys.exit(1)
import xml.etree.ElementTree as ET
from pathlib import Path
import os
import platform
import time
import threading
from typing import Any, Callable, TypeVar, cast

T = TypeVar("T")

class SyncManager:
    def __init__(self, url="http://localhost:8384"):
        self.url = url
        self.api_key = self._get_api_key()
        self.headers = {"X-API-Key": self.api_key} if self.api_key else {}
        self._session = requests.Session()
        self._cache_lock = threading.Lock()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._folder_alias: dict[str, str] = {}

    def refresh_api_key(self):
        self.api_key = self._get_api_key()
        self.headers = {"X-API-Key": self.api_key} if self.api_key else {}
        self._clear_cache()
        return bool(self.api_key)

    def _clear_cache(self, prefix: str = ""):
        with self._cache_lock:
            if not prefix:
                self._cache.clear()
                return
            for key in list(self._cache.keys()):
                if key.startswith(prefix):
                    self._cache.pop(key, None)

    def _cached(self, cache_key: str, ttl_s: float, loader: Callable[[], T]) -> T:
        now = time.time()
        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if entry and (now - entry[0]) < ttl_s:
                return cast(T, entry[1])
        value = loader()
        with self._cache_lock:
            self._cache[cache_key] = (now, value)
        return value

    def _request(self, method: str, path: str, timeout: float = 1.5, **kwargs):
        return self._session.request(
            method=method,
            url=f"{self.url}{path}",
            headers=self.headers,
            timeout=timeout,
            **kwargs,
        )

    def _request_noauth(self, method: str, path: str, timeout: float = 1.2, **kwargs):
        return self._session.request(
            method=method,
            url=f"{self.url}{path}",
            timeout=timeout,
            **kwargs,
        )

    def _get_api_key(self):
        paths = [Path.home() / ".config/syncthing/config.xml"]
        system = platform.system()
        if system == "Windows":
            appdata = os.environ.get("APPDATA")
            localapp = os.environ.get("LOCALAPPDATA")
            if appdata:
                paths.append(Path(appdata) / "Syncthing/config.xml")
            if localapp:
                paths.append(Path(localapp) / "Syncthing/config.xml")
        else:
            paths.append(Path.home() / ".local/share/syncthing/config.xml")
            # Newer distros may keep Syncthing config in state dir.
            paths.append(Path.home() / ".local/state/syncthing/config.xml")

        # Portable/fallback locations.
        stconfdir = os.environ.get("STCONFDIR")
        if stconfdir:
            paths.append(Path(stconfdir) / "config.xml")
        paths.extend(
            [
                Path(__file__).parent.parent / "bin/config.xml",
                Path(__file__).parent.parent / "bin/syncthing/config.xml",
            ]
        )

        seen = set()
        for p in paths:
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            if p.exists():
                try:
                    tree = ET.parse(p)
                    root = tree.getroot()
                    apikey_elem = root.find(".//apikey")
                    if apikey_elem is not None:
                        return apikey_elem.text
                except (ET.ParseError, PermissionError): continue
        return ""

    def _norm_path(self, p):
        try:
            return str(Path(p).expanduser().resolve())
        except Exception:
            return str(Path(p).expanduser())

    def _effective_folder_id(self, folder_id: str):
        return self._folder_alias.get(folder_id, folder_id)

    def get_my_id(self):
        try:
            if not self.api_key:
                self.refresh_api_key()
            r = self._request("GET", "/rest/system/status", timeout=1.2)
            return r.json().get("myID") if r.status_code == 200 else None
        except: return None

    def is_running_noauth(self):
        """Detect if Syncthing HTTP API is reachable even without API key."""
        try:
            r = self._request_noauth("GET", "/rest/noauth/health", timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        try:
            # fallback: web UI root reachable
            r = self._request_noauth("GET", "/", timeout=1.0)
            return r.status_code in (200, 302, 401, 403)
        except Exception:
            return False

    def set_paused(self, folder_id, paused=True):
        """Pause/Resume syncing to prevent corruption during server run"""
        try:
            if not self.api_key:
                self.refresh_api_key()
            folder_id = self._effective_folder_id(folder_id)
            cfg = self._request("GET", "/rest/config", timeout=2.0).json()
            for f in cfg.get("folders", []):
                if f["id"] == folder_id:
                    f["paused"] = paused
                    self._request("PUT", "/rest/config", timeout=2.0, json=cfg)
                    self._clear_cache("health:")
                    return True
            return False
        except: return False

    def ensure_folder(self, folder_path, folder_id="mc-shared"):
        try:
            if not self.api_key:
                self.refresh_api_key()
            if not self.api_key:
                return False
            desired_path = self._norm_path(folder_path)
            cfg = self._request("GET", "/rest/config", timeout=2.0).json()
            for f in cfg.get("folders", []):
                if f.get("id") == folder_id:
                    self._folder_alias[folder_id] = folder_id
                    return True
            # Folder may already exist with different ID; map by path to avoid false failures.
            for f in cfg.get("folders", []):
                try:
                    if self._norm_path(f.get("path", "")) == desired_path:
                        fid = str(f.get("id", "") or "")
                        if fid:
                            self._folder_alias[folder_id] = fid
                            return True
                except Exception:
                    continue
            
            new_f = {
                "id": folder_id,
                "label": "MC Shared World",
                "path": str(folder_path),
                "type": "sendreceive",
                "rescanIntervalS": 60
            }
            cfg["folders"].append(new_f)
            self._request("PUT", "/rest/config", timeout=2.0, json=cfg)
            self._request("POST", "/rest/system/restart", timeout=2.0)
            self._folder_alias[folder_id] = folder_id
            self._clear_cache()
            return True
        except: return False

    def get_health(self, folder_id="mc-shared") -> dict[str, Any]:
        def _load_health():
            health = {
                "api_key_ok": bool(self.api_key),
                "running": False,
                "my_id": None,
                "connected_peers": 0,
                "folder_exists": False,
                "folder_paused": None,
            }
            try:
                # First: detect process/UI availability without requiring API key.
                health["running"] = bool(self.is_running_noauth())

                if not self.api_key:
                    self.refresh_api_key()
                    health["api_key_ok"] = bool(self.api_key)
                if not self.api_key:
                    return health

                status_r = self._request("GET", "/rest/system/status", timeout=1.2)
                if status_r.status_code != 200:
                    return health
                status = status_r.json()
                health["running"] = True
                health["my_id"] = status.get("myID")

                conn_r = self._request("GET", "/rest/system/connections", timeout=1.2)
                if conn_r.status_code == 200:
                    conns = conn_r.json().get("connections", {})
                    health["connected_peers"] = sum(1 for c in conns.values() if c.get("connected"))

                cfg_r = self._request("GET", "/rest/config", timeout=1.2)
                if cfg_r.status_code == 200:
                    folders = cfg_r.json().get("folders", [])
                    eff = self._effective_folder_id(folder_id)
                    for f in folders:
                        if f.get("id") == eff:
                            health["folder_exists"] = True
                            health["folder_paused"] = bool(f.get("paused", False))
                            break
            except Exception:
                pass
            return health

        # Status endpoint is polled very frequently by UI; avoid repeated HTTP bursts.
        return self._cached(f"health:{folder_id}", 1.8, _load_health)

    def scan_folder(self, folder_id="mc-shared"):
        try:
            if not self.api_key:
                self.refresh_api_key()
            if not self.api_key:
                return False
            eff = self._effective_folder_id(folder_id)
            r = self._request("POST", f"/rest/db/scan?folder={eff}", timeout=2.0)
            if r.status_code == 404 and eff != folder_id:
                # Alias stale; retry with canonical id.
                r = self._request("POST", f"/rest/db/scan?folder={folder_id}", timeout=2.0)
            self._clear_cache("pending:")
            return r.status_code in (200, 204)
        except Exception:
            return False

    def get_pending_count(self, folder_id="mc-shared") -> int:
        def _load_pending():
            try:
                if not self.api_key:
                    self.refresh_api_key()
                if not self.api_key:
                    return 0
                eff = self._effective_folder_id(folder_id)
                # Lightweight status endpoint first (much cheaper than /db/need on large folders).
                rs = self._request("GET", f"/rest/db/status?folder={eff}", timeout=1.0)
                if rs.status_code == 200:
                    ds = rs.json()
                    for key in ("needTotalItems", "needTotalFiles", "needFiles", "needItems"):
                        if key in ds:
                            try:
                                return max(0, int(ds.get(key, 0)))
                            except Exception:
                                pass
                    try:
                        g = ds.get("globalFiles")
                        l = ds.get("localFiles")
                        if isinstance(g, int) and isinstance(l, int):
                            return max(0, g - l)
                    except Exception:
                        pass

                # Fallback: expensive endpoint (can be large JSON on big sync queues).
                r = self._request("GET", f"/rest/db/need?folder={eff}", timeout=2.0)
                if r.status_code != 200:
                    return 0
                d = r.json()
                return len(d.get("progress", [])) + len(d.get("queued", [])) + len(d.get("rest", []))
            except Exception:
                return 0

        return self._cached(f"pending:{folder_id}", 8.0, _load_pending)

    def ensure_device_for_folder(self, device_id, folder_id="mc-shared", label="MC Host Peer"):
        """Best-effort: add remote device and share folder with it."""
        dev_id = str(device_id or "").strip()
        if not dev_id:
            return False
        try:
            if not self.api_key:
                self.refresh_api_key()
            if not self.api_key:
                return False
            cfg_r = self._request("GET", "/rest/config", timeout=2.2)
            if cfg_r.status_code != 200:
                return False
            cfg = cfg_r.json()
            changed = False

            devices = cfg.get("devices", [])
            found_device = None
            for d in devices:
                if str(d.get("deviceID", "") or "").strip() == dev_id:
                    found_device = d
                    break
            if not found_device:
                devices.append(
                    {
                        "deviceID": dev_id,
                        "name": str(label or "MC Host Peer"),
                        "addresses": ["dynamic"],
                        "compression": "metadata",
                        "introducer": False,
                        "skipIntroductionRemovals": False,
                        "paused": False,
                        "autoAcceptFolders": True,
                    }
                )
                cfg["devices"] = devices
                changed = True

            eff = self._effective_folder_id(folder_id)
            folders = cfg.get("folders", [])
            target_folder = None
            for f in folders:
                if str(f.get("id", "") or "") == eff:
                    target_folder = f
                    break
            if target_folder is None and eff != folder_id:
                for f in folders:
                    if str(f.get("id", "") or "") == folder_id:
                        target_folder = f
                        break
            if target_folder is None:
                return False

            folder_devices = target_folder.get("devices")
            if not isinstance(folder_devices, list):
                folder_devices = []
            has_dev = any(str(it.get("deviceID", "") or "").strip() == dev_id for it in folder_devices if isinstance(it, dict))
            if not has_dev:
                folder_devices.append({"deviceID": dev_id, "introducedBy": ""})
                target_folder["devices"] = folder_devices
                changed = True

            if not changed:
                return True

            put_r = self._request("PUT", "/rest/config", timeout=2.4, json=cfg)
            if put_r.status_code not in (200, 204):
                return False
            # Apply config quickly.
            try:
                self._request("POST", "/rest/system/restart", timeout=1.5)
            except Exception:
                pass
            self._clear_cache()
            return True
        except Exception:
            return False

    def _pending_devices(self):
        """Return pending device IDs offered to this node."""
        paths = [
            "/rest/cluster/pending/devices",
            "/rest/cluster/pendingdevices",
        ]
        for path in paths:
            try:
                r = self._request("GET", path, timeout=1.8)
                if r.status_code != 200:
                    continue
                data = r.json()
                out = []
                if isinstance(data, dict):
                    # Common shape: { "<deviceID>": {...}, ... }
                    for k, v in data.items():
                        did = str(k or "").strip()
                        if did:
                            out.append({"deviceID": did, "meta": v if isinstance(v, dict) else {}})
                    # Alternate shape: {"pendingDevices":[...]}
                    arr = data.get("pendingDevices")
                    if isinstance(arr, list):
                        for row in arr:
                            if not isinstance(row, dict):
                                continue
                            did = str(row.get("deviceID", "") or "").strip()
                            if did:
                                out.append({"deviceID": did, "meta": row})
                elif isinstance(data, list):
                    for row in data:
                        if not isinstance(row, dict):
                            continue
                        did = str(row.get("deviceID", "") or "").strip()
                        if did:
                            out.append({"deviceID": did, "meta": row})
                # de-dup
                seen = set()
                uniq = []
                for row in out:
                    did = str(row.get("deviceID", "") or "")
                    if not did or did in seen:
                        continue
                    seen.add(did)
                    uniq.append(row)
                return uniq
            except Exception:
                continue
        return []

    def _pending_folders(self):
        """Return pending folders offered by remote devices."""
        paths = [
            "/rest/cluster/pending/folders",
            "/rest/cluster/pendingfolders",
        ]
        for path in paths:
            try:
                r = self._request("GET", path, timeout=1.8)
                if r.status_code != 200:
                    continue
                data = r.json()
                out = []
                if isinstance(data, dict):
                    # Common shape: { "<folderID>": { "<deviceID>": {...} } }
                    for fid, offers in data.items():
                        folder_id = str(fid or "").strip()
                        if not folder_id:
                            continue
                        if isinstance(offers, dict):
                            for did, meta in offers.items():
                                dev_id = str(did or "").strip()
                                if dev_id:
                                    out.append({"folderID": folder_id, "deviceID": dev_id, "meta": meta if isinstance(meta, dict) else {}})
                        elif isinstance(offers, list):
                            for row in offers:
                                if not isinstance(row, dict):
                                    continue
                                dev_id = str(row.get("deviceID", "") or "").strip()
                                if dev_id:
                                    out.append({"folderID": folder_id, "deviceID": dev_id, "meta": row})
                    # Alternate shape: {"pendingFolders":[...]}
                    arr = data.get("pendingFolders")
                    if isinstance(arr, list):
                        for row in arr:
                            if not isinstance(row, dict):
                                continue
                            folder_id = str(row.get("folderID", "") or "").strip()
                            dev_id = str(row.get("deviceID", "") or "").strip()
                            if folder_id and dev_id:
                                out.append({"folderID": folder_id, "deviceID": dev_id, "meta": row})
                elif isinstance(data, list):
                    for row in data:
                        if not isinstance(row, dict):
                            continue
                        folder_id = str(row.get("folderID", "") or "").strip()
                        dev_id = str(row.get("deviceID", "") or "").strip()
                        if folder_id and dev_id:
                            out.append({"folderID": folder_id, "deviceID": dev_id, "meta": row})
                # de-dup
                seen = set()
                uniq = []
                for row in out:
                    key = (str(row.get("folderID", "")), str(row.get("deviceID", "")))
                    if not key[0] or not key[1] or key in seen:
                        continue
                    seen.add(key)
                    uniq.append(row)
                return uniq
            except Exception:
                continue
        return []

    def auto_accept_pending(self, folder_path, folder_id="mc-shared", allowed_device_ids=None):
        """
        Best-effort auto accept pending Syncthing devices/folders.
        Safety:
        - If `allowed_device_ids` is provided, only those IDs are accepted.
        """
        allow = set()
        if allowed_device_ids:
            for d in allowed_device_ids:
                did = str(d or "").strip()
                if did:
                    allow.add(did)

        result = {
            "ok": False,
            "accepted_devices": 0,
            "accepted_folders": 0,
            "pending_devices": 0,
            "pending_folders": 0,
            "skipped_devices": 0,
            "skipped_folders": 0,
            "changed": False,
        }
        try:
            if not self.api_key:
                self.refresh_api_key()
            if not self.api_key:
                return result
            cfg_r = self._request("GET", "/rest/config", timeout=2.2)
            if cfg_r.status_code != 200:
                return result
            cfg = cfg_r.json()
            changed = False

            pending_devices = self._pending_devices()
            pending_folders = self._pending_folders()
            result["pending_devices"] = len(pending_devices)
            result["pending_folders"] = len(pending_folders)

            # Existing devices in config are tracked to avoid duplicate inserts.
            known = set()
            for d in cfg.get("devices", []) if isinstance(cfg.get("devices"), list) else []:
                if not isinstance(d, dict):
                    continue
                did = str(d.get("deviceID", "") or "").strip()
                if did:
                    known.add(did)

            def _is_allowed(did: str) -> bool:
                if not did:
                    return False
                return did in allow

            devices_cfg = cfg.get("devices")
            if not isinstance(devices_cfg, list):
                devices_cfg = []
                cfg["devices"] = devices_cfg

            for row in pending_devices:
                did = str(row.get("deviceID", "") or "").strip()
                if not _is_allowed(did):
                    result["skipped_devices"] += 1
                    continue
                if did not in known:
                    devices_cfg.append(
                        {
                            "deviceID": did,
                            "name": "MC Host Peer",
                            "addresses": ["dynamic"],
                            "compression": "metadata",
                            "introducer": False,
                            "skipIntroductionRemovals": False,
                            "paused": False,
                            "autoAcceptFolders": True,
                        }
                    )
                    known.add(did)
                    changed = True
                    result["accepted_devices"] += 1

            folders_cfg = cfg.get("folders")
            if not isinstance(folders_cfg, list):
                folders_cfg = []
                cfg["folders"] = folders_cfg

            eff = self._effective_folder_id(folder_id)
            target_folder = None
            for f in folders_cfg:
                if not isinstance(f, dict):
                    continue
                if str(f.get("id", "") or "") in (eff, folder_id):
                    target_folder = f
                    break
            if target_folder is None:
                target_folder = {
                    "id": folder_id,
                    "label": "MC Shared World",
                    "path": str(folder_path),
                    "type": "sendreceive",
                    "rescanIntervalS": 60,
                    "devices": [],
                }
                folders_cfg.append(target_folder)
                changed = True

            # normalize folder path
            if str(target_folder.get("path", "") or "") != str(folder_path):
                target_folder["path"] = str(folder_path)
                changed = True

            folder_devices = target_folder.get("devices")
            if not isinstance(folder_devices, list):
                folder_devices = []
                target_folder["devices"] = folder_devices
                changed = True
            folder_dev_ids = {str(it.get("deviceID", "") or "").strip() for it in folder_devices if isinstance(it, dict)}

            for row in pending_folders:
                fid = str(row.get("folderID", "") or "").strip()
                did = str(row.get("deviceID", "") or "").strip()
                if not fid or not did:
                    continue
                # accept only target folder id or alias.
                if fid not in (folder_id, eff):
                    result["skipped_folders"] += 1
                    continue
                if not _is_allowed(did):
                    result["skipped_folders"] += 1
                    continue
                if did not in folder_dev_ids:
                    folder_devices.append({"deviceID": did, "introducedBy": ""})
                    folder_dev_ids.add(did)
                    changed = True
                    result["accepted_folders"] += 1

            if changed:
                put_r = self._request("PUT", "/rest/config", timeout=2.6, json=cfg)
                if put_r.status_code not in (200, 204):
                    return result
                try:
                    self._request("POST", "/rest/system/restart", timeout=1.6)
                except Exception:
                    pass
                self._clear_cache()

            result["changed"] = bool(changed)
            result["ok"] = True
            return result
        except Exception:
            return result
