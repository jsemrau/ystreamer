#!/usr/bin/env bash
# Go live with one command, no systemd:
#
#   stream/live.sh                         # local model performs, jevtypesafe judges
#   stream/live.sh --style darkwave        # extra flags go to orchestrator.py
#   stream/live.sh --judge none            # ...and win over the defaults
#
# Starts the audio sink, the engine (resuming the saved track) and the
# stream (stream/stream.sh: the stream page, captured and sent to YouTube),
# in this terminal. Ctrl-C stops everything. Needs YT_STREAM_KEY (or
# YS_STREAM_URL) in .env. The engine's output is shown here and kept in
# logs/; the stream's output goes to logs/stream.log.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
PY="$REPO/venv/bin/python"; [[ -x "$PY" ]] || PY=python3
mkdir -p logs

"$REPO/stream/setup_audio.sh" || { echo "Couldn't create the audio sink (see above)" >&2; exit 1; }

PIDS=()
stop() {
    echo; echo "[live] stopping..."
    for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
    wait 2>/dev/null
    echo "[live] stopped"
    exit 0
}
trap stop INT TERM

# The engine first: SuperCollider, Tidal and the local model need a moment.
"$PY" orchestrator.py --resume --llm local --judge jevtypesafe "$@" & PIDS+=($!)
sleep 20

echo "[live] starting the stream (log: logs/stream.log)"
"$REPO/stream/stream.sh" >> "$REPO/logs/stream.log" 2>&1 & PIDS+=($!)

# If either stops by itself, stop the other too.
while kill -0 "${PIDS[0]}" 2>/dev/null && kill -0 "${PIDS[1]}" 2>/dev/null; do
    sleep 2
done
echo "[live] the engine or the stream exited; see the output above and logs/stream.log" >&2
stop
