#!/usr/bin/env bash
# Create the dedicated PipeWire/PulseAudio sink the stream captures from.
#
#   stream/setup_audio.sh            # create ystream_sink (idempotent)
#   stream/setup_audio.sh --monitor  # also play it on the default speakers
#
# SuperCollider is pointed at this sink via YS_JACK_OUTPUTS (see .env.example),
# so the stream carries only the music, never desktop sounds or notifications.
set -euo pipefail

SINK="${YS_SINK_NAME:-ystream_sink}"

if ! pactl list short sinks | awk '{print $2}' | grep -qx "$SINK"; then
    pactl load-module module-null-sink \
        sink_name="$SINK" \
        rate=48000 channels=2 \
        sink_properties=device.description="$SINK" >/dev/null
    echo "created sink $SINK"
else
    echo "sink $SINK already exists"
fi

if [[ "${1:-}" == "--monitor" ]]; then
    if ! pactl list short modules | grep -q "module-loopback.*source=$SINK.monitor"; then
        pactl load-module module-loopback source="$SINK.monitor" latency_msec=100 >/dev/null
        echo "monitoring $SINK on the default output"
    fi
fi
