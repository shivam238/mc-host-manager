from __future__ import annotations

import json
from typing import Any

from utils.config import (
    ensure_project_key,
    get_syncthing_folder,
    load_config,
    load_user,
    normalize_path,
    save_config,
    save_user,
)
from utils.server_layout import (
    detect_server_candidates,
    normalize_server_id,
    read_server_id_file,
    resolve_layout,
)
from utils import members_registry


def is_setup_complete(cfg: dict[str, Any]) -> bool:
    return bool(
        normalize_path(cfg.get("server_dir", ""))
        and normalize_server_id(str(cfg.get("server_id", "") or ""))
    )


def build_next_steps(cfg: dict[str, Any], syn_h: dict[str, Any]) -> list[str]:
    steps: list[str] = []
    sid = str(cfg.get("server_id", "") or "").strip()
    if sid:
        steps.append(f"Share Server ID with friends: {sid}")
    if not syn_h.get("running"):
        steps.append("Install & start Syncthing (for file sync between friends).")
    elif not syn_h.get("folder_exists"):
        steps.append("Save once more if Syncthing folder was not created.")
    elif int(syn_h.get("connected_peers", 0) or 0) <= 0:
        steps.append("Add friends: copy your invite QR / paste friend's invite.")
    else:
        steps.append("You're ready — press START when you want to host.")
    return steps


def _parse_invite(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    sid = normalize_server_id(text)
    if len(sid) >= 4 and len(sid) <= 24:
        return {"server_id": sid}
    return {"device_id": text}


def run_quick_setup(body: dict[str, Any], st_api) -> dict[str, Any]:
    from utils.flow_manager import validate_paths

    mode = str(body.get("mode", "host") or "host").strip().lower()
    user = str(body.get("user", "") or "").strip()
    if user:
        save_user(user)

    cfg = load_config(force=True)
    cfg["project_name"] = str(body.get("project_name", "") or cfg.get("project_name") or "Minecraft Server")

    server_dir = normalize_path(body.get("server_dir", "") or cfg.get("server_dir", ""))
    if not server_dir:
        hits = detect_server_candidates(limit=1)
        if not hits:
            return {
                "ok": False,
                "msg": "No Minecraft server folder found. Put server.jar in a folder and retry.",
            }
        server_dir = hits[0]["path"]
        cfg["server_jar"] = hits[0].get("jar", cfg.get("server_jar", "server.jar"))

    cfg["server_dir"] = server_dir

    invite_raw = str(body.get("friend_invite", "") or body.get("invite", "") or "").strip()
    sid_in = normalize_server_id(str(body.get("server_id", "") or ""))

    invite_payload: dict[str, Any] = {}
    if mode == "join":
        invite_payload = _parse_invite(invite_raw)
        if invite_payload.get("server_id"):
            sid_in = normalize_server_id(str(invite_payload["server_id"]))
        if not sid_in and not invite_payload.get("device_id"):
            return {
                "ok": False,
                "msg": "Paste friend's invite (from QR) or their Server ID.",
            }
        if sid_in:
            cfg["server_id"] = sid_in
    elif sid_in:
        cfg["server_id"] = sid_in

    saved = resolve_layout(save_config(cfg), create_shared=True)

    if mode == "join" and invite_payload.get("device_id"):
        folder_id = str(invite_payload.get("folder_id") or "") or get_syncthing_folder(saved)
        invite_payload["folder_id"] = folder_id
        invite_payload["server_id"] = str(saved.get("server_id", "") or sid_in)
        ok_add, add_msg = st_api.apply_invite_payload(invite_payload)
        if not ok_add:
            return {"ok": False, "msg": add_msg}
    shared = normalize_path(saved.get("shared_dir", ""))

    on_disk = read_server_id_file(shared) if shared else ""
    sid = str(saved.get("server_id", "") or "")
    if on_disk and sid and on_disk != sid:
        return {
            "ok": False,
            "msg": f"This folder already uses Server ID {on_disk}.",
        }

    ok_paths, path_msg = validate_paths(saved, True, True)
    if not ok_paths:
        return {"ok": False, "msg": path_msg}

    ensure_project_key(saved)
    syn_folder = get_syncthing_folder(saved)
    syn_msg = ""
    if shared:
        _, syn_msg = st_api.ensure_folder(syn_folder, shared)
        members_registry.touch_presence(shared, server_id=sid, hosting=False)
        try:
            inv = st_api.build_invite_payload(sid, syn_folder)
            from pathlib import Path

            Path(shared).joinpath("invite.json").write_text(
                json.dumps(inv, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    syn_h = st_api.get_health(syn_folder)
    complete = is_setup_complete(saved)
    steps = build_next_steps(saved, syn_h) if complete else ["Setup incomplete — try again."]

    return {
        "ok": True,
        "msg": "Setup complete!" if complete else "Partial setup",
        "setup_complete": complete,
        "server_id": sid,
        "server_dir": saved.get("server_dir", ""),
        "shared_dir": shared,
        "syncthing_folder": syn_folder,
        "syncthing_msg": syn_msg,
        "next_steps": steps,
    }
