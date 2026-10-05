#!/usr/bin/env bash
# Go live with one command:
#
#   stream/live.sh                         # local model performs, jevtypesafe judges
#   stream/live.sh --style darkwave        # extra flags go to orchestrator.py
#   stream/live.sh --judge none            # ...and win over the defaults
#   stream/live.sh --hours 3               # stop after 3 hours, once the song playing then ends
#
# Runs, in this terminal, until Ctrl-C (or --hours):
#   - the stream page (stream/ui_server.py) on http://127.0.0.1:8765, to
#     preview what the stream shows, even before YouTube is set up
#   - the audio sink the stream captures (stream/setup_audio.sh)
#   - the engine (orchestrator.py ...); if it stops or crashes it is
#     restarted with --resume and picks up the track that was playing
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

# --hours N: how long to stream (any number above 0, e.g. 2.5); the rest of
# the flags go to orchestrator.py.
HOURS=""
ARGS=()
while (($#)); do
    case "$1" in
        --hours) HOURS="${2:-}"; shift; (($#)) && shift ;;
        --hours=*) HOURS="${1#*=}"; shift ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
set -- ${ARGS[@]+"${ARGS[@]}"}
if [[ -n "$HOURS" ]] && { ! [[ "$HOURS" =~ ^[0-9]*\.?[0-9]+$ ]] || awk -v h="$HOURS" 'BEGIN { exit !(h <= 0) }'; }; then
    echo "--hours needs a number above 0, e.g. --hours 1 or --hours 2.5 (got '$HOURS')" >&2
    exit 1
fi

"$REPO/stream/setup_audio.sh" || { echo "Couldn't create the audio sink (see above)" >&2; exit 1; }

PIDS=()
STOPPING=0

# The stream page, served from the start: open it in a browser to preview
# the stream (it shows the video only; the sound goes into the stream).
UI_PORT="${YS_UI_PORT:-8765}"
"$PY" -m stream.ui_server >> "$REPO/logs/ui.log" 2>&1 & PIDS+=($!)
echo "[live] stream page: http://127.0.0.1:${UI_PORT}  (what the stream shows, with your 👍/👎 and requests; log: logs/ui.log)"
stop() {
    [[ "$STOPPING" == 1 ]] && return        # a second Ctrl-C while stopping
    STOPPING=1
    trap '' INT TERM
    echo; echo "[live] stopping..."
    # The restart loop first (so it can't start the engine again), then the
    # engine, which shuts down Tidal and SuperCollider, then everything else.
    [[ -n "${LOOP_PID:-}" ]] && kill "$LOOP_PID" 2>/dev/null
    local engine=""
    [[ -f "$PIDFILE" ]] && engine="$(cat "$PIDFILE")" && kill "$engine" 2>/dev/null
    for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
    # Wait for exactly these (a bare `wait` would also wait on the log tail,
    # which only ends when this script does), at most 20 s, then force it.
    local all=("${PIDS[@]}") left
    [[ -n "$engine" ]] && all+=("$engine")
    for _ in $(seq 40); do
        left=()
        for p in "${all[@]}"; do kill -0 "$p" 2>/dev/null && left+=("$p"); done
        ((${#left[@]} == 0)) && break
        sleep 0.5
    done
    if ((${#left[@]})); then
        echo "[live] still running after 20 s, forcing: ${left[*]}"
        kill -9 "${left[@]}" 2>/dev/null
    fi
    rm -f "$PIDFILE"
    echo "[live] stopped"
    exit 0
}
trap stop INT TERM

# The engine, restarted whenever it exits (the watchdog stops it when the
# stream goes silent or the engine hangs). It starts fresh, with your theme;
# a restart adds --resume to pick up the track that was playing.
(
    trap 'exit 0' TERM
    RESUME=()
    while true; do
        "$PY" orchestrator.py ${RESUME[@]+"${RESUME[@]}"} --llm local --judge jevtypesafe "$@" &
        echo $! > "$PIDFILE"
        wait $!
        status=$?
        echo "[live] engine exited ($status); restarting in 5 s, resuming the track" >&2
        RESUME=(--resume)
        sleep 5
    done
) & LOOP_PID=$!; PIDS+=($LOOP_PID)

# SuperCollider, Tidal and the local model need a moment before the stream.
# A recording (stream/record.sh) waits for the first track instead, so the
# file starts with music, not with the model loading.
if [[ -n "${YS_STREAM_ONCE:-}" ]]; then
    echo "[live] the recording starts with the first track..."
    for _ in $(seq 600); do
        [[ "$REPO/runtime/now_playing.txt" -nt "$PIDFILE" ]] && break
        sleep 1
    done
    [[ "$REPO/runtime/now_playing.txt" -nt "$PIDFILE" ]] \
        || echo "[live] no track after 10 minutes; recording anyway" >&2
else
    sleep 20
fi
echo "[live] starting the stream (log: logs/stream.log) and the watchdog (log: logs/watchdog.log)"
"$REPO/stream/stream.sh" >> "$REPO/logs/stream.log" 2>&1 & STREAM_PID=$!; PIDS+=($STREAM_PID)
# The stream's health (keeping up? how much it sends) and warnings, here in
# the terminal; tail stops with this script.
tail -n0 -F --pid=$$ "$REPO/logs/stream.log" 2>/dev/null \
    > >(grep --line-buffered -E '^\[stream\]|^Stream:|exited|Error|error') & PIDS+=($!)
# If the stream part stops (no stream key, missing Chromium/Xvfb, ...), say
# so here instead of only in its log.
(
    sleep 15
    while kill -0 "$STREAM_PID" 2>/dev/null; do sleep 5; done
    [[ "$STOPPING" == 1 ]] && exit 0
    if [[ -n "${YS_STREAM_ONCE:-}" ]]; then
        # Recording (stream/record.sh): the stream ends when the file is done.
        echo "[live] the recording ended; stopping everything" >&2
        kill -TERM $$
        exit 0
    fi
    echo "[live] the stream stopped; the last lines of logs/stream.log:" >&2
    tail -n 8 "$REPO/logs/stream.log" | sed 's/^/[live]   /' >&2
) & PIDS+=($!)
YS_ENGINE_PIDFILE="$PIDFILE" "$PY" -m stream.watchdog >> "$REPO/logs/watchdog.log" 2>&1 & PIDS+=($!)

# --hours: when the time is up, let the song that is playing finish (the
# title in now_playing.txt changes when the next one starts), then stop
# everything; at most 15 minutes more, whatever happens.
if [[ -n "$HOURS" ]]; then
    LIMIT=$(awk -v h="$HOURS" 'BEGIN { printf "%d", h * 3600 + 0.5 }')
    echo "[live] streaming for ${HOURS} h; then the song playing finishes and everything stops"
    (
        title() { sed -n '1s/ *·.*//p' "$REPO/runtime/now_playing.txt" 2>/dev/null; }
        # (A Ctrl-C before then stops the wait too, leaving no sleep behind.)
        trap 'kill "$WAIT_PID" 2>/dev/null; exit 0' TERM
        sleep "$LIMIT" & WAIT_PID=$!; wait "$WAIT_PID"
        playing="$(title)"
        echo "[live] ${HOURS} h are up; stopping after this song (${playing:-?})" >&2
        for _ in $(seq 180); do
            sleep 5
            now="$(title)"
            [[ -n "$now" && "$now" != "$playing" ]] && break
        done
        echo "[live] the song is over: stopping" >&2
        kill -TERM $$
    ) & PIDS+=($!)
fi

wait
