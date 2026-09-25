#!/usr/bin/env bash
# Stream the music to YouTube (or any RTMP endpoint) with a visual overlay.
#
# Video = background (image, looping video, or a plain colour) + live
# waveform + "now playing" + the live Tidal code + the spoken narration as a
# subtitle, all re-read every frame
# from runtime/*.txt which the engine rewrites atomically.
# Audio = ystream_sink.monitor -> limiter -> loudness normalised to -14 LUFS.
#
# Required:   YT_STREAM_KEY           (or YS_STREAM_URL for any RTMP/file target)
# Optional:   YS_STREAM_BG            image (.png/.jpg) or video (.mp4) background
#             YS_STREAM_SEGMENT_HOURS reconnect every N hours (default 11; YouTube
#                                     stops archiving very long single sessions)
#             YS_AUDIO_INPUT          ffmpeg audio input args (default: pulse sink monitor)
#             YS_FONT / YS_MONO_FONT  font files for drawtext
#             YS_VIDEO_SIZE, YS_FPS, YS_VIDEO_BITRATE
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env when run by hand (systemd already provides it). Values are read
# literally (no shell evaluation), and variables already set win.
if [[ -f "$REPO/.env" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
        key="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]}"
        value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
        [[ -z "${!key+x}" ]] && export "$key=$value"
    done < "$REPO/.env"
fi
RUNTIME="${YS_RUNTIME_DIR:-$REPO/runtime}"
mkdir -p "$RUNTIME"
[[ -f "$RUNTIME/now_playing.txt" ]] || echo "starting..." > "$RUNTIME/now_playing.txt"
[[ -f "$RUNTIME/code.txt" ]] || echo "" > "$RUNTIME/code.txt"
[[ -f "$RUNTIME/narration.txt" ]] || echo "" > "$RUNTIME/narration.txt"

if [[ -n "${YS_STREAM_URL:-}" ]]; then
    TARGET="$YS_STREAM_URL"
elif [[ -n "${YT_STREAM_KEY:-}" ]]; then
    TARGET="rtmp://a.rtmp.youtube.com/live2/${YT_STREAM_KEY}"
else
    echo "Set YT_STREAM_KEY (or YS_STREAM_URL)" >&2
    exit 1
fi

SIZE="${YS_VIDEO_SIZE:-1280x720}"
W="${SIZE%x*}"; H="${SIZE#*x}"
FPS="${YS_FPS:-30}"
VBITRATE="${YS_VIDEO_BITRATE:-3000k}"
SEGMENT_SECONDS=$(( ${YS_STREAM_SEGMENT_HOURS:-11} * 3600 ))
FONT="${YS_FONT:-/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf}"
MONO="${YS_MONO_FONT:-/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf}"
SINK="${YS_SINK_NAME:-ystream_sink}"

if [[ -n "${YS_AUDIO_INPUT:-}" ]]; then
    read -r -a AUDIO_IN <<< "$YS_AUDIO_INPUT"
else
    AUDIO_IN=(-thread_queue_size 1024 -f pulse -i "$SINK.monitor")
fi

BG="${YS_STREAM_BG:-}"
if [[ -z "$BG" ]]; then
    BG_IN=(-f lavfi -i "color=c=0x14161f:s=${SIZE}:r=${FPS}")
elif [[ "$BG" =~ \.(png|jpg|jpeg)$ ]]; then
    BG_IN=(-loop 1 -framerate "$FPS" -i "$BG")
else
    BG_IN=(-stream_loop -1 -re -i "$BG")
fi

WAVE_H=$(( H / 5 ))
FILTER="\
[0:a]alimiter=limit=0.89:level=disabled,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,asplit=2[aout][awave];\
[awave]showwaves=s=${W}x${WAVE_H}:mode=cline:rate=${FPS}:colors=0xE8DCC8@0.55[wave];\
[1:v]scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H},fps=${FPS},format=yuv420p[bg];\
[bg][wave]overlay=0:H-h-10[v1];\
[v1]drawtext=fontfile=${FONT}:textfile=${RUNTIME}/now_playing.txt:reload=1:expansion=none:\
fontsize=$(( H / 24 )):fontcolor=0xF4EEE4:x=40:y=36:shadowcolor=black@0.6:shadowx=2:shadowy=2[v2];\
[v2]drawtext=fontfile=${MONO}:textfile=${RUNTIME}/code.txt:reload=1:expansion=none:\
fontsize=$(( H / 48 )):line_spacing=4:fontcolor=0xCFC6B8@0.75:x=40:y=$(( H / 24 + 60 ))[v3];\
[v3]drawtext=fontfile=${FONT}:textfile=${RUNTIME}/narration.txt:reload=1:expansion=none:\
fontsize=$(( H / 30 )):fontcolor=0xF4EEE4@0.9:x=(w-text_w)/2:y=H-${WAVE_H}-$(( H / 10 )):\
shadowcolor=black@0.7:shadowx=2:shadowy=2[vout]"

run_once() {
    ffmpeg -hide_banner -loglevel warning -nostats \
        "${AUDIO_IN[@]}" "${BG_IN[@]}" \
        -filter_complex "$FILTER" -map "[vout]" -map "[aout]" \
        -c:v libx264 -preset veryfast -tune stillimage -b:v "$VBITRATE" -maxrate "$VBITRATE" \
        -bufsize "$VBITRATE" -g $(( FPS * 2 )) -keyint_min $(( FPS * 2 )) -pix_fmt yuv420p \
        -c:a aac -b:a 160k -ar 48000 -ac 2 \
        -t "$SEGMENT_SECONDS" -f "${YS_STREAM_FORMAT:-flv}" "$TARGET"
}

# Reconnect forever, with backoff on failures. A clean exit (segment ended)
# restarts immediately.
backoff=2
while true; do
    start=$(date +%s)
    run_once
    status=$?
    elapsed=$(( $(date +%s) - start ))
    if [[ -n "${YS_STREAM_ONCE:-}" ]]; then
        exit $status
    fi
    if (( status == 0 || elapsed > 300 )); then
        backoff=2
    else
        echo "ffmpeg exited with $status after ${elapsed}s; retrying in ${backoff}s" >&2
        sleep "$backoff"
        backoff=$(( backoff < 60 ? backoff * 2 : 60 ))
    fi
done
