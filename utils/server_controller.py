from pathlib import Path
from typing import Optional
import os
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
    FATAL_START_PATTERNS = (
        re.compile(r"not recognized as an internal or external command", re.IGNORECASE),
        re.compile(r"unable to access jarfile", re.IGNORECASE),
        re.compile(r"unsupportedclassversionerror", re.IGNORECASE),
        re.compile(r"invalid maximum heap size", re.IGNORECASE),
        re.compile(r"could not reserve enough space", re.IGNORECASE),
        re.compile(r"could not find or load main class", re.IGNORECASE),
        re.compile(r"failed to load datapacks, can't proceed", re.IGNORECASE),
    )

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, server_dir, jar_name, java_args, shared_dir=None):
        if self.is_running(): return False, "Already running"
        
        server_path = Path(server_dir)
        try:
            server_path.mkdir(parents=True, exist_ok=True)
            (server_path / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        except Exception:
            pass

        run_sh = server_path / "run.sh"
        run_bat = server_path / "run.bat"
        start_bat = server_path / "start.bat"

        cmd: list[str]
        system = platform.system()
        env = self._build_process_env()
        if system == "Windows" and run_bat.exists():
            cmd = ["cmd", "/d", "/s", "/c", "call", "run.bat"]
        elif system == "Windows" and start_bat.exists():
            cmd = ["cmd", "/d", "/s", "/c", "call", "start.bat"]
        elif run_sh.exists() and system != "Windows":
            cmd = ["bash", "run.sh"]
        else:
            jar_file = self._resolve_server_jar(server_path, jar_name)
            if jar_file is None:
                return False, f"Server jar not found in {server_path}. Set correct 'server_jar' in Options."
            java_bin = self._resolve_java_binary()
            cmd = [java_bin] + java_args.split() + ["-jar", jar_file.name, "nogui"]

        try:
            with self.state_lock:
                self.ready = False
                self.started_at = time.time()
                self.online_players.clear()
                self.player_stats.clear()
            with self.log_lock:
                self.logs.clear()
            
            # Windows optimization: use CREATE_NO_WINDOW to reduce UI flickering
            creationflags = 0
            if system == "Windows":
                creationflags = 0x08000000  # CREATE_NO_WINDOW
            
            self.proc = subprocess.Popen(
                cmd, cwd=str(server_path),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", bufsize=1, env=env, creationflags=creationflags
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

            failed, fail_msg = self._check_immediate_exit(proc)
            if failed:
                return False, fail_msg

            if shared_dir:
                def log_sync_worker():
                    shared_path = Path(shared_dir)
                    console_file = shared_path / ".remote_console.json"
                    is_windows = platform.system() == "Windows"
                    sync_interval = 5.0 if is_windows else 2.0  # Longer interval on Windows to reduce file lock contention
                    last_lines = None
                    while self.is_running():
                        try:
                            lines = self.get_logs(100)
                            # Skip write if no new lines to reduce file I/O
                            if lines == last_lines:
                                time.sleep(sync_interval)
                                continue
                            last_lines = lines
                            import json
                            # Retry loop to handle Windows file lock contention with Syncthing
                            max_retries = 3 if is_windows else 2
                            for attempt in range(max_retries):
                                try:
                                    with open(str(console_file), "w", encoding="utf-8", errors="replace") as f:
                                        # Use compact JSON (no indent) to reduce file size and write time
                                        json.dump({"logs": lines}, f, ensure_ascii=False, separators=(',', ':'))
                                    break
                                except (PermissionError, OSError):
                                    if attempt < max_retries - 1:
                                        time.sleep(0.2 if is_windows else 0.1)
                        except Exception:
                            pass
                        time.sleep(sync_interval)
                threading.Thread(target=log_sync_worker, daemon=True).start()

            return True, "Server started"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _resolve_java_binary() -> str:
        try:
            from utils.dependency_manager import resolve_java_binary

            return resolve_java_binary() or "java"
        except Exception:
            return "java"

    def _build_process_env(self) -> dict[str, str]:
        env = os.environ.copy()
        java_bin = self._resolve_java_binary()
        if java_bin == "java":
            return env

        java_path = Path(java_bin)
        java_dir = java_path.parent
        if not java_dir:
            return env

        env["PATH"] = str(java_dir) + os.pathsep + env.get("PATH", "")
        if java_dir.name.lower() == "bin":
            env.setdefault("JAVA_HOME", str(java_dir.parent))
        return env

    def _check_immediate_exit(self, proc: subprocess.Popen, wait_seconds: float = 2.0) -> tuple[bool, str]:
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            fatal_msg = self._fatal_start_log_message()
            if fatal_msg:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                self._mark_failed_start()
                return True, fatal_msg

            code = proc.poll()
            if code is None:
                time.sleep(0.05)
                continue

            time.sleep(0.1)
            tail = self._recent_logs()
            self._mark_failed_start()
            detail = "\n".join(tail).strip()
            if detail:
                return True, f"Server exited immediately (code {code}). Last log:\n{detail}"
            return True, f"Server exited immediately (code {code}). Check your server jar/start script."
        return False, ""

    def _recent_logs(self) -> list[str]:
        with self.log_lock:
            return list(self.logs)[-8:]

    def _fatal_start_log_message(self) -> str:
        tail = self._recent_logs()
        if not tail:
            return ""
        for line in tail:
            if any(p.search(line) for p in self.FATAL_START_PATTERNS):
                return "Server startup failed. Last log:\n" + "\n".join(tail).strip()
        return ""

    def _mark_failed_start(self) -> None:
        self.proc = None
        with self.state_lock:
            self.ready = False
            self.started_at = None
            self.online_players.clear()
            self.player_stats.clear()

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
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
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
            except Exception:
                return False
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
        # Linux /proc path (fast, no dependency)
        try:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        return round(kb / 1024, 1)
        except Exception:
            pass
        # Windows / macOS fallback via psutil (already a soft dep for CPU metrics)
        try:
            import psutil
            proc = psutil.Process(pid)
            return round(proc.memory_info().rss / (1024 * 1024), 1)
        except Exception:
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
