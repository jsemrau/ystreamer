"""Tabs and scores into melody and harmony statistics (eval/tabs.py,
eval/tab_corpus.py). One made-up darkwave song, written in every format the
readers take: A minor, i-VI-III-VII two bars a chord, the bass playing
post-punk's "0 0 7 0 4 0 2 0" under a strummed triad."""
import io
import struct
import zipfile

import pytest

from eval import tab_corpus
from eval.tabs import load

A_MINOR = [0, 2, 3, 5, 7, 8, 10]
ROOTS = [0, 0, 5, 5, 2, 2, 6, 6] * 2        # i i VI VI III III VII VII, twice: degrees of A minor
FIGURE = [0, 0, 7, 0, 4, 0, 2, 0]


def _pitch(degree, base=33):                 # A1 = 33
    octave, i = divmod(degree, 7)
    return base + 12 * octave + A_MINOR[i]


def _song_notes():
    """(bar, start in quarters within the bar, length, pitch, bass?)."""
    out = []
    for bar, root in enumerate(ROOTS):
        for i, step in enumerate(FIGURE):
            out.append((bar, i * 0.5, 0.5, _pitch(root + step), True))
        for step in (0, 2, 4):
            out.append((bar, 0.0, 4.0, _pitch(root + step, base=57), False))
    return out


def _check(song, timed=True):
    stats = tab_corpus.analyse(song)
    assert stats.key == "A minor" and stats.in_scale == 1.0
    if timed:
        assert stats.loop == "i-VI-III-VII"
        assert stats.bass_figures == ["0 0 7 0 4 0 2 0"] * len(ROOTS)
    return stats


# ------------------------------------------------------------------ the formats

def _gp5(path):
    import guitarpro
    from guitarpro import models as m

    song = m.Song(title="Cold Song", tempo=132)
    guitar = song.tracks[0]
    guitar.name = "Guitar"
    bass = m.Track(song, number=2, name="Bass",
                   strings=[m.GuitarString(n, v) for n, v in ((1, 43), (2, 38), (3, 33), (4, 28))])
    song.tracks.append(bass)
    for bar in range(len(ROOTS)):
        header = song.measureHeaders[0] if not bar else m.MeasureHeader(number=bar + 1, start=960 + bar * 3840)
        if bar:
            song.measureHeaders.append(header)
        for track in song.tracks:
            if len(track.measures) <= bar:
                track.measures.append(m.Measure(track, header))
    for bar, root in enumerate(ROOTS):
        for track, notes in ((bass, [(s, l, p) for b, s, l, p, x in _song_notes() if b == bar and x]),
                             (guitar, [(s, l, p) for b, s, l, p, x in _song_notes() if b == bar and not x])):
            voice = track.measures[bar].voices[0]
            starts = sorted({s for s, _, _ in notes})
            for s in starts:
                here = [(l, p) for s2, l, p in notes if s2 == s]
                beat = m.Beat(voice, duration=m.Duration(value=int(4 / here[0][0])), status=m.BeatStatus.normal)
                used = set()
                for _, p in here:
                    string = min((st for st in track.strings if st.value <= p and st.number not in used),
                                 key=lambda st: p - st.value)
                    used.add(string.number)
                    beat.notes.append(m.Note(beat, value=p - string.value, string=string.number,
                                             type=m.NoteType.normal))
                voice.beats.append(beat)
    guitarpro.write(song, path)


def test_guitar_pro_5(tmp_path):
    path = str(tmp_path / "cold.gp5")
    _gp5(path)
    song = load(path)
    assert song.title == "Cold Song" and song.tempo == 132
    assert [t.bass for t in song.tracks] == [False, True]
    _check(song)


