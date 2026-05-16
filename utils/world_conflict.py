from __future__ import annotations

from pathlib import Path

from utils.config import normalize_path


def _level_sig(root: Path) -> str:
    for name in ("world", "world_nether", "world_the_end"):
        ld = root / name / "level.dat"
        if ld.is_file():
            st = ld.stat()
            return f"{name}:{st.st_size}:{int(st.st_mtime)}"
    return ""


def check_world_conflict(cfg: dict[str, Any]) -> dict[str, Any]:
    server_dir = normalize_path(cfg.get("server_dir", ""))
    shared_dir = normalize_path(cfg.get("shared_dir", ""))
    out: dict = {
        "has_conflict": False,
        "message": "",
        "local_sig": "",
        "shared_sig": "",
    }
    if not server_dir or not shared_dir:
        return out

    local = Path(server_dir)
    shared_latest = Path(shared_dir) / "world_latest"
    local_sig = _level_sig(local)
    shared_sig = _level_sig(shared_latest)
    out["local_sig"] = local_sig
    out["shared_sig"] = shared_sig

    if not local_sig:
        return out
    if shared_sig and local_sig != shared_sig:
        out["has_conflict"] = True
        out["message"] = (
            "Tumhari local world shared copy se alag hai. "
            "START se pehle confirm karo — overwrite ho sakti hai."
        )
    return out
