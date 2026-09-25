// SuperDirt boot for ystreamer: server options, SuperDirt, custom SynthDefs
// and a master bus chain (glue compressor + brickwall limiter).
//
// Launched by audio_stack.start_superdirt() as `sclang superdirt_boot.scd`.
// Prints YSTREAMER_SUPERDIRT_READY once everything is up.

(
s.options.numBuffers = 1024 * 256;
s.options.memSize = 8192 * 32;
s.options.maxNodes = 1024 * 32;
s.options.numOutputBusChannels = 2;
s.options.numInputBusChannels = 0;
s.latency = 0.3;

s.waitForBoot {
    var synthdefs = "YS_SYNTHDEFS_FILE".getenv;

    ~dirt = SuperDirt(2, s);
    ~dirt.loadSoundFiles;
    s.sync;
    ~dirt.start(57120, 0 ! 12);

    if(synthdefs.notNil and: { File.exists(synthdefs) }) {
        synthdefs.load;
        "Loaded ystreamer SynthDefs from %".format(synthdefs).postln;
    } {
        "WARNING: ystreamer SynthDefs not found (YS_SYNTHDEFS_FILE=%)".format(synthdefs).postln;
    };
    s.sync;

    // Master chain at the very end of the node tree, after every orbit.
    // Gentle 2:1 glue compression above -12 dBFS, then a limiter at -1 dBFS
    // so stacked layers can never clip the stream.
    SynthDef(\ys_master, {
        var sig = In.ar(0, 2);
        sig = Compander.ar(sig, sig, thresh: 0.25, slopeBelow: 1, slopeAbove: 0.5,
            clampTime: 0.02, relaxTime: 0.25);
        sig = sig * 1.4;  // make-up gain
        sig = Limiter.ar(sig, 0.89, 0.01);
        ReplaceOut.ar(0, sig);
    }).add;
    s.sync;
    ~ysMaster = Synth(\ys_master, target: RootNode(s), addAction: \addToTail);

    "YSTREAMER_SUPERDIRT_READY".postln;
};

// keep the interpreter alive
Routine { inf.do { 1.wait } }.play;
)
