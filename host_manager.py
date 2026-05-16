"""
MC Host Manager - Lightweight Core Build
Entry Point
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
import time
from http.server import HTTPServer
from socketserver import ThreadingMixIn

# Configure console encoding
try:
    reconfig_out = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfig_out):
        reconfig_out(encoding="utf-8", errors="replace")
    reconfig_err = getattr(sys.stderr, "reconfigure", None)
    if callable(reconfig_err):
        reconfig_err(encoding="utf-8", errors="replace")
except Exception:
    pass

from utils.config import load_config, ensure_project_key, load_user, normalize_path, get_syncthing_folder
from utils import members_registry
from utils.flow_manager import st_api
from utils.app_state import host_lock, host_state, is_task_running
from utils.flow_manager import mc_server, finalize_stop_flow
from api_handler import APIHandler
from utils.dependency_manager import ensure_dependencies_background

# ---------------------------------------------------------------------------
# Monitors
# ---------------------------------------------------------------------------

last_list_poll = 0.0

def monitor_ready_and_recover() -> None:
    global last_list_poll
    from utils.flow_manager import run_task
    while True:
        time.sleep(1.4)
        with host_lock:
            active = bool(host_state["active"])
            cfg = dict(host_state.get("last_cfg") or {})
        if not active:
            continue

        if mc_server.is_running():
            if mc_server.is_ready() or mc_server.get_uptime_seconds() >= 12:
                with host_lock:
                    host_state["ready"] = True
            if time.time() - last_list_poll >= 16:
                mc_server.send_command("list")
                last_list_poll = time.time()
            continue

        if is_task_running() or not cfg.get("server_dir") or not cfg.get("shared_dir"):
            continue

        def crash_task(cb):
            finalize_stop_flow(cfg, cb, reason="unexpected")

        run_task("recovering", crash_task)

def monitor_members_presence() -> None:
    from utils import matchmaker
    while True:
        time.sleep(12.0)
        try:
            cfg = load_config(force=True)
            shared = normalize_path(cfg.get("shared_dir", ""))
            if not shared:
                continue
            hosting = False
            with host_lock:
                hosting = bool(host_state.get("active")) and mc_server.is_running()
            sid = str(cfg.get("server_id", "") or "")
            members_registry.touch_presence(
                shared,
                server_id=sid,
                hosting=hosting,
            )
            
            # Global Presence (Firebase)
            fb_url = cfg.get("firebase_url", "")
            if fb_url and sid:
                from utils.config import get_node_id, load_user, get_local_ip
                import socket
                matchmaker.update_presence(
                    fb_url, sid, get_node_id(),
                    {
                        "user": load_user(),
                        "hostname": socket.gethostname(),
                        "ip": get_local_ip(),
                        "hosting": hosting
                    }
                )

            # Matchmaking: Auto-accept peers if we are the host
            if hosting and fb_url and sid:
                ok, _, peers = matchmaker.fetch_peer_invites(fb_url, sid)
                if ok and peers:
                    for peer_payload in peers:
                        st_api.apply_invite_payload(peer_payload)
        except Exception:
            pass


def monitor_lock_heartbeat() -> None:
    from utils import lock_manager
    from utils.config import get_node_id, ensure_project_key
    while True:
        time.sleep(8.0)
        with host_lock:
            active = bool(host_state["active"])
            cfg = dict(host_state.get("last_cfg") or {})
        if not active:
            continue
        if not mc_server.is_running() and not is_task_running():
            continue
        shared = normalize_path(cfg.get("shared_dir", ""))
        if not shared:
            continue
        lock_manager.refresh_lock(
            shared,
            load_user(),
            ensure_project_key(cfg),
            owner_node_id=get_node_id(),
        )

        # Firebase Lock Heartbeat
        fb_url = cfg.get("firebase_url", "")
        sid = cfg.get("server_id", "")
        if fb_url and sid and (mc_server.is_running() or is_task_running()):
            from utils import matchmaker
            import socket
            ok_l, msg_l = matchmaker.acquire_lock(
                fb_url, sid,
                {
                    "node_id": get_node_id(),
                    "user": load_user(),
                    "hostname": socket.gethostname(),
                    "ip": get_local_ip()
                }
            )
            if not ok_l:
                # Someone else stole the lock? (should not happen if we are running)
                # If we lose the lock, we should probably warn or stop
                pass

# ---------------------------------------------------------------------------
# Server Main
# ---------------------------------------------------------------------------

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = (os.name != "nt")

shutdown_lock = threading.Lock()
shutdown_started = False

def safe_shutdown(reason: str = "exit") -> None:
    global shutdown_started
    with shutdown_lock:
        if shutdown_started:
            return
        shutdown_started = True

    # wait for in-flight task briefly
    end = time.time() + 12
    while is_task_running() and time.time() < end:
        time.sleep(0.2)

    with host_lock:
        active = bool(host_state["active"])
        cfg = dict(host_state.get("last_cfg") or {})

    if not cfg:
        cfg = load_config(force=True)

    if active or mc_server.is_running():
        try:
            finalize_stop_flow(cfg, cb=lambda *_: None, reason=reason)
        except Exception:
            try:
                mc_server.stop()
            except Exception:
                pass

def _signal(signum, _frame):
    safe_shutdown(reason=f"signal-{signum}")
    raise SystemExit(0)

if __name__ == "__main__":
    atexit.register(safe_shutdown)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal)

    ensure_dependencies_background()

    cfg = load_config(force=True)
    if not cfg.get("project_key"):
        ensure_project_key(cfg)

    shared = normalize_path(cfg.get("shared_dir", ""))
    if shared and cfg.get("server_id"):
        try:
            st_api.ensure_folder(get_syncthing_folder(cfg), shared)
            members_registry.touch_presence(
                shared,
                server_id=str(cfg.get("server_id", "")),
                hosting=False,
            )
        except Exception:
            pass

    threading.Thread(target=monitor_ready_and_recover, daemon=True).start()
    threading.Thread(target=monitor_lock_heartbeat, daemon=True).start()
    threading.Thread(target=monitor_members_presence, daemon=True).start()

    PORT = 7842
    print(f"[INFO] MC Host Manager (modular) running on http://localhost:{PORT}")

    server = None
    try:
        server = ThreadedHTTPServer(("0.0.0.0", PORT), APIHandler)
        server.serve_forever()
    except OSError as e:
        print(f"[ERROR] Server error: {e}")
    except KeyboardInterrupt:
        print("[INFO] Shutting down...")
    finally:
        try:
            safe_shutdown(reason="app-exit")
        finally:
            if server is not None:
                try:
                    server.server_close()
                except Exception:
                    pass
