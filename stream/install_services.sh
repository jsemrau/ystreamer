#!/usr/bin/env bash
# Install the ystreamer systemd *user* units.
#
#   stream/install_services.sh          # install + enable + start everything
#   stream/install_services.sh --no-llm # skip the local llama.cpp unit
#
# Units restart automatically; `loginctl enable-linger $USER` keeps them
# running when you're logged out (needed for a 24/7 stream).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$REPO/venv/bin/python" ]]; then DEFAULT_PY="$REPO/venv/bin/python"; else DEFAULT_PY="$(command -v python3)"; fi
PYTHON="${PYTHON:-$DEFAULT_PY}"
DEST="$HOME/.config/systemd/user"
mkdir -p "$DEST"

[[ -f "$REPO/.env" ]] || { echo "Create $REPO/.env first (see .env.example)"; exit 1; }

UNITS=(ystreamer-audio ystreamer-engine ystreamer-stream ystreamer-watchdog ystreamer-quality)
[[ "${1:-}" == "--no-llm" ]] || UNITS+=(ystreamer-llm)

for unit in "${UNITS[@]}"; do
    sed -e "s|@REPO@|$REPO|g" -e "s|@PYTHON@|$PYTHON|g" \
        "$REPO/stream/systemd/$unit.service" > "$DEST/$unit.service"
done

systemctl --user daemon-reload
for unit in "${UNITS[@]}"; do
    systemctl --user enable --now "$unit.service"
done
loginctl enable-linger "$USER" 2>/dev/null || echo "(run 'sudo loginctl enable-linger $USER' to keep running after logout)"
systemctl --user --no-pager status "${UNITS[@]/%/.service}" | grep -E "●|Active:" || true