def _gpif():
    """The song as Guitar Pro 6/7 score XML (GPIF), one beat per bass 8th and one chord a bar."""
    notes, beats, rhythms, voices, bars, masterbars = [], [], [], [], [], []
    rhythms = ['<Rhythm id="0"><NoteValue>Eighth</NoteValue></Rhythm>',
               '<Rhythm id="1"><NoteValue>Whole</NoteValue></Rhythm>']

    def note(pitch, string, opens):
        notes.append(f'<Note id="{len(notes)}"><Properties><Property name="String"><String>{string}</String>'
                     f'</Property><Property name="Fret"><Fret>{pitch - opens[string]}</Fret></Property>'
                     f'</Properties></Note>')
        return len(notes) - 1

    guitar, bass = [40, 45, 50, 55, 59, 64], [28, 33, 38, 43]
    for bar in range(len(ROOTS)):
        ids = []
        for opens, is_bass in ((guitar, False), (bass, True)):
            mine = [(s, p) for b, s, _, p, x in _song_notes() if b == bar and x == is_bass]
            beat_ids = []
            if is_bass:
                for _, p in mine:
                    string = max(i for i, o in enumerate(opens) if o <= p)
                    beats.append(f'<Beat id="{len(beats)}"><Rhythm ref="0"/><Notes>{note(p, string, opens)}</Notes></Beat>')
                    beat_ids.append(len(beats) - 1)
            else:
                ns = [note(p, i + 2, opens) for i, (_, p) in enumerate(sorted(mine, key=lambda x: x[1]))]
                beats.append(f'<Beat id="{len(beats)}"><Rhythm ref="1"/><Notes>{" ".join(map(str, ns))}</Notes></Beat>')
                beat_ids.append(len(beats) - 1)
            voices.append(f'<Voice id="{len(voices)}"><Beats>{" ".join(map(str, beat_ids))}</Beats></Voice>')
            bars.append(f'<Bar id="{len(bars)}"><Voices>{len(voices) - 1} -1 -1 -1</Voices></Bar>')
            ids.append(len(bars) - 1)
        masterbars.append(f'<MasterBar><Time>4/4</Time><Bars>{ids[0]} {ids[1]}</Bars></MasterBar>')
    tracks = (f'<Track id="0"><Name>Guitar</Name><Properties><Property name="Tuning"><Pitches>'
              f'{" ".join(map(str, guitar))}</Pitches></Property></Properties></Track>'
              f'<Track id="1"><Name>Bass</Name><Properties><Property name="Tuning"><Pitches>'
              f'{" ".join(map(str, bass))}</Pitches></Property></Properties></Track>')
    return (f'<GPIF><Score><Title>Cold Song</Title></Score><MasterTrack><Tracks>0 1</Tracks><Automations>'
            f'<Automation><Type>Tempo</Type><Value>132 2</Value></Automation></Automations></MasterTrack>'
            f'<Tracks>{tracks}</Tracks><MasterBars>{"".join(masterbars)}</MasterBars><Bars>{"".join(bars)}</Bars>'
            f'<Voices>{"".join(voices)}</Voices><Beats>{"".join(beats)}</Beats><Notes>{"".join(notes)}</Notes>'
            f'<Rhythms>{"".join(rhythms)}</Rhythms></GPIF>').encode()


def test_guitar_pro_7(tmp_path):
    path = tmp_path / "cold.gp"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Content/score.gpif", _gpif())
    song = load(str(path))
    assert song.title == "Cold Song" and song.tempo == 132
    _check(song)


def _bcfz(data: bytes) -> bytes:
    """Literal-only BCFZ: a 0 flag, a count of 1-3 bytes (2 bits, low bit first), the bytes."""
    bits = []
    for i in range(0, len(data), 3):
        chunk = data[i:i + 3]
        bits += [0, len(chunk) & 1, len(chunk) >> 1]
        for byte in chunk:
            bits += [(byte >> (7 - k)) & 1 for k in range(8)]
    bits += [0] * (-len(bits) % 8)
    body = bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))
    return b"BCFZ" + struct.pack("<i", len(data)) + body


def _bcfs(name: str, payload: bytes) -> bytes:
    sector = 0x1000
    blocks = [payload[i:i + sector] for i in range(0, len(payload), sector)]
    fs = bytearray(sector)                                   # sector 0: unused
    entry = bytearray(sector)
    struct.pack_into("<i", entry, 0, 2)
    entry[4:4 + len(name)] = name.encode()
    struct.pack_into("<i", entry, 0x8C, len(payload))
    for i in range(len(blocks)):
        struct.pack_into("<i", entry, 0x94 + 4 * i, 2 + i)    # data from sector 2 on
    fs += entry
    for b in blocks:
        fs += b.ljust(sector, b"\0")
    return b"BCFS" + bytes(fs)


