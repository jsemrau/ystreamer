"""
Licensed acapellas: a sung hook over the track, in its key and on its beat.

Put each acapella (a loop cut on the bar, or a longer take with the first
downbeat noted) in acapellas/ and describe it in acapellas/acapellas.json:

    [{"name": "Night Calls", "file": "night_calls.wav", "key": "A minor",
      "bpm": 124, "start": 0.48, "bars": 8, "styles": ["deephouse", "trance"],
      "license": "Loopmasters Deep House Vocals, royalty-free licence"}]

`start` is the first downbeat in seconds, `bars` how many bars from there
make the loop, `styles` which styles may sing it (all of them if left out),
and `license` where it comes from and why the stream may play it: every
acapella needs one (YouTube's Content ID flags vocals lifted from songs).

    python -m music.vocals            # prepare them for SuperDirt, then restart the stream

renders each into its own bank (samples/vox_<name>/): the loop cut exactly
to its bars, at -16 LUFS, in seven versions from three semitones down to
three up (0.wav ... 6.wav), so a track can sing it in seven keys. A track
that picks one takes its tempo (or double or half), and the nearest key the
seven versions reach, in its mode; the Director's hook and sketch are in
scale degrees, so they follow. The line plays the loop with loopAt, chopped
into beats, so it starts in step wherever it comes in.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

import config

from .theory import DISPLAY_NAMES, Key

SAMPLES_DIR = os.path.join(config.REPO_DIR, "samples")
MANIFEST = "acapellas.json"
SHIFTS = range(-3, 4)            # semitones; sample n = shift + 3
FILE_LUFS = -16.0
# Sung hooks sit a little above the lead (mix.ROLE_TARGET_LUFS lead -21):
# SuperDirt plays every event at amp 0.4 (-8 dB from the measured synths), so
# a -16 LUFS file at amp 0.25 lands at -16 - 12 + 8 = -20.
ACAPELLA_AMP = 0.25
CHOPS_PER_BAR = 4
# Tempos an acapella can be sung at: its own, double time, half time.
TEMPO_FACTORS = (1.0, 2.0, 0.5)


@dataclass
class Acapella:
    name: str
    file: str
    key: str
    bpm: float
    bars: int
    license: str
    start: float = 0.0
    styles: list[str] = field(default_factory=list)

    @property
    def bank(self) -> str:
        """Its SuperDirt bank (a Tidal sound name: lower case, digits, _)."""
        return "vox_" + (re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_") or "untitled")

    @property
    def seconds(self) -> float:
        return self.bars * 240.0 / self.bpm


def load_manifest(folder: str | None = None) -> list[Acapella]:
    """The acapellas described in acapellas.json (none if there's no file).
    Raises ValueError on an entry that can't be used."""
    folder = folder or config.ACAPELLA_DIR
    path = os.path.join(folder, MANIFEST)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for i, entry in enumerate(data):
        where = f"{MANIFEST} entry {i + 1} ({entry.get('name', '?')})"
        missing = [k for k in ("name", "file", "key", "bpm", "bars", "license") if not entry.get(k)]
        if missing:
            raise ValueError(f"{where}: needs {', '.join(missing)}")
        try:
            Key.parse(entry["key"])
        except (KeyError, ValueError) as e:
            raise ValueError(f"{where}: can't read the key {entry['key']!r} ({e})")
        if int(entry["bars"]) < 1 or not 40 <= float(entry["bpm"]) <= 220:
            raise ValueError(f"{where}: bars must be 1 or more, bpm 40-220")
        out.append(Acapella(name=entry["name"], file=entry["file"], key=entry["key"], bpm=float(entry["bpm"]),
                            bars=int(entry["bars"]), license=entry["license"], start=float(entry.get("start", 0)),
                            styles=list(entry.get("styles") or [])))
    banks = [a.bank for a in out]
    dupes = {b for b in banks if banks.count(b) > 1}
    if dupes:
        raise ValueError(f"{MANIFEST}: two acapellas share a bank name ({', '.join(sorted(dupes))}); rename one")
    return out


def bank_dir(acapella: Acapella, samples_dir: str | None = None) -> str:
    return os.path.join(samples_dir or SAMPLES_DIR, acapella.bank)


def is_prepared(acapella: Acapella, samples_dir: str | None = None) -> bool:
    folder = bank_dir(acapella, samples_dir)
    return all(os.path.exists(os.path.join(folder, f"{i}.wav")) for i in range(len(SHIFTS)))


def fit(acapella: Acapella, key: str, bpm_range: tuple[float, float], modes: list[str]) -> dict | None:
    """How a track in `key` (the Director's choice) sings this acapella: its
    tempo, the key it moves to and the version (shift) that sings in it; None
    if no tempo fits the range or its mode isn't one the style plays."""
    sung = Key.parse(acapella.key)
    if modes and sung.mode not in modes:
        return None
    lo, hi = bpm_range
    for factor in TEMPO_FACTORS:
        bpm, cycles = acapella.bpm * factor, acapella.bars * factor
        if lo <= bpm <= hi and cycles >= 1 and cycles == int(cycles):
            break
    else:
        return None
    wanted = Key.parse(key).root
    shift = max(min(SHIFTS), min(max(SHIFTS), (wanted - sung.root + 6) % 12 - 6))
    return {"name": acapella.name, "bank": acapella.bank, "shift": shift, "n": shift - min(SHIFTS),
            "key": f"{DISPLAY_NAMES[(sung.root + shift) % 12]} {sung.mode}", "bpm": round(bpm, 2),
            "cycles": int(cycles)}


def choose(rng: random.Random, style: str, key: str, bpm_range: tuple[float, float], modes: list[str],
           recent: list[str] | None = None, chance: float | None = None, acapellas: list[Acapella] | None = None,
           samples_dir: str | None = None) -> dict | None:
    """An acapella for the next track (see fit), or None: only prepared ones
    the style may sing, not one of the `recent` names while others fit, and
    only on `chance` of the tracks (config.ACAPELLA_CHANCE)."""
    chance = config.ACAPELLA_CHANCE if chance is None else chance
    if chance <= 0:
        return None
    if acapellas is None:
        try:
            acapellas = load_manifest()
        except (OSError, ValueError):
            return None
    fits = [f for a in acapellas if (not a.styles or style in a.styles) and is_prepared(a, samples_dir)
            for f in [fit(a, key, bpm_range, modes)] if f]
    # (No dice when nothing fits, so a track's other choices stay as they were.)
    if not fits or rng.random() >= chance:
        return None
    fresh = [f for f in fits if f["name"] not in (recent or [])] or fits
    return rng.choice(fresh)


def acapella_block(vocal: dict):
    """The track's acapella as a line: the loop stretched over its bars and
    chopped into beats, so it starts in step when it comes in mid-phrase.
    The loop is locked (livecode.LOCKED_SLOTS): it's the one this track sings."""
    from .blocks import Block

    cycles = int(vocal["cycles"])
    return Block("acapella", "vocal",
                 f'loopAt {cycles} $ chop {cycles * CHOPS_PER_BAR} $ s "{{loop}}" # amp {ACAPELLA_AMP:g}',
                 {"loop": [f"{vocal['bank']}:{vocal['n']}"]},
                 fx=[["gain", "1.0"], ["room", "0.25"], ["size", "0.6"], ["hpf", "150"]], drums=True)


# ------------------------------------------------------------- preparing

def _loudness(path: str) -> float | None:
    out = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path,
                          "-af", "apad=pad_dur=1,ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    return float(found[-1]) if found else None


def prepare(acapella: Acapella, source_dir: str | None = None, samples_dir: str | None = None,
            log=print) -> str:
    """Render one acapella's bank: cut to its bars, seven keys, -16 LUFS."""
    if not shutil.which("sox") or not shutil.which("ffmpeg"):
        raise RuntimeError("needs sox and ffmpeg (sudo apt install sox ffmpeg)")
    src = os.path.join(source_dir or config.ACAPELLA_DIR, acapella.file)
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    folder = bank_dir(acapella, samples_dir)
    os.makedirs(folder, exist_ok=True)
    seconds = f"{acapella.seconds:.6f}"
    for shift in SHIFTS:
        out = os.path.join(folder, f"{shift - min(SHIFTS)}.wav")
        tmp = out + ".tmp.wav"
        # Cut on the downbeat, shift the pitch (sox keeps the length), and
        # pad / trim to the exact bars so loopAt plays it at the track's tempo.
        effects = ["trim", f"{acapella.start:.6f}", seconds]
        if shift:
            effects += ["pitch", str(shift * 100)]
        effects += ["pad", "0", seconds, "trim", "0", seconds, "fade", "t", "0.005", seconds, "0.01"]
        subprocess.run(["sox", "-G", src, "-r", "48000", "-b", "16", tmp, *effects], check=True,
                       capture_output=True)
        level = _loudness(tmp)
        gain = 0.0 if level is None else FILE_LUFS - level
        subprocess.run(["sox", "-G", tmp, out, "vol", f"{gain:.2f}dB"], check=True, capture_output=True)
        os.remove(tmp)
    log(f"  {acapella.name}: {acapella.key}, {acapella.bpm:g} BPM, {acapella.bars} bars -> {folder}/0..6.wav")
    return folder


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        acapellas = load_manifest()
    except ValueError as e:
        print(f"[vocals] {e}")
        return 1
    if not acapellas:
        print(f"[vocals] no acapellas: describe them in {os.path.join(config.ACAPELLA_DIR, MANIFEST)} "
              "(see music/vocals.py)")
        return 1
    force = "--force" in argv
    print(f"[vocals] preparing {len(acapellas)} acapella(s) into {SAMPLES_DIR}/vox_*")
    failed = 0
    for a in acapellas:
        if is_prepared(a) and not force:
            print(f"  {a.name}: ready (--force renders it again)")
            continue
        try:
            prepare(a)
        except (OSError, RuntimeError, subprocess.CalledProcessError) as e:
            failed += 1
            print(f"  {a.name}: FAILED ({e})")
    print("[vocals] done; restart the stream (stream/live.sh) so SuperDirt loads the new banks")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
