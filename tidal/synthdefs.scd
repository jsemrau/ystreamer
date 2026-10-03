// ystreamer custom SynthDefs for SuperDirt.
//
// SuperDirt's stock synths (supersaw, superzow, superchip, ...) are demo
// sounds, not mix-ready instruments. These are soft, warm voices tuned for
// long-form chill/ambient listening. They follow SuperDirt's conventions:
// SuperDirt passes `freq` (from note/n), `sustain` (from legato x event
// length), `pan` and `speed`; each synth frees itself when its envelope ends.
// Filters (lpf/hpf), reverb (room/size) and delay are applied afterwards by
// SuperDirt's global effects, so they're deliberately not duplicated here.
//
// Loaded by superdirt_boot.scd after ~dirt exists.

(
var numChannels = ~dirt.numChannels;

// Warm pad: five detuned saws + sub sine through a slowly drifting low-pass.
SynthDef(\ys_pad, { |out, sustain = 4, pan, freq = 440, speed = 1|
    var detune = [-0.11, -0.04, 0, 0.05, 0.12];
    var f = freq * speed;
    var env = EnvGen.ar(Env([0, 1, 0.85, 0], [0.3, 0.4, 0.3] * sustain.max(0.5), \sine), doneAction: 2);
    var saws = Mix(LFSaw.ar(f * detune.midiratio, { Rand(0, 2) } ! 5)) / 5;
    var sub = SinOsc.ar(f * 0.5) * 0.3;
    var cutoff = LFTri.kr(Rand(0.05, 0.12), Rand(0, 2)).range(f * 1.5, f * 3.5).clip(200, 3500);
    var sig = RLPF.ar(saws, cutoff, 0.8) + sub;
    sig = LPF.ar(sig, 4500) * 0.28;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Electric piano (Rhodes-ish): phase-modulated sine whose FM index decays,
// a short tine partial and a gentle tremolo.
SynthDef(\ys_epiano, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var decay = sustain.max(0.4) * 1.5;
    var env = EnvGen.ar(Env.perc(0.004, decay, 1, -4), doneAction: 2);
    var modEnv = EnvGen.ar(Env.perc(0.001, 0.7, 1, -6));
    var car = SinOsc.ar(f, SinOsc.ar(f) * (0.4 + (1.6 * modEnv)));
    var tine = SinOsc.ar(f * 4.01) * EnvGen.ar(Env.perc(0.001, 0.12)) * 0.06;
    var sig = (car + tine) * (1 + (SinOsc.kr(4.2) * 0.12)) * 0.4;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Felt piano: a few slightly inharmonic sine partials with per-partial decay
// and a soft, low-passed hammer noise.
SynthDef(\ys_keys, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var decay = sustain.max(0.5) * 2;
    var ratios = [1, 2.001, 3.004, 4.01, 5.02];
    var amps = [1, 0.35, 0.15, 0.06, 0.03];
    var env = EnvGen.ar(Env.perc(0.006, decay, 1, -3), doneAction: 2);
    var partials = Mix.fill(ratios.size, { |i|
        SinOsc.ar(f * ratios[i], Rand(0, 2)) * amps[i]
            * EnvGen.ar(Env.perc(0.006, decay / (i + 1), 1, -4))
    });
    var hammer = LPF.ar(WhiteNoise.ar, 1800) * EnvGen.ar(Env.perc(0.001, 0.03)) * 0.05;
    var sig = LPF.ar(partials + hammer, (f * 6).clip(800, 7000)) * 0.35;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Round sub bass: sine + a touch of 2nd harmonic, softly saturated.
SynthDef(\ys_sub, { |out, sustain = 1, pan, freq = 110, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env.linen(0.015, sustain.max(0.2) * 0.85, 0.15, 1, \sine), doneAction: 2);
    var sig = SinOsc.ar(f) + (SinOsc.ar(f * 2) * 0.12);
    sig = (sig * 1.3).tanh * 0.32;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Soft plucked string (Karplus-Strong), for arps and plucked basses.
SynthDef(\ys_pluck, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = (freq * speed).max(30);
    var decay = sustain.max(0.3) * 3;
    var env = EnvGen.ar(Env.perc(0.001, decay + 0.2, 1, -2), doneAction: 2);
    var exc = LPF.ar(PinkNoise.ar(0.5), 3000);
    var sig = Pluck.ar(exc, Impulse.ar(0), 1 / 30, 1 / f, decay, 0.35);
    // Karplus-Strong output is very quiet; calibrated to the other voices.
    sig = LPF.ar(sig, 4500) * 4;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Glass/bell lead: sine with a quiet partial that rings briefly at each note,
// and a slow vibrato. The partial is the 3rd harmonic (an octave and a fifth
// up), so it's in every chord the note is; it used to be 2.76 (a real bell's
// inharmonic partial), an octave and a tritone up, 42 cents flat: a sour
// "clang" on every note of a lead. `partial` keeps the old one for A/B.
SynthDef(\ys_glass, { |out, sustain = 1, pan, freq = 440, speed = 1, partial = 3|
    var f = freq * speed;
    var decay = sustain.max(0.4) * 2;
    var env = EnvGen.ar(Env.perc(0.01, decay, 1, -3), doneAction: 2);
    var vib = SinOsc.kr(5, 0, 0.003, 1);
    var sig = SinOsc.ar(f * vib) + (SinOsc.ar(f * partial) * 0.12 * EnvGen.ar(Env.perc(0.005, decay * 0.3)));
    sig = sig * 0.3;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Airy drone texture: band-passed pink noise shaped around the note.
SynthDef(\ys_air, { |out, sustain = 8, pan, freq = 220, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env([0, 1, 1, 0], [0.35, 0.3, 0.35] * sustain.max(1), \sine), doneAction: 2);
    var noise = PinkNoise.ar;
    var sig = Mix([1, 2, 3].collect { |h| BPF.ar(noise, (f * h).clip(60, 8000), 0.02) * (1 / h) });
    sig = sig * 2.5 + (SinOsc.ar(f, 0, 0.05));
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Vinyl crackle + hiss bed (no samples needed, so no licensing questions).
SynthDef(\ys_vinyl, { |out, sustain = 8, pan, freq = 440, speed = 1|
    var env = EnvGen.ar(Env([0, 1, 1, 0], [0.1, 0.8, 0.1] * sustain.max(1)), doneAction: 2);
    var crackle = HPF.ar(Dust2.ar(12) * LFNoise1.kr(0.3).range(0.3, 1), 1500) * 0.4;
    var pops = LPF.ar(Dust2.ar(0.8), 900) * 0.5;
    var hiss = LPF.ar(HPF.ar(PinkNoise.ar(0.015), 2000), 9000);
    var sig = (crackle + pops + hiss) * 2;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Soft string ensemble: slow swell, gentle vibrato and a dark, bowed top.
SynthDef(\ys_strings, { |out, sustain = 4, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env([0, 1, 0.9, 0], [0.4, 0.3, 0.3] * sustain.max(0.8), [2, 0, -2]), doneAction: 2);
    var vib = SinOsc.kr(Rand(4.5, 5.5), 0, 0.004, 1) * LFNoise1.kr(0.3, 0.002, 1);
    var detune = [-0.07, 0, 0.06];
    var saws = Mix(Saw.ar(f * vib * detune.midiratio)) / 3;
    var sig = LPF.ar(saws, (f * 4).clip(600, 3000));
    sig = BPeakEQ.ar(sig, 1200, 1, -4) * 0.75;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Soft pad, no saws and no organ: three slightly detuned triangles (odd
// harmonics that fall away fast, so it's round, never buzzy), a sine an
// octave up and one below, and a faint drifting fifth-above shimmer. Slow
// swell, gently low-passed.
SynthDef(\ys_softpad, { |out, sustain = 4, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env([0, 1, 0.85, 0], [0.35, 0.35, 0.3] * sustain.max(0.8), \sine), doneAction: 2);
    var detune = [-0.08, 0, 0.07];
    var tri = Mix(LFTri.ar(f * detune.midiratio, { Rand(0, 4) } ! 3)) / 3;
    var sines = SinOsc.ar(f * 2, 0, 0.16) + SinOsc.ar(f * 0.5, 0, 0.22);
    var shimmer = SinOsc.ar(f * 3.003, 0, 0.04) * LFNoise1.kr(0.2).range(0.2, 1);
    var sig = LPF.ar(tri + sines + shimmer, (f * 3).clip(400, 2400));
    sig = sig * 0.29;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// --- 1990s trance and house (no saws, no organ) ---------------------------

// Juno-style pad: two pulse waves whose widths drift slowly (PWM), detuned
// against each other like the Juno's chorus, through a soft resonant
// low-pass, with a sine sub. The early-90s trance / house pad.
SynthDef(\ys_pulsepad, { |out, sustain = 4, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env([0, 1, 0.85, 0], [0.3, 0.4, 0.3] * sustain.max(0.8), \sine), doneAction: 2);
    var widths = [LFTri.kr(Rand(0.25, 0.4), Rand(0, 2)).range(0.2, 0.5), LFTri.kr(Rand(0.2, 0.35), Rand(0, 2)).range(0.25, 0.5)];
    var osc = Mix(Pulse.ar(f * [-0.07, 0.07].midiratio, widths)) / 2;
    var sig = RLPF.ar(osc, (f * 3).clip(400, 1600), 0.8) + SinOsc.ar(f * 0.5, 0, 0.12);
    sig = LPF.ar(LPF.ar(sig, 1800), 2200) * 0.34;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// M1-style house piano: bright additive partials that die away faster the
// higher they are, slightly stretched like a real string, a second voice a
// few cents off, and a short hammer "ding". The 90s house piano stab.
SynthDef(\ys_housepiano, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = (sustain * 1.5).clip(0.4, 2.5);
    var env = EnvGen.ar(Env([0, 1, 0], [0.002, len + 0.3], [0, -4]), doneAction: 2);
    var ratios = [1, 2, 3, 4.02, 5.04, 6.08], amps = [1, 0.55, 0.32, 0.22, 0.14, 0.09];
    var partials = Mix(ratios.collect { |r, i|
        SinOsc.ar(f * r * [1, 1.0015], 0, amps[i]).sum * EnvGen.ar(Env.perc(0.002, len / (1 + (i * 0.6)), 1, -4))
    });
    var ding = BPF.ar(WhiteNoise.ar, (f * 6).clip(800, 6000), 0.3) * EnvGen.ar(Env.perc(0.001, 0.03)) * 0.3;
    var sig = LPF.ar(LPF.ar(partials + ding, 3500), 4000) * 0.16;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Square lead: two pulse waves a few cents apart with a light vibrato, a
// filter that opens on each note and settles. The 90s trance lead, hollow
// and bright rather than buzzy.
SynthDef(\ys_pulselead, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env.linen(0.01, sustain.max(0.1) * 0.9, 0.15), doneAction: 2);
    var vib = SinOsc.kr(5.2, 0, 0.003, 1);
    var osc = Pulse.ar(f * vib, 0.4) + (Pulse.ar(f * 1.004 * vib, 0.45) * 0.6);
    var cutoff = EnvGen.kr(Env([f * 3, f * 1.4], [0.25], -3)).clip(300, 1800);
    var sig = LPF.ar(LPF.ar(RLPF.ar(osc, cutoff, 0.7), 2000), 2400) * 0.17;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Soft drawbar organ with a slow rotary-speaker shimmer.
SynthDef(\ys_organ, { |out, sustain = 2, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env.linen(0.03, sustain.max(0.3) * 0.9, 0.25, 1, \sine), doneAction: 2);
    var bars = [1, 2, 3, 4] * f;
    var amps = [1, 0.5, 0.2, 0.12];
    var sig = Mix(SinOsc.ar(bars, 0, amps)) / 1.8;
    sig = sig * (1 + (SinOsc.kr(0.8) * 0.15));
    sig = LPF.ar(sig, 3500) * 0.22;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Airy "aah" choir: a saw through vowel formants, slow attack.
SynthDef(\ys_choir, { |out, sustain = 4, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env([0, 1, 0.85, 0], [0.35, 0.35, 0.3] * sustain.max(0.8), \sine), doneAction: 2);
    var vib = SinOsc.kr(5, 0, 0.003, 1);
    var src = Saw.ar(f * vib) + (PinkNoise.ar * 0.05);
    var sig = Mix([[700, 1, 0.12], [1200, 0.5, 0.1], [2600, 0.25, 0.1]].collect { |fm|
        BPF.ar(src, fm[0], fm[2]) * fm[1]
    });
    sig = sig * 1.9;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// --- Trance (after Switch Angel's SuperCollider trance) --------------------

// Supersaw: 7 detuned saws with random phases, plus a sub. Filtering (lpf,
// resonance) comes from SuperDirt so Tidal can sweep it and gate it.
SynthDef(\ys_supersaw, { |out, sustain = 1, pan, freq = 440, speed = 1,
                          duckdepth = 0, duckattack = 0.16, beattime = 0.44|
    var f = freq * speed;
    // Sidechain: duck on every beat (kick) and recover over duckattack seconds.
    // A new note always starts on a beat, so Impulse's first trigger is the kick.
    // (A retriggered EnvGen moves from its current level, so the first stage
    // is a 1 ms drop to the ducked level, then it recovers.)
    var duck = EnvGen.ar(Env([1 - duckdepth.clip(0, 1), 1 - duckdepth.clip(0, 1), 1],
        [0.001, duckattack.max(0.01)], \sin), Impulse.ar(beattime.reciprocal));
    var len = sustain.max(0.05);
    var env = EnvGen.ar(Env([0, 1, 0.8, 0], [0.005, len * 0.85, 0.08 + (len * 0.15)], [0, -1, -3]), doneAction: 2);
    var detune = [0.98, 0.99, 0.995, 1.0, 1.005, 1.01, 1.02];
    var sig = Mix(LFSaw.ar(f * detune, { Rand(0, 2) } ! 7)) / 7;
    sig = sig + (SinOsc.ar(f * 0.5) * 0.12);
    sig = HPF.ar(sig, 70) * 0.5 * duck;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Trance kick: fast downward pitch sweep (800 -> 130 -> 50 Hz) with a little clipping.
SynthDef(\ys_kick, { |out, pan, sustain = 0.3|
    var env = EnvGen.ar(Env.perc(0.001, 0.28, 1, -4), doneAction: 2);
    var pitch = EnvGen.ar(Env([800, 130, 50], [0.005, 0.05], \exp));
    var sig = (SinOsc.ar(pitch) * 1.2).clip2(0.8) * 0.9;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Rolling trance bass: saw + square an octave down under a plucky filter
// envelope, a sine body and a sub an octave below it for weight, all
// saturated so the harmonics carry on small speakers (a phone or laptop
// can't play the fundamental). Notes hold a little past their step, so the
// low end stays continuous between kicks.
SynthDef(\ys_tbass, { |out, sustain = 0.2, pan, freq = 55, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env.perc(0.003, sustain.max(0.12) * 1.4, 1, -3), doneAction: 2);
    var cut = EnvGen.ar(Env.perc(0.001, 0.15)).linlin(0, 1, f * 3, f * 10).clip(80, 6000);
    var top = RLPF.ar(Saw.ar(f) + Pulse.ar(f * 0.5, 0.5, 0.5), cut, 0.35);
    var body = SinOsc.ar(f, 0, 0.55) + SinOsc.ar(f * 0.5, 0, 0.3);
    var sig = ((top + body) * 2.2).tanh * 0.72;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Gabber / hardcore kick: the kick *is* the bass. A fast pitch drop into a
// long, heavily overdriven 50 Hz boom.
SynthDef(\ys_gabber, { |out, pan, sustain = 0.3|
    var len = 0.34;
    var env = EnvGen.ar(Env([0, 1, 0.85, 0], [0.001, len * 0.55, len * 0.45], [0, -1, -4]), doneAction: 2);
    var pitch = EnvGen.ar(Env([1500, 170, 58, 50], [0.004, 0.035, len], \exp));
    var sig = SinOsc.ar(pitch) + SinOsc.ar(pitch * 2, 0, 0.2);
    sig = (sig * 9).tanh;
    sig = LPF.ar(sig + ((sig * 3).distort * 0.25), 7000) * 0.36;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Real trance gate: sustained supersaw chords chopped by an amplitude gate
// inside the synth (not separate notes). Two kinds of gate, both remapped
// to 0..1 so closed really means silent:
//   gatepat 0    - regular 16th chopper: PulseDPW (clean square), mul 2 so it
//                  spans -1..1, then .range(0, 1); LFPulse fallback without
//                  sc3-plugins
//   gatepat 1..3 - stutter gates: an envelope built from a 16-step pattern,
//                  retriggered every bar with Impulse, timeScale = gatetime
// gatetime = one bar in seconds (Tidal sends 1/cps), so the gate locks to the
// tempo; gatedepth 1 = full chop, 0 = no gate.
~ysGatePatterns = [
    "1011010110110101",   // 1: the classic 1 0 1 1 0 1 0 1, twice
    "1110111011101110",   // 2: driving stutter, a gap before each beat
    "1010101110101011"    // 3: off-beat skip with a push into the next bar
];
~ysGateEnv = { |pattern|
    var levels = [0], times = [];
    pattern.do { |step|
        var on = if(step == $1) { 1 } { 0 };
        // per 16th (1/16 of the bar): quick rise, hold, soft fall - no clicks
        levels = levels ++ [on, on, 0];
        times = times ++ [0.004, 0.036, 0.0225];
    };
    Env(levels, times)
};
// The trance supersaw done properly (ys_supersaw, kept for old sets, is a
// raw LFSaw stack: aliasing, unfiltered, evenly detuned, hence "buzzy"):
// seven band-limited saws spread with the JP-8000's detune curve (Adam
// Szabo's measurements), the centre voice loudest, the side voices split
// left and right, a low-pass that opens for a moment at each note, the low
// end left to the bass, a little saturation, and the kick's duck.
// `detune` (0..1) is how wide it spreads: 0.3 is the classic sound.
SynthDef(\ys_hypersaw, { |out, sustain = 0.5, pan, freq = 440, speed = 1, detune = 0.3,
                          duckdepth = 0, duckattack = 0.16, beattime = 0.44|
    var f = freq * speed;
    var duck = EnvGen.ar(Env([1 - duckdepth.clip(0, 1), 1 - duckdepth.clip(0, 1), 1],
        [0.001, duckattack.max(0.01)], \sin), Impulse.ar(beattime.reciprocal));
    var len = sustain.max(0.04);
    var env = EnvGen.ar(Env([0, 1, 0.85, 0], [0.004, len * 0.9, 0.09 + (len * 0.1)], [0, -1, -4]), doneAction: 2);
    var offsets = [-0.11002313, -0.06288439, -0.01952356, 0, 0.01991221, 0.06216538, 0.10745242];
    var amount = (detune.clip(0, 1) ** 1.5) * 0.25;
    var saw = { |fr|
        if(\SawDPW.asClass.notNil) { \SawDPW.asClass.ar(fr, Rand(-1, 1)) } { Saw.ar(fr) }
    };
    var v = offsets.collect { |o| saw.(f * (1 + (o * amount))) };
    var sig = [v[0] + v[2] + v[5] + (v[3] * 0.8), v[1] + v[4] + v[6] + (v[3] * 0.8)] * 0.16;
    var bright = EnvGen.ar(Env([1.7, 1, 0.8], [0.03, len], -3));
    sig = LPF.ar(sig, (6000 * bright).clip(400, 18000));
    sig = HPF.ar(sig, 160);
    sig = (sig * 1.4).tanh * 0.7 * duck;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;
SynthDef(\ys_gatesaw, { |out, sustain = 4, pan, freq = 440, speed = 1,
                         gatetime = 1.74, gatepat = 1, gatedepth = 0.9,
                         duckdepth = 0, duckattack = 0.16, beattime = 0.44|
    var f = freq * speed;
    // Sidechain: duck on every beat (kick) and recover over duckattack seconds.
    // A new note always starts on a beat, so Impulse's first trigger is the kick.
    // (A retriggered EnvGen moves from its current level, so the first stage
    // is a 1 ms drop to the ducked level, then it recovers.)
    var duck = EnvGen.ar(Env([1 - duckdepth.clip(0, 1), 1 - duckdepth.clip(0, 1), 1],
        [0.001, duckattack.max(0.01)], \sin), Impulse.ar(beattime.reciprocal));
    var len = sustain.max(0.1);
    var env = EnvGen.ar(Env([0, 1, 1, 0], [0.01, len, 0.08]), doneAction: 2);
    var detune = [0.98, 0.99, 0.995, 1.0, 1.005, 1.01, 1.02];
    var sig = Mix(LFSaw.ar(f * detune, { Rand(0, 2) } ! 7)) / 7;
    var trig = Impulse.ar(gatetime.reciprocal);
    // PulseDPW (sc3-plugins) has the cleanest square; without it, LFPulse
    // (already 0..1) with a 2 ms lag so the edges don't click.
    var chopper = if(\PulseDPW.asClass.notNil) {
        \PulseDPW.asClass.ar(16 / gatetime, 0.5, 2).range(0, 1)
    } {
        Lag.ar(LFPulse.ar(16 / gatetime, 0, 0.5), 0.002)
    };
    var stutters = ~ysGatePatterns.collect { |p| EnvGen.ar(~ysGateEnv.(p), trig, timeScale: gatetime) };
    var gate = Select.ar(gatepat.clip(0, ~ysGatePatterns.size), [chopper] ++ stutters);
    sig = sig + (SinOsc.ar(f * 0.5) * 0.1);
    sig = HPF.ar(sig, 90) * 0.5 * (1 - (gatedepth.clip(0, 1) * (1 - gate))) * duck;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Acid line, after Switch Angel's Strudel `acidenv`:
//   pat.lpf(100).lpenv(x * 9).lps(.2).lpd(.12)
// A raw saw through a resonant low-pass sitting at 100 Hz; every note sweeps
// the cutoff up by acid * 9 octaves and decays in 0.12 s to a sustain of 0.2.
// `acid` is the slider (0..1): Tidal automates it like a hand on the knob.
SynthDef(\ys_acid, { |out, sustain = 0.1, pan, freq = 110, speed = 1, acid = 0.5, acidres = 0.18,
                        accent = 0, drive = 1|
    // A 303: a saw through a resonant low-pass with a per-note filter
    // envelope. Accented steps (accent 0..1, usually a pattern like
    // "[0 0 1 0]*4") are louder, brighter and more resonant, the way the
    // 303's accent bites; drive adds grit.
    var f = freq * speed;
    var len = sustain.max(0.04);
    var acc = accent.clip(0, 1);
    var env = EnvGen.ar(Env([0, 1, 0.9, 0], [0.002, len, 0.03]), doneAction: 2);
    var fenv = EnvGen.ar(Env([0, 1, 0.2], [0.001, 0.12 - (0.04 * acc)], -4));
    var cutoff = (100 * (2 ** (acid.clip(0, 1) * (9 + (2 * acc)) * fenv))).clip(40, 16000);
    var sig = RLPF.ar(Saw.ar(f), cutoff, (acidres - (0.07 * acc)).clip(0.05, 1));
    sig = (sig * 1.8 * drive.clip(0.5, 4)).tanh * 0.3 * (1 + (0.5 * acc));
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// 909-style kick for trance: a click, a fast pitch drop into a round ~52 Hz
// body that rings for about 0.4 s, lightly saturated.
SynthDef(\ys_kick909, { |out, pan, sustain = 0.4|
    var env = EnvGen.ar(Env([0, 1, 0.55, 0], [0.002, 0.12, 0.3], [0, -3, -4]), doneAction: 2);
    var pitch = EnvGen.ar(Env([260, 90, 52], [0.012, 0.09], \exp));
    var click = HPF.ar(WhiteNoise.ar, 3000) * EnvGen.ar(Env.perc(0.0005, 0.012)) * 0.35;
    var sig = (SinOsc.ar(pitch, 0.5pi) + click) * 1.6;
    sig = sig.tanh * 0.8;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// A riser for the bars before a drop: noise through a band-pass that climbs
// from 300 Hz to 9 kHz over the note (sustain = its length), swelling up.
SynthDef(\ys_riser, { |out, pan, sustain = 8|
    var len = sustain.clip(1, 40);
    var env = EnvGen.ar(Env([0, 0.15, 1, 0], [len * 0.05, len * 0.93, len * 0.02], [2, 3, -4]), doneAction: 2);
    var sweep = EnvGen.ar(Env([300, 9000], [len], \exp));
    var sig = BPF.ar(WhiteNoise.ar + PinkNoise.ar, sweep, 0.35) * 1.5 + (SinOsc.ar(sweep * 0.25) * 0.05);
    sig = sig * 0.35;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// The impact on the downbeat of a drop: a crash-like noise tail and a low boom.
SynthDef(\ys_impact, { |out, pan, sustain = 2|
    var env = EnvGen.ar(Env.perc(0.002, 2.5, 1, -5), doneAction: 2);
    var noise = HPF.ar(WhiteNoise.ar, 4000) * EnvGen.ar(Env.perc(0.001, 1.8, 1, -6)) * 0.5;
    var boom = SinOsc.ar(EnvGen.ar(Env([90, 40], [0.4], \exp))) * EnvGen.ar(Env.perc(0.005, 1.2, 1, -4)) * 0.8;
    var sig = ((noise + boom) * 1.2).tanh * 0.6;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;
// --- darkwave / coldwave / post-punk -------------------------------------
// After She Past Away, Twin Tribes, Linea Aspera, Kirlian Camera: a picked
// bass and a clean guitar through a chorus pedal, a string machine's
// ensemble, a drum machine's gated snare. Chorus here is the effect, not
// detuned oscillators, so nothing turns buzzy.

// Post-punk bass guitar, played with a pick, through a chorus pedal: a
// string whose top falls away after the pick, a short pick click, and two
// copies 6-12 ms late, swept slowly, one each side. It carries the tune.
SynthDef(\ys_chorusbass, { |out, sustain = 0.25, pan, freq = 110, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.12);
    var env = EnvGen.ar(Env([0, 1, 0.75, 0], [0.002, len, 0.09], [0, -2, -4]), doneAction: 2);
    var bright = EnvGen.ar(Env([f * 14, f * 4, f * 2.5], [0.03, len], -4)).clip(120, 6000);
    var str = LPF.ar((Saw.ar(f) * 0.5) + Pulse.ar(f, 0.3, 0.4), bright) + SinOsc.ar(f, 0, 0.55);
    var pick = BPF.ar(WhiteNoise.ar, 2400, 0.6) * EnvGen.ar(Env.perc(0.0005, 0.012)) * 0.6;
    var dry = ((str + pick) * 1.3).tanh;
    var sweep = SinOsc.kr(0.7, Rand(0, 2pi) + [0, pi]).range(0.006, 0.012);
    var wet = DelayC.ar(dry, 0.02, sweep);
    var sig = HPF.ar([dry + (wet[0] * 0.7), dry + (wet[1] * 0.7)], 35) * 0.2;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Clean single-coil guitar through a chorus pedal: a bright plucked string
// (Karplus-Strong), a little presence, and a wide, slow chorus. The jangly
// arpeggio of post-punk.
SynthDef(\ys_coldguitar, { |out, sustain = 0.5, pan, freq = 440, speed = 1|
    var f = (freq * speed).max(40);
    var decay = sustain.max(0.3) * 2.5 + 0.3;
    var env = EnvGen.ar(Env.perc(0.001, decay + 0.2, 1, -3), doneAction: 2);
    var exc = HPF.ar(LPF.ar(WhiteNoise.ar(0.6), 7000), 250);
    var str = Pluck.ar(exc, Impulse.ar(0), 1 / 30, 1 / f, decay, 0.18);
    var dry = BPeakEQ.ar(LPF.ar(HPF.ar(str, 120), 7500), 2600, 1, 5);
    var sweep = SinOsc.kr(0.9, Rand(0, 2pi) + [0, pi]).range(0.005, 0.014);
    var wet = DelayC.ar(dry, 0.02, sweep);
    var sig = ([dry + (wet[0] * 0.8), dry + (wet[1] * 0.8)] * 3).tanh * 0.27;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// String machine (Solina / ARP Omni) without the saws: hollow triangles an
// octave apart through a dark low-pass, and the ensemble, three taps swept
// by a slow and a fast LFO a third of a cycle apart. Slow swell, slow fade.
SynthDef(\ys_coldstrings, { |out, sustain = 4, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env([0, 1, 0.9, 0], [0.3, 0.4, 0.3] * sustain.max(0.8), [2, 0, -3]), doneAction: 2);
    var osc = (LFTri.ar(f) * 0.7) + (LFTri.ar(f * 0.5) * 0.3) + (Pulse.ar(f * 2, 0.5) * 0.1);
    var dry = LPF.ar(LPF.ar(osc, (f * 3).clip(400, 2200)), 2600);
    var phases = [0, 2pi / 3, 4pi / 3] + Rand(0, 2pi);
    var taps = phases.collect { |p|
        DelayC.ar(dry, 0.03, SinOsc.kr(0.55, p, 0.0035, 0.009) + SinOsc.kr(6.1, p, 0.0006))
    };
    var sig = (([taps[0] + (taps[1] * 0.6), taps[2] + (taps[1] * 0.6)] * 0.45) + (dry * 0.3)) * 0.22;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// The drum machine's gated-reverb snare: a tuned body, a noise crack, and
// a bright room that holds for a moment and is cut off dead.
SynthDef(\ys_gatesnare, { |out, pan, sustain = 0.3|
    var hold = 0.2;
    var env = EnvGen.ar(Env([0, 1, 1, 0], [0.0005, hold + 0.03, 0.012]), doneAction: 2);
    var gate = EnvGen.ar(Env([0, 1, 0.6, 0.5, 0], [0.002, 0.03, hold, 0.012], [0, -3, 0, -6]));
    var body = SinOsc.ar(EnvGen.ar(Env([330, 195, 185], [0.015, 0.1], \exp))) * EnvGen.ar(Env.perc(0.001, 0.09)) * 0.8;
    var crack = BPF.ar(WhiteNoise.ar, 3200, 0.9) * EnvGen.ar(Env.perc(0.0008, 0.06)) * 1.2;
    var room = [[0.0123, 0.0171, 0.0237], [0.0139, 0.0193, 0.0261]].collect { |times|
        Mix(CombC.ar(BPF.ar(WhiteNoise.ar, 1900, 1.4) * 0.12, 0.05, times, 0.35))
    };
    var sig = ((body + crack) ! 2) + (LPF.ar(HPF.ar(room, 450), 8000) * gate);
    sig = (sig * 1.4).tanh * 0.48;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;
// Lingering wave lead (Linea Aspera, Kirlian Camera): a soft analog lead that
// swells in, scoops up into the note, sings with a vibrato that fades in and
// rings out long. VarSaws whose width drifts slowly (pulse-width movement
// without the buzz), a sine an octave down, a low-pass that opens with the swell.
SynthDef(\ys_wavelead, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.2);
    var env = EnvGen.ar(Env([0, 1, 0.8, 0], [0.12, len, 1.4], [2, -1, -3]), doneAction: 2);
    var scoop = EnvGen.kr(Env([-0.35, 0], [0.09], 3)).midiratio;
    var vib = 1 + (SinOsc.kr(5.2, Rand(0, 2pi)) * 0.005 * EnvGen.kr(Env([0, 0, 1], [0.35, 0.6])));
    var fr = f * scoop * vib;
    var width = SinOsc.kr(Rand(0.15, 0.3), Rand(0, 2pi)).range(0.25, 0.5);
    var osc = VarSaw.ar(fr, 0, width) + (VarSaw.ar(fr * 1.003, 0.3, width) * 0.6) + SinOsc.ar(fr * 0.5, 0, 0.35);
    var cut = EnvGen.kr(Env([f * 1.2, f * 4, f * 2.5], [0.25, len], [2, -2])).clip(300, 3500);
    var sig = LPF.ar(LPF.ar(osc, cut), 4000) * 0.2;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Wave synth: a soft polysynth note that blooms and rings on for seconds, so
// an arpeggio's notes overlap into a slowly shifting wash; the filter closes
// as it fades, and a slow chorus makes it shimmer.
SynthDef(\ys_wavesynth, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var ring = (sustain * 3).clip(1.2, 4);
    var env = EnvGen.ar(Env([0, 1, 0], [0.02, ring], [0, -4]), doneAction: 2);
    var osc = LFTri.ar(f) + (VarSaw.ar(f * 2.002, 0, 0.4) * 0.25) + SinOsc.ar(f, 0, 0.4);
    var cut = EnvGen.kr(Env([f * 6, f * 1.5], [ring], -3)).clip(250, 5000);
    var dry = LPF.ar(osc, cut);
    var sweep = SinOsc.kr(0.6, Rand(0, 2pi) + [0, pi]).range(0.004, 0.011);
    var wet = DelayC.ar(dry, 0.02, sweep);
    var sig = [dry + (wet[0] * 0.7), dry + (wet[1] * 0.7)] * 0.09;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;
// --- saws, done properly (YS_HYPERSAW) ------------------------------------
// The old saws (ys_supersaw, ys_pad, ys_strings) are raw LFSaw/Saw stacks:
// aliasing, evenly detuned, barely filtered, hence harsh. These use
// band-limited saws (SawDPW, sc3-plugins; plain Saw otherwise), filter them
// well below the harsh range and leave the low end to the bass.

// Saw lead: two band-limited saws 8 cents apart, one each side, and a quiet
// one an octave down; a low-pass that opens on each note and settles (the
// 90s trance and hardcore lead), a short scoop into the pitch, a vibrato that
// fades in on held notes, softly saturated.
SynthDef(\ys_sawlead, { |out, sustain = 0.5, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.08);
    var env = EnvGen.ar(Env([0, 1, 0.8, 0], [0.005, len, 0.25], [0, -1, -4]), doneAction: 2);
    var saw = { |fr| if(\SawDPW.asClass.notNil) { \SawDPW.asClass.ar(fr, Rand(-1, 1)) } { Saw.ar(fr) } };
    var scoop = EnvGen.kr(Env([-0.25, 0], [0.04], 3)).midiratio;
    var vib = 1 + (SinOsc.kr(5.5, Rand(0, 2pi)) * 0.004 * EnvGen.kr(Env([0, 0, 1], [0.25, 0.4])));
    var fr = f * scoop * vib;
    var sub = saw.(fr * 0.5) * 0.25;
    var sig = [saw.(fr * (-0.08).midiratio) + sub, saw.(fr * 0.08.midiratio) + sub];
    var cut = EnvGen.kr(Env([f * 6, f * 2.5], [0.18 + (len * 0.3)], -4)).clip(400, 5000);
    sig = HPF.ar(RLPF.ar(sig, cut, 0.6), 150);
    sig = (sig * 1.2).tanh * 0.18;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Saw pad: seven band-limited saws on the JP-8000's detune curve, spread
// left and right, through a slowly breathing low-pass that stays under
// 2.5 kHz, a slow swell; `detune` (0..1) is how wide it spreads.
SynthDef(\ys_sawpad, { |out, sustain = 4, pan, freq = 440, speed = 1, detune = 0.25|
    var f = freq * speed;
    var env = EnvGen.ar(Env([0, 1, 0.9, 0], [0.35, 0.35, 0.3] * sustain.max(0.8), [2, 0, -3]), doneAction: 2);
    var offsets = [-0.11002313, -0.06288439, -0.01952356, 0, 0.01991221, 0.06216538, 0.10745242];
    var amount = (detune.clip(0, 1) ** 1.5) * 0.25;
    var saw = { |fr| if(\SawDPW.asClass.notNil) { \SawDPW.asClass.ar(fr, Rand(-1, 1)) } { Saw.ar(fr) } };
    var v = offsets.collect { |o| saw.(f * (1 + (o * amount))) };
    var sig = [v[0] + v[2] + v[5] + (v[3] * 0.8), v[1] + v[4] + v[6] + (v[3] * 0.8)] * 0.12;
    var cut = LFTri.kr(Rand(0.05, 0.1), Rand(0, 2)).range(f * 1.8, f * 4.5).clip(500, 2500);
    sig = HPF.ar(LPF.ar(LPF.ar(sig, cut), 4500), 180);
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;
// --- more lead voices: each one a different instrument ---------------------

// Breathy flute (pan flute, shakuhachi: the 90s Ibiza and Enigma lead): a sine
// with quiet 2nd and 3rd harmonics, breath noise at the note, strongest in
// the attack, a soft chiff, a vibrato that comes in late.
SynthDef(\ys_flute, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.12);
    var env = EnvGen.ar(Env([0, 1, 0.85, 0], [0.06, len, 0.3], [2, -1, -3]), doneAction: 2);
    var vib = 1 + (SinOsc.kr(5.5, Rand(0, 2pi)) * 0.006 * EnvGen.kr(Env([0, 0, 1], [0.3, 0.5])));
    var tone = SinOsc.ar(f * vib) + (SinOsc.ar(f * 2 * vib) * 0.18) + (SinOsc.ar(f * 3 * vib) * 0.06);
    var breath = BPF.ar(PinkNoise.ar, f * vib, 0.08) * (0.5 + EnvGen.ar(Env([2.5, 0.6], [0.12], -4)));
    var chiff = BPF.ar(WhiteNoise.ar, (f * 4).clip(500, 8000), 0.5) * EnvGen.ar(Env.perc(0.002, 0.04)) * 0.15;
    var sig = LPF.ar((tone * 0.32) + (breath * 0.5) + chiff, 6000) * 0.5;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Marimba: a struck wooden bar, its partials tuned 1 : 4 : 10 the way a
// marimba's are (so in tune), the high ones dying first, a soft mallet click.
SynthDef(\ys_marimba, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var decay = (sustain * 1.5).clip(0.3, 1.2);
    var env = EnvGen.ar(Env.perc(0.001, decay + 0.1, 1, -4), doneAction: 2);
    var p1 = SinOsc.ar(f) * EnvGen.ar(Env.perc(0.001, decay, 1, -4));
    var p2 = SinOsc.ar(f * 4) * EnvGen.ar(Env.perc(0.001, decay * 0.25, 1, -6)) * 0.35;
    var p3 = SinOsc.ar(f * 10) * EnvGen.ar(Env.perc(0.001, decay * 0.08, 1, -8)) * 0.12;
    var mallet = LPF.ar(WhiteNoise.ar, 3000) * EnvGen.ar(Env.perc(0.0005, 0.008)) * 0.2;
    var sig = (p1 + p2 + p3 + mallet) * 0.35;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Sitar (Goa and psy trance): a plucked string (Karplus-Strong) slid up into
// the note (meend), and the bridge's buzz (jawari): the string folded back on
// itself for bright overtones that ring with it.
SynthDef(\ys_sitar, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = (freq * speed).max(40);
    var decay = (sustain * 2.5).clip(0.6, 3);
    var env = EnvGen.ar(Env.perc(0.001, decay + 0.2, 1, -3), doneAction: 2);
    var slide = EnvGen.kr(Env([-0.6, 0], [0.07], -3)).midiratio;
    var exc = HPF.ar(WhiteNoise.ar(0.7), 400);
    var str = Pluck.ar(exc, Impulse.ar(0), 1 / 30, 1 / (f * slide), decay, 0.1);
    var buzz = (str * 6).fold2(1) * 0.25;
    var sig = LPF.ar(HPF.ar(BPeakEQ.ar(str + buzz, 2500, 1, 6), 150), 8000) * 0.48;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Vocal lead (Delerium, 90s trance vocal pads): a voice singing "aah" that
// closes to "ooh" over the note, from three formants on a saw, breath, and a
// vibrato that comes in late.
SynthDef(\ys_vox, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.15);
    var env = EnvGen.ar(Env([0, 1, 0.8, 0], [0.05, len, 0.25], [2, -1, -3]), doneAction: 2);
    var vib = 1 + (SinOsc.kr(5.2, Rand(0, 2pi)) * 0.005 * EnvGen.kr(Env([0, 0, 1], [0.2, 0.4])));
    var src = Saw.ar(f * vib) + (PinkNoise.ar * 0.03);
    var morph = EnvGen.kr(Env([0, 1], [len], -2));
    var aah = [800, 1150, 2900], ooh = [450, 800, 2830], amps = [1, 0.5, 0.25];
    var sig = Mix.fill(3, { |i| BPF.ar(src, morph.linlin(0, 1, aah[i], ooh[i]), 0.1) * amps[i] });
    sig = (sig * 2.2).tanh * 0.35;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// FM lead (DX7): a sine modulated by one an octave up, the modulation bright
// on the attack and settling (hollow, a little like a clarinet), a vibrato
// that comes in late.
SynthDef(\ys_fmlead, { |out, sustain = 0.5, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.08);
    var env = EnvGen.ar(Env([0, 1, 0.75, 0], [0.004, len, 0.2], [0, -1, -4]), doneAction: 2);
    var index = EnvGen.kr(Env([3.5, 1.2], [0.25 + (len * 0.3)], -4));
    var vib = 1 + (SinOsc.kr(5.8, Rand(0, 2pi)) * 0.004 * EnvGen.kr(Env([0, 0, 1], [0.3, 0.4])));
    var mod = SinOsc.ar(f * 2 * vib) * f * 2 * index;
    var sig = LPF.ar(SinOsc.ar((f * vib) + mod), 6000) * 0.22;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;
// --- 1990s signature sounds (The Prodigy, Chicane, Thievery Corporation,
// Nightmares on Wax, Yonderboi, Massive Attack, Axelle Red) ----------------

// The hoover (Mentasm, The Prodigy, 90s hardcore): five band-limited saws and
// a sub-octave pulse whose width wobbles, swooping up a third and back down
// at each note, through a wide chorus.
SynthDef(\ys_hoover, { |out, sustain = 0.5, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.1);
    var env = EnvGen.ar(Env([0, 1, 0.85, 0], [0.01, len, 0.3], [0, -1, -4]), doneAction: 2);
    var fr = f * EnvGen.kr(Env([0, 3, 0], [0.04, 0.25], [2, -4])).midiratio;
    var saws = [-0.18, -0.07, 0, 0.08, 0.17].collect { |d| Saw.ar(fr * d.midiratio) };
    var dry = (Mix(saws) / 5) + (Pulse.ar(fr * 0.5, SinOsc.kr(3.2).range(0.2, 0.45)) * 0.6);
    var wet = DelayC.ar(dry, 0.03, SinOsc.kr(0.9, [0, pi]).range(0.005, 0.015));
    var sig = HPF.ar(LPF.ar([dry + wet[0], dry + wet[1]], 4500), 120);
    sig = (sig * 1.3).tanh * 0.16;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// M1 organ stab (Robin S, 90s house and rave): drawbars an octave down to
// two up, a key click, a quick decay: a stab, never a drone.
SynthDef(\ys_m1organ, { |out, sustain = 0.3, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.clip(0.08, 1.5);
    var env = EnvGen.ar(Env([0, 1, 0.6, 0], [0.003, len * 0.6, 0.12], [0, -3, -4]), doneAction: 2);
    var ratios = [0.5, 1, 2, 3, 4], amps = [0.5, 1, 0.7, 0.35, 0.25];
    var tone = Mix(ratios.collect { |r, i| SinOsc.ar(f * r, 0, amps[i]) });
    var click = BPF.ar(WhiteNoise.ar, 3000, 0.6) * EnvGen.ar(Env.perc(0.0005, 0.01)) * 0.4;
    var sig = LPF.ar((tone + click) * 0.16, 5000);
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Nylon-string guitar (Chicane's Balearic guitar): a soft, warm pluck
// (Karplus-Strong) and the wooden body's resonances.
SynthDef(\ys_nylon, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = (freq * speed).max(40);
    var decay = (sustain * 3).clip(0.8, 4);
    var env = EnvGen.ar(Env.perc(0.002, decay + 0.2, 1, -3), doneAction: 2);
    var exc = LPF.ar(WhiteNoise.ar(0.8), 1600) * EnvGen.ar(Env.perc(0.002, 0.02));
    var str = Pluck.ar(exc, Impulse.ar(0), 1 / 30, 1 / f, decay, 0.45);
    var body = (BPF.ar(str, 210, 0.6) * 1.5) + BPF.ar(str, 420, 0.8);
    // Soft saturation rounds off the pluck's spike, so fast notes never clip.
    var sig = (LPF.ar(str + body, 5000) * 4).tanh * 0.6;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Melodica (Thievery Corporation's dub, Augustus Pablo): two reeds a hair
// apart, nasal, a breath at the start, a light vibrato.
SynthDef(\ys_melodica, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.1);
    var env = EnvGen.ar(Env([0, 1, 0.9, 0], [0.03, len, 0.12], [2, 0, -3]), doneAction: 2);
    var vib = 1 + (SinOsc.kr(4.5, Rand(0, 2pi)) * 0.003);
    var reed = Pulse.ar(f * vib, 0.35) + (Pulse.ar(f * vib * 1.002, 0.3) * 0.5);
    var nasal = (BPF.ar(reed, 1400, 0.8) * 1.5) + LPF.ar(reed, 900);
    var breath = BPF.ar(PinkNoise.ar, 2500, 0.5) * 0.05 * EnvGen.ar(Env.perc(0.01, 0.2));
    var sig = LPF.ar(nasal + breath, 4500) * 0.22;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Vibraphone (Thievery Corporation, Nightmares on Wax, Yonderboi): metal bars
// tuned 1 : 4 : 10, a long ring, the motor's tremolo, a soft mallet.
SynthDef(\ys_vibes, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var decay = (sustain * 3).clip(0.8, 3.5);
    var env = EnvGen.ar(Env.perc(0.002, decay, 1, -3), doneAction: 2);
    var p2 = SinOsc.ar(f * 4) * 0.3 * EnvGen.ar(Env.perc(0.002, decay * 0.2, 1, -5));
    var p3 = SinOsc.ar(f * 10) * 0.06 * EnvGen.ar(Env.perc(0.001, decay * 0.05, 1, -8));
    var motor = SinOsc.kr(5.5, Rand(0, 2pi)).range(0.65, 1);
    var mallet = LPF.ar(WhiteNoise.ar, 4000) * EnvGen.ar(Env.perc(0.0005, 0.006)) * 0.15;
    var sig = (((SinOsc.ar(f) + p2 + p3) * motor) + mallet) * 0.3;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Muted trumpet (Yonderboi, lounge jazz): a brass tone that brightens as it
// is blown, the lips sliding up into the note, a harmon mute's nasal bite,
// a vibrato that comes in late.
SynthDef(\ys_trumpet, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.1);
    var env = EnvGen.ar(Env([0, 1, 0.8, 0], [0.04, len, 0.15], [2, -1, -3]), doneAction: 2);
    var lip = EnvGen.kr(Env([-0.4, 0], [0.05], -3)).midiratio;
    var vib = 1 + (SinOsc.kr(5, Rand(0, 2pi)) * 0.005 * EnvGen.kr(Env([0, 0, 1], [0.25, 0.4])));
    var bright = EnvGen.kr(Env([f * 1.5, f * 6, f * 4], [0.05, len], [2, -2])).clip(300, 6000);
    var horn = LPF.ar(Saw.ar(f * lip * vib), bright);
    var sig = (((BPF.ar(horn, 1600, 0.5) * 2) + (HPF.ar(horn, 800) * 0.3)) * 0.9).tanh * 0.55;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Harpsichord (Massive Attack's "Teardrop"): two strings plucked by a quill,
// one an octave up (8' and 4'), bright and quick to fade.
SynthDef(\ys_harpsichord, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = (freq * speed).max(40);
    var decay = (sustain * 2).clip(0.5, 2.5);
    var env = EnvGen.ar(Env.perc(0.001, decay + 0.1, 1, -4), doneAction: 2);
    var exc = HPF.ar(WhiteNoise.ar(0.8), 1000) * EnvGen.ar(Env.perc(0.0005, 0.004));
    var s8 = Pluck.ar(exc, Impulse.ar(0), 1 / 30, 1 / f, decay, 0.05);
    var s4 = Pluck.ar(exc, Impulse.ar(0), 1 / 30, 1 / (f * 2), decay * 0.7, 0.05) * 0.5;
    // Soft saturation rounds off the quill's spike, so fast notes never clip.
    var sig = (HPF.ar(LPF.ar(s8 + s4, 9000), 120) * 2.5).tanh * 0.45;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;
// --- voices of 90s trance and rave (Guru Josh, Charly Lownoise, Chicane) ---

// Saxophone (Guru Josh's "Infinity"): a reed blown harder at the start
// (brighter, then settling), the player bending up into each note, the horn's
// formants, breath and a little growl, a vibrato that comes in late.
SynthDef(\ys_sax, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.1);
    var env = EnvGen.ar(Env([0, 1, 0.85, 0], [0.03, len, 0.15], [2, -1, -3]), doneAction: 2);
    var scoop = EnvGen.kr(Env([-0.7, 0], [0.08], -3)).midiratio;
    var vib = 1 + (SinOsc.kr(5.5, Rand(0, 2pi)) * 0.007 * EnvGen.kr(Env([0, 0, 1], [0.25, 0.4])));
    var fr = f * scoop * vib;
    var blow = EnvGen.kr(Env([0.3, 1, 0.75], [0.05, len], [2, -2]));
    var body = LPF.ar(Saw.ar(fr) + (Pulse.ar(fr, 0.4) * 0.5), (f * (2 + (blow * 6))).clip(300, 7000));
    var horn = (BPF.ar(body, 550, 0.6) * 1.2) + (BPF.ar(body, 1500, 0.5) * 0.9) + (BPF.ar(body, 2700, 0.4) * 0.4);
    var growl = 1 + (SinOsc.ar(f * 0.5) * 0.08 * blow);
    var breath = BPF.ar(PinkNoise.ar, 2000, 0.8) * 0.06 * blow;
    var sig = (((horn * growl) + breath + (body * 0.15)) * 1.6).tanh * 0.25;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Angel choir (the ethereal, layered female voices of Celtic and Balearic
// trance): five voices singing a soft open "aah", each a little apart in
// pitch and vibrato, breath in the sound, a slow swell and a long release.
SynthDef(\ys_angelchoir, { |out, sustain = 2, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.3);
    var env = EnvGen.ar(Env([0, 1, 0.9, 0], [0.25, len, 0.9], [2, 0, -3]), doneAction: 2);
    var aah = [850, 1200, 2800], amps = [1, 0.45, 0.2];
    var voices = [-0.12, -0.05, 0, 0.06, 0.11].collect { |det|
        var vib = 1 + (SinOsc.kr(Rand(4.6, 5.6), Rand(0, 2pi)) * 0.004 * EnvGen.kr(Env([0, 0, 1], [0.3, 0.6])));
        var src = Saw.ar(f * det.midiratio * vib) + (PinkNoise.ar * 0.08);
        Mix.fill(3, { |k| BPF.ar(src, aah[k] * Rand(0.97, 1.03), 0.09) * amps[k] })
    };
    var sig = [voices[0] + voices[1] + (voices[2] * 0.7), voices[3] + voices[4] + (voices[2] * 0.7)] * 0.5;
    sig = HPF.ar(LPF.ar(sig, 6000), 200) * 0.67;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// The sped-up vocal of happy hardcore ("Wonderful Days"): a short sung "ya",
// "ee" opening to "aah", with the formants raised the way a voice sped up on
// a sampler sounds (the chipmunk), quick and bright.
SynthDef(\ys_chipvox, { |out, sustain = 0.3, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.clip(0.08, 1.2);
    var env = EnvGen.ar(Env([0, 1, 0.8, 0], [0.01, len, 0.08], [0, -1, -3]), doneAction: 2);
    var src = Saw.ar(f) + (PinkNoise.ar * 0.02);
    var glide = EnvGen.kr(Env([0, 1], [0.06], -2));
    var ee = [270, 2290, 3010] * 1.6, aah = [850, 1200, 2800] * 1.6, amps = [1, 0.5, 0.3];
    var sig = Mix.fill(3, { |k| BPF.ar(src, glide.linlin(0, 1, ee[k], aah[k]), 0.12) * amps[k] });
    sig = (sig * 2.5).tanh * 0.47;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;
)
