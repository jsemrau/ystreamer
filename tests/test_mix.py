"""Mix balance and sound variety."""

import math
import os
import random
import re

from music import presets as P
from music.plan import STYLES, arrange, plan_piece, validate_piece
from music.templates import HarmonyContext, render_layer
from music.theory import PROGRESSIONS_BY_NAME, Key, progression_chords

# Single-voice loudness of each synth, measured by rendering the SynthDefs
# offline with scsynth (LUFS): one table, in music/mix.py (eval/synth_levels.sh).
from music.mix import SYNTH_LUFS as MEASURED_LUFS  # noqa: E402


def in_mix(preset):
    return MEASURED_LUFS[preset.synth] + 80 * math.log10(preset.gain)


def test_pads_sit_behind_keys_and_melody():
    pads = [p for p in P.PRESETS["pad"].values() if p.synth in MEASURED_LUFS and p.synth != "ys_air"]
    melodies = [p for p in P.PRESETS["melody"].values() if p.synth in MEASURED_LUFS]
    keys = [p for p in P.PRESETS["keys"].values() if p.synth in MEASURED_LUFS]
    loudest_pad = max(in_mix(p) for p in pads)
    assert loudest_pad < min(in_mix(p) for p in keys) - 2
    assert loudest_pad < min(in_mix(p) for p in melodies) - 5


def test_every_synth_used_by_a_preset_exists_in_the_synthdefs():
    with open(os.path.join(os.path.dirname(__file__), "..", "tidal", "synthdefs.scd")) as f:
        defined = set(re.findall(r"SynthDef\(\\(\w+)", f.read()))
    used = {p.synth for role in P.PRESETS.values() for p in role.values() if p.synth.startswith("ys_")}
    assert used <= defined, used - defined
    assert {"ys_strings", "ys_organ", "ys_choir"} <= defined


def ctx(n_notes_color="sevenths", busy=0):
    key = Key.parse("D minor")
    chords = progression_chords(key, PROGRESSIONS_BY_NAME["i-VI-III-VII"], color=n_notes_color)
    return HarmonyContext(key, chords, 1, seed=2, busy=busy)


def gain_of(expr):
    return float(re.search(r"# gain ([\d.]+)", expr).group(1))


def test_pad_gain_compensates_chord_size_and_busy_mix():
    pad = P.PRESETS["pad"]["warm_pad"]
    sevenths = gain_of(render_layer("pad", ctx("sevenths"), pad, 0.5))
    ninths = gain_of(render_layer("pad", ctx("ninths"), pad, 0.5))
    busy = gain_of(render_layer("pad", ctx("sevenths", busy=5), pad, 0.5))
    assert ninths < sevenths         # 5-note chords are pulled down
    assert busy < sevenths * 0.95    # and the pad backs off in a full mix


def test_pieces_start_differently():
    rng = random.Random(3)
    previous, openings, pads = None, [], []
    for _ in range(12):
        piece = plan_piece(rng, previous, style="lofi")
        openings.append(tuple(piece.opening))
        pads.append(piece.instruments["pad"])
        intro = arrange(piece, piece.sections[0], rng)
        assert set(intro.active_roles()) <= set(piece.opening) | {"pad", "texture"}
        if previous is not None:
            assert tuple(piece.opening) != tuple(previous.opening)
            assert piece.instruments["pad"] != previous.instruments["pad"]
        previous = piece
    assert len(set(openings)) >= 3 and len(set(pads)) >= 3


def test_instruments_come_from_the_style_palette():
    for style, cfg in STYLES.items():
        piece = plan_piece(random.Random(1), style=style)
        for role, name in piece.instruments.items():
            options = cfg.get("palette", {}).get(role) or [cfg["presets"][role]]
            assert name in options


def test_llm_can_choose_instruments_and_opening():
    fallback = plan_piece(random.Random(2), style="lofi")
    piece, notes = validate_piece({"instruments": {"pad": "choir_pad", "keys": "not-a-preset"},
                                   "opening": ["melody", "texture"]}, fallback)
    assert piece.instruments["pad"] == "choir_pad"
    assert piece.opening == ["melody", "texture"]
    assert any("keys" in n for n in notes)


# --------------------------------------------------------------- live mix

def test_live_blocks_enter_at_their_roles_level():
    from music import mix
    from music.blocks import BLOCKS

    for style, blocks in BLOCKS.items():
        for block in blocks:
            if "struct" in block.body:
                continue
            for seed in range(3):
                line = block.make(random.Random(seed))
                synth = mix.synth_of(block.body.replace('"{synth}"', f'"{line.patterns.get("synth", "")}"'))
                gain = float(dict(line.fx).get("gain", 1))
                level = mix.level_db(synth, "{chords}" in block.body, gain) if synth else None
                target = mix.ROLE_TARGET_LUFS.get(block.role)
                if level is None or target is None or synth in mix.UNCALIBRATED or gain >= 1.2:
                    continue
                assert abs(level - target) < 0.5, (style, block.name, synth, level, target)


def test_beds_make_room_when_the_front_is_busy():
    from music.blocks import find_block
    from music.livecode import Edit, Program, Track, apply_edit, changed_statements

    program = Program(Track("t", "piano", "C major", 70, [0, 5, 3, 4], 2))
    for name in ("strings", "piano_chords"):
        b = find_block("piano", name)
        program = apply_edit(program, Edit("add", role=b.role, line=b.make(random.Random(0))))
    assert "|* gain" not in program.statement("chords")
    lead = find_block("piano", "piano_melody")
    fuller = apply_edit(program, Edit("add", role="lead", line=lead.make(random.Random(0))))
    assert fuller.statement("chords").endswith("|* gain 0.93")
    # adding the lead re-sends the strings at their new level
    assert fuller.statement("chords") in changed_statements(program, fuller)


def test_lines_fade_in_and_out():
    from music.live_engine import LiveEngine
    from music.performer import Performer

    class Clock:
        t = 0.0

        def now(self):
            return self.t

        def sleep(self, s):
            self.t += s

    sent = []
    clock = Clock()
    engine = LiveEngine(lambda lines: sent.extend(lines), Performer(random.Random(3), style="lofi",
                                                                    log=lambda *_: None),
                        log=lambda *_: None, now=clock.now, sleep=clock.sleep, max_tracks=1)
    engine.run()
    fades_in = [s for s in sent if s.startswith("xfadeIn") and not s.endswith("silence")]
    fades_out = [s for s in sent if s.startswith("xfadeIn") and s.endswith("silence")]
    assert fades_in and fades_out
    assert all(re.match(r"xfadeIn (\d) 4 \$ \(.*\) \|< orbit (\d)$", s) for s in fades_in)
    for s in fades_in:
        ch, orbit = re.match(r"xfadeIn (\d) .* orbit (\d)$", s).groups()
        assert int(orbit) == int(ch) - 1
