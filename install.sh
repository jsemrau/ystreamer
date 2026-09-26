#!/usr/bin/env bash
# One-time setup on Ubuntu/Debian. See INSTALL_TIDAL.md for details.
set -euo pipefail

# 1. System packages: Haskell + Tidal, SuperCollider, ffmpeg, PipeWire tools, fonts
# A broken third-party repository (e.g. an outdated PPA) makes `apt-get update`
# fail even though the Ubuntu repositories are fine, so don't stop on it.
sudo apt-get update || echo "WARNING: apt-get update reported errors (often a broken PPA); continuing"
sudo apt-get install -y ghc cabal-install supercollider sc3-plugins ffmpeg \
    pipewire-jack pipewire-bin pulseaudio-utils fonts-dejavu-core python3-venv xvfb
# The stream page (stream/ui) is rendered by Chromium in a virtual display (Xvfb).
# Debian ships "chromium"; on Ubuntu "chromium-browser" is a snap, so fall back to
# Playwright's Chromium (installed with the Python packages below) if neither works.
sudo apt-get install -y chromium 2>/dev/null || sudo apt-get install -y chromium-browser 2>/dev/null \
    || echo "NOTE: no Chromium package; the Playwright Chromium below is used for the stream page"

tidal_importable() {
    ! echo 'import Sound.Tidal.Context' | ghci -v0 2>&1 | grep -qi "error"
}
# Installing Tidal twice leaves two copies in the GHC environment and breaks
# `import Sound.Tidal.Context`, so repair duplicates first and only install
# when it's really missing.
python3 tidal/fix_ghc_env.py
if tidal_importable; then
    echo "Tidal is already installed."
else
    cabal update
    cabal install --lib tidal
    python3 tidal/fix_ghc_env.py
fi
tidal_importable || { echo "ERROR: GHCi still can't import Sound.Tidal.Context"; exit 1; }
echo 'Quarks.install("SuperDirt"); thisProcess.shutdown;' | sclang -D
[[ -d ~/Dirt-Samples ]] || git clone https://github.com/tidalcycles/Dirt-Samples.git ~/Dirt-Samples

# 2. Python environment
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
# A Chromium for the stream page when the system has none (stream.sh finds it).
if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null && ! command -v google-chrome >/dev/null; then
    ./venv/bin/pip install playwright && ./venv/bin/python -m playwright install chromium \
        || echo "NOTE: couldn't install Chromium; the stream falls back to the plain overlay look"
fi

# Voice for the spoken narration (warm British female; see README to change it)
[[ -f voices/en_GB-jenny_dioco-medium.onnx ]] || \
    ./venv/bin/python -m piper.download_voices en_GB-jenny_dioco-medium --download-dir voices

# 3. Configuration
[[ -f .env ]] || cp .env.example .env
echo "Edit .env (stream key, model), then:"
echo "  ./venv/bin/python orchestrator.py --selftest   # type-check the generated Tidal code"
echo "  stream/setup_audio.sh --monitor && ./venv/bin/python orchestrator.py   # listen locally"
echo "  stream/live.sh                                 # go live (Ctrl-C stops)"
