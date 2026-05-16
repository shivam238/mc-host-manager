from __future__ import annotations
import json
import os
import shutil
import tempfile
import zipfile
import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from utils import backup_manager, lock_manager
from utils.config import (
    RESOURCE_DIR, load_config, save_config, save_user, load_user,
    normalize_path, ensure_project_key, get_syncthing_folder,
    get_local_ip,
)
from utils.server_layout import (
    detect_server_candidates,
    normalize_server_id,
    read_server_id_file,
    resolve_layout,
)
from utils import members_registry
from utils.app_state import (
    host_state, host_lock, is_task_running, get_task, cache_get, clear_cache
)
from utils.flow_manager import (
    mc_server, st_api, run_task, validate_paths,
    start_flow, finalize_stop_flow, restart_flow, backup_now, restore_backup,
    update_server_properties
)
from utils.host_policy import evaluate_start_gate
from utils.setup_flow import is_setup_complete, run_quick_setup, build_next_steps
from utils.group_manager import create_server_group, join_server_group, format_invite_code, parse_invite_input
from utils import dependency_manager

def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if length <= 0:
        return {}
    try:
        return json.loads(handler.rfile.read(length) or b"{}")
    except Exception:
        return {}

def can_control_request(handler: BaseHTTPRequestHandler, cfg: dict[str, Any], body: dict[str, Any] | None = None) -> bool:
    ip = str(handler.client_address[0] if handler.client_address else "")
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    expected = ensure_project_key(cfg)
    sent = str(handler.headers.get("X-MC-Project-Key", "") or "").strip()
    if not sent and isinstance(body, dict):
        sent = str(body.get("project_key", "") or "").strip()
    return bool(expected and sent and sent == expected)

def get_server_metrics(pid: int | None, ram_used_mb: float | None, ram_alloc_mb: int | None) -> dict[str, int]:
    try:
        import psutil
    except ImportError:
        psutil = None

    cpu_pct = 0
    mem_pct = 0
    disk_pct = 0

    if ram_used_mb is not None and ram_alloc_mb and ram_alloc_mb > 0:
        try:
            mem_pct = int(max(0, min(100, round((float(ram_used_mb) / float(ram_alloc_mb)) * 100))))
        except Exception:
            mem_pct = 0

    if not pid:
        return {"cpu_pct": cpu_pct, "mem_pct": mem_pct, "disk_pct": disk_pct}

    try:
        if psutil is not None:
            proc = psutil.Process(pid)
            raw_cpu = float(proc.cpu_percent(interval=None))
            cpu_count = float(psutil.cpu_count() or 1)
            cpu_pct = int(max(0, min(100, raw_cpu / max(1.0, cpu_count))))
            if mem_pct <= 0:
                mem_pct = int(max(0, min(100, round(float(proc.memory_percent())))))
            io_c = proc.io_counters()
            bps = int(io_c.read_bytes + io_c.write_bytes)
            disk_pct = int(max(0, min(100, round((bps / (40 * 1024 * 1024)) * 100))))
    except Exception:
        pass

    return {"cpu_pct": cpu_pct, "mem_pct": mem_pct, "disk_pct": disk_pct}

def parse_ram_to_mb(raw: str) -> int | None:
    try:
        s = str(raw or "").strip().upper()
        if s.endswith("G"):
            return int(float(s[:-1]) * 1024)
        if s.endswith("M"):
            return int(float(s[:-1]))
        if s.isdigit():
            return int(s)
    except Exception:
        return None
    return None

