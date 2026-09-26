#!/usr/bin/env bash
# Go live with one command:
#
#   stream/live.sh                         # local model performs, jevtypesafe judges
#   stream/live.sh --style darkwave        # extra flags go to orchestrator.py
#   stream/live.sh --judge none            # ...and win over the defaults
#
# Runs, in this terminal, until Ctrl-C:
#   - the audio sink the stream captures (stream/setup_audio.sh)
#   - the engine (orchestrator.py --resume ...); if it stops or crashes it is
#     restarted and resumes the saved track
#   - the stream (stream/stream.sh: the stream page, captured and sent to
#     YouTube; it reconnects by itself), output in logs/stream.log
#   - the watchdog (stream/watchdog.py): restarts the engine on sustained
#     silence or a stale heartbeat, output in logs/watchdog.log
# Needs YT_STREAM_KEY (or YS_STREAM_URL) in .env.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
PY="$REPO/venv/bin/python"; [[ -x "$PY" ]] || PY=python3
mkdir -p logs runtime
PIDFILE="$REPO/runtime/engine.pid"

"$REPO/stream/setup_audio.sh" || { echo "Couldn't create the audio sink (see above)" >&2; exit 1; }

PIDS=()
STOPPING=0
stop() {
    STOPPING=1
    echo; echo "[live] stopping..."
    [[ -f "$PIDFILE" ]] && kill "$(cat "$PIDFILE")" 2>/dev/null
    for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
    wait 2>/dev/null
    rm -f "$PIDFILE"
    echo "[live] stopped"
    exit 0
}
trap stop INT TERM

# The engine, restarted whenever it exits (the watchdog stops it when the
# stream goes silent or the engine hangs). --resume picks up the saved track.
(
    trap 'exit 0' TERM
    while true; do
        "$PY" orchestrator.py --resume --llm local --judge jevtypesafe "$@" &
        echo $! > "$PIDFILE"
        wait $!
        status=$?
        echo "[live] engine exited ($status); restarting in 5 s" >&2
        sleep 5
    done
) & PIDS+=($!)

# SuperCollider, Tidal and the local model need a moment before the stream.
sleep 20
echo "[live] starting the stream (log: logs/stream.log) and the watchdog (log: logs/watchdog.log)"
"$REPO/stream/stream.sh" >> "$REPO/logs/stream.log" 2>&1 & PIDS+=($!)
YS_ENGINE_PIDFILE="$PIDFILE" "$PY" -m stream.watchdog >> "$REPO/logs/watchdog.log" 2>&1 & PIDS+=($!)

wait
