"""
Sampled instruments: real recordings a SynthDef plays by pitch.

    python -m music.instruments              # download and prepare all, then restart the stream
    python -m music.instruments piano sax    # only these

From FreePats (https://freepats.zenvoid.org), the Versilian Studios
Chamber Orchestra 2 (VSCO-2 CE) and the Versilian Community Sample Library
(VCSL), each published under CC0 1.0 (public domain), so the stream may play
them; and voices from Freesound (https://freesound.org), only sounds under
CC0, fetched with FREESOUND_API_KEY (.env). A FreePats set's SFZ map says which recording plays which notes; a
VSCO/VCSL set is a folder of recordings named by note ("..._A#2_v1_rr1.wav"),
each measured for its octave and tuning. One recording per note is kept (a
set's velocity layer chosen below, the first of its round robins), cut to start
just before the attack and levelled to the same loudness in its first half
second (the recordings are up to 10 dB apart). They go to
instruments/<name>/<midi>.wav with meta.scd (each note's tuning in cents and
its loop points). superdirt_boot.scd loads them and the instrument's SynthDef
(tidal/synthdefs.scd, ~ysSampled) plays the nearest note at the pitch asked
for; without them, the modelled SynthDef plays (harp and kalimba, which have
no model, then aren't offered).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import wave
from dataclasses import dataclass

import numpy as np

import config

INSTRUMENTS_DIR = config.INSTRUMENTS_DIR
SR = 44100
# Each note's first half second at this RMS (dBFS): an even touch across the range.
ATTACK_RMS_DB = -20.0
_FP = "https://freepats.zenvoid.org"


@dataclass
class Instrument:
    name: str           # instruments/<name>/
    synth: str          # the SynthDef that plays it
    page: str           # where it's published (CC0)
    url: str            # the SFZ + WAV archive
    about: str
    velocity: int = 64  # which velocity layer to keep
    # A VSCO/VCSL set: its GitHub repo and folder of recordings, the layer to
    # keep (0 the softest .. 1 the loudest), how long a note may ring (s), and
    # how much of its start sets its level (a bowed note swells in slowly).
    repo: str = ""
    folder: str = ""
    layer: float = 0.5
    seconds: float = 6.0
    level_window: float = 0.5
    # A Freesound set: the pack, which of its sounds (a name pattern, a
    # length range), each named with its pitch class ("Note G#", "Near F"),
    # its octave measured.
    pack: int = 0
    include: str = ""
    lengths: tuple[float, float] = (0.0, 60.0)


INSTRUMENTS = {
    "nylon": Instrument("nylon", "ys_nylon", f"{_FP}/Guitar/acoustic-guitar.html",
                        f"{_FP}/Guitar/SpanishClassicalGuitar/SpanishClassicalGuitar-SFZ-20190618.7z",
                        "Spanish classical guitar (2008, AKG Perception 120), 48 notes"),
    "piano": Instrument("piano", "ys_keys", f"{_FP}/Piano/acoustic-grand-piano.html",
                        f"{_FP}/Piano/UprightPianoKW/UprightPianoKW-SFZ-20220221.7z",
                        "Upright Piano KW, the soft layer (the felt piano)", velocity=60),
    "sax": Instrument("sax", "ys_sax", f"{_FP}/Reed/saxophone.html",
                      f"{_FP}/Reed/TenorSaxophone/TenorSaxophone-SFZ-20200717.tar.xz",
                      "Tenor saxophone, the loud layer, looped for long notes", velocity=110),
    "bass": Instrument("bass", "ys_chorusbass", f"{_FP}/ElectricGuitar/clean-electric-bass.html",
                       "https://github.com/freepats/electric-bass-YR/releases/download/2019-09-30/"
                       "PickedBassYR-SFZ+WAV-20190930.7z",
                       "Picked electric bass YR (through the chorus pedal)"),
    "harp": Instrument("harp", "ys_harp", f"{_FP}/OrchestralStrings/harp.html",
                       f"{_FP}/OrchestralStrings/ConcertHarp/ConcertHarp-small-SFZ-20200702.tar.xz",
                       "Concert harp"),
    "kalimba": Instrument("kalimba", "ys_kalimba", f"{_FP}/Ethnic/kalimba.html",
                          f"{_FP}/Ethnic/Kalimba/Kalimba-SFZ-20190723.tar.xz", "Kalimba"),
}


_VSCO, _VCSL = "sgossner/VSCO-2-CE", "sgossner/VCSL"


def _gh(repo: str, folder: str, synth: str, about: str, **kw) -> Instrument:
    return Instrument(folder.split("/")[-2] if folder.count("/") > 1 else folder, synth,
                      f"https://github.com/{repo}", "", about, repo=repo, folder=folder, **kw)


# Recorded instruments from the Versilian libraries (CC0): the ones the
# stream only had as models (the muted trumpet, the vibraphone, the marimba)
# and new voices. Each plays as its synth, like any other sound.
_ST, _WW, _BR = "Strings", "Woodwinds", "Brass"
_ID, _CH, _AE = "Idiophones", "Chordophones", "Aerophones"
for _key, _inst in {
    "grandpiano": _gh(_VCSL, f"{_CH}/Zithers/Grand Piano, Kawai/Sustains", "ys_grandpiano",
                      "Kawai grand piano", seconds=7.0),
    "violins": _gh(_VSCO, f"{_ST}/Violin Section/susVib", "ys_violins", "violin section, sustained with vibrato",
                   level_window=2.0),
    "cellos": _gh(_VSCO, f"{_ST}/Cello Section/susvib", "ys_cellos", "cello section, sustained with vibrato",
                  level_window=2.0),
    "pizz": _gh(_VSCO, f"{_ST}/Violin Section/Pizz", "ys_pizz", "violin section, pizzicato", seconds=2.0),
    "violin": _gh(_VSCO, f"{_ST}/Solo Violin/Arco Vib", "ys_violin", "solo violin, bowed with vibrato, soft",
                  layer=0.0, level_window=2.0),
    "flute": _gh(_VSCO, f"{_WW}/Flute/susvib", "ys_concertflute", "concert flute with vibrato", level_window=1.5),
    "oboe": _gh(_VSCO, f"{_WW}/Oboe/Vib", "ys_oboe", "oboe with vibrato", level_window=1.5),
    "clarinet": _gh(_VSCO, f"{_WW}/Clarinet/susLong", "ys_clarinet", "clarinet, long notes", level_window=1.5),
    "trumpet": _gh(_VSCO, f"{_BR}/Trumpet/harmonM-sus", "ys_trumpet", "trumpet with a harmon mute",
                   level_window=1.0),
    "horn": _gh(_VSCO, f"{_BR}/F Horn/sus", "ys_horn", "French horn, sustained", level_window=1.5),
    "vibes": _gh(_VCSL, f"{_ID}/Struck Idiophones/Vibraphone/Soft Mallets", "ys_vibes",
                 "vibraphone, soft mallets", seconds=5.0),
    "marimba": _gh(_VCSL, f"{_ID}/Struck Idiophones/Marimba", "ys_marimba", "marimba", seconds=3.0),
    "dantranh": _gh(_VCSL, f"{_CH}/Zithers/Dan Tranh/Normal", "ys_dantranh", "dan tranh, the Vietnamese zither",
                    seconds=4.0),
    "harmonica": _gh(_VCSL, f"{_AE}/Free Aerophones/Harmonica-Hohner-Special20-C/Sustains/Normal", "ys_harmonica",
                     "harmonica (Hohner Special 20)", seconds=3.5),
    "ocarina": _gh(_VCSL, f"{_AE}/Edge-blown Aerophones/Ocarina, Typical/Sustains/SusVib", "ys_ocarina",
                   "ocarina with vibrato", level_window=1.0),
    "mbira": _gh(_VCSL, f"{_ID}/Plucked Idiophones/Mbira dzaVadzimu Nyamaropa, Zimbabwe, Low B", "ys_mbira",
                 "mbira dzaVadzimu (Zimbabwe)", seconds=4.0),
    "glockenspiel": _gh(_VCSL, f"{_ID}/Struck Idiophones/Glockenspiel", "ys_glockenspiel", "glockenspiel",
                        seconds=4.0),
    "uprightbass": _gh(_VSCO, f"{_ST}/Solo Contrabass/Pizz", "ys_uprightbass", "double bass, pizzicato",
                       seconds=2.5),
    "chimes": _gh(_VCSL, f"{_ID}/Struck Idiophones/Tubular Bells 1", "ys_chimes", "tubular bells", seconds=8.0,
                  layer=0.0),
}.items():
    _inst.name = _key
    INSTRUMENTS[_key] = _inst


# Voices from Freesound (CC0): one singer's sung notes (owstu's "Female Vocal
# Single Notes"), long ones as a voice and the short ones as vocal chops; a
# crowd singing "ahh" together, as a choir bed.
_FS = "https://freesound.org/apiv2"
INSTRUMENTS["femalevox"] = Instrument(
    "femalevox", "ys_femalevox", "https://freesound.org/people/owstu/packs/28504/", "",
    "owstu's female vocal single notes, sung", pack=28504, include=r"^Female Vocal SIngle Note",
    lengths=(1.2, 60.0), seconds=4.0, level_window=1.0)
INSTRUMENTS["voxchop"] = Instrument(
    "voxchop", "ys_voxchop", "https://freesound.org/people/owstu/packs/28504/", "",
    "owstu's female vocal single notes, the short ones", pack=28504, include=r"^Female Vocal SIngle Note",
    lengths=(0.0, 1.0), seconds=1.0, level_window=0.3)
INSTRUMENTS["crowdchoir"] = Instrument(
    "crowdchoir", "ys_crowdchoir", "https://freesound.org/people/ShangusBurger/packs/42102/", "",
    "a crowd singing ahh together (GameSoundCon 2024 walla, Shane Vincent)", pack=42102,
    include=r"^CRWDSing_Ahh Vowel Sung Near F 02", seconds=8.0, level_window=2.0)


def installed(name: str, folder: str | None = None) -> bool:
    path = os.path.join(folder or INSTRUMENTS_DIR, name)
    return os.path.isdir(path) and any(f.endswith(".wav") for f in os.listdir(path))


def nrt_sclang(folder: str | None = None, first_buffer: int = 100) -> str:
    """sclang that sets up ~ysInstruments for an offline (NRT) render, as
    superdirt_boot.scd does live: Buffer objects for the recordings and
    ~bufMsgs, the b_allocRead commands to put at the start of the score."""
    folder = folder or INSTRUMENTS_DIR
    lines, n = ["~ysInstruments = (); ~bufMsgs = [];"], first_buffer
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        path = os.path.join(folder, name)
        if not installed(name, folder):
            continue
        meta = os.path.join(path, "meta.scd")
        lines.append(f'~ysInstruments[\\{name}] = (bufs: (), meta: '
                     + (f'"{meta}".load' if os.path.exists(meta) else "()") + ");")
        for wav in sorted(f for f in os.listdir(path) if f.endswith(".wav")):
            lines.append(f'~ysInstruments[\\{name}][\\bufs][{int(wav[:-4])}] = Buffer.new(s, 0, 1, {n}); '
                         f'~bufMsgs = ~bufMsgs.add([0.0, [\\b_allocRead, {n}, "{os.path.join(path, wav)}"]]);')
            n += 1
    return "\n".join(lines)


# ------------------------------------------------------------- SFZ maps

_PC = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def sfz_key(value: str) -> int:
    """An SFZ key: a MIDI number or a note name ("c#4" = 61)."""
    value = value.strip().lower()
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    m = re.fullmatch(r"([a-g])([#b]?)(-?\d)", value)
    if not m:
        raise ValueError(f"not a key: {value!r}")
    shift = {"#": 1, "b": -1}.get(m.group(2), 0)
    return 12 * (int(m.group(3)) + 1) + _PC[m.group(1)] + shift


def parse_sfz(path: str) -> list[dict]:
    """Each <region>'s opcodes, with those of its <global>, <master> and
    <group> (a region's own win), and "_path", its sample file."""
    text = re.sub(r"//[^\n]*", "", open(path, encoding="utf-8", errors="replace").read())
    headers = {"control": {}, "global": {}, "master": {}, "group": {}}
    regions, current = [], None
    for token in re.split(r"(<\w+>)", text):
        m = re.fullmatch(r"<(\w+)>", token)
        if m:
            header = m.group(1)
            if header == "region":
                current = {}
                regions.append((current, dict(headers["global"]), dict(headers["master"]), dict(headers["group"])))
            else:
                current = headers.setdefault(header, {})
                current.clear()
                if header == "global":
                    headers["master"], headers["group"] = {}, {}
                elif header == "master":
                    headers["group"] = {}
            continue
        if current is not None:
            for k, v in re.findall(r"(\w+)=(.*?)(?=\s+\w+=|\s*$)", token.strip().replace("\n", " ")):
                current[k] = v.strip()
    base = os.path.join(os.path.dirname(path), headers["control"].get("default_path", ""))
    out = []
    for own, glob_, master, group in regions:
        region = {**glob_, **master, **group, **own}
        if "sample" in region:
            region["_path"] = os.path.normpath(os.path.join(base, region["sample"].replace("\\", "/")))
            out.append(region)
    return out


def pick_regions(regions: list[dict], velocity: int) -> dict[int, dict]:
    """One recording per note: the attack samples of the velocity layer that
    holds `velocity`, the first of any round robins; note -> region."""
    out: dict[int, dict] = {}
    for r in regions:
        if r.get("trigger", "attack") != "attack":
            continue
        if not int(r.get("lovel", 1)) <= velocity <= int(r.get("hivel", 127)):
            continue
        if int(r.get("seq_position", 1)) != 1:
            continue
        center = sfz_key(r.get("pitch_keycenter", r.get("key", "60")))
        out.setdefault(center, r)
    return out


# ------------------------------------------------------------- preparing

def _read(path: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def _rate(path: str) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    return int(out or SR)


def _write(path: str, x: np.ndarray) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def prepare_note(x: np.ndarray, loop: tuple[int, int] | None = None, window: float = 0.5,
                 seconds: float | None = None) -> tuple[np.ndarray, tuple[int, int] | None]:
    """One recorded note: start 3 ms before the attack, its first `window`
    seconds at ATTACK_RMS_DB, at most `seconds` long, a fade at the end (none
    if it loops); the loop points move with the cut."""
    peak = float(np.abs(x).max()) or 1.0
    start = max(0, int(np.argmax(np.abs(x) > 0.05 * peak)) - int(0.003 * SR))
    x = x[start:]
    if loop is not None:
        loop = (loop[0] - start, loop[1] - start)
        loop = loop if 0 <= loop[0] < loop[1] <= len(x) and loop[1] - loop[0] > SR // 20 else None
    if seconds and loop is None and len(x) > int(seconds * SR):
        x = x[:int(seconds * SR)].copy()
        fade = int(0.3 * SR)
        x[-fade:] *= np.linspace(1, 0, fade) ** 2
    rms = float(np.sqrt(np.mean(x[:int(window * SR)] ** 2))) or 1e-6
    x = x * (10 ** (ATTACK_RMS_DB / 20) / rms)
    if np.abs(x).max() > 0.98:                       # never clip: a little less for a spiky attack
        x = x * (0.98 / np.abs(x).max())
    if loop is None:
        fade = min(len(x), int(0.02 * SR))
        x[-fade:] *= np.linspace(1, 0, fade)
    return x, loop


def prepare(inst: Instrument, sfz_path: str, out_dir: str | None = None, log=print) -> list[int]:
    out_dir = out_dir or os.path.join(INSTRUMENTS_DIR, inst.name)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    meta = []
    for midi, region in sorted(pick_regions(parse_sfz(sfz_path), inst.velocity).items()):
        x = _read(region["_path"])
        loop = None
        if region.get("loop_mode", "no_loop") in ("loop_continuous", "loop_sustain") and "loop_end" in region:
            scale = SR / _rate(region["_path"])           # loop points are in the file's own frames
            loop = (int(int(region["loop_start"]) * scale), int(int(region["loop_end"]) * scale))
        x, loop = prepare_note(x, loop)
        _write(os.path.join(out_dir, f"{midi}.wav"), x)
        tune = float(region.get("tune", 0))
        meta.append(f"{midi}: (tune: {tune:g}, loop: {list(loop) if loop else 'nil'})")
    with open(os.path.join(out_dir, "meta.scd"), "w", encoding="utf-8") as f:
        f.write("// music/instruments.py: per note, its tuning (cents) and loop points (frames)\n("
                + ", ".join(meta) + ")\n")
    with open(os.path.join(out_dir, "SOURCE.txt"), "w", encoding="utf-8") as f:
        f.write(f"FreePats: {inst.about}\nCC0 1.0 public domain dedication\n{inst.page}\n"
                "Prepared by music/instruments.py: one layer, cut to the attack, levelled; <midi note>.wav\n")
    notes = [int(m.split(":")[0]) for m in meta]
    log(f"  {inst.name} ({inst.synth}): {len(notes)} notes, {min(notes)}-{max(notes)} -> {out_dir}")
    return notes


# ------------------------------------------------------------- VSCO / VCSL

_NOTE_RE = re.compile(r"(?:^|_)([A-Ga-g]#?)(-?\d)(?=_|\.|$)")
_DYNAMICS = {"ppp": 0, "pp": 1, "p": 2, "mp": 3, "mf": 4, "f": 5, "ff": 6, "fff": 7,
             "soft": 2, "med": 4, "medium": 4, "loud": 6}


def file_note(name: str) -> int | None:
    """The MIDI note a recording is named for, octaves as written (C4 = 60)."""
    m = _NOTE_RE.search(name)
    return sfz_key(m.group(1) + m.group(2)) if m else None


def _layer(name: str) -> int:
    for token in re.split(r"[_.\- ]", name.lower()):
        if token in _DYNAMICS:
            return _DYNAMICS[token]
        if m := re.fullmatch(r"vl?(\d)", token):
            return int(m.group(1))
    return 0


def _round_robin(name: str) -> int:
    tokens = re.split(r"[_. ]", name.lower())[:-1]           # (without the extension)
    for token in tokens:
        if m := re.fullmatch(r"rr(\d+)", token):
            return int(m.group(1))
    plain = [int(t) for t in tokens[1:] if t.isdigit()]
    return plain[-1] if plain and len(tokens) > 2 and tokens[-1].isdigit() else 1


def pick_files(names: list[str], layer: float) -> dict[int, str]:
    """One recording per note: the layer at `layer` (0 the softest .. 1 the
    loudest) of the note's own layers, the first round robin; note -> name."""
    by_note: dict[int, list[str]] = {}
    for n in names:
        note = file_note(n.rsplit("/", 1)[-1])
        if note is not None:
            by_note.setdefault(note, []).append(n)
    out = {}
    for note, options in by_note.items():
        layers = sorted({_layer(o) for o in options})
        want = layers[int(layer * (len(layers) - 1) + 0.5)]
        out[note] = min((o for o in options if _layer(o) == want), key=lambda o: (_round_robin(o), o))
    return out


def _spectrum(x: np.ndarray, sr: int = SR) -> tuple[np.ndarray, np.ndarray] | None:
    """The magnitude spectrum of half a second just after the attack."""
    start = int(np.argmax(np.abs(x) > 0.1 * (np.abs(x).max() or 1))) + int(0.08 * sr)
    seg = x[start:start + int(0.5 * sr)]
    if len(seg) < sr // 10:
        return None
    n = 1 << 18
    return np.fft.rfftfreq(n, 1 / sr), np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n))


