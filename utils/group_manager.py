from __future__ import annotations

import json
from pathlib import Path
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
    generate_server_id,
    normalize_server_id,
    read_server_id_file,
    resolve_layout,
    write_server_id_file,
)
from utils import members_registry
from utils import matchmaker


def format_invite_code(server_id: str, device_id: str = "", folder_id: str = "") -> str:
    sid = normalize_server_id(server_id)
    if not sid:
        return ""
    if device_id:
        return f"MCHOST:{sid}:{device_id.strip().upper()}"
    return f"MCHOST:{sid}"


def parse_invite_input(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("MCHOST:"):
        parts = text.split(":")
        out: dict[str, Any] = {}
        if len(parts) >= 2:
            out["server_id"] = normalize_server_id(parts[1])
        if len(parts) >= 3:
            out["device_id"] = ":".join(parts[2:]).strip()
        return out
    if text.startswith("{"):
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    sid = normalize_server_id(text)
    if sid and len(sid) >= 4:
        return {"server_id": sid}
    return {"device_id": text}


def create_server_group(
    *,
    user: str = "",
    server_dir: str = "",
    project_name: str = "Minecraft Server",
    st_api=None,
) -> dict[str, Any]:
    if user:
        save_user(user)

    cfg = load_config(force=True)
    cfg["project_name"] = project_name or cfg.get("project_name") or "Minecraft Server"
    cfg["server_id"] = generate_server_id()

    if server_dir:
        cfg["server_dir"] = normalize_path(server_dir)
    elif not normalize_path(cfg.get("server_dir", "")):
        hits = detect_server_candidates(limit=1)
        if hits:
            cfg["server_dir"] = hits[0]["path"]
            cfg["server_jar"] = hits[0].get("jar", cfg.get("server_jar", "server.jar"))

    res = _finalize_group(cfg, role="host", st_api=st_api)
    
    fb_url = cfg.get("firebase_url", "")
    if fb_url and res.get("ok") and st_api:
        payload = st_api.build_invite_payload(cfg["server_id"], res.get("syncthing_folder", ""))
        ok, msg = matchmaker.upload_host_invite(fb_url, cfg["server_id"], payload)
        if not ok:
            res["msg"] += f" (Matchmaker warning: {msg})"
            
    return res


def join_server_group(
    *,
    invite_raw: str = "",
    server_id: str = "",
    user: str = "",
    server_dir: str = "",
    st_api=None,
) -> dict[str, Any]:
    if user:
        save_user(user)

    payload = parse_invite_input(invite_raw)
    sid = normalize_server_id(server_id) or normalize_server_id(str(payload.get("server_id", "")))
    if not sid:
        return {"ok": False, "msg": "Enter a valid Server ID or paste friend's invite code."}

    # ── Step 1: Prepare config ──────────────────────────────────
    cfg = load_config(force=True)
    cfg["server_id"] = sid
    cfg["_overwrite_server_id"] = True

    if server_dir:
        cfg["server_dir"] = normalize_path(server_dir)
    elif not normalize_path(cfg.get("server_dir", "")):
        hits = detect_server_candidates(limit=1)
        if hits:
            cfg["server_dir"] = hits[0]["path"]
            cfg["server_jar"] = hits[0].get("jar", cfg.get("server_jar", "server.jar"))

    # ── Step 2: Finalize group (saves config, creates shared_dir) ──
    result = _finalize_group(cfg, role="join", st_api=st_api)
    if not result.get("ok"):
        return result

    # ── Step 3: Ensure shared_dir and Syncthing folder exist ────
    saved_cfg = load_config(force=True)
    shared = normalize_path(saved_cfg.get("shared_dir", ""))
    syn_folder = result.get("syncthing_folder") or get_syncthing_folder(saved_cfg)
    _debug = []

    # Force-create shared_dir if still missing
    if not shared:
        from pathlib import Path as _P
        shared = str(_P.home() / "mc-host-shared")
        saved_cfg["shared_dir"] = shared
        save_config(saved_cfg)
        saved_cfg = load_config(force=True)
        _debug.append(f"created shared: {shared}")
    else:
        _debug.append(f"shared exists: {shared}")

    # Create directory on disk
    from pathlib import Path as _P2
    _P2(shared).mkdir(parents=True, exist_ok=True)
    _debug.append(f"dir on disk: {_P2(shared).is_dir()}")

    # Force-create Syncthing folder
    if st_api is not None:
        ef_ok, ef_msg = st_api.ensure_folder(syn_folder, shared)
        _debug.append(f"ensure_folder({syn_folder}): ok={ef_ok}, msg={ef_msg}")
        # Double-check with health
        h = st_api.get_health(syn_folder)
        _debug.append(f"health: folder_exists={h.get('folder_exists')}, api_key={h.get('api_key_ok')}, running={h.get('running')}")
    
    result["_debug"] = _debug
    print(f"[JOIN DEBUG] {_debug}")

    if st_api is None:
        return result

    # ── Step 4: Connect to host (via invite payload or Firebase) ──
    device_id = str(payload.get("device_id", "") or "").strip()
    folder = str(payload.get("folder_id", "") or "").strip() or syn_folder

    if device_id:
        # Direct invite (copy-paste or QR)
        ok_add, add_msg = st_api.apply_invite_payload(
            {
                "t": "mc-host",
                "server_id": sid,
                "device_id": device_id,
                "folder_id": folder,
                "device_name": str(payload.get("device_name", "") or "Friend"),
            }
        )
        if ok_add:
            result["msg"] = f"Joined group {sid}. File sync peer added."
        else:
            result["msg"] = f"Joined group {sid}. Syncthing: {add_msg}"
    else:
        # Firebase auto-connect
        fb_url = saved_cfg.get("firebase_url", "")
        if fb_url:
            ok_f, msg_f, data = matchmaker.fetch_host_invite(fb_url, sid)
            if ok_f and data:
                ok_add, add_msg = st_api.apply_invite_payload(data)
                if ok_add:
                    result["msg"] = f"Joined group {sid}. Auto-connected to Host via Firebase."
                    # Push our invite back so host can auto-accept us
                    my_payload = st_api.build_invite_payload(sid, syn_folder)
                    my_did = my_payload.get("device_id", "")
                    if my_did:
                        matchmaker.upload_peer_invite(fb_url, sid, my_did, my_payload)
                else:
                    result["msg"] = f"Joined group {sid}. Firebase ok, but sync: {add_msg}"
            else:
                result["msg"] = (
                    f"Joined server group {sid}. "
                    f"Auto-connect failed ({msg_f}). World files sync when friend sends full invite."
                )
        else:
            result["msg"] = (
                f"Joined server group {sid}. "
                "World files sync when friend sends full invite (Copy invite) or QR."
            )

    return result


def _finalize_group(cfg: dict[str, Any], *, role: str, st_api=None) -> dict[str, Any]:
    from utils.flow_manager import validate_paths

    saved = resolve_layout(save_config(cfg), create_shared=True)
    shared = normalize_path(saved.get("shared_dir", ""))
    sid = str(saved.get("server_id", "") or "")

    if shared and sid:
        write_server_id_file(shared, sid)

    ok_paths, path_msg = validate_paths(saved, True, True)
    if not ok_paths:
        return {"ok": False, "msg": path_msg}

    ensure_project_key(saved)
    syn_folder = get_syncthing_folder(saved)
    syn_msg = ""
    if shared and st_api is not None:
        _, syn_msg = st_api.ensure_folder(syn_folder, shared)

    if shared:
        members_registry.touch_presence(shared, server_id=sid, hosting=False)
        try:
            meta = {
                "server_id": sid,
                "role": role,
                "project_key": str(saved.get("project_key", "") or ""),
            }
            Path(shared).joinpath("group.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    invite_code = format_invite_code(sid)

    return {
        "ok": True,
        "msg": f"{'Created' if role == 'host' else 'Joined'} server group {sid}.",
        "setup_complete": bool(sid and normalize_path(saved.get("server_dir", ""))),
        "server_id": sid,
        "invite_code": invite_code,
        "server_dir": saved.get("server_dir", ""),
        "shared_dir": shared,
        "syncthing_folder": syn_folder,
        "syncthing_msg": syn_msg,
        "project_key": saved.get("project_key", ""),
    }
