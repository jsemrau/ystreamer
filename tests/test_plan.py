import random

from music import presets as P
from music.plan import (STYLES, arrange, plan_piece, validate_arrangement,
                        validate_piece, Piece)
from music.theory import PROGRESSIONS_BY_NAME, Key, progression_named


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
            "progression": "i-VI-III-VII",  # minor numerals: become lydian's own chords on those degrees
            "sections": [{"name": "intro", "bars": 16, "energy": 0.2},
                         {"name": "verse", "bars": 33, "energy": 0.5},
                         {"name": "outro", "bars": 16, "energy": 9}]}
    piece, notes = validate_piece(data, fallback)
    assert piece.title == "Rain Letters"
    assert piece.key == "F lydian"
    assert piece.progression == "I-vi-iii-vii"
    assert progression_named(piece.progression, "lydian") is not None
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


def test_theme_selects_style():
    from music.plan import style_from_theme

    assert style_from_theme("frankfurt deep house") == "deephouse"
    assert style_from_theme("lofi beats to study to") == "lofi"
    assert style_from_theme("ambient music for sleep") == "ambient"
    assert style_from_theme("warehouse party") is None  # "house" only as a word
    assert style_from_theme("rainy evening") is None
    assert style_from_theme("hardcore techno") == "hardcore"
    assert style_from_theme("dark techno in a berlin basement") == "techno"
    assert style_from_theme("rotterdam gabber") == "hardcore"
    assert style_from_theme("cold electro dark wave") == "darkwave"
    assert style_from_theme("berlin coldwave, post-punk night") == "darkwave"


def test_fast_styles_plan_fast_tempos():
    for style, lo in (("techno", 138), ("hardcore", 160)):
        for seed in range(10):
            piece = plan_piece(random.Random(seed), style=style)
            assert lo <= piece.bpm <= STYLES[style]["bpm"][1]


def test_deephouse_starts_with_the_groove_and_is_not_in_the_chill_rotation():
    rng = random.Random(7)
    piece = plan_piece(rng, style="deephouse")
    lo, hi = STYLES["deephouse"]["bpm"]
    assert lo <= piece.bpm <= hi
    intro = arrange(piece, piece.sections[0], rng)
    assert intro.active_roles()[:2] == ["drums", "bass"] or set(intro.active_roles()) >= {"drums"}
    assert intro.layers["drums"] == "house_kit"
    rotating = {plan_piece(random.Random(i)).style for i in range(60)}
    assert "deephouse" not in rotating
