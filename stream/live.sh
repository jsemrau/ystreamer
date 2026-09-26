#!/usr/bin/env bash
# Go live with one command:
#
#   stream/live.sh                         # local model performs, jevtypesafe judges
#   stream/live.sh --style darkwave        # extra flags go to orchestrator.py
#   stream/live.sh --judge none            # ...and win over the defaults
#
# Runs, in this terminal, until Ctrl-C:
#   - the stream page (stream/ui_server.py) on http://127.0.0.1:8765, to
#     preview what the stream shows, even before YouTube is set up
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

# The stream page, served from the start: open it in a browser to preview
# the stream (it shows the video only; the sound goes into the stream).
UI_PORT="${YS_UI_PORT:-8765}"
"$PY" -m stream.ui_server >> "$REPO/logs/ui.log" 2>&1 & PIDS+=($!)
echo "[live] stream page: http://127.0.0.1:${UI_PORT}  (preview in a browser; log: logs/ui.log)"
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
"$REPO/stream/stream.sh" >> "$REPO/logs/stream.log" 2>&1 & STREAM_PID=$!; PIDS+=($STREAM_PID)
# If the stream part stops (no stream key, missing Chromium/Xvfb, ...), say
# so here instead of only in its log.
(
    sleep 15
    while kill -0 "$STREAM_PID" 2>/dev/null; do sleep 5; done
    [[ "$STOPPING" == 1 ]] && exit 0
    echo "[live] the stream stopped; the last lines of logs/stream.log:" >&2
    tail -n 8 "$REPO/logs/stream.log" | sed 's/^/[live]   /' >&2
) & PIDS+=($!)
YS_ENGINE_PIDFILE="$PIDFILE" "$PY" -m stream.watchdog >> "$REPO/logs/watchdog.log" 2>&1 & PIDS+=($!)

wait
