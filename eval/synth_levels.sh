#!/usr/bin/env bash
# Compile tidal/synthdefs.scd and measure each synth's loudness offline.
#
#   eval/synth_levels.sh
#
# Renders every ys_* SynthDef (one note, and a chord for pads) with scsynth in
# non-realtime mode, then prints integrated loudness (LUFS) and true peak.
# Use it after changing a SynthDef, then update MEASURED_LUFS in
# tests/test_mix.py and the preset gains in music/presets.py.
# Needs sclang + scsynth (SuperCollider) and ffmpeg; SuperDirt is not needed
# (DirtPan is replaced by Pan2 in a temporary copy).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
export QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox

sed -e 's/DirtPan\.ar(\([^,]*\), numChannels, pan, \(env\))/Pan2.ar((\1) * (\2), pan)/' \
    -e 's/var numChannels = ~dirt.numChannels;/var numChannels = 2;/' \
    "$REPO/tidal/synthdefs.scd" > "$OUT/defs.scd"

cat > "$OUT/render.scd" <<SC
(
"$OUT/defs.scd".load;
~jobs = [
  [\ys_pad, [62], 4], [\ys_pad, [50, 57, 62, 65], 4], [\ys_strings, [62], 4], [\ys_organ, [62], 4],
  [\ys_choir, [62], 4], [\ys_air, [62], 4], [\ys_epiano, [62], 1], [\ys_keys, [62], 1],
  [\ys_sub, [38], 1], [\ys_glass, [74], 1], [\ys_pluck, [62], 1], [\ys_vinyl, [60], 4],
  [\ys_supersaw, [69], 0.15], [\ys_supersaw, [57, 60, 64], 2], [\ys_kick, [36], 0.3], [\ys_gabber, [36], 0.3], [\ys_tbass, [33], 0.1],
  [\ys_gatesaw, [57, 60, 64], 3.4], [\ys_acid, [43], 0.08]
];
Routine {
  ~jobs.do { |job|
    var name = job[0], notes = job[1], sus = job[2], cond = Condition.new;
    var desc = SynthDescLib.global.at(name);
    if(desc.isNil) { ("MISSING " ++ name).postln } {
      Score([[0.0, [\d_recv, desc.def.asBytes]]]
        ++ notes.collect { |m, i| [0.01, [\s_new, name, 1000 + i, 0, 0, \freq, m.midicps, \sustain, sus, \pan, 0]] }
        ++ [[sus + 2.5, [\c_set, 0, 0]]]
      ).recordNRT(nil, "$OUT/" ++ name ++ "_x" ++ notes.size ++ ".wav", headerFormat: "wav", sampleFormat: "float",
        options: ServerOptions.new.numOutputBusChannels_(2), duration: sus + 2.5, action: { cond.unhang });
      cond.hang;
    };
  };
  0.exit;
}.play;
)
SC
timeout 300 sclang "$OUT/render.scd" 2>&1 | grep -E "ERROR|MISSING" || true

for f in "$OUT"/*.wav; do
  stats=$(ffmpeg -hide_banner -nostats -i "$f" -af ebur128=peak=true -f null - 2>&1 | sed -n '/Summary:/,$p')
  lufs=$(echo "$stats" | awk '/ I:/ {print $2}')
  peak=$(echo "$stats" | awk '/Peak:/ {print $2}')
  printf "%-16s %7s LUFS   peak %6s dBFS\n" "$(basename "$f" .wav)" "$lufs" "$peak"
done