def _peak(freqs: np.ndarray, spec: np.ndarray, f: float, cents: float = 60) -> tuple[float, float]:
    """The strongest frequency within `cents` of f, and its level (dB)."""
    band = (freqs > f * 2 ** (-cents / 1200)) & (freqs < f * 2 ** (cents / 1200))
    if not band.any():
        return f, -120.0
    i = int(np.argmax(spec[band]))
    return float(freqs[band][i]), float(20 * np.log10(spec[band][i] + 1e-12))


def measure_note(x: np.ndarray, note: int, sr: int = SR) -> tuple[int, float] | None:
    """Which octave of `note` a recording really sounds (-1..+3) and how many
    cents it's off from it: the lowest octave whose own pitch is in the
    spectrum (an octave too low falls between the partials) and whose first
    four harmonics score near the best (an octave too high lands on the
    even harmonics, which are there too: the lowest wins)."""
    got = _spectrum(x, sr)
    if got is None:
        return None
    freqs, spec = got
    loudest = float(20 * np.log10(spec[(freqs > 25) & (freqs < 6000)].max() + 1e-12))
    scores, present = {}, {}
    for k in range(-1, 4):
        f = 440 * 2 ** ((note + 12 * k - 69) / 12)
        if f < 25 or f * 4 > sr / 2:
            continue
        levels = [_peak(freqs, spec, f * h, 40)[1] for h in (1, 2, 3, 4)]
        scores[k], present[k] = float(np.mean(levels)), levels[0] >= loudest - 30
    if not scores:
        return None
    best = max(scores.values())
    near = [k for k in scores if present[k] and scores[k] >= best - 10]
    k = min(near) if near else max(scores, key=scores.get)
    f = 440 * 2 ** ((note + 12 * k - 69) / 12)
    # The tuning from the strongest of the first three harmonics (a muted
    # trumpet's or a low piano note's fundamental is weak).
    found = max(((_peak(freqs, spec, f * h, 40), h) for h in (1, 2, 3)), key=lambda p: p[0][1])
    (peak, _), h = found
    return k, float(1200 * np.log2(peak / h / f))


