import random

from music import presets as P
from music.plan import (STYLES, arrange, plan_piece, validate_arrangement,
                        validate_piece, Piece)
from music.theory import PROGRESSIONS_BY_NAME, Key


def test_plan_piece_is_valid_and_sections_align_to_period():
    rng = random.Random(4)
    previous = None
    for _ in range(30):
        piece = plan_piece(rng, previous)
        key = Key.parse(piece.key)
        assert key.mode in STYLES[piece.style]["modes"] or previous is not None
        assert key.mode in PROGRESSIONS_BY_NAME[piece.progression].modes
        lo, hi = STYLES[piece.style]["bpm"]
        assert lo <= piece.bpm <= hi
        assert all(s.bars % piece.period == 0 for s in piece.sections)
        minutes = piece.total_bars / piece.cps / 60
        assert 5 <= minutes <= 25, minutes
        if previous is not None:
            assert piece.progression != previous.progression
            shared = len(Key.parse(previous.key).pitch_classes & key.pitch_classes)
            assert shared >= 6, (previous.key, piece.key)
        previous = piece


def test_piece_roundtrip():
    piece = plan_piece(random.Random(1))
    assert Piece.from_dict(piece.to_dict()) == piece


def test_arrangement_respects_energy_and_cap():
    rng = random.Random(2)
    piece = plan_piece(rng)
    for section in piece.sections:
        arr = arrange(piece, section, rng, max_layers=5)
        active = arr.active_roles()
        assert 1 <= len(active) <= 5
        if section.energy < 0.3:
            assert len(active) <= 3
        if section.name == "breakdown":
            assert len(active) <= 2


def test_validate_piece_rejects_nonsense():
    fallback = plan_piece(random.Random(3), style="lofi")
    data = {"title": "x" * 200, "style": "death-metal", "key": "H minor", "bpm": 400,
            "progression": "I-V-vi-IV", "sections": "lots"}
    piece, notes = validate_piece(data, fallback)
    assert piece.style == fallback.style
    assert piece.key == fallback.key
    assert piece.bpm <= STYLES[piece.style]["bpm"][1]
    assert notes


def test_validate_piece_accepts_good_plan_and_fixes_progression_mode():
    fallback = plan_piece(random.Random(3), style="lofi")
    data = {"title": "Rain Letters", "style": "ambient", "key": "F lydian", "bpm": 70,
            "progression": "i-VI-III-VII",  # minor progression, invalid for lydian
            "sections": [{"name": "intro", "bars": 16, "energy": 0.2},
                         {"name": "verse", "bars": 33, "energy": 0.5},
                         {"name": "outro", "bars": 16, "energy": 9}]}
    piece, notes = validate_piece(data, fallback)
    assert piece.title == "Rain Letters"
    assert piece.key == "F lydian"
    assert "lydian" in PROGRESSIONS_BY_NAME[piece.progression].modes
    assert all(s.bars % piece.period == 0 for s in piece.sections)
    assert piece.sections[-1].energy == 1.0


def test_validate_arrangement():
    rng = random.Random(5)
    piece = plan_piece(rng, style="lofi")
    fallback = arrange(piece, piece.sections[3], rng)
    data = {"layers": {"pad": "warm_pad", "bass": "dubstep_wobble", "keys": None,
                       "drums": "lofi_kit", "melody": "glass", "arp": "pluck_arp",
                       "texture": "vinyl"}, "melody_character": "lively"}
    arr, notes = validate_arrangement(data, piece, fallback, max_layers=4)
    assert len(arr.active_roles()) <= 4
    assert arr.layers["keys"] is None
    assert arr.melody_character == "lively"
    assert any("dubstep" in n for n in notes)
    for role, name in arr.layers.items():
        assert name is None or name in P.PRESETS[role]


def test_validate_arrangement_never_empty():
    rng = random.Random(6)
    piece = plan_piece(rng)
    fallback = arrange(piece, piece.sections[0], rng)
    arr, _ = validate_arrangement({"layers": {r: None for r in P.ENERGY_ORDER}}, piece, fallback)
    assert arr.active_roles() == ["pad"]
