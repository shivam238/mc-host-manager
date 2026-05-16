from __future__ import annotations

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
        steps.append("Syncthing chahiye — yellow banner mein 'Ab install karo' dabao (internet on).")
    elif not syn_h.get("folder_exists"):
        steps.append("Save once more if Syncthing folder was not created.")
    elif int(syn_h.get("connected_peers", 0) or 0) <= 0:
        steps.append("Add friends: copy your invite QR / paste friend's invite.")
    else:
        steps.append("You're ready — press START when you want to host.")
    return steps


def run_quick_setup(body: dict[str, Any], st_api) -> dict[str, Any]:
    from utils.group_manager import create_server_group, join_server_group

    mode = str(body.get("mode", "host") or "host").strip().lower()
    user = str(body.get("user", "") or "").strip()
    invite_raw = str(body.get("friend_invite", "") or body.get("invite", "") or "").strip()

    if mode == "join":
        if not invite_raw:
            return {"ok": False, "msg": "Friend ka Server ID ya invite code paste karo."}
        result = join_server_group(
            invite_raw=invite_raw,
            server_id=str(body.get("server_id", "") or ""),
            user=user,
            st_api=st_api,
        )
    else:
        result = create_server_group(
            user=user,
            server_dir=str(body.get("server_dir", "") or ""),
            st_api=st_api,
        )

    if not result.get("ok"):
        return result

    saved = load_config(force=True)
    syn_h = st_api.get_health(get_syncthing_folder(saved)) if st_api else {}
    complete = is_setup_complete(saved)
    steps = build_next_steps(saved, syn_h) if complete else ["Setup incomplete — try again."]

    result["setup_complete"] = complete
    result["next_steps"] = steps
    return result
