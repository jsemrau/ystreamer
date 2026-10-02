"""
Central configuration. Every machine-specific path or tunable lives here and
can be overridden from the environment (or a .env file next to this module),
so nothing is hardcoded to one developer's home directory.
"""

import os

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

def load_env_file(path: str) -> None:
    """Load a .env file into os.environ. .env wins over the shell, but an
    empty entry (e.g. a leftover `ANTHROPIC_API_KEY=` from .env.example,
    before or after the real one) never wipes out a real value. ${VAR},
    $VAR and ~ are expanded like python-dotenv does."""
    try:
        from dotenv.parser import parse_stream
    except ImportError:  # python-dotenv is optional for the engine/tests
        if os.path.exists(path):
            print("[config] WARNING: python-dotenv isn't installed, so .env is NOT loaded "
                  "(./venv/bin/pip install python-dotenv)")
        return
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for binding in parse_stream(f):
            if binding.error:
                print(f"[config] WARNING: .env line {binding.original.line} can't be read "
                      "(unclosed quote or stray character?), so it is ignored")
            elif binding.key and binding.value and binding.value.strip():
                os.environ[binding.key] = os.path.expanduser(os.path.expandvars(binding.value.strip()))


load_env_file(os.path.join(REPO_DIR, ".env"))


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# --- Tidal / SuperCollider -------------------------------------------------
TIDAL_BOOT_FILE = _env("YS_TIDAL_BOOT_FILE", os.path.join(REPO_DIR, "tidal", "BootTidal.hs"))
SUPERDIRT_BOOT_FILE = _env("YS_SUPERDIRT_BOOT_FILE", os.path.join(REPO_DIR, "tidal", "superdirt_boot.scd"))
SYNTHDEFS_FILE = _env("YS_SYNTHDEFS_FILE", os.path.join(REPO_DIR, "tidal", "synthdefs.scd"))
# Command prefix used to launch sclang. "pw-jack" makes it talk to PipeWire's
# JACK shim; set YS_SCLANG_PREFIX="" on a machine running real JACK.
SCLANG_PREFIX = _env("YS_SCLANG_PREFIX", "pw-jack").split()
# Comma-separated JACK ports scsynth auto-connects to, e.g.
# "ystream_sink:playback_FL,ystream_sink:playback_FR" to feed the stream sink.
JACK_OUTPUTS = _env("YS_JACK_OUTPUTS", "")
SUPERDIRT_BOOT_TIMEOUT = _env_float("YS_SUPERDIRT_BOOT_TIMEOUT", 90.0)
TIDAL_BOOT_TIMEOUT = _env_float("YS_TIDAL_BOOT_TIMEOUT", 60.0)

# --- Local LLM server (llama.cpp) ------------------------------------------
LLAMA_SERVER_BIN = _env("LLAMA_SERVER_BIN", "llama-server")
LLAMA_GGUF_MODEL = _env("LLAMA_GGUF_MODEL", "")
LOCAL_LLM_API_BASE = _env("LOCAL_LLM_API_BASE", "http://127.0.0.1:8080/v1")
LLAMA_SERVER_LOG = _env("LLAMA_SERVER_LOG", os.path.join(REPO_DIR, "logs", "llama-server.log"))
# llama-server launch options: context size (shared by the parallel slots),
# layers on the GPU (999 = all; "auto" leaves it to llama.cpp, which then
# fits what it can on the GPUs and keeps the rest, e.g. a big MoE model's
# experts, in RAM), and any extra flags (e.g. "-fa on --cache-type-k q8_0").
# The channel's name, shown on the stream page, the poster and the channel art.
CHANNEL_NAME = _env("YS_CHANNEL_NAME", "InfiniTrance")
# The new trance supersaw (tidal/synthdefs.scd ys_hypersaw): off until you've
# listened to it (python -m eval.sound_preview), then YS_HYPERSAW=1.
HYPERSAW = _env("YS_HYPERSAW", "0") == "1"
# The art director's surprises (music/surprises.py): at most one every N tracks.
SURPRISE_EVERY = max(1, int(_env("YS_SURPRISE_EVERY", "2") or 2))
LLAMA_CTX_SIZE = int(_env("LLAMA_CTX_SIZE", "16384"))
LLAMA_GPU_LAYERS = _env("LLAMA_GPU_LAYERS", "999")
LLAMA_SERVER_ARGS = _env("LLAMA_SERVER_ARGS", "")
LLM_TIMEOUT_SECONDS = _env_float("YS_LLM_TIMEOUT", 60.0)
# The local model gets longer: a big model on one GPU can take minutes on a
# long prompt. Its Performer waits up to YS_LOCAL_PERFORMER_WAIT for an edit;
# if that is longer than the edit interval, the music keeps playing and the
# edit comes in late, on the next bar, instead of being dropped.
LOCAL_LLM_TIMEOUT_SECONDS = _env_float("YS_LOCAL_LLM_TIMEOUT", 180.0)
LOCAL_PERFORMER_WAIT_SECONDS = _env_float("YS_LOCAL_PERFORMER_WAIT", 60.0)
# Bars between edits while a track builds up (a new line every few bars, so
# it gets interesting fast); after that, the style's own edit interval.
BUILD_BARS = _env_int("YS_BUILD_BARS", 4)
# How long to wait at startup for the local model to finish loading.
LLM_LOAD_TIMEOUT = _env_float("YS_LLM_LOAD_TIMEOUT", 300.0)

# --- Output / runtime files --------------------------------------------------
RECORDINGS_DIR = _env("YS_RECORDINGS_DIR", os.path.join(REPO_DIR, "recordings"))
# Split recordings into files of this many seconds (0 = one file per run).
RECORDING_SEGMENT_SECONDS = _env_int("YS_RECORDING_SEGMENT_SECONDS", 3600)
RUNTIME_DIR = _env("YS_RUNTIME_DIR", os.path.join(REPO_DIR, "runtime"))
LOGS_DIR = _env("YS_LOGS_DIR", os.path.join(REPO_DIR, "logs"))
# PulseAudio/PipeWire source to record from. Empty = auto-detect the default
# sink's monitor. For streaming use "ystream_sink.monitor".
AUDIO_SOURCE = _env("YS_AUDIO_SOURCE", "")

# --- Musical defaults --------------------------------------------------------
MAX_ACTIVE_LAYERS = _env_int("YS_MAX_ACTIVE_LAYERS", 6)
# Legacy agent loop: seconds between director suggestions.
LEGACY_DIRECTOR_INTERVAL = _env_int("YS_DIRECTOR_INTERVAL", 90)

# Bounded history sizes (the stream is meant to run forever).
HISTORY_LIMIT = _env_int("YS_HISTORY_LIMIT", 200)


def ensure_dirs() -> None:
    for path in (RUNTIME_DIR, LOGS_DIR, RECORDINGS_DIR):
        os.makedirs(path, exist_ok=True)
