import subprocess
import threading
import platform
from pathlib import Path

class TunnelManager:
    def __init__(self, bin_dir):
        self.bin_dir = Path(bin_dir)
        self.playit = self.bin_dir / ("playit.exe" if platform.system() == "Windows" else "playit")
        self.proc: subprocess.Popen[str] | None = None
        self.tunnel_addr = "Not Active"

    def start(self):
        # Guard: Check if a process is already running
        proc = self.proc
        if proc is not None:
            if proc.poll() is None:
                return
            
        if not self.playit.exists():
            print(f"Tunnel: {self.playit} not found")
            return

        try:
            cmd = [str(self.playit)]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                text=True, bufsize=1
            )
            self.proc = proc
            
            stdout = proc.stdout
            if stdout is None:
                return

            def reader():
                if proc is None or stdout is None:
                    return
                for line in stdout:
                    if "address:" in line:
                        self.tunnel_addr = line.split("address:")[1].strip()
                    if proc.poll() is not None: 
                        break

            threading.Thread(target=reader, daemon=True).start()
        except Exception as e:
            print(f"Tunnel: Failed to start: {e}")

    def stop(self):
        proc = self.proc
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except: pass
            self.proc = None
            self.tunnel_addr = "Not Active"

    def get_address(self):
        return self.tunnel_addr
