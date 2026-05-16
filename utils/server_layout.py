from __future__ import annotations

import re
import secrets
import socket
from pathlib import Path
from typing import Any

from utils.config import normalize_path

SHARED_DIR_NAME = ".mc-host-shared"
SERVER_ID_FILE = "server.id"
JAR_CANDIDATES = ("server.jar", "minecraft_server.jar", "paper.jar", "spigot.jar", "forge.jar")


def syncthing_folder_id(server_id: str) -> str:
    sid = re.sub(r"[^a-zA-Z0-9-]", "", str(server_id or "").strip().lower())
    return f"mc-{sid[:32] if sid else 'default'}"


def default_shared_for_server(server_dir: str | Path) -> str:
    return str(Path(server_dir).expanduser() / SHARED_DIR_NAME)


def normalize_server_id(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9-]", "", str(raw or "").strip().upper())
    return s[:24]


def generate_server_id() -> str:
    return secrets.token_hex(4).upper()


def is_minecraft_server_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    markers = ("eula.txt", "server.properties", "server.jar")
    if any((path / m).is_file() for m in markers):
        return True
    for name in JAR_CANDIDATES:
        if (path / name).is_file():
            return True
    if (path / "start.sh").is_file() or (path / "start.bat").is_file():
        return True
    return False


def pick_jar_name(path: Path) -> str:
    for name in JAR_CANDIDATES:
        if (path / name).is_file():
            return name
    if (path / "start.sh").is_file():
        return "start.sh"
    if (path / "start.bat").is_file():
        return "start.bat"
    return "server.jar"


def detect_server_candidates(limit: int = 12) -> list[dict[str, Any]]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            r = p.expanduser().resolve()
        except Exception:
            return
        key = str(r).lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(r)

    add(Path.cwd())
    add(Path.cwd().parent)
    home = Path.home()
    for rel in (
        "minecraft-server",
        "mc-server",
        "MinecraftServer",
        "Projects/MC Hosting Manager/Testing/Server",
        "Projects/minecraft-server",
    ):
        add(home / rel)

    if getattr(__import__("utils.config", fromlist=["RUNTIME_DIR"]), "RUNTIME_DIR", None):
        from utils.config import RUNTIME_DIR

        add(RUNTIME_DIR.parent / "Testing" / "Server")
        add(RUNTIME_DIR / ".." / "Testing" / "Server")

    found: list[dict[str, Any]] = []
    for root in roots:
        checks = [root]
        if root.is_dir():
            try:
                checks.extend([p for p in root.iterdir() if p.is_dir()][:8])
            except Exception:
                pass
        for p in checks:
            if not is_minecraft_server_dir(p):
                continue
            found.append(
                {
                    "path": str(p),
                    "jar": pick_jar_name(p),
                    "label": p.name or str(p),
                }
            )
            if len(found) >= limit:
                return found
    return found


def read_server_id_file(shared_dir: str | Path) -> str:
    p = Path(shared_dir).expanduser() / SERVER_ID_FILE
    if not p.is_file():
        return ""
    try:
        return normalize_server_id(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""


def write_server_id_file(shared_dir: str | Path, server_id: str) -> None:
    p = Path(shared_dir).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    (p / SERVER_ID_FILE).write_text(normalize_server_id(server_id) + "\n", encoding="utf-8")


def resolve_layout(cfg: dict[str, Any], *, create_shared: bool = True) -> dict[str, Any]:
    """Fill server_dir (detect), shared_dir (auto), server_id (file/sync)."""
    out = dict(cfg)
    server_dir = normalize_path(out.get("server_dir", ""))

    # Fully automatic server_dir resolution
    from pathlib import Path as _P
    if not server_dir or not _P(server_dir).is_dir():
        hits = detect_server_candidates(limit=1)
        if hits:
            server_dir = hits[0]["path"]
            if not out.get("server_jar"):
                out["server_jar"] = hits[0].get("jar", "server.jar")
        else:
            # Fallback: create a default server folder in home
            server_dir = str(_P.home() / "mc-host-server")
            _P(server_dir).mkdir(parents=True, exist_ok=True)
            if not out.get("server_jar"):
                out["server_jar"] = "server.jar"

    out["server_dir"] = server_dir
    shared_dir = normalize_path(out.get("shared_dir", ""))

    if server_dir and (not shared_dir or shared_dir == server_dir):
        shared_dir = default_shared_for_server(server_dir)
    elif not server_dir and not shared_dir:
        # No server dir found (e.g. joiner PC) — create default shared in home
        from pathlib import Path as _P
        shared_dir = str(_P.home() / "mc-host-shared")

    out["shared_dir"] = shared_dir

    if create_shared and shared_dir:
        from utils.config import ensure_shared_layout

        ensure_shared_layout(shared_dir)

    sid = normalize_server_id(str(out.get("server_id", "") or ""))
    overwrite_sid = bool(out.pop("_overwrite_server_id", False))
    file_sid = read_server_id_file(shared_dir) if shared_dir else ""

    if overwrite_sid and sid and shared_dir:
        write_server_id_file(shared_dir, sid)
        file_sid = sid

    if file_sid and sid and file_sid != sid:
        out["server_id_mismatch"] = True
        out["server_id_file"] = file_sid
    elif file_sid and not sid:
        sid = file_sid
    elif sid and shared_dir and not out.get("server_id_mismatch"):
        write_server_id_file(shared_dir, sid)
    elif not sid:
        sid = generate_server_id()
        if shared_dir:
            write_server_id_file(shared_dir, sid)

    out["server_id"] = sid
    out["syncthing_folder"] = syncthing_folder_id(sid)
    return out
