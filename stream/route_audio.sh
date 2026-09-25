#!/usr/bin/env bash
# Fallback routing: link SuperCollider's JACK outputs to the stream sink with
# pw-link. Normally SC_JACK_DEFAULT_OUTPUTS (YS_JACK_OUTPUTS) does this at
# boot; run this if `pw-link -l` shows SuperCollider going elsewhere.
set -euo pipefail
SINK="${YS_SINK_NAME:-ystream_sink}"

for i in 1 2 3 4 5 6 7 8 9 10; do
    if pw-link -o | grep -q "^SuperCollider:out_1$"; then
        break
    fi
    sleep 2
done

pw-link "SuperCollider:out_1" "$SINK:playback_FL" 2>/dev/null || true
pw-link "SuperCollider:out_2" "$SINK:playback_FR" 2>/dev/null || true
pw-link -l | grep -A2 "^SuperCollider" || true
