"""
Read tabs and scores into one simple form: per track, its notes as (start,
length in quarter notes, MIDI pitch), and whether it is the bass.

    Guitar Pro 3-5 (.gp3 .gp4 .gp5)   PyGuitarPro
    Guitar Pro 6 (.gpx)               its BCFZ-compressed file system, then the score XML (GPIF)
    Guitar Pro 7/8 (.gp)              a zip holding the same score XML
    ASCII tabs (.txt .tab)            string lines ("e|--0--3--|"): order of notes, no rhythm
    MIDI, MusicXML (.mid .midi .musicxml .mxl .xml)   music21

eval/tab_corpus.py turns these into melody and harmony statistics.
"""

from __future__ import annotations

import io
import os
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

GP5 = (".gp3", ".gp4", ".gp5")
SCORE_XML = (".gpx", ".gp")
ASCII = (".txt", ".tab")
MUSIC21 = (".mid", ".midi", ".musicxml", ".mxl", ".xml")
SUFFIXES = GP5 + SCORE_XML + ASCII + MUSIC21

# The open strings, low to high (MIDI): six-string guitar, four-string bass.
GUITAR_TUNING = [40, 45, 50, 55, 59, 64]
BASS_TUNING = [28, 33, 38, 43]
_BASS_RE = re.compile(r"\bbass|\bbajo|\bbasse", re.I)
_DRUM_RE = re.compile(r"drum|perc|kit\b", re.I)


@dataclass
class Note:
    start: float      # in quarter notes from the top
    length: float
    pitch: int        # MIDI


@dataclass
class Track:
    name: str
    bass: bool
    notes: list[Note] = field(default_factory=list)


@dataclass
class Song:
    title: str
    artist: str = ""
    tempo: float = 0.0
    beats_per_bar: float = 4.0       # in quarter notes
    tracks: list[Track] = field(default_factory=list)
    timed: bool = True               # False for ASCII tabs: only the order of notes is known
    path: str = ""
    sections: list[tuple[str, int]] = field(default_factory=list)   # the score's markers: (name, bars)


def _sections(markers: list[str | None]) -> list[tuple[str, int]]:
    """Per-bar markers (None where a bar has none) as (name, bars) sections."""
    out: list[list] = []
    for marker in markers:
        if marker and marker.strip():
            out.append([marker.strip(), 1])
        elif out:
            out[-1][1] += 1
    return [(name, bars) for name, bars in out]


def is_bass(name: str, lowest_open: int | None = None, program: int | None = None) -> bool:
    if _BASS_RE.search(name or ""):
        return True
    if program is not None and 32 <= program <= 39:
        return True
    return lowest_open is not None and lowest_open < 36


def load(path: str) -> Song:
    ext = os.path.splitext(path)[1].lower()
    if ext in GP5:
        song = _load_gp5(path)
    elif ext == ".gpx":
        song = _load_gpif(_gpx_files(open(path, "rb").read())["score.gpif"])
    elif ext == ".gp":
        with zipfile.ZipFile(path) as z:
            song = _load_gpif(z.read("Content/score.gpif"))
    elif ext in ASCII:
        song = _load_ascii(open(path, encoding="utf-8", errors="replace").read())
    elif ext in MUSIC21:
        song = _load_music21(path)
    else:
        raise ValueError(f"can't read {ext} files ({', '.join(SUFFIXES)})")
    song.path = path
    if not song.title:
        song.title = os.path.splitext(os.path.basename(path))[0]
    song.tracks = [t for t in song.tracks if t.notes]
    return song


# ---------------------------------------------------------------- Guitar Pro 3-5

