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

// Glass/bell lead: sine with a quiet inharmonic partial and slow vibrato.
SynthDef(\ys_glass, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var decay = sustain.max(0.4) * 2;
    var env = EnvGen.ar(Env.perc(0.01, decay, 1, -3), doneAction: 2);
    var vib = SinOsc.kr(5, 0, 0.003, 1);
    var sig = SinOsc.ar(f * vib) + (SinOsc.ar(f * 2.76) * 0.12 * EnvGen.ar(Env.perc(0.005, decay * 0.3)));
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
SynthDef(\ys_supersaw, { |out, sustain = 1, pan, freq = 440, speed = 1|
    var f = freq * speed;
    var len = sustain.max(0.05);
    var env = EnvGen.ar(Env([0, 1, 0.8, 0], [0.005, len * 0.85, 0.08 + (len * 0.15)], [0, -1, -3]), doneAction: 2);
    var detune = [0.98, 0.99, 0.995, 1.0, 1.005, 1.01, 1.02];
    var sig = Mix(LFSaw.ar(f * detune, { Rand(0, 2) } ! 7)) / 7;
    sig = sig + (SinOsc.ar(f * 0.5) * 0.12);
    sig = HPF.ar(sig, 70) * 0.5;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Trance kick: fast downward pitch sweep (800 -> 130 -> 50 Hz) with a little clipping.
SynthDef(\ys_kick, { |out, pan, sustain = 0.3|
    var env = EnvGen.ar(Env.perc(0.001, 0.28, 1, -4), doneAction: 2);
    var pitch = EnvGen.ar(Env([800, 130, 50], [0.005, 0.05], \exp));
    var sig = (SinOsc.ar(pitch) * 1.2).clip2(0.8) * 0.9;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;

// Rolling trance bass: saw + square an octave down, a plucky filter envelope, and a sine body.
SynthDef(\ys_tbass, { |out, sustain = 0.2, pan, freq = 55, speed = 1|
    var f = freq * speed;
    var env = EnvGen.ar(Env.perc(0.003, sustain.max(0.08) * 1.2, 1, -4), doneAction: 2);
    var cut = EnvGen.ar(Env.perc(0.001, 0.12)).linlin(0, 1, f * 2, f * 8).clip(60, 5000);
    var sig = RLPF.ar(Saw.ar(f) + Pulse.ar(f * 0.5, 0.5, 0.5), cut, 0.4) + SinOsc.ar(f, 0, 0.4);
    sig = (sig * 1.5).tanh * 0.8;
    OffsetOut.ar(out, DirtPan.ar(sig, numChannels, pan, env));
}).add;
)
