"""
Process management for the audio stack: SuperCollider/SuperDirt, the GHCi
Tidal session, and ffmpeg recording.

Shared by the harmony engine (music/engine.py) and the legacy agent loop
(tidal_backend.py). Has no LLM dependencies, so it imports cleanly anywhere.
"""

import os
import re
import shlex
import signal
import subprocess
import threading
import time
import urllib.parse
from collections import deque
from datetime import datetime
from queue import Empty, Queue
from typing import Callable

import config

_log_fn: Callable[[str], None] = print


def set_logger(fn: Callable[[str], None]) -> None:
    global _log_fn
    _log_fn = fn


def log(msg: str) -> None:
    _log_fn(msg)


# ============================================================================
# SUPERCOLLIDER / SUPERDIRT
# ============================================================================

_superdirt_process: subprocess.Popen | None = None
_superdirt_ready = threading.Event()


SUPERDIRT_PORT = 57120


def _superdirt_port_free() -> bool:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", SUPERDIRT_PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _stale_superdirt_pids() -> list[int]:
    """sclang processes running our superdirt_boot.scd (from an earlier run)."""
    pids = []
    boot = os.path.basename(config.SUPERDIRT_BOOT_FILE)
    for entry in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            continue
        if argv and os.path.basename(argv[0].decode(errors="replace")) == "sclang" and \
                any(a.decode(errors="replace").endswith(boot) for a in argv[1:]):
            pids.append(int(entry))
    return pids


def superdirt_running() -> bool:
    return _superdirt_process is not None and _superdirt_process.poll() is None


def start_superdirt() -> str:
    """Boot scsynth + SuperDirt with the custom SynthDefs and master chain.

    Blocks until SuperDirt reports ready (or the boot timeout expires) instead
    of sleeping a fixed amount of time.
    """
    global _superdirt_process
    if superdirt_running():
        return "SuperDirt is already running"
    if not _superdirt_port_free():
        # Usually a SuperCollider left over from an earlier run. Stop ours;
        # never touch anyone else's SuperCollider.
        stale = _stale_superdirt_pids()
        if stale:
            log(f"[SuperCollider] port {SUPERDIRT_PORT} is held by a leftover ystreamer SuperCollider "
                f"(pid {', '.join(map(str, stale))}); stopping it")
            for pid in stale:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            subprocess.run(["pkill", "-x", "scsynth"], capture_output=True)
            for _ in range(20):
                if _superdirt_port_free():
                    break
                time.sleep(0.5)
        if not _superdirt_port_free():
            return (f"Error: UDP port {SUPERDIRT_PORT} is in use by another program (another SuperCollider or "
                    "Tidal session?). Close it, or stop it with: pkill sclang; pkill scsynth")

    _superdirt_ready.clear()
    env = os.environ.copy()
    if config.JACK_OUTPUTS:
        env["SC_JACK_DEFAULT_OUTPUTS"] = config.JACK_OUTPUTS
    env["YS_SYNTHDEFS_FILE"] = config.SYNTHDEFS_FILE

    cmd = [*config.SCLANG_PREFIX, "sclang", config.SUPERDIRT_BOOT_FILE]
    try:
        _superdirt_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError as e:
        return f"Error starting SuperDirt: {e}"

    proc = _superdirt_process

    def pump():
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            log(f"[SuperCollider] {line}")
            if "YSTREAMER_SUPERDIRT_READY" in line:
                _superdirt_ready.set()

    threading.Thread(target=pump, daemon=True).start()

    log("Waiting for SuperCollider server to boot and load samples...")
    if _superdirt_ready.wait(timeout=config.SUPERDIRT_BOOT_TIMEOUT):
        return "SuperDirt started on port 57120"
    return f"SuperDirt did not report ready within {config.SUPERDIRT_BOOT_TIMEOUT:.0f}s (continuing anyway)"