def _load_gp5(path: str) -> Song:
    import guitarpro

    gp = guitarpro.parse(path)
    header = gp.measureHeaders[0].timeSignature if gp.measureHeaders else None
    song = Song(title=gp.title or "", artist=gp.artist or "", tempo=float(gp.tempo or 0),
                beats_per_bar=(header.numerator * 4 / header.denominator.value) if header else 4.0)
    song.sections = _sections([(h.marker.title if h.marker else None) for h in gp.measureHeaders])
    for track in gp.tracks:
        if track.isPercussionTrack:
            continue
        opens = {s.number: s.value for s in track.strings}
        program = getattr(getattr(track, "channel", None), "instrument", None)
        out = Track(track.name or "", is_bass(track.name, min(opens.values(), default=None), program))
        capo = track.offset or 0
        last: dict[int, Note] = {}                 # per string: the note a tie extends
        for measure in track.measures:
            for voice in measure.voices[:1]:       # the first voice: the line itself
                for beat in voice.beats:
                    start, length = beat.start / 960 - 1, beat.duration.time / 960
                    for n in beat.notes:
                        if n.type.name == "tie" and n.string in last:
                            last[n.string].length = start + length - last[n.string].start
                        elif n.type.name == "normal" and n.string in opens:
                            note = Note(start, length, opens[n.string] + n.value + capo)
                            out.notes.append(note)
                            last[n.string] = note
        song.tracks.append(out)
    return song


# ---------------------------------------------------------------- Guitar Pro 6/7 (GPIF)

def _gpx_files(data: bytes) -> dict[str, bytes]:
    """The files in a .gpx: BCFZ (bit-level LZ77) around a BCFS sector file system."""
    if data[:4] == b"BCFZ":
        data = _bcfz(data)
    if data[:4] != b"BCFS":
        raise ValueError("not a Guitar Pro 6 file")
    data, sector, files = data[4:], 0x1000, {}
    offset = sector
    while offset + 3 < len(data):
        if struct.unpack_from("<i", data, offset)[0] == 2:
            name = data[offset + 4:offset + 131].split(b"\0", 1)[0].decode("latin-1")
            size = struct.unpack_from("<i", data, offset + 0x8C)[0]
            blocks, pos = [], offset + 0x94
            while pos + 4 <= len(data):
                block = struct.unpack_from("<i", data, pos)[0]
                if block == 0:
                    break
                blocks.append(data[block * sector:(block + 1) * sector])
                pos += 4
            files[name] = b"".join(blocks)[:size]
        offset += sector
    return files


def _bcfz(data: bytes) -> bytes:
    expected = struct.unpack_from("<i", data, 4)[0]
    src, out, pos = data[8:], bytearray(), 0

    def bits(n: int, reversed_: bool = False) -> int:
        nonlocal pos
        value = 0
        for i in range(n):
            byte = src[pos >> 3] if (pos >> 3) < len(src) else 0
            bit = (byte >> (7 - (pos & 7))) & 1
            pos += 1
            value = value | (bit << i) if reversed_ else (value << 1) | bit
        return value

    while len(out) < expected and (pos >> 3) < len(src):
        if bits(1):
            size_bits = bits(4)
            offset, size = bits(size_bits, True), bits(size_bits, True)
            start = len(out) - offset
            for i in range(min(offset, size)):
                out.append(out[start + i])
        else:
            for _ in range(bits(2, True)):
                out.append(bits(8))
    return bytes(out)


_NOTE_VALUES = {"Whole": 4.0, "Half": 2.0, "Quarter": 1.0, "Eighth": 0.5, "16th": 0.25, "32nd": 0.125,
                "64th": 0.0625, "128th": 0.03125}


def _ids(text: str | None) -> list[str]:
    return (text or "").split()