def frame_cents(seg: np.ndarray, f: float, sr: int = SR, reach: float = 150) -> float | None:
    """A frame's pitch in cents from f, by autocorrelation searched only
    within `reach` cents of it (a voice's harmonics, strong and shifting
    with the vowel, mislead a spectral peak; its period doesn't)."""
    seg = seg - seg.mean()
    n = 1 << int(np.ceil(np.log2(2 * len(seg))))
    spec = np.fft.rfft(seg, n)
    ac = np.fft.irfft(spec * np.conj(spec), n)[:len(seg)]
    if ac[0] <= 0:
        return None
    lo = int(sr / (f * 2 ** (reach / 1200)))
    hi = min(len(seg) // 2, int(np.ceil(sr / (f * 2 ** (-reach / 1200)))) + 1)
    if hi - lo < 3:
        return None
    i = lo + int(np.argmax(ac[lo:hi]))
    if ac[i] < 0.3 * ac[0] or not lo < i < hi - 1:
        return None                                  # no clear period there
    a, b, c = ac[i - 1], ac[i], ac[i + 1]
    lag = i + 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else i
    return float(1200 * np.log2(sr / lag / f))


def steady_cents(x: np.ndarray, midi: int, sr: int = SR) -> float | None:
    """How many cents a held note sits from `midi`: the median over its
    steady middle (a voice scoops into a note and wavers with vibrato, so
    its first half second says little), frame by frame from the strongest
    of its first three harmonics."""
    cents = _held_cents(x, midi, sr)
    return float(np.median(cents)) if cents else None


def pitch_spread(x: np.ndarray, midi: int, sr: int = SR) -> float:
    """How much a held note wavers (cents, standard deviation over its
    middle): a vibrato is 10-20, a note sung unsteadily or trilled more."""
    cents = _held_cents(x, midi, sr)
    return float(np.std(cents)) if len(cents) > 2 else 0.0


def _held_cents(x: np.ndarray, midi: int, sr: int = SR) -> list[float]:
    """The pitch (cents from midi) every 20 ms over a note's held middle."""
    start = int(np.argmax(np.abs(x) > 0.1 * (np.abs(x).max() or 1)))
    body = x[start:]
    a, b = int(len(body) * 0.25), int(len(body) * 0.85)
    frame, hop = int(0.06 * sr), int(0.02 * sr)
    f = 440 * 2 ** ((midi - 69) / 12)
    out = []
    for i in range(a, max(a + 1, b - frame), hop):
        seg = body[i:i + frame]
        if len(seg) == frame and np.sqrt(np.mean(seg ** 2)) > 1e-3:
            c = frame_cents(seg, f, sr)
            if c is not None:
                out.append(c)
    return out


def hold_pitch(x: np.ndarray, midi: int, sr: int = SR, smooth: float = 0.3, vibrato: float = 0.4) -> np.ndarray:
    """A sung note held at `midi`: its pitch tracked every 10 ms and the
    recording read faster or slower by it, moment by moment: the slow drift
    and most of the scoop (the track smoothed over `smooth` seconds) taken
    out, the vibrato kept to `vibrato` of its depth (a wide, operatic one
    tamed to a singer's; 0 would be hard autotune)."""
    f = 440 * 2 ** ((midi - 69) / 12)
    hop, frame = int(0.01 * sr), int(0.06 * sr)
    floor = 0.05 * float(np.sqrt(np.mean(x ** 2)) or 1e-6)
    centers, cents = [], []
    for i in range(0, max(1, len(x) - frame), hop):
        seg = x[i:i + frame]
        if np.sqrt(np.mean(seg ** 2)) < floor:
            continue
        c = frame_cents(seg, f, sr, reach=200)
        if c is not None:
            centers.append(i + frame // 2)
            cents.append(c)
    if len(cents) < 3:
        return x
    def smoothed(seconds: float) -> np.ndarray:
        k = max(1, int(seconds / 0.01))
        return np.convolve(np.pad(np.array(cents), k // 2, mode="edge"), np.ones(k) / k, mode="valid")[:len(cents)]

    slow, fast = smoothed(smooth), smoothed(0.03)
    drift = np.interp(np.arange(len(x)), centers, slow + (1 - vibrato) * (fast - slow))
    pos = np.cumsum(2 ** (-drift / 1200)) - 1
    pos = pos[pos < len(x) - 1]
    return np.interp(pos, np.arange(len(x)), x).astype(np.float32)


def _octave_shift(measured: dict[int, tuple[int, float] | None]) -> int:
    """How far the names' octaves are from the recordings' (a set that calls
    its lowest A "A#-1"): the shift most notes agree on."""
    votes: dict[int, int] = {}
    for m in measured.values():
        if m is not None:
            votes[m[0]] = votes.get(m[0], 0) + 1
    return 12 * max(votes, key=votes.get) if votes else 0


def _tree(repo: str) -> list[str]:
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/git/trees/master?recursive=1",
                                 headers={"User-Agent": "youtube-stream-music-generation"})
    import json

    with urllib.request.urlopen(req, timeout=60) as r:
        return [t["path"] for t in json.load(r)["tree"] if t["type"] == "blob"]


def install_github(inst: Instrument, log=print, tree: list[str] | None = None) -> list[int]:
    """Download one layer of a VSCO/VCSL set, measure each recording's pitch
    (the octave its name means, its tuning), and prepare it like an SFZ set."""
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import quote

    tree = tree if tree is not None else _tree(inst.repo)
    names = [p[len(inst.folder) + 1:] for p in tree
             if p.startswith(inst.folder + "/") and p.lower().endswith(".wav") and "/" not in p[len(inst.folder) + 1:]]
    chosen = pick_files(names, inst.layer)
    if not chosen:
        raise RuntimeError(f"no recordings named by note in {inst.repo}/{inst.folder}")
    log(f"[instruments] {inst.name}: {inst.about} ({len(chosen)} notes, CC0, {inst.page})")
    with tempfile.TemporaryDirectory() as tmp:
        def fetch(item):
            note, name = item
            path = os.path.join(tmp, f"{note}.wav")
            url = f"https://raw.githubusercontent.com/{inst.repo}/master/{quote(inst.folder + '/' + name)}"
            req = urllib.request.Request(url, headers={"User-Agent": "youtube-stream-music-generation"})
            with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
                shutil.copyfileobj(r, f)
            return note, _read(path)

        with ThreadPoolExecutor(8) as pool:
            audio = dict(pool.map(fetch, sorted(chosen.items())))
    shift = _octave_shift({note: measure_note(x, note) for note, x in audio.items()})
    out_dir = os.path.join(INSTRUMENTS_DIR, inst.name)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    meta = []
    for note, x in sorted(audio.items()):
        midi = note + shift
        if not 0 <= midi <= 127:
            continue
        # SFZ's sense: the cents the recording is raised to play in tune (a
        # measurement more than 35 cents out is a misheard partial: none).
        m = measure_note(x, midi)
        tune = -m[1] if m is not None and m[0] == 0 else 0.0
        tune = round(tune, 1) if abs(tune) <= 35 else 0.0
        y, _ = prepare_note(x, None, window=inst.level_window, seconds=inst.seconds)
        _write(os.path.join(out_dir, f"{midi}.wav"), y)
        meta.append(f"{midi}: (tune: {tune:g}, loop: nil)")
    with open(os.path.join(out_dir, "meta.scd"), "w", encoding="utf-8") as f:
        f.write("// music/instruments.py: per note, its tuning (cents) and loop points (frames)\n("
                + ", ".join(meta) + ")\n")
    with open(os.path.join(out_dir, "SOURCE.txt"), "w", encoding="utf-8") as f:
        f.write(f"{inst.repo.split('/')[-1]}: {inst.about}\nCC0 1.0 public domain dedication\n"
                f"{inst.page}/tree/master/{inst.folder}\n"
                "Prepared by music/instruments.py: one layer, cut to the attack, levelled, tuned; <midi note>.wav\n")
    notes = [int(m.split(":")[0]) for m in meta]
    log(f"  {inst.name} ({inst.synth}): {len(notes)} notes, {min(notes)}-{max(notes)}"
        + (f" (named {shift // 12:+d} octave)" if shift else "") + f" -> {out_dir}")
    return notes


# ------------------------------------------------------------- Freesound

_PITCH_CLASS_RE = re.compile(r"\b(?:Note|Near)\s+([A-G]#?)", re.I)


def _freesound(path: str, **params) -> dict:
    import json
    from urllib.parse import urlencode

    key = os.getenv("FREESOUND_API_KEY", "")
    if not key:
        raise RuntimeError("needs FREESOUND_API_KEY in .env (freesound.org/apiv2/apply)")
    url = f"{_FS}/{path}?{urlencode({**params, 'token': key})}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "youtube-stream"}), timeout=60) as r:
        return json.load(r)


def install_freesound(inst: Instrument, log=print) -> list[int]:
    """A Freesound pack's chosen sounds (CC0 only), each measured for the
    octave of the pitch class in its name and its tuning; the longest
    recording of each note kept, prepared like an SFZ set."""
    sounds, page = [], 1
    while True:
        got = _freesound(f"packs/{inst.pack}/sounds/", fields="id,name,username,duration,license,previews",
                         page_size=150, page=page)
        sounds += got.get("results", [])
        if not got.get("next"):
            break
        page += 1
    lo, hi = inst.lengths
    chosen = [s for s in sounds if re.search(inst.include, s["name"]) and lo <= s["duration"] <= hi
              and "publicdomain/zero" in s["license"] and _PITCH_CLASS_RE.search(s["name"])]
    if not chosen:
        raise RuntimeError(f"no CC0 sounds named by note in pack {inst.pack} match {inst.include!r}")
    log(f"[instruments] {inst.name}: {inst.about} ({len(chosen)} recordings, CC0, {inst.page})")
    takes: dict[int, list[tuple[float, np.ndarray, dict]]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for snd in chosen:
            path = os.path.join(tmp, f"{snd['id']}.ogg")
            req = urllib.request.Request(snd["previews"]["preview-hq-ogg"], headers={"User-Agent": "youtube-stream"})
            with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
                shutil.copyfileobj(r, f)
            x = _read(path)
            pc = sfz_key(_PITCH_CLASS_RE.search(snd["name"]).group(1) + "4")      # in octave 4 ...
            m = measure_note(x, pc - 12)                                         # ... measured from 3 up
            if m is None:
                continue
            takes.setdefault(pc - 12 + 12 * m[0], []).append((snd["duration"], x, snd))
    out_dir = os.path.join(INSTRUMENTS_DIR, inst.name)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    meta, credits = [], []
    # A singer is off by a little here and there: each take's held middle
    # says which note it is (one nearer the next semitone is that one); its
    # drift is taken out and its vibrato tamed (hold_pitch); of a note's
    # takes the steadiest is kept, and one that still wavers more than a
    # vibrato (a trill) is left out: the next note, shifted, plays it.
    best: dict[int, tuple[float, np.ndarray, dict]] = {}
    for midi0, options in takes.items():
        for _, x, snd in options:
            cents = steady_cents(x, midi0) or 0.0
            midi = midi0 + round(cents / 100)
            held = hold_pitch(x, midi)
            spread = pitch_spread(held, midi)
            if midi not in best or spread < best[midi][0]:
                best[midi] = (spread, held, snd)
    unsteady = sorted(m for m, (spread, _, _) in best.items() if spread > 30)
    if unsteady and len(unsteady) < len(best):
        log(f"  {inst.name}: left out, unsteady: {', '.join(map(str, unsteady))}")
    by_note = {m: v for m, v in best.items() if m not in unsteady or len(unsteady) == len(best)}
    for midi, (_, x, snd) in sorted(by_note.items()):
        tune = round(-(steady_cents(x, midi) or 0.0), 1)
        y, _ = prepare_note(x, None, window=inst.level_window, seconds=inst.seconds)
        _write(os.path.join(out_dir, f"{midi}.wav"), y)
        meta.append(f"{midi}: (tune: {tune:g}, loop: nil)")
        credits.append(f"{midi}.wav: \"{snd['name']}\" by {snd['username']}, https://freesound.org/s/{snd['id']}/")
    with open(os.path.join(out_dir, "meta.scd"), "w", encoding="utf-8") as f:
        f.write("// music/instruments.py: per note, its tuning (cents) and loop points (frames)\n("
                + ", ".join(meta) + ")\n")
    with open(os.path.join(out_dir, "SOURCE.txt"), "w", encoding="utf-8") as f:
        f.write(f"Freesound: {inst.about}\nCC0 1.0 public domain dedication\n{inst.page}\n"
                + "\n".join(credits) + "\nPrepared by music/instruments.py (from the HQ previews): "
                "cut to the attack, levelled, tuned; <midi note>.wav\n")
    notes = sorted(by_note)
    log(f"  {inst.name} ({inst.synth}): {len(notes)} notes, {min(notes)}-{max(notes)} -> {out_dir}")
    return notes


def _extract(archive: str, into: str) -> None:
    if archive.endswith(".7z"):
        if not shutil.which("7z"):
            raise RuntimeError("needs 7z (sudo apt install p7zip-full)")
        subprocess.run(["7z", "x", "-y", f"-o{into}", archive], check=True, capture_output=True)
    else:
        subprocess.run(["tar", "xf", archive, "-C", into], check=True, capture_output=True)


def install(inst: Instrument, log=print) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, os.path.basename(inst.url).replace("+", "_"))
        log(f"[instruments] {inst.name}: downloading {inst.about} (CC0, {inst.page})")
        req = urllib.request.Request(inst.url, headers={"User-Agent": "Mozilla/5.0", "Referer": inst.page})
        with urllib.request.urlopen(req, timeout=300) as r, open(archive, "wb") as f:
            shutil.copyfileobj(r, f)
        _extract(archive, tmp)
        maps = [os.path.join(root, n) for root, _, files in os.walk(tmp) for n in files if n.endswith(".sfz")]
        if not maps:
            raise RuntimeError("no SFZ map in the archive")
        prepare(inst, sorted(maps, key=len)[0], log=log)


def main(argv: list[str] | None = None) -> int:
    names = (sys.argv[1:] if argv is None else argv) or list(INSTRUMENTS)
    unknown = [n for n in names if n not in INSTRUMENTS]
    if unknown:
        print(f"[instruments] unknown: {', '.join(unknown)} (known: {', '.join(INSTRUMENTS)})")
        return 1
    failed = 0
    trees: dict[str, list[str]] = {}
    for name in names:
        try:
            inst = INSTRUMENTS[name]
            if inst.pack:
                install_freesound(inst)
                continue
            if inst.repo:
                if inst.repo not in trees:
                    trees[inst.repo] = _tree(inst.repo)
                install_github(inst, tree=trees[inst.repo])
                continue
            install(inst)
        except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as e:
            failed += 1
            print(f"[instruments] {name}: FAILED ({e})")
    print("[instruments] done; restart the stream (stream/live.sh) so the instruments play the recordings")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
