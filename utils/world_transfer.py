from __future__ import annotations

import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from utils.config import normalize_path

# Folders skipped when packing world (large / regenerated)
SKIP_DIRS = {"logs", "crash-reports", "cache", ".git", "__pycache__", "libraries", "versions"}
# Top-level paths always included if present
INCLUDE_NAMES = (
    "world",
    "server.properties",
    "eula.txt",
    "bukkit.yml",
    "spigot.yml",
    "paper.yml",
    "config",
    "plugins",
    "mods",
    "ops.json",
    "whitelist.json",
    "banned-players.json",
    "banned-ips.json",
)


def _should_skip_dir(name: str) -> bool:
    return name.lower() in SKIP_DIRS


def build_world_zip(server_dir: str | Path) -> tuple[bool, str, bytes]:
    root = Path(server_dir).expanduser()
    if not root.is_dir():
        return False, "Server folder not found.", b""

    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in INCLUDE_NAMES:
            p = root / name
            if p.is_file():
                zf.write(p, p.name)
                count += 1
            elif p.is_dir():
                for fp in p.rglob("*"):
                    if not fp.is_file():
                        continue
                    rel = fp.relative_to(root)
                    if any(part in SKIP_DIRS for part in rel.parts):
                        continue
                    zf.write(fp, str(rel).replace("\\", "/"))
                    count += 1

        jar = root / "server.jar"
        if jar.is_file() and count == 0:
            return False, "No world folder yet — host ne server start karke world banani hogi.", b""

    if count == 0:
        return False, "Nothing to sync (world/ missing on host).", b""

    return True, f"Packed {count} file(s).", buf.getvalue()


def apply_world_zip(server_dir: str | Path, data: bytes) -> tuple[bool, str]:
    root = Path(server_dir).expanduser()
    if not root.is_dir():
        root.mkdir(parents=True, exist_ok=True)

    if not data:
        return False, "Empty download."

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            zf.extractall(tmp_path)

        moved = 0
        for item in tmp_path.iterdir():
            dest = root / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
            moved += 1

    return True, f"World installed into {root} ({moved} item(s))."


def pull_world_from_host(
    host_ip: str,
    server_id: str,
    server_dir: str | Path,
    *,
    port: int = 7842,
    timeout: float = 300.0,
) -> tuple[bool, str]:
    host = str(host_ip or "").strip().strip("[]")
    if not host:
        return False, "Host IP likho (e.g. 192.168.0.10)."
    sid = str(server_id or "").strip().upper()
    if not sid:
        return False, "Server ID missing."

    url = f"http://{host}:{port}/sync/world/lan?server_id={sid}"
    try:
        import requests

        r = requests.get(url, timeout=timeout)
        if r.status_code == 409:
            return False, "Host abhi server chala raha hai — pehle STOP karo, phir download."
        if r.status_code == 403:
            return False, "Server ID match nahi hua — same group join karo."
        if r.status_code != 200:
            return False, f"Download failed ({r.status_code}): {r.text[:200]}"
        ok, msg = apply_world_zip(server_dir, r.content)
        return ok, msg
    except Exception as e:
        return False, f"LAN download failed: {e}. Same WiFi? Host IP sahi?"