def _load_gpif(xml: bytes) -> Song:
    root = ET.fromstring(xml)
    by_id = {kind: {e.get("id"): e for e in root.findall(f"{kind}s/{kind}")}
             for kind in ("Track", "Bar", "Voice", "Beat", "Note", "Rhythm")}
    title = (root.findtext("Score/Title") or "").strip()
    artist = (root.findtext("Score/Artist") or "").strip()
    tempo = 0.0
    for auto in root.iter("Automation"):
        if (auto.findtext("Type") or "") == "Tempo":
            tempo = float((auto.findtext("Value") or "0").split()[0])
            break
    order = _ids(root.findtext("MasterTrack/Tracks")) or list(by_id["Track"])
    tracks = []
    for tid in order:
        t = by_id["Track"].get(tid)
        if t is None:
            continue
        pitches = None
        for prop in t.iter("Property"):
            if prop.get("name") == "Tuning" and prop.findtext("Pitches"):
                pitches = [int(p) for p in prop.findtext("Pitches").split()]
                break
        name = (t.findtext("Name") or "").strip()
        drums = _DRUM_RE.search(name) or (t.findtext(".//InstrumentSet/Type") or "") == "drumKit"
        capo = next((int(p.findtext("Fret") or 0) for p in t.iter("Property") if p.get("name") == "CapoFret"), 0)
        tracks.append((tid, name, pitches, capo, bool(drums), is_bass(name, min(pitches) if pitches else None)))
    song = Song(title=title, artist=artist, tempo=tempo)
    masterbars = root.findall("MasterBars/MasterBar")
    if masterbars and "/" in (masterbars[0].findtext("Time") or ""):
        num, den = masterbars[0].findtext("Time").split("/")
        song.beats_per_bar = int(num) * 4 / int(den)
    song.sections = _sections([(mb.findtext("Section/Text") or mb.findtext("Section/Letter") or None)
                               for mb in masterbars])
    outs = [Track(name, bass) for _, name, _, _, _, bass in tracks]
    last: list[dict[int, Note]] = [{} for _ in tracks]
    bar_start = 0.0
    for mb in masterbars:
        num, den = (mb.findtext("Time") or "4/4").split("/")
        bar_len = int(num) * 4 / int(den)
        for i, bar_id in enumerate(_ids(mb.findtext("Bars"))[:len(tracks)]):
            _, _, pitches, capo, drums, _ = tracks[i]
            bar = by_id["Bar"].get(bar_id)
            if drums or bar is None:
                continue
            voice_id = next((v for v in _ids(bar.findtext("Voices")) if v != "-1"), None)
            voice = by_id["Voice"].get(voice_id)
            if voice is None:
                continue
            t = bar_start
            for beat_id in _ids(voice.findtext("Beats")):
                beat = by_id["Beat"].get(beat_id)
                if beat is None:
                    continue
                length = _rhythm(by_id["Rhythm"].get(beat.find("Rhythm").get("ref")
                                                     if beat.find("Rhythm") is not None else ""))
                for note_id in _ids(beat.findtext("Notes")):
                    n = by_id["Note"].get(note_id)
                    if n is None:
                        continue
                    pitch, string = _gpif_pitch(n, pitches, capo)
                    tie = n.find("Tie")
                    if tie is not None and tie.get("destination") == "true" and string in last[i]:
                        last[i][string].length = t + length - last[i][string].start
                    elif pitch is not None:
                        note = Note(t, length, pitch)
                        outs[i].notes.append(note)
                        last[i][string] = note
                t += length
        bar_start += bar_len
    song.tracks = outs
    return song


def _rhythm(r) -> float:
    if r is None:
        return 1.0
    value = _NOTE_VALUES.get((r.findtext("NoteValue") or "Quarter").strip(), 1.0)
    dot = r.find("AugmentationDot")
    if dot is not None:
        value *= {1: 1.5, 2: 1.75}.get(int(dot.get("count", "1")), 1.5)
    tup = r.find("PrimaryTuplet")
    if tup is not None:
        value *= int(tup.get("den", "1")) / int(tup.get("num", "1"))
    return value


def _gpif_pitch(note, pitches: list[int] | None, capo: int) -> tuple[int | None, int]:
    props = {p.get("name"): p for p in note.iter("Property")}
    string = int(props["String"].findtext("String")) if "String" in props else -1
    if "Midi" in props and props["Midi"].findtext("Number"):
        return int(props["Midi"].findtext("Number")), string
    if "Fret" in props and pitches and 0 <= string < len(pitches):
        return pitches[string] + int(props["Fret"].findtext("Fret")) + capo, string
    if "ConcertPitch" in props:
        step = props["ConcertPitch"].find("Pitch")
        if step is not None:
            from music.theory import parse_pitch_class

            pc = parse_pitch_class((step.findtext("Step") or "C") + (step.findtext("Accidental") or "").replace("#", "s"))
            return 12 * (int(step.findtext("Octave") or 4) + 1) + pc, string
    return None, string