def get_status(cfg: dict[str, Any]) -> dict[str, Any]:
    running = mc_server.is_running()
    task = get_task()

    with host_lock:
        ready = bool(host_state["ready"])
        last_sync = str(host_state.get("last_sync", "") or "")
        last_error = str(host_state.get("last_error", "") or "")
        if last_error and not task.get("running"):
            host_state["last_error"] = ""

    server_state = "offline"
    if task.get("running"):
        act = str(task.get("action", "") or "")
        if act in ("starting", "restart"):
            server_state = "starting"
        elif act in ("stopping", "recovering"):
            server_state = "stopping"
        else:
            server_state = "working"
    elif running and not (ready or mc_server.is_ready()):
        server_state = "starting"
    elif running:
        server_state = "running"

    shared = normalize_path(cfg.get("shared_dir", ""))
    project_key = ensure_project_key(cfg)
    server_id = str(cfg.get("server_id", "") or "").strip()
    syn_folder = get_syncthing_folder(cfg)
    lock_info = lock_manager.get_lock(shared) if shared else None
    lock_host = str(lock_info.get("host", "") or "") if lock_info and not lock_info.get("expired") else ""

    syn_h = cache_get(f"syn_health:{syn_folder}", 2.0, lambda: st_api.get_health(syn_folder))
    syn_status = "missing"
    if syn_h.get("running"):
        syn_status = "connected" if (syn_h.get("connected_peers", 0) or 0) > 0 else "running"
    elif syn_h.get("api_key_ok"):
        syn_status = "stopped"

    ram_used = mc_server.get_ram_mb()
    ram_alloc = parse_ram_to_mb(cfg.get("ram", ""))
    pid = mc_server.get_pid()
    m = cache_get(f"server_metrics:{pid}:{ram_used}:{ram_alloc}", 0.6 if running else 1.2, lambda: get_server_metrics(pid, ram_used, ram_alloc))

    players = cache_get("players_online", 1.0 if running else 2.5, mc_server.get_online_players)
    pinfo = cache_get("players_info", 1.4 if running else 3.0, mc_server.get_player_stats)

    gate = evaluate_start_gate(
        cfg,
        running=running,
        task_running=bool(task.get("running")),
        lock_info=lock_info,
        syn_h=syn_h,
    )

    file_sid = read_server_id_file(shared) if shared else ""
    members = cache_get(
        f"members:{shared}:{lock_host}",
        1.2,
        lambda: members_registry.members_summary(shared, lock_host=lock_host),
    )

    setup_done = is_setup_complete(cfg)
    next_steps = build_next_steps(cfg, syn_h) if setup_done else [
        "Run Quick Setup to detect your server and create a Server ID.",
    ]

    return {
        "setup_complete": setup_done,
        "setup_next_steps": next_steps,
        "project_name": cfg.get("project_name", "Minecraft Server"),
        "user": load_user(),
        "server_id": server_id,
        "server_id_synced": bool(file_sid and server_id and file_sid == server_id),
        "server_id_on_disk": file_sid,
        "syncthing_folder": syn_folder,
        "project_key": project_key,
        "running": running,
        "server_state": server_state,
        "server_ready": bool(ready or mc_server.is_ready()),
        "lock": lock_info,
        "local_ip": get_local_ip(),
        "ram": cfg.get("ram", "4G"),
        "max_players": int(cfg.get("max_players", 20)),
        "whitelist_enabled": bool(cfg.get("whitelist_enabled", False)),
        "allow_remote_stop": bool(cfg.get("allow_remote_stop", True)),
        "task": task,
        "last_sync": {"time": last_sync} if last_sync else None,
        "last_error": last_error,
        "server_dir": cfg.get("server_dir", ""),
        "shared_dir": cfg.get("shared_dir", ""),
        "server_jar": cfg.get("server_jar", "server.jar"),
        "syncthing_status": syn_status,
        "syncthing_connected_peers": int(syn_h.get("connected_peers", 0) or 0),
        "syncthing_device_id": cache_get("st_device", 3.0, lambda: st_api.get_local_device().get("device_id", "")),
        "syncthing_device_name": cache_get("st_device_name", 3.0, lambda: st_api.get_local_device().get("name", "")),
        "syncthing_peers": cache_get("st_peers", 2.0, st_api.list_peers),
        "server_pid": pid,
        "server_uptime_s": mc_server.get_uptime_seconds(),
        "server_ram_mb": ram_used,
        "players_online": players,
        "players_count": len(players),
        "players_info": pinfo,
        "sync_pending_count": cache_get(
            f"sync_pending:{syn_folder}", 2.5, lambda: st_api.get_pending_count(syn_folder)
        ),
        "members": members.get("members", []),
        "members_online": int(members.get("members_online", 0)),
        "members_total": int(members.get("members_total", 0)),
        "server_cpu_pct": int(m.get("cpu_pct", 0)),
        "server_mem_pct": int(m.get("mem_pct", 0)),
        "server_disk_pct": int(m.get("disk_pct", 0)),
        "can_open_server_files": (not running and not is_task_running()),
        "can_start": bool(gate.get("can_start")),
        "start_block_reason": str(gate.get("start_block_reason") or ""),
        "sync_isolated": bool(gate.get("sync_isolated")),
        "remote_host": str(gate.get("remote_host") or ""),
        "invite_code": format_invite_code(server_id),
        "deps": cache_get("deps_status", 2.5, dependency_manager.status_snapshot),
    }

