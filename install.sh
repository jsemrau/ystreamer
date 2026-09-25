#!/usr/bin/env bash
# One-time setup on Ubuntu/Debian. See INSTALL_TIDAL.md for details.
set -euo pipefail

# 1. System packages: Haskell + Tidal, SuperCollider, ffmpeg, PipeWire tools, fonts
# A broken third-party repository (e.g. an outdated PPA) makes `apt-get update`
# fail even though the Ubuntu repositories are fine, so don't stop on it.
sudo apt-get update || echo "WARNING: apt-get update reported errors (often a broken PPA); continuing"
sudo apt-get install -y ghc cabal-install supercollider sc3-plugins ffmpeg \
    pipewire-jack pipewire-bin pulseaudio-utils fonts-dejavu-core python3-venv

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

# 3. Configuration
[[ -f .env ]] || cp .env.example .env
echo "Edit .env (stream key, model), then:"
echo "  ./venv/bin/python orchestrator.py --selftest   # type-check the generated Tidal code"
echo "  stream/setup_audio.sh --monitor && ./venv/bin/python orchestrator.py   # listen locally"
echo "  stream/install_services.sh                     # go live 24/7"
