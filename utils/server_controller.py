from pathlib import Path
from typing import Optional
import subprocess
import threading
import platform
import time
import re
from collections import deque

class ServerController:
    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.logs = deque(maxlen=500)
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.started_at: Optional[float] = None
        self.ready = False
        self.online_players: set[str] = set()
        self.player_stats: dict[str, dict] = {}

    READY_PATTERNS = (
        re.compile(r"Done \([0-9.]+s\)!", re.IGNORECASE),
        re.compile(r"For help, type \"help\"", re.IGNORECASE),
        re.compile(r"RCON running on", re.IGNORECASE),
        re.compile(r"Listening on .*25565", re.IGNORECASE),
        re.compile(r"Thread RCON Listener started", re.IGNORECASE),
    )
    JOIN_PATTERN = re.compile(r":\s+([A-Za-z0-9_]{1,32}) joined the game", re.IGNORECASE)
    LEAVE_PATTERN = re.compile(r":\s+([A-Za-z0-9_]{1,32}) left the game", re.IGNORECASE)
    LIST_PATTERN = re.compile(r"There are (\d+) of a max .* players online(?::\s*(.*))?$", re.IGNORECASE)
    NBT_PATTERN = re.compile(r":\s+([A-Za-z0-9_]{1,32}) has the following entity data: \{(.+)\}\s*$", re.IGNORECASE)

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, server_dir, jar_name, java_args):
        if self.is_running(): return False, "Already running"
        
        server_path = Path(server_dir)
        run_sh = server_path / "run.sh"
        run_bat = server_path / "run.bat"
        start_bat = server_path / "start.bat"

        cmd: list[str]
        system = platform.system()
        if system == "Windows" and run_bat.exists():
            cmd = ["cmd", "/c", "run.bat"]
        elif system == "Windows" and start_bat.exists():
            cmd = ["cmd", "/c", "start.bat"]
        elif run_sh.exists() and system != "Windows":
            cmd = ["bash", "run.sh"]
        else:
            jar_file = self._resolve_server_jar(server_path, jar_name)
            if jar_file is None:
                return False, f"Server jar not found in {server_path}. Set correct 'server_jar' in Options."
            cmd = ["java"] + java_args.split() + ["-jar", jar_file.name, "nogui"]

        try:
            with self.state_lock:
                self.ready = False
                self.started_at = time.time()
                self.online_players.clear()
                self.player_stats.clear()
            self.proc = subprocess.Popen(
                cmd, cwd=str(server_path),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            
            proc = self.proc
            def reader():
                if proc is not None and (stdout := proc.stdout) is not None:
                    for line in stdout:
                        line = line.rstrip()
                        with self.state_lock:
                            if not self.ready and any(p.search(line) for p in self.READY_PATTERNS):
                                self.ready = True
                        self._update_player_tracking(line)
                        with self.log_lock:
                            self.logs.append(line)
            
            threading.Thread(target=reader, daemon=True).start()
            return True, "Server started"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _resolve_server_jar(server_path: Path, jar_name: str) -> Optional[Path]:
        direct = server_path / str(jar_name).strip()
        if direct.exists() and direct.is_file():
            return direct

        jars = sorted(
            [p for p in server_path.glob("*.jar") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not jars:
            return None

        preferred_names = ("server.jar", "fabric-server-launch.jar", "forge", "paper", "spigot")
        for jar in jars:
            low = jar.name.lower()
            if any(name in low for name in preferred_names):
                return jar
        return jars[0]

    def stop(self):
        proc = self.proc
        if proc is None: return
        
        # Phase 2 improvement: Save world before stopping
        self.send_command("save-all")
        time.sleep(2)
        
        try:
            if (stdin := proc.stdin) is not None:
                stdin.write("stop\n")
                stdin.flush()
            proc.wait(timeout=30)
        except:
            proc.terminate()
        self.proc = None
        with self.state_lock:
            self.ready = False
            self.started_at = None
            self.online_players.clear()
            self.player_stats.clear()

    def send_command(self, cmd):
        proc = self.proc
        if proc is not None and self.is_running():
            try:
                if (stdin := proc.stdin) is not None:
                    stdin.write(cmd + "\n")
                    stdin.flush()
                    return True
            except: return False
        return False

    def is_ready(self):
        if not self.is_running():
            return False
        with self.state_lock:
            return self.ready

    def get_pid(self):
        if self.proc is None:
            return None
        return self.proc.pid

    def get_uptime_seconds(self):
        with self.state_lock:
            started_at = self.started_at
        if not self.is_running() or started_at is None:
            return 0
        return int(max(0, time.time() - started_at))

    def get_ram_mb(self):
        """Best-effort RSS lookup. Returns None when unavailable."""
        pid = self.get_pid()
        if pid is None:
            return None
        # Linux /proc path
        try:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        return round(kb / 1024, 1)
        except Exception:
            pass
        return None

    def get_online_players(self):
        with self.state_lock:
            return sorted(self.online_players)

    def get_player_stats(self):
        info = {}
        with self.state_lock:
            players = sorted(self.online_players)
            stats_copy = {k: dict(v) for k, v in self.player_stats.items()}
        for p in players:
            entry = dict(stats_copy.get(p, {}))
            info[p] = {
                "gamemode": entry.get("gamemode", "unknown"),
                "health": entry.get("health"),
                "hunger": entry.get("hunger"),
                "updated_at": entry.get("updated_at"),
            }
        return info

    def _update_player_tracking(self, line: str):
        m_join = self.JOIN_PATTERN.search(line)
        if m_join:
            with self.state_lock:
                self.online_players.add(m_join.group(1))
            return

        m_leave = self.LEAVE_PATTERN.search(line)
        if m_leave:
            with self.state_lock:
                self.online_players.discard(m_leave.group(1))
                self.player_stats.pop(m_leave.group(1), None)
            return

        m_list = self.LIST_PATTERN.search(line)
        if m_list:
            count = int(m_list.group(1))
            names_blob = (m_list.group(2) or "").strip()
            if count == 0:
                with self.state_lock:
                    self.online_players.clear()
                    self.player_stats.clear()
                return
            if names_blob:
                names = [n.strip() for n in names_blob.split(",") if n.strip()]
                if names:
                    with self.state_lock:
                        self.online_players = set(names)
                        # prune stale entries
                        for k in list(self.player_stats.keys()):
                            if k not in self.online_players:
                                self.player_stats.pop(k, None)
            return

        m_nbt = self.NBT_PATTERN.search(line)
        if m_nbt:
            player = m_nbt.group(1)
            body = m_nbt.group(2)
            hm = re.search(r"Health:([0-9]+(?:\.[0-9]+)?)f", body)
            fm = re.search(r"foodLevel:(\d+)", body)
            gm = re.search(r"playerGameType:(\d+)", body)
            with self.state_lock:
                self.online_players.add(player)
                e = self.player_stats.setdefault(player, {})
                if hm:
                    try:
                        e["health"] = round(float(hm.group(1)), 1)
                    except Exception:
                        pass
                if fm:
                    try:
                        e["hunger"] = int(fm.group(1))
                    except Exception:
                        pass
                if gm:
                    gm_map = {"0": "survival", "1": "creative", "2": "adventure", "3": "spectator"}
                    e["gamemode"] = gm_map.get(gm.group(1), "unknown")
                e["updated_at"] = time.time()

    def get_logs(self, n=50):
        with self.log_lock:
            return list(self.logs)[-n:]

    def clear_logs(self):
        with self.log_lock:
            self.logs.clear()

    def prepare_for_copy(self):
        """Recommended save-off flow for world backup"""
        if self.is_running():
            self.send_command("save-off")
            self.send_command("save-all")
            time.sleep(3)
            return True
        return False

    def resume_saves(self):
        if self.is_running():
            self.send_command("save-on")
