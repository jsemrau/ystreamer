#!/usr/bin/env bash
# Stream the music to YouTube (or any RTMP endpoint) with a visual overlay.
#
# Two looks (YS_STREAM_UI):
#   browser  (default when Xvfb and Chromium are installed) the stream page in
#            stream/ui/: the live code, every agent decision, the narration and a
#            beat-synced visual per style, rendered by Chromium in a virtual
#            display (Xvfb) and captured with ffmpeg x11grab.
#   overlay  the classic look below (ffmpeg only).
#
# Overlay video = background (image, looping video, or a plain colour) + live
# waveform + "now playing" + the live Tidal code + the spoken narration as a
# subtitle, all re-read every frame
# from runtime/*.txt which the engine rewrites atomically.
# Audio = ystream_sink.monitor -> limiter -> loudness normalised to -14 LUFS.
#
# Required:   YT_STREAM_KEY           (or YS_STREAM_URL for any RTMP/file target)
# Optional:   YS_STREAM_BG            background: image (png/jpg/webp), animated gif,
#                                     or video (mp4/mov/webm/mkv, looped, sound ignored)
#             YS_BG_DIM / YS_BG_BLUR  darken (0-1, default 0.35) / blur (pixels) the background
#             YS_STREAM_SEGMENT_HOURS reconnect every N hours (default 11; YouTube
#                                     stops archiving very long single sessions)
#             YS_AUDIO_INPUT          ffmpeg audio input args (default: pulse sink monitor)
#             YS_FONT / YS_MONO_FONT  font files for drawtext
#             YS_VIDEO_SIZE, YS_FPS, YS_VIDEO_BITRATE
#             YS_STREAM_UI            browser | overlay (default: browser if available)
#             YS_CHROMIUM             Chromium/Chrome binary for the browser look
#             YS_UI_PORT              port of the stream page server (default 8765)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env. Values are read
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

SIZE="${YS_VIDEO_SIZE:-1920x1080}"
W="${SIZE%x*}"; H="${SIZE#*x}"
FPS="${YS_FPS:-30}"
VBITRATE="${YS_VIDEO_BITRATE:-4500k}"
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
BG_EXT="${BG##*.}"; BG_EXT="${BG_EXT,,}"   # lower-case, so .JPG from a phone works too
if [[ -n "$BG" && ! -f "$BG" ]]; then
    echo "YS_STREAM_BG not found: $BG (using a plain background)" >&2
    BG=""
fi
if [[ -z "$BG" ]]; then
    BG_IN=(-f lavfi -i "color=c=0x14161f:s=${SIZE}:r=${FPS}")
elif [[ "$BG_EXT" =~ ^(png|jpg|jpeg|webp|bmp)$ ]]; then
    BG_IN=(-loop 1 -framerate "$FPS" -i "$BG")          # still image
elif [[ "$BG_EXT" == "gif" ]]; then
    BG_IN=(-ignore_loop 0 -re -i "$BG")                 # animated GIF, looped
else
    BG_IN=(-stream_loop -1 -re -i "$BG")                # video (mp4, mov, webm, mkv...), looped, its audio ignored
fi

# Keep the overlay readable on busy/bright backgrounds: darken (YS_BG_DIM,
# 0 = off, 1 = black; default 0.35) and optionally blur (YS_BG_BLUR, pixels).
BG_LOOK=""
if [[ -n "$BG" ]]; then
    DIM="${YS_BG_DIM:-0.35}"
    KEEP=$(awk -v d="$DIM" 'BEGIN { k = 1 - d; if (k < 0) k = 0; if (k > 1) k = 1; printf "%.3f", k }')
    BG_LOOK=",colorchannelmixer=rr=${KEEP}:gg=${KEEP}:bb=${KEEP}"
    if [[ "${YS_BG_BLUR:-0}" != "0" ]]; then
        BG_LOOK="${BG_LOOK},boxblur=${YS_BG_BLUR}:1"
    fi