def stop_superdirt() -> str:
    global _superdirt_process
    msg = "No SuperDirt session running"
    if _superdirt_process is not None:
        try:
            _superdirt_process.terminate()
            _superdirt_process.wait(timeout=5)
            msg = "SuperDirt stopped"
        except Exception as e:
            msg = f"Error stopping SuperDirt: {e}"
        _superdirt_process = None
    # sclang spawns scsynth as its own child, which survives sclang's
    # termination holding the audio device. Best-effort clean it up too.
    try:
        subprocess.run(["pkill", "-f", "scsynth"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    return msg


# ============================================================================
# TIDAL (GHCi)
# ============================================================================

_GHCI_ERROR_RE = re.compile(r"<interactive>:\d+:\d+: error|<no location info>: error|^\*\*\* Exception:",
                            flags=re.MULTILINE)


def diagnose_tidal_boot(output: str) -> str | None:
    """Explain a failed BootTidal.hs load in plain words, or None if it looks fine."""
    if "Ambiguous module name" in output and "Sound.Tidal" in output:
        return ("Tidal is installed twice, so GHCi can't choose which copy to import "
                "('Ambiguous module name Sound.Tidal.Context'). Fix it with:\n"
                "    python3 tidal/fix_ghc_env.py\n"
                "then start again.")
    if "Could not find module" in output and "Sound.Tidal" in output:
        return ("GHCi can't find the Tidal library. Install it with:\n"
                "    cabal update && cabal install --lib tidal")
    if re.search(r"Not in scope: .?(startTidal|superdirtTarget|oLatency)", output):
        return ("BootTidal.hs doesn't match the installed Tidal version (startTidal/superdirtTarget "
                "not found). Check `ghc-pkg list tidal` and INSTALL_TIDAL.md.")
    return None


class TidalSession:
    """A persistent GHCi process running BootTidal.hs.

    All writes go through one lock, so the engine thread, the watchdog replay
    and the legacy agent loop can't interleave half-written lines.
    """

    def __init__(self, boot_file: str | None = None):
        self.boot_file = boot_file or config.TIDAL_BOOT_FILE
        self.process: subprocess.Popen | None = None
        self._output: Queue[str] = Queue()
        self._recent = deque(maxlen=200)
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._boot_lines: list[str] = []
        # Set by start() when BootTidal.hs failed to load; a human-readable fix.
        self.boot_error: str | None = None

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, cps: float | None = None) -> str:
        if self.running():
            return "TidalCycles session is already running"
        self._ready.clear()
        self._boot_lines = []
        self.boot_error = None
        try:
            self.process = subprocess.Popen(
                ["ghci", "-ghci-script", self.boot_file],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as e:
            return f"Error starting TidalCycles session: {e}"

        proc = self.process

        def pump():
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                if "YSTREAMER_TIDAL_READY" in line:
                    self._ready.set()
                    continue
                if not self._ready.is_set():
                    self._boot_lines.append(line)
                log(f"[GHCi/Tidal] {line}")
                self._output.put(line)
                self._recent.append(line)

        threading.Thread(target=pump, daemon=True).start()
        self._write_raw('putStrLn "YSTREAMER_TIDAL_READY"')
        if not self._ready.wait(timeout=config.TIDAL_BOOT_TIMEOUT):
            log(f"[Tidal] did not report ready within {config.TIDAL_BOOT_TIMEOUT:.0f}s")

        # Did BootTidal.hs actually load? `hush` only exists if it did.
        boot_output = "\n".join(self._boot_lines)
        check_error = self.send([":t hush"], settle=1.0)
        if check_error or _GHCI_ERROR_RE.search(boot_output):
            self.boot_error = diagnose_tidal_boot(boot_output + "\n" + (check_error or "")) or (
                "BootTidal.hs did not load; see the [GHCi/Tidal] errors above.")
            return f"TidalCycles session FAILED to start: {self.boot_error}"
        if cps:
            self.send([f"setcps {cps:.6f}"])
        return "TidalCycles session started"

    def stop(self) -> str:
        if self.process is None:
            return "No TidalCycles session running"
        try:
            self._write_raw("hush")
            time.sleep(0.3)
            self._write_raw(":quit")
            self.process.wait(timeout=5)
            msg = "TidalCycles session stopped"
        except Exception as e:
            msg = f"Error stopping TidalCycles: {e}"
            try:
                self.process.kill()
            except Exception:
                pass
        self.process = None
        return msg

    def _write_raw(self, line: str) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def _drain(self) -> str:
        lines = []
        while True:
            try:
                lines.append(self._output.get_nowait())
            except Empty:
                break
        return "\n".join(lines)

    def send(self, lines: list[str], settle: float = 0.5) -> str | None:
        """Send single-line statements; return GHCi's error text if any failed."""
        if not self.running():
            return "Tidal session is not running"
        with self._lock:
            self._drain()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if "\n" in line:
                    raise ValueError("TidalSession.send expects single-line statements")
                self._write_raw(line)
                time.sleep(0.05)
            time.sleep(settle)
            output = self._drain()
        if _GHCI_ERROR_RE.search(output):
            return output.strip()[-800:]
        return None


# ============================================================================
# RECORDING
# ============================================================================

_recording_process: subprocess.Popen | None = None
audio_output_info = "(Audio output not detected yet)"


def pick_pulse_source() -> tuple[str, str]:
    """Pick a PulseAudio/PipeWire source that captures the audio output."""
    if config.AUDIO_SOURCE:
        return config.AUDIO_SOURCE, "configured via YS_AUDIO_SOURCE"

    def run(args):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=2).stdout
        except Exception:
            return ""

    default_sink = run(["pactl", "get-default-sink"]).strip()
    sources = [line.split()[1] for line in run(["pactl", "list", "sources", "short"]).splitlines()
               if len(line.split()) >= 2]
    if default_sink and f"{default_sink}.monitor" in sources:
        return f"{default_sink}.monitor", f"default sink monitor ({default_sink})"
    monitors = sorted((s for s in sources if s.endswith(".monitor")),
                      key=lambda s: (0 if "analog" in s else 1, s))
    if monitors:
        return monitors[0], "first available monitor source"
    return "default", "fallback to Pulse default (may be mic)"


def start_recording() -> tuple[str, str]:
    """Record the audio output to MP3, split into hourly (configurable) files."""
    global _recording_process, audio_output_info
    if _recording_process is not None:
        return "Recording already in progress", ""

    os.makedirs(config.RECORDINGS_DIR, exist_ok=True)
    source, reason = pick_pulse_source()
    audio_output_info = f"Source: {source} ({reason})"
    log(f"[Recording] Using source: {source} ({reason})")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostats",
           "-f", "pulse", "-i", source, "-vn", "-acodec", "libmp3lame", "-b:a", "192k"]
    if config.RECORDING_SEGMENT_SECONDS > 0:
        target = os.path.join(config.RECORDINGS_DIR, f"session_{stamp}_%04d.mp3")
        cmd += ["-f", "segment", "-segment_time", str(config.RECORDING_SEGMENT_SECONDS), "-y", target]
    else:
        target = os.path.join(config.RECORDINGS_DIR, f"session_{stamp}.mp3")
        cmd += ["-y", target]

    try:
        _recording_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                              text=True, bufsize=1)
    except FileNotFoundError as e:
        return f"Error starting recording: {e}", ""

    proc = _recording_process

    def drain():
        assert proc.stderr is not None
        for line in proc.stderr:
            if line.strip():
                log(f"[Recording/ffmpeg] {line.rstrip()}")

    threading.Thread(target=drain, daemon=True).start()
    return f"Recording started: {os.path.basename(target)}", target


