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
# A set with an end (stream/live.sh --hours): its start and end as Unix
# times, so the Director plans each track's length to finish on time.
SESSION_START = float(_env("YS_SESSION_START", "0") or 0)
SESSION_END = float(_env("YS_SESSION_END", "0") or 0)
# The art director's persona: its character, ambition and goal (the Director's system prompt).
DIRECTOR_PERSONA = _env("YS_DIRECTOR_PERSONA", os.path.join(REPO_DIR, "prompts", "director_persona.md"))
# The art director's skills (song design, the basics and the advanced
# techniques), every *.md in this folder, in her system prompt after the persona.
DIRECTOR_SKILLS = _env("YS_DIRECTOR_SKILLS", os.path.join(REPO_DIR, "prompts", "skills"))
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
# The saws done properly (tidal/synthdefs.scd: ys_hypersaw, the supersaw;
# ys_sawlead; ys_sawpad): off until you've listened to them
# (python -m eval.sound_preview, and --saws), then YS_HYPERSAW=1.
HYPERSAW = _env("YS_HYPERSAW", "0") == "1"
# The reversed voice (ys_revvox, the ambient-house texture): off until you've
# listened to it (python -m eval.sound_preview --ambienthouse), then YS_REVVOX=1.
REVVOX = _env("YS_REVVOX", "0") == "1"
# A little human feel on what plays (livecode.HUMANIZE): velocities that vary
# from hit to hit and notes a few milliseconds late, as hands play them; the
# kick and bass stay tight. YS_HUMANIZE=0 plays everything exactly on the grid.
HUMANIZE = _env("YS_HUMANIZE", "1") != "0"
# The art director's surprises (music/surprises.py): at most one every N tracks.
SURPRISE_EVERY = max(1, int(_env("YS_SURPRISE_EVERY", "2") or 2))
# A drum kit and a bass sound for each track (music/kits.py, samples/kit_*
# from eval/drum_kits.py); YS_DRUM_KITS=0 plays the stream's stock hits.
DRUM_KITS = _env("YS_DRUM_KITS", "1") != "0"
# The art director develops each track live (music/director.py,
# develop_section): at every section change she hears what plays and writes
# the next section's instructions. YS_DIRECTOR_DEVELOP=0: the plan as written.
DIRECTOR_DEVELOP = _env("YS_DIRECTOR_DEVELOP", "1") != "0"
# Explore or exploit (music/explore.py): the base chance that a track explores
# (the least-tried touchstone, spark, mode and tempo, and the bold choice at
# every turn) rather than build on what earned the host's thumbs. It rises
# after a 👎, falls after a 👍. 0 always exploits, 1 always explores.
EXPLORE = min(1.0, max(0.0, float(_env("YS_EXPLORE", "0.5") or 0.5)))
# Licensed acapellas (music/vocals.py): the sources and their manifest, and
# the share of tracks that sing one when one fits the style (0 = never).
ACAPELLA_DIR = _env("YS_ACAPELLA_DIR", os.path.join(REPO_DIR, "acapellas"))
# Sampled instruments (music/instruments.py: real recordings, CC0).
INSTRUMENTS_DIR = _env("YS_INSTRUMENTS_DIR", os.path.join(REPO_DIR, "instruments"))
ACAPELLA_CHANCE = _env_float("YS_ACAPELLA_CHANCE", 0.5)
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
