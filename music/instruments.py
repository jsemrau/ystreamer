"""
Sampled instruments: real recordings a SynthDef plays by pitch.

    python -m music.instruments              # download and prepare all, then restart the stream
    python -m music.instruments piano sax    # only these

All from FreePats (https://freepats.zenvoid.org), each published under CC0
1.0 (public domain), so the stream may play them. Each set's SFZ map says
which recording plays which notes; one recording per note is kept (a set's
velocity layer chosen below, the first of its round robins), cut to start
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


def prepare_note(x: np.ndarray, loop: tuple[int, int] | None = None) -> tuple[np.ndarray, tuple[int, int] | None]:
    """One recorded note: start 3 ms before the attack, its first half second
    at ATTACK_RMS_DB, a fade at the end (none if it loops); the loop points
    move with the cut."""
    peak = float(np.abs(x).max()) or 1.0
    start = max(0, int(np.argmax(np.abs(x) > 0.05 * peak)) - int(0.003 * SR))
    x = x[start:]
    if loop is not None:
        loop = (loop[0] - start, loop[1] - start)
        loop = loop if 0 <= loop[0] < loop[1] <= len(x) and loop[1] - loop[0] > SR // 20 else None
    rms = float(np.sqrt(np.mean(x[:int(0.5 * SR)] ** 2))) or 1e-6
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
    for name in names:
        try:
            install(INSTRUMENTS[name])
        except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as e:
            failed += 1
            print(f"[instruments] {name}: FAILED ({e})")
    print("[instruments] done; restart the stream (stream/live.sh) so the instruments play the recordings")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