def stop_recording() -> str:
    global _recording_process
    if _recording_process is None:
        return "No recording in progress"
    try:
        # SIGINT lets ffmpeg finalize the MP3 properly.
        _recording_process.send_signal(signal.SIGINT)
        _recording_process.wait(timeout=10)
        msg = "Recording stopped and saved"
    except Exception as e:
        try:
            _recording_process.kill()
        except Exception:
            pass
        msg = f"Error stopping recording: {e}"
    _recording_process = None
    return msg


# ============================================================================
# LOCAL LLM SERVER
# ============================================================================

def local_llm_status() -> str:
    """"ready", "loading" (server up, model still loading) or "down"."""
    import requests

    base = config.LOCAL_LLM_API_BASE.rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    try:
        # llama-server: /health is 503 while the model loads, 200 when ready.
        resp = requests.get(f"{root}/health", timeout=2)
        if resp.status_code == 200:
            return "ready"
        if resp.status_code == 503:
            return "loading"
        # Other OpenAI-compatible servers may not have /health.
        resp = requests.get(f"{base}/models", timeout=2)
        return "ready" if resp.status_code == 200 else "loading"
    except requests.exceptions.RequestException:
        return "down"


_llama_process: subprocess.Popen | None = None

# llama-server log lines -> loading stage shown to the user. The last log line
# that matches wins; within a line, the more specific needle (listed first) wins.
_LLAMA_STAGES = [
    ("server is listening", "listening"),
    ("warming up", "warming up"),
    ("kv_self", "allocating the KV cache"),
    ("kv cache", "allocating the KV cache"),
    ("llama_context", "creating the context"),
    ("offloaded", None),                      # "offloaded 65/65 layers to GPU", shown as is
    ("load_tensors", "loading weights"),
    ("loaded meta data", "reading model metadata"),
]


