"""
Process management for the audio stack: SuperCollider/SuperDirt, the GHCi
Tidal session, and ffmpeg recording.

Shared by the harmony engine (music/engine.py) and the legacy agent loop
(tidal_backend.py). Has no LLM dependencies, so it imports cleanly anywhere.
"""

import os
import re
import signal
import subprocess
import threading
import time
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

def ensure_local_llm_server() -> None:
    """Start the local llama-server if configured and not already running."""
    import requests

    try:
        requests.get(f"{config.LOCAL_LLM_API_BASE}/models", timeout=2)
        log(f"[llama-server] already running at {config.LOCAL_LLM_API_BASE}")
        return
    except requests.exceptions.RequestException:
        pass

    if not config.LLAMA_GGUF_MODEL or not os.path.exists(config.LLAMA_GGUF_MODEL):
        log(f"[llama-server] not starting: LLAMA_GGUF_MODEL in .env is "
            f"{'not set' if not config.LLAMA_GGUF_MODEL else 'missing: ' + config.LLAMA_GGUF_MODEL}. "
            "The Director will use its built-in planner until a server answers at "
            f"{config.LOCAL_LLM_API_BASE}.")
        return

    log(f"[llama-server] launching {config.LLAMA_SERVER_BIN}...")
    os.makedirs(os.path.dirname(config.LLAMA_SERVER_LOG), exist_ok=True)
    log_file = open(config.LLAMA_SERVER_LOG, "a")
    try:
        subprocess.Popen(
            [config.LLAMA_SERVER_BIN, "-m", config.LLAMA_GGUF_MODEL, "-ngl", "999",
             "--port", "8080", "--ctx-size", "32768", "--parallel", "2"],
            stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
        )
    except FileNotFoundError as e:
        log(f"[llama-server] could not launch: {e}")
        return

    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            requests.get(f"{config.LOCAL_LLM_API_BASE}/models", timeout=2)
            log("[llama-server] ready")
            return
        except requests.exceptions.RequestException:
            time.sleep(2)
    log(f"[llama-server] did not become ready within 60s; see {config.LLAMA_SERVER_LOG}")
