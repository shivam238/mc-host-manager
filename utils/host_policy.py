from __future__ import annotations

from typing import Any

from utils.config import get_syncthing_folder, load_user, normalize_path
from utils import lock_manager


def _sync_isolated(syn_h: dict[str, Any]) -> bool:
    if not syn_h.get("running"):
        return True
    if not syn_h.get("folder_exists", False):
        return True
    if int(syn_h.get("connected_peers", 0) or 0) <= 0:
        return True
    return False


def evaluate_start_gate(
    cfg: dict[str, Any],
    *,
    running: bool,
    task_running: bool,
    lock_info: dict[str, Any] | None,
    syn_h: dict[str, Any],
    remote_lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user = load_user()
    shared = normalize_path(cfg.get("shared_dir", ""))
    isolated = _sync_isolated(syn_h)
    strict = bool(cfg.get("strict_sync_gate", False))

    out: dict[str, Any] = {
        "can_start": True,
        "start_block_reason": "",
        "sync_isolated": isolated,
        "remote_host": "",
        "lock_expired": bool(lock_info.get("expired")) if lock_info else True,
        "strict_sync_gate": strict,
    }

    if task_running:
        out["can_start"] = False
        out["start_block_reason"] = "Another operation is running."
        return out

    if running:
        out["can_start"] = False
        out["start_block_reason"] = "Server is already running on this PC."
        return out

    if not shared:
        out["can_start"] = False
        out["start_block_reason"] = "Pick a Server Folder (or use Auto-detect) and Save."
        return out

    if not str(cfg.get("server_id", "") or "").strip():
        out["can_start"] = False
        out["start_block_reason"] = "Set Server ID and Save (share this ID with friends)."
        return out

    if lock_info and not lock_info.get("expired"):
        remote = str(lock_info.get("host", "") or "").strip()
        if remote and remote != user:
            out["can_start"] = False
            out["remote_host"] = remote
            ui = str(lock_info.get("ui_url", "") or "").strip()
            hint = f" Open {ui}" if ui else ""
            out["start_block_reason"] = f"{remote} is hosting right now.{hint}"
            return out

    if remote_lock:
        rlock = remote_lock.get("lock") if isinstance(remote_lock.get("lock"), dict) else None
        remote_user = str(
            (rlock or {}).get("host", "") or remote_lock.get("user", "") or ""
        ).strip()
        peer_ip = str(remote_lock.get("peer_ip", "") or "").strip()
        peer_hosting = bool(remote_lock.get("hosting")) or bool(remote_lock.get("running"))
        if rlock and rlock.get("expired"):
            peer_hosting = bool(remote_lock.get("running"))
        if peer_hosting and remote_user and remote_user.lower() != user.lower():
            out["can_start"] = False
            out["remote_host"] = remote_user
            hint = f" ({peer_ip})" if peer_ip else ""
            out["start_block_reason"] = (
                f"{remote_user} is hosting on another PC{hint}. "
                "Use “Yahan host karo” after they STOP, or ask them to stop."
            )
            return out

    if isolated and strict:
        out["can_start"] = False
        out["sync_isolated"] = True
        if not syn_h.get("running"):
            out["start_block_reason"] = (
                "Strict mode: Syncthing chalu karo taaki world / lock sync ho sake."
            )
        elif not syn_h.get("folder_exists", False):
            out["start_block_reason"] = "Strict mode: Save settings to create Syncthing folder."
        else:
            out["start_block_reason"] = (
                "Strict mode: Pehle friend se Syncthing peer connect karo (Invite / Join)."
            )
        return out

    if isolated:
        out["can_start"] = True
        out["sync_isolated"] = True
        if not syn_h.get("running"):
            out["start_block_reason"] = (
                "Tip: Start Syncthing so friends share the same world files."
            )
        elif not syn_h.get("folder_exists", False):
            out["start_block_reason"] = (
                "Tip: Syncthing folder will be created when you save or join."
            )
        else:
            out["start_block_reason"] = (
                "Tip: No Syncthing peers yet — use Invite Friend so others can sync files."
            )
        return out

    return out