def _llama_pid() -> int | None:
    """PID of the running llama-server (ours, or one started earlier)."""
    if _llama_process is not None and _llama_process.poll() is None:
        return _llama_process.pid
    name = os.path.basename(config.LLAMA_SERVER_BIN or "llama-server")
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return None
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv0 = f.read().split(b"\0", 1)[0].decode(errors="replace")
        except OSError:
            continue
        if os.path.basename(argv0) == name:
            return pid
    return None


def _llama_bytes_loaded(pid: int) -> int | None:
    """Bytes of the model the server has read so far: read() calls (rchar) or,
    with the default mmap loading, the file pages it has touched (RssFile)."""
    loaded = None
    try:
        with open(f"/proc/{pid}/io") as f:
            for line in f:
                if line.startswith("rchar:"):
                    loaded = int(line.split()[1])
    except (OSError, ValueError):
        pass
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("RssFile:"):
                    rss = int(line.split()[1]) * 1024
                    loaded = max(loaded or 0, rss)
    except (OSError, ValueError):
        pass
    return loaded


def _llama_stage(since: float) -> str | None:
    """The loading stage from the tail of llama-server's log (if written since `since`)."""
    path = config.LLAMA_SERVER_LOG
    try:
        if os.path.getmtime(path) < since:
            return None
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 32768))
            lines = f.read().decode(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        low = line.lower()
        for needle, label in _LLAMA_STAGES:
            if needle in low:
                if label is None:
                    m = re.search(r"offloaded \d+/\d+ layers to GPU", line)
                    return m.group(0) if m else "loading weights"
                return label
    return None


def llama_load_progress(since: float) -> tuple[int | None, str]:
    """(percent of the model file loaded or None, text like
    '43% (7.1/16.5 GB) · loading weights')."""
    parts = []
    pct = None
    pid = _llama_pid()
    size = 0
    if config.LLAMA_GGUF_MODEL and os.path.exists(config.LLAMA_GGUF_MODEL):
        size = os.path.getsize(config.LLAMA_GGUF_MODEL)
    loaded = _llama_bytes_loaded(pid) if pid else None
    if size and loaded:
        pct = min(100, int(100 * loaded / size))
        parts.append(f"{pct}% ({min(loaded, size) / 1e9:.1f}/{size / 1e9:.1f} GB)")
    stage = _llama_stage(since)
    if stage:
        parts.append(stage)
    return pct, " · ".join(parts)


# llama-server log patterns -> what to do about them.
_LLAMA_ERRORS = [
    (r"out of memory|failed to allocate|cudaMalloc failed|ErrorOutOfDeviceMemory|unable to allocate",
     "the GPU ran out of memory. Lower LLAMA_CTX_SIZE in .env (e.g. 8192), or offload fewer layers "
     "with LLAMA_GPU_LAYERS (e.g. 50), or add LLAMA_SERVER_ARGS=--cache-type-k q8_0 --cache-type-v q8_0 -fa on"),
    (r"unknown model architecture|unknown architecture|unsupported model",
     "this llama.cpp build doesn't know the model's architecture. Update and rebuild llama.cpp"),
    (r"failed to load model|error loading model|invalid magic|gguf_init.*failed",
     "llama.cpp couldn't load the model file. Check LLAMA_GGUF_MODEL and that the download is complete, "
     "or update llama.cpp"),
    (r"address already in use|couldn't bind|bind.*failed",
     "port 8080 is taken by another program. Stop it (or the old llama-server: pkill -f llama-server)"),
    (r"invalid argument|unknown argument|error: unrecognized",
     "llama-server rejected a command-line flag. Check LLAMA_SERVER_ARGS, or update llama.cpp"),
]


def _llama_log_tail(n: int = 12) -> list[str]:
    try:
        with open(config.LLAMA_SERVER_LOG, "rb") as f:
            f.seek(max(0, os.path.getsize(config.LLAMA_SERVER_LOG) - 65536))
            lines = [line.rstrip() for line in f.read().decode(errors="replace").splitlines()]
    except OSError:
        return []
    return [line for line in lines if line.strip()][-n:]


def diagnose_llama_failure() -> str:
    """Last lines of llama-server's log plus a likely fix, for when it died."""
    tail = _llama_log_tail(40)
    hint = ""
    for line in reversed(tail):
        for pattern, fix in _LLAMA_ERRORS:
            if re.search(pattern, line, re.IGNORECASE):
                hint = fix
                break
        if hint:
            break
    shown = "\n".join(f"    {line}" for line in tail[-12:]) or "    (the log is empty)"
    text = f"last lines of {config.LLAMA_SERVER_LOG}:\n{shown}"
    if hint:
        text += f"\n  Likely cause: {hint}."
    return text


def wait_for_local_llm(timeout: float) -> bool:
    started = time.time()
    deadline = started + timeout
    announced = False
    last_key, last_print = None, 0.0
    # Log lines written shortly before we started waiting belong to this load
    # (the server is launched while the audio stack boots).
    log_since = started - 120
    while time.time() < deadline:
        status = local_llm_status()
        exited = _llama_process is not None and _llama_process.poll() is not None
        if exited or (status == "down" and announced):
            code = f" with exit code {_llama_process.returncode}" if exited else ""
            log(f"[llama-server] stopped{code} while loading the model; the built-in performer plays instead.\n"
                f"  {diagnose_llama_failure()}")
            return False
        if status == "ready":
            log(f"[llama-server] ready after {time.time() - started:.0f}s" if announced else "[llama-server] ready")
            return True
        if status == "loading":
            if not announced:
                log("[llama-server] loading the model, waiting...")
                announced = True
            pct, text = llama_load_progress(log_since)
            now = time.time()
            # A new line when the next 10% or the stage is reached, else every 15 s.
            key = (None if pct is None else pct // 10, text.rsplit(" · ", 1)[-1])
            if text and (key != last_key or now - last_print >= 15):
                elapsed = int(now - started)
                log(f"[llama-server] {text} · {elapsed // 60}:{elapsed % 60:02d}")
                last_key, last_print = key, now
        time.sleep(2)
    log(f"[llama-server] not ready after {timeout:.0f}s; the built-in performer plays until it is.\n"
        f"  {diagnose_llama_failure()}")
    return False


def ensure_local_llm_server(wait: bool = True) -> None:
    """Start the local llama-server if needed; with wait=True also wait until
    its model is loaded (use wait=False to boot the audio meanwhile, then call
    wait_for_local_llm)."""
    status = local_llm_status()
    if status != "down":
        log(f"[llama-server] already running at {config.LOCAL_LLM_API_BASE}")
        if wait:
            wait_for_local_llm(config.LLM_LOAD_TIMEOUT)
        return

    if not config.LLAMA_GGUF_MODEL or not os.path.exists(config.LLAMA_GGUF_MODEL):
        log(f"[llama-server] not starting: LLAMA_GGUF_MODEL in .env is "
            f"{'not set' if not config.LLAMA_GGUF_MODEL else 'missing: ' + config.LLAMA_GGUF_MODEL}. "
            "The built-in performer plays until a server answers at "
            f"{config.LOCAL_LLM_API_BASE}.")
        return

    log(f"[llama-server] launching {config.LLAMA_SERVER_BIN}...")
    os.makedirs(os.path.dirname(config.LLAMA_SERVER_LOG), exist_ok=True)
    log_file = open(config.LLAMA_SERVER_LOG, "a")
    try:
        global _llama_process
        port = urllib.parse.urlparse(config.LOCAL_LLM_API_BASE).port or 8080
        cmd = [config.LLAMA_SERVER_BIN, "-m", config.LLAMA_GGUF_MODEL, "-ngl", str(config.LLAMA_GPU_LAYERS),
               "--port", str(port), "--ctx-size", str(config.LLAMA_CTX_SIZE), "--parallel", "2",
               *shlex.split(config.LLAMA_SERVER_ARGS)]
        log(f"[llama-server] {' '.join(cmd)}")
        log_file.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(cmd)}\n")
        log_file.flush()
        _llama_process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
    except FileNotFoundError as e:
        log(f"[llama-server] could not launch: {e}")
        return
    if wait:
        wait_for_local_llm(config.LLM_LOAD_TIMEOUT)
