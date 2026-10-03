import random
from collections import Counter

from music.blocks import LIVE_SOUNDS, SOUND_CHARACTER
from music.director import Director
from music.plan import director_vocabulary, plan_piece, theme_moods


def _plan(style, theme, n=12, seed=0):
    rng, prev, recent, pieces = random.Random(seed), None, [], []
    for _ in range(n):
        p = plan_piece(rng, prev, style=style, theme=theme, recent_sounds=recent[-3:], target_minutes=5)
        pieces.append(p)
        recent.append(dict(p.sounds))
        prev = p
    return pieces


def test_theme_words_become_moods():
    assert theme_moods("Vampyre Nights in London") == {"dark"}
    assert theme_moods("Summer sunrise in Ibiza") >= {"bright"}
    assert theme_moods("Rotterdam warehouse rave") >= {"hard"}
    assert theme_moods("") == set() and theme_moods(None) == set()


def test_every_live_sound_has_a_character():
    assert {s for role in LIVE_SOUNDS.values() for s in role} <= set(SOUND_CHARACTER)


def test_the_theme_picks_the_sounds_inside_the_style():
    dark = Counter(p.sounds["lead"] for seed in range(4) for p in _plan("darkwave", "Vampyre Nights in London", seed=seed))
    bright = Counter(p.sounds["lead"] for seed in range(4) for p in _plan("darkwave", "Summer sunrise in Ibiza", seed=seed))
    gloomy = {s for s, moods in SOUND_CHARACTER.items() if "dark" in moods}
    assert sum(dark[s] for s in gloomy) > 0.7 * sum(dark.values())
    assert sum(bright[s] for s in gloomy) < 0.3 * sum(bright.values())


def test_the_theme_never_puts_another_styles_voice_on_the_chords():
    from music.blocks import subgenre_sounds
    from music.plan import STYLE_SOUNDS

    from music.blocks import SUBGENRES

    own = set(STYLE_SOUNDS["trance"]["chords"]).union(*(subgenre_sounds("trance", n).get("chords", [])
                                                        for n in SUBGENRES["trance"]))
    for p in _plan("trance", "Vampyre Nights in London", n=20):
        assert p.sounds["chords"] in own, (p.subgenre, p.sounds["chords"])


def test_no_track_repeats_a_recent_set_of_sounds():
    for style, theme in (("darkwave", "Vampyre Nights in London"), ("trance", ""), ("hardcore", "rave")):
        sets = [tuple(sorted(p.sounds.items())) for p in _plan(style, theme, n=15)]
        assert not any(sets[i] in sets[max(0, i - 3):i] for i in range(len(sets))), style


def test_the_director_reads_the_sounds_character_and_the_recent_sets():
    assert director_vocabulary("darkwave")["sound_character"]["ys_wavelead"].startswith("dark")
    director = Director(None, random.Random(0), style="darkwave", theme="Vampyre Nights in London", log=lambda *_: None)
    director.recent_sounds.append({"lead": "ys_wavelead", "arp": "ys_wavesynth"})
    previous = _plan("darkwave", "x", n=1)[0]
    prompt = director.piece_prompt(previous, previous, {"progressions": set(), "presets": set()})
    assert "The style is the frame, the theme the details" in prompt
    assert "arp ys_wavesynth, lead ys_wavelead" in prompt and "sound_character" in prompt


def test_the_director_turns_a_repeated_set_into_a_new_one():
    director = Director(None, random.Random(0), style="darkwave", theme="Vampyre Nights in London", log=lambda *_: None)
    fallback = _plan("darkwave", "Vampyre Nights in London", n=1)[0]
    same = {"chords": "ys_coldstrings", "keys": "ys_glass", "lead": "ys_wavelead", "arp": "ys_wavesynth"}
    director.recent_sounds.append(dict(same))
    fallback.sounds = {**same, "lead": "ys_coldguitar"}
    piece, notes = director.review_piece({"sounds": same}, fallback, {"progressions": set(), "presets": set()})
    assert piece.sounds != same and any("repeat" in n for n in notes)


def test_a_halloween_theme_gets_eerie_sounds_dark_keys_and_spooky_titles():
    from music.plan import MOOD_TITLES

    assert "eerie" in theme_moods("Halloween trance party")
    pieces = [p for seed in range(3) for p in _plan("trance", "Halloween trance party", n=10, seed=seed)]
    words = {w for first, second in MOOD_TITLES.values() for w in first + second}
    assert sum(p.title.split()[0] in words for p in pieces) > 0.7 * len(pieces)
    # "party" also asks for euphoric (dorian, major too): two in three dark is right.
    assert sum(p.key.endswith(("phrygian", "minor")) for p in pieces) > 0.6 * len(pieces)
    eerie = {s for s, moods in SOUND_CHARACTER.items() if "eerie" in moods}
    assert sum(p.sounds["lead"] in eerie for p in pieces) > 0.5 * len(pieces)


def test_the_director_keeps_the_chords_in_the_styles_frame():
    director = Director(None, random.Random(0), style="trance", theme="Halloween trance party", log=lambda *_: None)
    fallback = _plan("trance", "Halloween trance party", n=1)[0]
    piece, notes = director.review_piece({"sounds": {"chords": "ys_coldstrings", "lead": "ys_wavelead"}}, fallback,
                                         {"progressions": set(), "presets": set()})
    assert piece.sounds["chords"] != "ys_coldstrings" and piece.sounds["lead"] == "ys_wavelead"
    assert any("chord sounds" in n for n in notes)
