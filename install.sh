#!/usr/bin/env bash
# One-time setup on Ubuntu/Debian. See INSTALL_TIDAL.md for details.
set -euo pipefail

# 1. System packages: Haskell + Tidal, SuperCollider, ffmpeg, PipeWire tools, fonts
sudo apt-get update
sudo apt-get install -y ghc cabal-install supercollider sc3-plugins ffmpeg \
    pipewire-jack pipewire-bin pulseaudio-utils fonts-dejavu-core python3-venv
cabal update
cabal install --lib tidal
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
