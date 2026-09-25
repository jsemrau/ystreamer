import pytest

from music.theory import (PROGRESSIONS, Key, diatonic_chord, progression_chords, progressions_for,
                          voice_chords)


@pytest.mark.parametrize("text,root,mode", [
    ("C minor", 0, "minor"), ("f# dorian", 6, "dorian"), ("Bb major", 10, "major"),
    ("Am", 9, "minor"), ("Eb", 3, "major"), ("ef lydian", 3, "lydian"),
])
def test_key_parse(text, root, mode):
    key = Key.parse(text)
    assert (key.root, key.mode) == (root, mode)


def test_key_parse_rejects_garbage():
    with pytest.raises(ValueError):
        Key.parse("H minor")
    with pytest.raises(ValueError):
        Key.parse("C bebop")


def test_c_minor_scale_degrees():
    key = Key.parse("C minor")
    assert [key.degree_pitch(d) for d in range(8)] == [0, 2, 3, 5, 7, 8, 10, 12]
    assert key.degree_pitch(-1) == -2


def test_diatonic_qualities_in_c_major():
    key = Key.parse("C major")
    qualities = [diatonic_chord(key, d).quality for d in range(7)]
    assert qualities == ["maj", "min", "min", "maj", "maj", "min", "dim"]


def test_no_progression_uses_a_diminished_chord():
    for prog in PROGRESSIONS:
        assert len(prog.degrees) == 4, prog.name
        for mode in prog.modes:
            for root in range(12):
                for chord in progression_chords(Key(root, mode), prog):
                    assert chord.quality in ("maj", "min"), (prog.name, mode, chord)


def test_every_mode_has_progressions():
    for mode in ("major", "minor", "dorian", "lydian", "mixolydian"):
        assert progressions_for(mode)


def test_voicings_are_in_key_in_range_and_smooth():
    for prog in PROGRESSIONS:
        for mode in prog.modes:
            key = Key(5, mode)
            chords = progression_chords(key, prog)
            voicings = voice_chords(chords, low=-10, high=14)
            for chord, voicing in zip(chords, voicings):
                assert all(-10 <= n <= 14 for n in voicing)
                assert {n % 12 for n in voicing} == chord.pitch_classes
                assert all(key.contains(n) for n in voicing)
            for a, b in zip(voicings, voicings[1:] + voicings[:1]):
                movement = sum(abs(x - y) for x, y in zip(a, b))
                assert movement <= 14, (prog.name, voicings)


def test_neighbour_keys_are_closely_related():
    key = Key.parse("A minor")
    for other in key.neighbours():
        shared = len(key.pitch_classes & other.pitch_classes)
        assert shared >= 6
