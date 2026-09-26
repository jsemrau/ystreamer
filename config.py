"""
Central configuration. Every machine-specific path or tunable lives here and
can be overridden from the environment (or a .env file next to this module),
so nothing is hardcoded to one developer's home directory.
"""

import os

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv.parser import parse_stream

    # .env wins over the shell, but an empty entry (e.g. a leftover
    # `ANTHROPIC_API_KEY=` from .env.example, before or after the real one)
    # never wipes out a real value.
    _env_file = os.path.join(REPO_DIR, ".env")
    if os.path.exists(_env_file):
        with open(_env_file, encoding="utf-8") as _f:
            for _binding in parse_stream(_f):
                if _binding.error:
                    print(f"[config] WARNING: .env line {_binding.original.line} can't be read "
                          "(unclosed quote or stray character?), so it is ignored")
                elif _binding.key and _binding.value and _binding.value.strip():
                    os.environ[_binding.key] = _binding.value.strip()
except ImportError:  # python-dotenv is optional for the engine/tests
    if os.path.exists(os.path.join(REPO_DIR, ".env")):
        print("[config] WARNING: python-dotenv isn't installed, so .env is NOT loaded "
              "(./venv/bin/pip install python-dotenv)")


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
# layers on the GPU (999 = all), and any extra flags (e.g. "-fa on --cache-type-k q8_0").
LLAMA_CTX_SIZE = int(_env("LLAMA_CTX_SIZE", "16384"))
LLAMA_GPU_LAYERS = _env("LLAMA_GPU_LAYERS", "999")
LLAMA_SERVER_ARGS = _env("LLAMA_SERVER_ARGS", "")
LLM_TIMEOUT_SECONDS = _env_float("YS_LLM_TIMEOUT", 60.0)
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
