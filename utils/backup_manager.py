import shutil
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, List, Dict, Any

def get_world_dirs(server_dir: Path) -> List[str]:
    """Dynamically detect world directory names from server.properties."""
    level_name = "world"
    prop_path = server_dir / "server.properties"
    if prop_path.exists():
        try:
            for line in prop_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() == "level-name":
                        level_name = v.strip()
                        break
        except Exception:
            pass
            
    dirs = [level_name]
    # Handle Spigot/Paper style nether/end directories
    for suffix in ("_nether", "_the_end"):
        dirs.append(level_name + suffix)
    # Standard fallback support
    for fallback in ("world", "world_nether", "world_the_end"):
        if fallback not in dirs:
            dirs.append(fallback)
            
    return dirs

def create_timestamped_backup(
    server_dir: str | Path, 
    backup_dir: str | Path, 
    keep: int = 5, 
    progress_cb: Optional[Callable[[int, str], None]] = None
) -> Optional[Path]:
    server_path = Path(server_dir)
    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)
    
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    zip_name = f"backup_{ts}.zip"
    target_zip = backup_path / zip_name
    suffix = 1
    while target_zip.exists():
        target_zip = backup_path / f"backup_{ts}_{suffix}.zip"
        suffix += 1
    
    files_to_zip: List[Path] = []
    world_dirs = get_world_dirs(server_path)
    for d in world_dirs:
        src = server_path / d
        if src.exists():
            files_to_zip.extend(list(src.rglob("*")))
    
    if not files_to_zip: 
        return None
    
    total = len(files_to_zip)
    with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, f in enumerate(files_to_zip):
            if f.is_file():
                try:
                    zf.write(f, f.relative_to(server_path))
                    if progress_cb is not None and (i + 1) % 10 == 0:
                        progress_cb(int((i + 1) / total * 100), f"Backing up: {i + 1}/{total} files")
                except Exception: 
                    continue
    
    _rotate_backups(backup_path, keep)
    return target_zip


def _rotate_backups(backup_dir: Path, keep: int) -> None:
    """Rotates backups by deleting old ones. Uses slicing for better type safety."""
    all_zips = list(backup_dir.glob("backup_*.zip"))
    zips = sorted(all_zips, key=lambda x: x.stat().st_mtime, reverse=True)
    
    if len(zips) > keep:
        # Avoid slicing for strict linters
        for i in range(len(zips)):
            if i >= keep:
                backup_file = zips[i]
                try: 
                    backup_file.unlink()
                except Exception as e:
                    print(f"Backup: Failed to delete old backup {backup_file.name}: {e}")

def list_backups(backup_dir: str | Path) -> List[Dict[str, Any]]:
    p = Path(backup_dir)
    if not p.exists(): 
        return []
    zips = sorted(p.glob("backup_*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)
    results = []
    for z in zips:
        size_mb = round(z.stat().st_size / (1024 * 1024), 1)
        results.append({
            "name": z.name,
            "size_mb": size_mb,
            "time": datetime.fromtimestamp(z.stat().st_mtime).strftime("%d %b %Y, %I:%M %p")
        })
    return results

def safe_rmtree(path: Path) -> None:
    import os
    import stat
    import time
    
    def handle_remove_readonly(func, path_str, exc_info):
        try:
            os.chmod(path_str, stat.S_IWRITE)
            func(path_str)
        except Exception:
            pass

    for attempt in range(5):
        try:
            if path.exists():
                shutil.rmtree(str(path), onerror=handle_remove_readonly)
            return
        except Exception:
            time.sleep(0.15)
    # Final try to let standard shutil do it or raise exception if completely locked
    if path.exists():
        shutil.rmtree(str(path))

def copy_world(
    src_dir: str | Path, 
    dst_dir: str | Path, 
    progress_cb: Optional[Callable[[int, str], None]] = None
) -> None:
    src_path = Path(src_dir)
    dst_path = Path(dst_dir)
    
    world_dirs = get_world_dirs(src_path)
    for i, d in enumerate(world_dirs):
        s = src_path / d
        t = dst_path / d
        if s.exists():
            if t.exists(): 
                safe_rmtree(t)
            shutil.copytree(s, t)
        cb = progress_cb
        if cb is not None:
            cb(int((i + 1) / len(world_dirs) * 100), f"Syncing {d}...")

def restore_backup(
    backup_zip: str | Path,
    server_dir: str | Path,
    progress_cb: Optional[Callable[[int, str], None]] = None
) -> bool:
    zip_path = Path(backup_zip)
    server_path = Path(server_dir)
    if not zip_path.exists() or not zip_path.is_file():
        return False
    try:
        cb = progress_cb if progress_cb is not None else (lambda *_: None)
        cb(10, "Reading backup archive...")
        world_dirs = get_world_dirs(server_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            world_members = [m for m in members if any(m.startswith(w + "/") for w in world_dirs)]
            if not world_members:
                return False
            for m in world_members:
                dest = (server_path / m).resolve()
                if dest != server_path and server_path not in dest.parents:
                    return False
            for w in world_dirs:
                target = server_path / w
                if target.exists():
                    safe_rmtree(target)
            cb(40, "Extracting backup...")
            zf.extractall(server_path, members=world_members)
        cb(100, "Backup restored.")
        return True
    except Exception:
        return False
