#!/usr/bin/env bash
# Start ystreamer in the foreground, always on the project venv.
#
#   ./start.sh                  # create the stream sink, play it on your speakers, play forever
#   ./start.sh --llm claude     # any orchestrator.py option is passed through
#   ./start.sh --resume         # continue the saved piece after a crash
#   ./start.sh --no-monitor     # create the sink but don't play it on your speakers
#   ./start.sh --selftest       # no-audio modes (--selftest, --dry-run, --help) skip the sink
#
# The local llama.cpp server is started by the orchestrator when needed.
# For the 24/7 YouTube stream use stream/install_services.sh instead.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$REPO/venv/bin/python"
[[ -x "$PYTHON" ]] || { echo "No venv at $REPO/venv; run ./install.sh first" >&2; exit 1; }

monitor=(--monitor)
audio=1
args=()
for arg in "$@"; do
    case "$arg" in
        --no-monitor) monitor=() ;;
        --selftest|--dry-run|-h|--help) audio=0; args+=("$arg") ;;
        *) args+=("$arg") ;;
    esac
done

if (( audio )); then
    "$REPO/stream/setup_audio.sh" "${monitor[@]}"
fi

cd "$REPO"
exec "$PYTHON" orchestrator.py "${args[@]}"
