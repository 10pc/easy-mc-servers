"""Process manager: MC server + e4mcbiat per dashboard entry. Stdlib only."""
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BIN_DIR = BASE_DIR / "bin"
LOG_DIR = BASE_DIR / "logs"
E4MC_JAR = BIN_DIR / "e4mcbiat.jar"
E4MC_RELEASE_API = "https://api.github.com/repos/DuncanRuns/e4mcbiat/releases/latest"

DOMAIN_RE = re.compile(r"Connected,\s*domain:\s*(\S+)", re.IGNORECASE)
MC_READY_RE = re.compile(r"Done\s*\(.+?\)!", re.IGNORECASE)

STATUS_OFFLINE = "offline"
STATUS_STARTING = "starting"
STATUS_ONLINE = "online"
STATUS_STOPPING = "stopping"
STATUS_CRASHED = "crashed"


def port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def ensure_e4mc_jar() -> Path:
    """Download latest e4mcbiat *-all.jar if missing. Returns jar path."""
    if E4MC_JAR.exists() and E4MC_JAR.stat().st_size > 0:
        return E4MC_JAR
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(E4MC_RELEASE_API, headers={"User-Agent": "mc-dashboard"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assets = data.get("assets", [])
    url = None
    for a in assets:
        dl = a.get("browser_download_url", "")
        if dl.endswith("-all.jar"):
            url = dl
            break
    if not url and assets:
        url = assets[0].get("browser_download_url")
    if not url:
        raise RuntimeError("No downloadable jar found in latest e4mcbiat release")
    tmp = E4MC_JAR.with_suffix(".tmp")
    req2 = urllib.request.Request(url, headers={"User-Agent": "mc-dashboard"})
    with urllib.request.urlopen(req2, timeout=120) as resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
    tmp.rename(E4MC_JAR)
    return E4MC_JAR


class ServerManager:
    def __init__(self):
        self._lock = threading.Lock()
        # id -> {mc, e4mc, domain, started_at, status, e4mc_error, mc_ready}
        self._procs: dict = {}
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def log_path(self, server_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", server_id)
        return LOG_DIR / f"{safe}.log"

    def _append(self, server_id: str, prefix: str, msg: str):
        try:
            with open(self.log_path(server_id), "a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}][{prefix}] {msg.rstrip()}\n")
        except OSError:
            pass

    def _pump(self, server_id: str, proc, prefix: str):
        try:
            for line in proc.stdout:
                self._append(server_id, prefix, line)
                if prefix == "e4mc":
                    m = DOMAIN_RE.search(line)
                    if m:
                        with self._lock:
                            st = self._procs.get(server_id)
                            if st is not None:
                                st["domain"] = m.group(1).strip()
                if prefix == "mc" and MC_READY_RE.search(line):
                    with self._lock:
                        st = self._procs.get(server_id)
                        if st is not None:
                            st["mc_ready"] = True
        except Exception as e:  # never kill thread on log error
            self._append(server_id, prefix, f"<log pump error: {e}>")

    def is_running(self, server_id: str) -> bool:
        with self._lock:
            st = self._procs.get(server_id)
            if not st or not st.get("mc"):
                return False
            return st["mc"].poll() is None

    def get_state(self, server: dict) -> dict:
        sid = server["id"]
        with self._lock:
            st = self._procs.get(sid)
            if not st or not st.get("mc"):
                # check stale crash log? plain offline
                return {
                    "id": sid, "name": server["name"], "mc_port": server["mc_port"],
                    "status": STATUS_OFFLINE, "domain": None,
                    "uptime": 0, "e4mc_error": None,
                }
            mc = st["mc"]
            e4mc = st.get("e4mc")
            rc = mc.poll()
            if rc is not None and rc != 0 and not st.get("stopping"):
                # MC exited unexpectedly
                if e4mc and e4mc.poll() is None:
                    self._kill(e4mc)
                status = STATUS_CRASHED if not st.get("stopping") else STATUS_OFFLINE
            elif rc is not None:
                status = STATUS_OFFLINE
            elif st.get("mc_ready") and e4mc is not None and e4mc.poll() is None and st.get("domain"):
                status = STATUS_ONLINE
            elif st.get("mc_ready"):
                status = STATUS_STARTING if not st.get("domain") else STATUS_ONLINE
            else:
                # MC up but not ready yet; also treat port-open as online-ish
                status = STATUS_STARTING
            uptime = int(time.time() - st["started_at"]) if st.get("started_at") else 0
            return {
                "id": sid, "name": server["name"], "mc_port": server["mc_port"],
                "status": status, "domain": st.get("domain"),
                "uptime": uptime, "e4mc_error": st.get("e4mc_error"),
            }

    def _kill(self, proc, wait_secs: float = 5):
        try:
            if proc.poll() is not None:
                return
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                proc.terminate()
            try:
                proc.wait(timeout=wait_secs)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    proc.kill()
        except Exception:
            pass

    def start(self, server: dict) -> dict:
        sid = server["id"]
        directory = server["directory"]
        command = server["command"]
        mc_port = int(server["mc_port"])
        if self.is_running(sid):
            raise RuntimeError("Server already running")
        if port_open(mc_port):
            raise RuntimeError(f"Port {mc_port} already in use on 127.0.0.1")
        if not os.path.isdir(directory):
            raise RuntimeError(f"Directory not found: {directory}")
        try:
            argv = shlex.split(command)
        except ValueError as e:
            raise RuntimeError(f"Invalid command: {e}")
        if not argv:
            raise RuntimeError("Empty start command")

        self._append(sid, "sys", f"Starting: {' '.join(argv)} (cwd={directory}, port={mc_port})")
        # truncate log if huge (>1MB) to keep dashboard snappy
        lp = self.log_path(sid)
        try:
            if lp.exists() and lp.stat().st_size > 1_000_000:
                lp.write_text(f"--- rotated at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        except OSError:
            pass

        mc = subprocess.Popen(
            argv, cwd=directory, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, start_new_session=True,
        )
        with self._lock:
            self._procs[sid] = {
                "mc": mc, "e4mc": None, "domain": None,
                "started_at": time.time(), "stopping": False,
                "e4mc_error": None, "mc_ready": False,
            }
        threading.Thread(target=self._pump, args=(sid, mc, "mc"), daemon=True).start()
        threading.Thread(target=self._startup_watcher, args=(dict(server),), daemon=True).start()
        return self.get_state(server)

    def _startup_watcher(self, server: dict):
        sid = server["id"]
        mc_port = int(server["mc_port"])
        # wait up to 120s for MC to be ready (log line or port open)
        deadline = time.time() + 120
        while time.time() < deadline:
            with self._lock:
                st = self._procs.get(sid)
                if not st or st.get("stopping"):
                    return
                if st["mc"].poll() is not None:
                    self._append(sid, "sys", f"MC exited early (code {st['mc'].poll()}); skipping e4mc")
                    return
                ready = st.get("mc_ready") or port_open(mc_port)
            if ready:
                break
            time.sleep(1)
        with self._lock:
            st = self._procs.get(sid)
            if not st or st.get("stopping") or st["mc"].poll() is not None:
                return
            if st.get("e4mc") is not None:
                return  # already launched (e.g. regenerate won the race)
            st["mc_ready"] = True
        self._spawn_e4mc(sid, mc_port)

    def _spawn_e4mc(self, server_id: str, mc_port: int):
        """Launch e4mcbiat sidecar (non-fatal if it fails). No lock held on entry."""
        sid = server_id
        try:
            jar = ensure_e4mc_jar()
        except Exception as e:
            with self._lock:
                st = self._procs.get(sid)
                if st is not None:
                    st["e4mc_error"] = f"e4mc download failed: {e}"
            self._append(sid, "sys", f"e4mc download failed: {e}")
            return
        self._append(sid, "sys", f"Starting e4mcbiat for port {mc_port}")
        try:
            e4mc = subprocess.Popen(
                ["java", "-jar", str(jar), "nogui", f"port={mc_port}"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                start_new_session=True,
            )
        except Exception as e:
            with self._lock:
                st = self._procs.get(sid)
                if st is not None:
                    st["e4mc_error"] = f"e4mc launch failed: {e}"
            self._append(sid, "sys", f"e4mc launch failed: {e}")
            return
        with self._lock:
            st = self._procs.get(sid)
            if not st or st.get("stopping"):
                self._kill(e4mc)
                return
            st["e4mc"] = e4mc
        threading.Thread(target=self._pump, args=(sid, e4mc, "e4mc"), daemon=True).start()
        # if e4mc dies quickly, record error but keep MC running
        def _watch_e4mc():
            rc = e4mc.wait()
            with self._lock:
                st2 = self._procs.get(sid)
                if st2 is not None and st2.get("e4mc") is e4mc and not st2.get("stopping"):
                    if st2.get("domain") is None:
                        st2["e4mc_error"] = f"e4mc exited (code {rc}), check logs"
                        self._append(sid, "sys", f"e4mc exited (code {rc}) before assigning domain")
        threading.Thread(target=_watch_e4mc, daemon=True).start()

    def restart_e4mc(self, server: dict) -> dict:
        """Kill the e4mc sidecar and start a fresh one for a new domain. MC keeps running."""
        sid = server["id"]
        mc_port = int(server["mc_port"])
        with self._lock:
            st = self._procs.get(sid)
            if not st or st.get("mc") is None or st["mc"].poll() is not None:
                raise RuntimeError("Server is not running")
            old = st.get("e4mc")
            st["e4mc"] = None
            st["domain"] = None
            st["e4mc_error"] = None
        if old is not None and old.poll() is None:
            if old.stdin:
                try:
                    old.stdin.write("stop\n")
                    old.stdin.flush()
                except Exception:
                    pass
            self._kill(old, wait_secs=5)
        self._append(sid, "sys", "Regenerating e4mc address (old domain discarded)")
        self._spawn_e4mc(sid, mc_port)
        return self.get_state(server)

    def stop(self, server: dict, timeout: float = 30) -> dict:
        sid = server["id"]
        with self._lock:
            st = self._procs.get(sid)
            if not st or (st["mc"].poll() is not None and (not st.get("e4mc") or st["e4mc"].poll() is not None)):
                self._procs.pop(sid, None)
                return self.get_state(server)
            st["stopping"] = True
            mc, e4mc = st.get("mc"), st.get("e4mc")
        self._append(sid, "sys", "Stopping (sending 'stop' to MC console)")
        # graceful: tell MC to stop, tell e4mc to stop
        for proc, label in ((mc, "mc"), (e4mc, "e4mc")):
            if proc is not None and proc.poll() is None and proc.stdin:
                try:
                    proc.stdin.write("stop\n")
                    proc.stdin.flush()
                except Exception:
                    pass
        # wait for MC up to timeout
        end = time.time() + timeout
        while time.time() < end:
            if mc.poll() is not None:
                break
            time.sleep(0.5)
        self._kill(mc)
        if e4mc is not None:
            self._kill(e4mc, wait_secs=5)
        self._append(sid, "sys", "Stopped")
        with self._lock:
            self._procs.pop(sid, None)
        return self.get_state(server)

    MAX_CMD_LEN = 500

    def send_command(self, server: dict, command: str) -> dict:
        """Send console command(s) to a running MC server. Each sent line is
        logged with a [cmd] prefix so it shows up in the log tail."""
        sid = server["id"]
        text = (command or "").strip()
        if not text:
            raise ValueError("Empty command")
        if len(text) > self.MAX_CMD_LEN:
            raise ValueError(f"Command too long (max {self.MAX_CMD_LEN} chars)")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            raise ValueError("Empty command")
        with self._lock:
            st = self._procs.get(sid)
            if not st or st.get("mc") is None or st["mc"].poll() is not None:
                raise RuntimeError("Server is not running")
            mc = st["mc"]
            if mc.stdin is None:
                raise RuntimeError("Console unavailable (no stdin)")
        try:
            for ln in lines:
                mc.stdin.write(ln + "\n")
            mc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as e:
            raise RuntimeError(f"Console unavailable: {e}")
        for ln in lines:
            self._append(sid, "cmd", f"> {ln}")
        return self.get_state(server)

    def tail_log(self, server_id: str, max_lines: int = 200, max_bytes: int = 100_000) -> str:
        lp = self.log_path(server_id)
        if not lp.exists():
            return ""
        try:
            size = lp.stat().st_size
            with open(lp, "rb") as f:
                if size > max_bytes:
                    f.seek(size - max_bytes)
                    f.readline()  # drop partial line
                data = f.read().decode("utf-8", errors="replace")
            lines = data.splitlines()
            return "\n".join(lines[-max_lines:])
        except OSError:
            return ""
