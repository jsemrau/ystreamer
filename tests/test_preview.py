import shutil

import pytest

from eval.analyze import analyze
from eval.preview import events_for_cycle, parse_statement, preview_piece, render, write_wav

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def test_parse_and_events():
    p = parse_statement('jumpMod 3 8 $ (slow 2 $ note "<[0,4,7] [2,5,9]>" # s "ys_pad" # legato 1.05 # gain 0.9) # orbit 2')
    assert p["slow"] == 2 and p["entries"] == ["[0,4,7]", "[2,5,9]"]
    assert events_for_cycle(p, 0) == [(0.0, 2.0, [0, 4, 7])]
    assert events_for_cycle(p, 2) == [(0.0, 2.0, [2, 5, 9])]
    m = parse_statement('d5 $ (note "<[0 _ ~ 4 ~ ~ 7 _]>" # s "ys_keys") # orbit 4')
    assert events_for_cycle(m, 0) == [(0.0, 0.25, [0]), (0.375, 0.125, [4]), (0.75, 0.25, [7])]


@pytest.mark.parametrize("style", ["lofi", "ambient", "downtempo", "piano"])
def test_rendered_pieces_are_in_key_end_to_end(tmp_path, style):
    piece, sections = preview_piece(style, seed=11)
    path = tmp_path / "p.wav"
    write_wav(str(path), render(sections))
    metrics = analyze(str(path), piece.key)
    assert metrics["key_conformance"] > 0.9, (piece.key, metrics["key_conformance"])