def test_guitar_pro_6(tmp_path):
    path = tmp_path / "cold.gpx"
    path.write_bytes(_bcfz(_bcfs("score.gpif", _gpif())))
    _check(load(str(path)))


def test_midi(tmp_path):
    from music21 import instrument, note, stream, tempo

    score = stream.Score()
    for name, inst, is_bass in (("Guitar", instrument.ElectricGuitar(), False),
                                ("Bass", instrument.ElectricBass(), True)):
        part = stream.Part()
        part.partName = name
        part.insert(0, inst)
        if not is_bass:
            part.insert(0, tempo.MetronomeMark(number=132))
        for bar, start, length, pitch, x in _song_notes():
            if x == is_bass:
                n = note.Note(pitch)
                n.quarterLength = length
                part.insert(bar * 4 + start, n)
        score.insert(0, part)
    path = str(tmp_path / "cold.mid")
    score.write("midi", fp=path)
    song = load(path)
    assert sorted(t.bass for t in song.tracks) == [False, True]
    _check(song)


def test_ascii_bass_tab(tmp_path):
    lines = {s: "" for s in "GDAE"}
    for bar, root in enumerate(ROOTS[:4]):
        for step in FIGURE:
            p = _pitch(root + step)
            string = max((s for s, o in zip("EADG", (28, 33, 38, 43)) if o <= p), key="EADG".index)
            for s in lines:
                cell = str(p - dict(zip("EADG", (28, 33, 38, 43)))[s]) if s == string else "-"
                lines[s] += cell.ljust(3, "-")
        for s in lines:
            lines[s] += "|"
    path = tmp_path / "cold.txt"
    path.write_text("Cold Song - bass\n\n" + "\n".join(f"{s}|{lines[s]}" for s in "GDAE") + "\n")
    song = load(str(path))
    assert not song.timed and song.tracks[0].bass
    assert [n.pitch for n in song.tracks[0].notes][:8] == [_pitch(s) for s in FIGURE]
    _check(song, timed=False)


# ------------------------------------------------------------------ the analysis

def test_the_engines_patterns_expand_to_bars():
    assert tab_corpus.expand("0*8") == ["0 0 0 0 0 0 0 0"]
    assert tab_corpus.expand("[0 0 7 0]*4") == [" ".join(["0", "0", "7", "0"] * 4)]
    assert tab_corpus.expand("<[0 0 7 0 0 0 4 0] [0 0 7 0 4 2 0 -3]>") == ["0 0 7 0 0 0 4 0",
                                                                          "0 0 7 0 4 2 0 -3"]


def test_the_corpus_against_darkwave(tmp_path):
    path = str(tmp_path / "cold.gp5")
    _gp5(path)
    c = tab_corpus.corpus([tab_corpus.analyse(load(path))], style="darkwave")
    assert c["loops"] == [("i-VI-III-VII", 1)]
    assert c["engine"]["bass_coverage"] == 1.0          # post-punk's own figure
    assert c["moves"][0][0] == "i -> VI"
    assert "Against the engine" in tab_corpus.report(c, [])


@pytest.mark.parametrize("loop,ok", [("i-VII", True), ("i-VI-III-VII", True), ("i-bII", False)])
def test_a_two_chord_loop_is_the_engines_four(loop, ok):
    assert tab_corpus.in_library(loop, {"i-VII-i-VII", "i-VI-III-VII"}) is ok


def test_modes_are_told_apart():
    from eval.tabs import Note, Song, Track

    def song(steps):
        return Song("x", tracks=[Track("lead", False, [Note(i, 1, 57 + s) for i, s in enumerate(steps * 4)])])

    assert tab_corpus.find_key(song([0, 2, 3, 5, 7, 9, 10, 0, 7, 0]))[:2] == (9, "dorian")
    assert tab_corpus.find_key(song([0, 1, 3, 5, 7, 8, 10, 0, 7, 0]))[:2] == (9, "phrygian")
