import shutil
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, List, Dict, Any

WORLD_DIRS = ["world", "world_nether", "world_the_end"]

def create_timestamped_backup(
    server_dir: str | Path, 
    backup_dir: str | Path, 
    keep: int = 5, 
    progress_cb: Optional[Callable[[int, str], None]] = None
) -> Optional[Path]:
    server_path = Path(server_dir)
    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)
    
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    zip_name = f"backup_{ts}.zip"
    target_zip = backup_path / zip_name
    
    files_to_zip: List[Path] = []
    for d in WORLD_DIRS:
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

def copy_world(
    src_dir: str | Path, 
    dst_dir: str | Path, 
    progress_cb: Optional[Callable[[int, str], None]] = None
) -> None:
    src_path = Path(src_dir)
    dst_path = Path(dst_dir)
    
    for i, d in enumerate(WORLD_DIRS):
        s = src_path / d
        t = dst_path / d
        if s.exists():
            if t.exists(): 
                shutil.rmtree(t)
            shutil.copytree(s, t)
        cb = progress_cb
        if cb is not None:
            cb(int((i + 1) / len(WORLD_DIRS) * 100), f"Syncing {d}...")

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
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            world_members = [m for m in members if any(m.startswith(w + "/") for w in WORLD_DIRS)]
            if not world_members:
                return False
            for w in WORLD_DIRS:
                target = server_path / w
                if target.exists():
                    shutil.rmtree(target)
            cb(40, "Extracting backup...")
            zf.extractall(server_path, members=world_members)
        cb(100, "Backup restored.")
        return True
    except Exception:
        return False
