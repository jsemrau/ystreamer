#!/usr/bin/env bash
# Record the show to a video file to upload later, instead of streaming it:
#
#   stream/record.sh                          # one hour, to recordings/show-<date>.mkv
#   stream/record.sh --hours 2.5              # any length, in hours
#   stream/record.sh --out ~/Videos/set.mkv   # another file (.mkv: playable even if cut short)
#   stream/record.sh --hours 1 --style darkwave   # other flags go to orchestrator.py
#
# Runs stream/live.sh with the stream going to the file, and stops everything
# when the recording is done (or on Ctrl-C; what is recorded so far is kept).
# Needs no stream key. YS_VIDEO_BITRATE etc. work as for the stream; the
# default here is 8000k, as only the machine has to keep up, not the upload.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

HOURS=1
OUT=""
ARGS=()
while (($#)); do
    case "$1" in
        --hours) HOURS="${2:-}"; shift 2 ;;
        --hours=*) HOURS="${1#*=}"; shift ;;
        --out) OUT="${2:-}"; shift 2 ;;
        --out=*) OUT="${1#*=}"; shift ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
if ! [[ "$HOURS" =~ ^[0-9]*\.?[0-9]+$ ]] || awk -v h="$HOURS" 'BEGIN { exit !(h <= 0) }'; then
    echo "--hours needs a number above 0, e.g. --hours 1 or --hours 2.5 (got '$HOURS')" >&2
    exit 1
fi
SECONDS_TOTAL=$(awk -v h="$HOURS" 'BEGIN { printf "%d", h * 3600 + 0.5 }')
[[ -n "$OUT" ]] || OUT="$REPO/recordings/show-$(date +%F-%H%M).mkv"
OUT="$(realpath -m "$OUT")"
mkdir -p "$(dirname "$OUT")"

case "${OUT,,}" in
    *.mkv) FORMAT=matroska ;;
    *.mp4|*.mov) FORMAT=mp4 ;;
    *.flv) FORMAT=flv ;;
    *) echo "Use a .mkv (recommended), .mp4 or .flv file for --out" >&2; exit 1 ;;
esac

echo "[record] recording ${HOURS} h to $OUT"
# Ctrl-C reaches live.sh too, which stops everything; wait for it here.
trap '' INT
YS_STREAM_URL="$OUT" YS_STREAM_FORMAT="$FORMAT" \
    YS_STREAM_SEGMENT_SECONDS="$SECONDS_TOTAL" YS_STREAM_ONCE=1 \
    YS_VIDEO_BITRATE="${YS_VIDEO_BITRATE:-8000k}" \
    "$REPO/stream/live.sh" ${ARGS[@]+"${ARGS[@]}"}

if [[ -s "$OUT" ]]; then
    echo "[record] done: $OUT ($(du -h "$OUT" | cut -f1))"
else
    echo "[record] nothing was recorded; the last lines of logs/stream.log:" >&2
    tail -n 8 "$REPO/logs/stream.log" | sed 's/^/[record]   /' >&2
    exit 1
fi