class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, filename: str, content_type: str) -> None:
        p = RESOURCE_DIR / filename
        if not p.exists():
            # Fallback for split assets in ui/ directory
            p = RESOURCE_DIR / "ui" / filename

        if not p.exists():
            self.send_response(404)
            self.end_headers()
            return

        raw = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MC-Project-Key")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        cfg = load_config()

        if self.path == "/":
            self._serve_file("ui.html", "text/html")
            return
        if self.path == "/style.css":
            self._serve_file("style.css", "text/css")
            return
        if self.path == "/script.js":
            self._serve_file("script.js", "application/javascript")
            return

        if self.path == "/status":
            ttl = 0.25 if (mc_server.is_running() or is_task_running()) else 0.9
            self._json(cache_get("status:snapshot", ttl, lambda: get_status(cfg)))
            return

        if self.path == "/setup/detect":
            self._json({"ok": True, "candidates": detect_server_candidates()})
            return

        if self.path == "/setup/state":
            cfg_s = load_config(force=True)
            syn_f = get_syncthing_folder(cfg_s)
            syn_hs = st_api.get_health(syn_f)
            done = is_setup_complete(cfg_s)
            self._json(
                {
                    "ok": True,
                    "setup_complete": done,
                    "server_dir": cfg_s.get("server_dir", ""),
                    "server_id": cfg_s.get("server_id", ""),
                    "next_steps": build_next_steps(cfg_s, syn_hs) if done else [],
                }
            )
            return

        if self.path == "/members":
            shared = normalize_path(cfg.get("shared_dir", ""))
            lk = lock_manager.get_lock(shared) if shared else None
            host = str(lk.get("host", "") or "") if lk and not lk.get("expired") else ""
            self._json(members_registry.members_summary(shared, lock_host=host))
            return

        if self.path == "/deps/status":
            self._json({"ok": True, **dependency_manager.status_snapshot()})
            return

        if self.path == "/syncthing/invite":
            sid = str(cfg.get("server_id", "") or "").strip()
            folder = get_syncthing_folder(cfg)
            payload = st_api.build_invite_payload(sid, folder)
            invite_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            simple = format_invite_code(sid, str(payload.get("device_id", "") or ""))
            self._json(
                {
                    "ok": bool(sid),
                    "server_id": sid,
                    "folder_id": folder,
                    "device_id": payload.get("device_id", ""),
                    "device_name": payload.get("device_name", ""),
                    "invite": invite_text,
                    "invite_code": simple or format_invite_code(sid),
                    "qr_url": st_api.invite_qr_url(simple or invite_text) if sid else "",
                    "syncthing_ui": "http://127.0.0.1:8384/",
                }
            )
            return

        if self.path.startswith("/logs") and self.path != "/logs/details":
            self._json(cache_get("logs:tail", 0.8, lambda: {"logs": mc_server.get_logs()}))
            return

        if self.path == "/task":
            self._json(get_task())
            return

        if self.path == "/backup/list":
            shared = normalize_path(cfg.get("shared_dir", ""))
            backups = backup_manager.list_backups(Path(shared) / "backups") if shared else []
            self._json({"backups": backups})
            return

        if self.path.startswith("/backup/get"):
            q = parse_qs(urlparse(self.path).query)
            name = str((q.get("name") or [""])[0])
            shared = normalize_path(cfg.get("shared_dir", ""))
            if not shared or not name:
                self.send_response(404)
                self.end_headers()
                return
            root = (Path(shared) / "backups").resolve()
            f = (root / name).resolve()
            if root not in f.parents or not f.exists() or not f.is_file():
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f"attachment; filename={f.name}")
            self.send_header("Content-Length", str(f.stat().st_size))
            self.end_headers()
            with open(f, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile)
            return

        if self.path.startswith("/server/download"):
            ok, msg = validate_paths(cfg, True, False)
            if not ok:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg.encode("utf-8", errors="replace"))
                return
            if mc_server.is_running() or is_task_running():
                self.send_response(409)
                self.end_headers()
                self.wfile.write(b"Stop server first before downloading files.")
                return
            server_root = Path(normalize_path(cfg.get("server_dir", ""))).resolve()
            with tempfile.NamedTemporaryFile(prefix="mc_server_", suffix=".zip", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, dirs, files in os.walk(server_root, topdown=True, followlinks=False):
                        rootp = Path(root)
                        dirs[:] = [d for d in dirs if not (rootp / d).is_symlink()]
                        for fn in files:
                            f = rootp / fn
                            if f.is_symlink() or not f.is_file():
                                continue
                            rel = f.relative_to(server_root)
                            zf.write(f, rel.as_posix())
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f"attachment; filename={server_root.name}_files.zip")
                self.send_header("Content-Length", str(tmp_path.stat().st_size))
                self.end_headers()
                with open(tmp_path, "rb") as fh:
                    shutil.copyfileobj(fh, self.wfile)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Download failed: {e}".encode("utf-8", errors="replace"))
            finally:
                tmp_path.unlink(missing_ok=True)
            return

        if self.path.startswith("/open-folder"):
            q = parse_qs(urlparse(self.path).query)
            target = str((q.get("target") or [""])[0]).strip().lower()
            custom = str((q.get("path") or [""])[0]).strip()
            folder: Path | None = None
            if target == "server":
                folder = Path(normalize_path(cfg.get("server_dir", ""))) if cfg.get("server_dir") else None
            elif target == "shared":
                folder = Path(normalize_path(cfg.get("shared_dir", ""))) if cfg.get("shared_dir") else None
            elif target == "backups":
                sd = normalize_path(cfg.get("shared_dir", ""))
                folder = (Path(sd) / "backups") if sd else None
            elif target == "custom" and custom:
                folder = Path(normalize_path(custom)) if normalize_path(custom) else None
            if folder is None:
                self._json({"ok": False, "msg": "Folder path not configured."})
                return
            try:
                folder = folder.expanduser()
                folder.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    subprocess.Popen(["explorer", str(folder)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(folder)])
                else:
                    subprocess.Popen(["xdg-open", str(folder)])
                self._json({"ok": True, "path": str(folder)})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        cfg = load_config()
        body = read_json(self)

        if self.path == "/setup/quick":
            result = run_quick_setup(body, st_api)
            clear_cache()
            self._json(result, code=200 if result.get("ok") else 400)
            return

        if self.path == "/deps/install":
            result = dependency_manager.ensure_all_dependencies()
            try:
                st_api.refresh_api_key()
            except Exception:
                pass
            clear_cache()
            self._json(result, code=200 if result.get("ok") else 500)
            return

        if self.path == "/config/save":
            if "user" in body:
                save_user(str(body.get("user", "")))
                body.pop("user", None)
            if "server_id" in body:
                body["server_id"] = normalize_server_id(str(body.get("server_id", "")))
            cfg.update(body)
            pre_shared = normalize_path(cfg.get("shared_dir", ""))
            if not pre_shared and normalize_path(cfg.get("server_dir", "")):
                from utils.server_layout import default_shared_for_server

                pre_shared = default_shared_for_server(cfg["server_dir"])
            on_disk = read_server_id_file(pre_shared) if pre_shared else ""
            sid_in = normalize_server_id(str(cfg.get("server_id", "")))
            if on_disk and sid_in and on_disk != sid_in:
                self._json(
                    {
                        "ok": False,
                        "msg": f"This shared folder belongs to Server ID {on_disk}, not {sid_in}.",
                    }
                )
                return
            saved = resolve_layout(save_config(cfg), create_shared=True)
            ensure_project_key(saved)
            syn_folder = get_syncthing_folder(saved)
            syn_msg = ""
            shared = normalize_path(saved.get("shared_dir", ""))
            if shared:
                ok_syn, syn_msg = st_api.ensure_folder(syn_folder, shared)
                members_registry.touch_presence(
                    shared,
                    server_id=str(saved.get("server_id", "")),
                    hosting=mc_server.is_running(),
                )
                try:
                    inv = st_api.build_invite_payload(
                        str(saved.get("server_id", "")),
                        syn_folder,
                    )
                    Path(shared).joinpath("invite.json").write_text(
                        json.dumps(inv, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            try:
                if normalize_path(saved.get("server_dir", "")):
                    update_server_properties(
                        normalize_path(saved.get("server_dir", "")),
                        int(saved.get("max_players", 20)),
                        bool(saved.get("whitelist_enabled", False)),
                    )
            except Exception:
                pass
            clear_cache()
            self._json(
                {
                    "ok": True,
                    "server_id": saved.get("server_id", ""),
                    "shared_dir": saved.get("shared_dir", ""),
                    "server_dir": saved.get("server_dir", ""),
                    "syncthing_folder": syn_folder,
                    "syncthing_msg": syn_msg,
                }
            )
            return

        if self.path == "/server/create":
            result = create_server_group(
                user=str(body.get("user", "") or load_user()),
                server_dir=str(body.get("server_dir", "") or ""),
                project_name=str(body.get("project_name", "") or "Minecraft Server"),
                st_api=st_api,
            )
            if result.get("ok"):
                shared = normalize_path(result.get("shared_dir", ""))
                if shared:
                    syn_folder = result.get("syncthing_folder") or get_syncthing_folder(load_config(force=True))
                    inv = st_api.build_invite_payload(str(result.get("server_id", "")), syn_folder)
                    try:
                        Path(shared).joinpath("invite.json").write_text(
                            json.dumps(inv, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
            clear_cache()
            self._json(result, code=200 if result.get("ok") else 400)
            return

        if self.path == "/server/join":
            raw = str(
                body.get("invite")
                or body.get("friend_invite")
                or body.get("invite_code")
                or body.get("server_id")
                or ""
            ).strip()
            parsed = parse_invite_input(raw)
            result = join_server_group(
                invite_raw=raw,
                server_id=str(body.get("server_id", "") or parsed.get("server_id", "")),
                user=str(body.get("user", "") or load_user()),
                server_dir=str(body.get("server_dir", "") or ""),
                st_api=st_api,
            )
            if result.get("ok"):
                shared = normalize_path(result.get("shared_dir", ""))
                if shared:
                    syn_folder = result.get("syncthing_folder") or get_syncthing_folder(load_config(force=True))
                    st_api.ensure_folder(syn_folder, shared)
            clear_cache()
            self._json(result, code=200 if result.get("ok") else 400)
            return

        if self.path == "/host/start":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            if mc_server.is_running():
                self._json({"ok": False, "msg": "Server already running."})
                return
            cfg_now = load_config(force=True)
            shared = normalize_path(cfg_now.get("shared_dir", ""))
            lock_info = lock_manager.get_lock(shared) if shared else None
            syn_h = st_api.get_health(get_syncthing_folder(cfg_now))
            gate = evaluate_start_gate(
                cfg_now,
                running=False,
                task_running=False,
                lock_info=lock_info,
                syn_h=syn_h,
            )
            if not gate.get("can_start"):
                remote = str(gate.get("remote_host") or "")
                allow_override = bool(body.get("ack_isolated_risk")) and not remote
                if not allow_override:
                    self._json({"ok": False, "msg": str(gate.get("start_block_reason") or "Cannot start server.")})
                    return
            ok = run_task("starting", lambda cb: start_flow(load_config(force=True), cb))
            self._json({"ok": ok, "msg": "Starting..." if ok else "Another operation is running."})
            return

        if self.path == "/host/stop":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            if not mc_server.is_running() and not host_state.get("active"):
                self._json({"ok": False, "msg": "Server already offline."})
                return
            ok = run_task("stopping", lambda cb: finalize_stop_flow(load_config(force=True), cb))
            self._json({"ok": ok, "msg": "Stopping..." if ok else "Another operation is running."})
            return

        if self.path == "/host/restart":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            ok = run_task("restart", lambda cb: restart_flow(load_config(force=True), cb))
            self._json({"ok": ok, "msg": "Restarting..." if ok else "Another operation is running."})
            return

        if self.path == "/host/kill":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            if not mc_server.is_running() or mc_server.proc is None:
                self._json({"ok": False, "msg": "Server is not running."})
                return
            try:
                mc_server.proc.kill()
                self._json({"ok": True, "msg": "Kill signal sent."})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)})
            return

        if self.path == "/host/force":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            if mc_server.is_running() or is_task_running():
                self._json({"ok": False, "msg": "Stop server first."})
                return
            shared = normalize_path(cfg.get("shared_dir", ""))
            if not shared:
                self._json({"ok": False, "msg": "Shared folder not configured."})
                return
            lk = lock_manager.get_lock(shared)
            if lk and not lk.get("expired"):
                self._json({"ok": False, "msg": "Active lock exists. Use normal stop on host."})
                return
            lock_manager.remove_lock(shared)
            with host_lock:
                host_state["active"] = False
                host_state["ready"] = False
            self._json({"ok": True, "msg": "Lock cleared."})
            return

        if self.path == "/backup/now":
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            ok = run_task("backup", lambda cb: backup_now(load_config(force=True), cb))
            self._json({"ok": ok, "msg": "Backup started." if ok else "Another operation is running."})
            return

        if self.path == "/backup/restore":
            if is_task_running():
                self._json({"ok": False, "msg": "Another operation is running."})
                return
            name = str(body.get("name", "") or "").strip()
            ok = run_task("restore", lambda cb: restore_backup(load_config(force=True), name, cb))
            self._json({"ok": ok, "msg": "Restore started." if ok else "Another operation is running."})
            return

        if self.path == "/syncthing/add-device":
            did = str(body.get("device_id", "") or body.get("deviceID", "") or "").strip()
            name = str(body.get("name", "") or "").strip()
            folder = get_syncthing_folder(cfg)
            ok, msg = st_api.add_remote_device(did, name=name, folder_id=folder)
            clear_cache()
            self._json({"ok": ok, "msg": msg})
            return

        if self.path == "/syncthing/apply-invite":
            invite_raw = str(body.get("invite", "") or body.get("payload", "") or "").strip()
            if not invite_raw:
                self._json({"ok": False, "msg": "Paste invite code or Server ID."})
                return
            result = join_server_group(
                invite_raw=invite_raw,
                user=str(body.get("user", "") or load_user()),
                st_api=st_api,
            )
            clear_cache()
            self._json(result, code=200 if result.get("ok") else 400)
            return

        if self.path == "/sync/now":
            try:
                done = st_api.scan_folder(get_syncthing_folder(cfg))
                self._json({"ok": bool(done), "msg": "Sync scan triggered." if done else "Sync trigger failed."})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)})
            return

        if self.path == "/command":
            if not can_control_request(self, cfg, body):
                self._json({"ok": False, "msg": "Control blocked (project key mismatch)."})
                return
            cmd = str(body.get("cmd", "") or "").strip()
            if not cmd:
                self._json({"ok": False, "msg": "Empty command."})
                return
            if len(cmd) > 240 or any(c in cmd for c in ("\n", "\r", "\0")):
                self._json({"ok": False, "msg": "Invalid command."})
                return
            if not mc_server.is_running():
                self._json({"ok": False, "msg": "Server is offline."})
                return
            ok = mc_server.send_command(cmd)
            self._json({"ok": bool(ok), "msg": "Sent" if ok else "Failed"})
            return

        if self.path == "/players/refresh":
            if mc_server.is_running():
                mc_server.send_command("list")
                for p in mc_server.get_online_players():
                    mc_server.send_command(f"data get entity {p}")
                self._json({"ok": True})
            else:
                self._json({"ok": False, "msg": "Server offline"})
            return

        self.send_response(404)
        self.end_headers()