# ---------------------------------------------------------------- ASCII tabs

_STRING_LINE = re.compile(r"^\s*([A-Ga-g][#b]?)\s*[|:]?([-0-9][-0-9hpbr/\\~x|()*. ]*)\|?\s*$")


def _load_ascii(text: str) -> Song:
    """String lines grouped in systems, read column by column. A bass system
    has four lines; open-string names give the tuning (standard if unclear).
    No rhythm: every note column is one step (an 8th)."""
    systems, block = [], []
    for line in text.splitlines():
        m = _STRING_LINE.match(line)
        if m and m.group(2).count("-") >= 3:
            block.append((m.group(1), m.group(2)))
        elif block:
            systems.append(block)
            block = []
    if block:
        systems.append(block)
    tracks: dict[bool, Track] = {}
    times: dict[bool, float] = {}
    for system in systems:
        if len(system) not in (4, 5, 6, 7):
            continue
        bass = len(system) in (4, 5)
        tuning = (BASS_TUNING if bass else GUITAR_TUNING)
        if len(system) == 5:
            tuning = [23] + BASS_TUNING
        if len(system) == 7:
            tuning = [35] + GUITAR_TUNING
        opens = list(reversed(tuning))               # tabs list the high string first
        track = tracks.setdefault(bass, Track("bass" if bass else "guitar", bass))
        width = min(len(s) for _, s in system)
        t = times.get(bass, 0.0)
        col = 0
        while col < width:
            hits = []
            for i, (_, line) in enumerate(system):
                m = re.match(r"\d{1,2}", line[col:])
                if m and (col == 0 or not line[col - 1].isdigit()):
                    hits.append((i, int(m.group())))
            if hits:
                for i, fret in hits:
                    if i < len(opens):
                        track.notes.append(Note(t, 0.5, opens[i] + fret))
                t += 0.5
                col += max(len(str(f)) for _, f in hits)
            else:
                col += 1
        times[bass] = t
    return Song(title="", tracks=list(tracks.values()), timed=False)


# ---------------------------------------------------------------- MIDI, MusicXML

def _load_music21(path: str) -> Song:
    from music21 import converter, instrument, meter, tempo as m21tempo

    score = converter.parse(path)
    song = Song(title=(score.metadata.title if score.metadata and score.metadata.title else ""),
                artist=(score.metadata.composer if score.metadata and score.metadata.composer else ""))
    marks = list(score.recurse().getElementsByClass(m21tempo.MetronomeMark))
    if marks and marks[0].number:
        song.tempo = float(marks[0].number)
    sigs = list(score.recurse().getElementsByClass(meter.TimeSignature))
    if sigs:
        song.beats_per_bar = float(sigs[0].barDuration.quarterLength)
    for part in score.parts:
        inst = part.getInstrument(returnDefault=True)
        name = part.partName or (inst.instrumentName if inst else "") or ""
        program = inst.midiProgram if inst is not None else None
        if isinstance(inst, instrument.UnpitchedPercussion) or _DRUM_RE.search(name) or \
                (inst is not None and inst.midiChannel == 9):
            continue
        track = Track(name, False)
        for el in part.flatten().notes:
            for p in getattr(el, "pitches", ()):
                track.notes.append(Note(float(el.offset), float(el.quarterLength), int(p.midi)))
        low = min((n.pitch for n in track.notes), default=60)
        track.bass = is_bass(name, None, program) or (low < 40 and _median(track) < 48)
        song.tracks.append(track)
    return song


def _median(track: Track) -> float:
    pitches = sorted(n.pitch for n in track.notes)
    return pitches[len(pitches) // 2] if pitches else 60