fi

# ---------------------------------------------------------------- browser look
find_chromium() {
    local c
    for c in "${YS_CHROMIUM:-}" chromium chromium-browser google-chrome google-chrome-stable; do
        [[ -n "$c" ]] && command -v "$c" >/dev/null 2>&1 && { command -v "$c"; return 0; }
    done
    for c in "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux/chrome /opt/pw-browsers/chromium-*/chrome-linux/chrome; do
        [[ -x "$c" ]] && { echo "$c"; return 0; }
    done
    return 1
}

UI_MODE="${YS_STREAM_UI:-}"
CHROMIUM="$(find_chromium || true)"
if [[ -z "$UI_MODE" ]]; then
    if [[ -n "$CHROMIUM" ]] && command -v Xvfb >/dev/null 2>&1; then UI_MODE=browser; else UI_MODE=overlay; fi
fi
if [[ "$UI_MODE" == "browser" && ( -z "$CHROMIUM" || -z "$(command -v Xvfb)" ) ]]; then
    echo "YS_STREAM_UI=browser needs Xvfb and Chromium (sudo apt-get install xvfb chromium, or set YS_CHROMIUM);" \
         "using the overlay look" >&2
    UI_MODE=overlay
fi

PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; }
trap cleanup EXIT INT TERM

if [[ "$UI_MODE" == "browser" ]]; then
    UI_PORT="${YS_UI_PORT:-8765}"
    DISPLAY_NUM="${YS_XVFB_DISPLAY:-:99}"
    PY="$REPO/venv/bin/python"; [[ -x "$PY" ]] || PY=python3
    (cd "$REPO" && exec "$PY" -m stream.ui_server) & PIDS+=($!)
    Xvfb "$DISPLAY_NUM" -screen 0 "${W}x${H}x24" -nolisten tcp & PIDS+=($!)
    sleep 1.5
    PROFILE="$(mktemp -d)"
    DISPLAY="$DISPLAY_NUM" "$CHROMIUM" --kiosk --no-first-run --noerrdialogs --disable-infobars \
        --disable-session-crashed-bubble --disable-features=Translate --password-store=basic \
        --no-sandbox --user-data-dir="$PROFILE" --window-position=0,0 --window-size="${W},${H}" \
        --force-device-scale-factor=1 --hide-scrollbars "http://127.0.0.1:${UI_PORT}/" \
        >/dev/null 2>&1 & PIDS+=($!)
    sleep 4
    echo "Streaming the browser look ($CHROMIUM on $DISPLAY_NUM, ${W}x${H})" >&2
    VIDEO_IN=(-thread_queue_size 512 -f x11grab -draw_mouse 0 -video_size "${W}x${H}" -framerate "$FPS"
              -i "${DISPLAY_NUM}.0")
    FILTER="[0:a]alimiter=limit=0.89:level=disabled,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout];\
[1:v]fps=${FPS},format=yuv420p[vout]"
    TUNE=(-tune zerolatency)
else
    VIDEO_IN=("${BG_IN[@]}")
    TUNE=(-tune stillimage)
fi

WAVE_H=$(( H / 5 ))
[[ "$UI_MODE" == "browser" ]] || FILTER="\
[0:a]alimiter=limit=0.89:level=disabled,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,asplit=2[aout][awave];\
[awave]showwaves=s=${W}x${WAVE_H}:mode=cline:rate=${FPS}:colors=0xE8DCC8@0.55[wave];\
[1:v]scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H},fps=${FPS}${BG_LOOK},format=yuv420p[bg];\
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
        "${AUDIO_IN[@]}" "${VIDEO_IN[@]}" \
        -filter_complex "$FILTER" -map "[vout]" -map "[aout]" \
        -c:v libx264 -preset veryfast "${TUNE[@]}" -b:v "$VBITRATE" -maxrate "$VBITRATE" \
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
