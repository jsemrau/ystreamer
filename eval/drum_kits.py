#!/usr/bin/env python3
"""
The drum kits a track can play (music/kits.py): built from the drum machines
and kits in Dirt-Samples and the project's own banks, each hit chosen by
measuring it (no bank is what its name says throughout: "hh27" holds kicks
too), levelled to the hit it stands in for, and copied into its own bank,
samples/kit_<name>/ (0 kick, 1 snare, 2 clap, 3 closed hat, 4 open hat),
which SuperDirt loads at boot. The list goes to music/data/drum_kits.json.

    python -m eval.drum_kits          # then restart the stream

A hit is known by its file name where that says (BD, SN, HANDCLP,
closedhh, OH, Ride ...: a "kicksnare" or a "lotom" is neither), else by
measuring its first half second, its DC taken out (an old sample's offset
reads as bass): a kick has most of its energy at 25-150 Hz, a hat above
5 kHz, a snare or clap is the bright, short rest.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

SR = 44100
DIRT = os.path.expanduser("~/.local/share/SuperCollider/downloaded-quarks/Dirt-Samples")
SAMPLES = os.path.join(config.REPO_DIR, "samples")
OUT = os.path.join(config.REPO_DIR, "music", "data", "drum_kits.json")
SLOTS = ("kick", "snare", "clap", "hat", "openhat")
# Each kit: its banks (Dirt-Samples or samples/), which styles it suits, and
# hits taken from elsewhere where its own banks have none.
KITS = {
    "house": (["house"], "club", {"clap": ("cp", 0)}),
    "electro": (["electro1"], "club", {"clap": ("realclaps", 1)}),
    "tech": (["tech"], "club", {"clap": ("cp", 1)}),
    "techno": (["techno"], "club", {"clap": ("cp", 0)}),
    "drumtraks": (["drumtraks"], "club", {}),
    "oberheim": (["odx"], "club", {}),
    "drummachine": (["dr"], "club", {}),
    "808": (["808bd", "808sd", "808oh"], "club", {"clap": ("cp", 0), "hat": ("hh", 0)}),
    "linn": (["kicklinn", "linnhats", "realclaps"], "both", {"snare": ("sn", 1)}),
    "studio": (["kick", "snare", "clap"], "club", {"hat": ("hh27", 0)}),
    "bloodsport": (["bskick", "bssnare", "bshihat"], "dark", {"clap": ("realclaps", 2)}),
    "gretsch": (["gretsch"], "acoustic", {}),
    "jazz": (["jazz"], "acoustic", {"hat": ("hh", 0)}),
}
STYLES = {"club": ["trance", "techno", "deephouse", "darkwave"],
          "dark": ["darkwave", "techno"],
          "acoustic": ["lofi", "downtempo", "ambient", "piano"],
          "both": ["trance", "deephouse", "darkwave", "lofi", "downtempo"]}
# What each slot is levelled to: the hits the stream played until now.
REFERENCE = {"kick": ("909", 0), "snare": ("sn", 1), "clap": ("cp", 0), "hat": ("hh27", 0), "openhat": ("808oh", 0)}


def bank_dir(bank: str) -> str:
    own = os.path.join(SAMPLES, bank)
    return own if os.path.isdir(own) else os.path.join(DIRT, bank)


def files(bank: str) -> list[str]:
    d = bank_dir(bank)
    return sorted(f for f in os.listdir(d) if f.lower().endswith((".wav", ".aif", ".aiff", ".flac")))


def read(path: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, np.float32).copy()
    return x - x.mean() if len(x) else x


def measure(x: np.ndarray) -> dict:
    peak = float(np.abs(x).max()) or 1.0
    on = int(np.argmax(np.abs(x) > 0.1 * peak))
    seg = x[on:on + SR // 2]
    seg = seg - seg.mean()
    above = np.nonzero(np.abs(seg) > 0.05 * peak)[0]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 15)) ** 2
    freqs = np.fft.rfftfreq(1 << 15, 1 / SR)
    spec[freqs < 20] = 0
    total = spec.sum() or 1.0
    low = spec[(freqs > 25) & (freqs < 150)].sum() / total
    high = spec[freqs > 5000].sum() / total
    centroid = (spec * freqs).sum() / total
    kind = ("kick" if low > 0.45 and centroid < 800 else "hat" if high > 0.45
            else "snare" if centroid > 700 else "other")
    return {"kind": kind, "low": low, "high": high, "centroid": centroid,
            "dur": (above[-1] / SR) if len(above) else 0.0, "onset": on}


def level(x: np.ndarray) -> float:
    on = int(np.argmax(np.abs(x) > 0.1 * (np.abs(x).max() or 1)))
    return float(np.sqrt(np.mean(x[on:on + int(0.1 * SR)] ** 2))) or 1e-6


_NAMES = {
    "kick": re.compile(r"kick|(^|[^a-z])bd([^a-z]|$)"),
    "snare": re.compile(r"snare|(^|[^a-z])(sn|sd)([^a-z]|$)"),
    "clap": re.compile(r"clap|clp|(^|[^a-z])cp([^a-z]|$)"),
    "hat": re.compile(r"closed|cl_hat|hihat|(^|[^a-z])hh([^a-z]|$)|hats?([^a-z]|$)"),
    "openhat": re.compile(r"open|(^|[^a-z])oh([^a-z]|$)|ride"),
}
_NOT = {"kick": re.compile(r"tom|snare"), "snare": re.compile(r"kick|rim"), "hat": re.compile(r"open"),
        "clap": re.compile(r"$^"), "openhat": re.compile(r"$^")}


def named(name: str, slot: str) -> bool:
    n = re.sub(r"\.\w+$", "", name.lower())
    return bool(_NAMES[slot].search(n)) and not _NOT[slot].search(n)


def choose(banks: list[str]) -> dict[str, tuple[str, int]]:
    """The kit's hit for each slot: the deepest punchy kick, the snare
    nearest a bright 2.5 kHz, the shortest hat closed and the longest open."""
    hits = [(b, i, measure(read(os.path.join(bank_dir(b), f))), f) for b in banks for i, f in enumerate(files(b))]
    pick: dict[str, tuple[str, int]] = {}

    def of(slot: str, kind: str, ok=lambda m: True) -> list:
        """The hits named for the slot (those also measuring as it first), else the ones measuring as it."""
        by_name = [h for h in hits if named(h[3], slot)]
        agree = [h for h in by_name if h[2]["kind"] == kind and ok(h[2])]
        return agree or by_name or [h for h in hits if h[2]["kind"] == kind and ok(h[2])
                                    and not any(named(h[3], s) for s in _NAMES if s != slot)]

    kicks = of("kick", "kick", lambda m: 0.08 <= m["dur"] <= 0.9)
    if kicks:
        b, i, _, _ = max(kicks, key=lambda h: h[2]["low"] - 0.3 * abs(h[2]["dur"] - 0.3))
        pick["kick"] = (b, i)
    snares = of("snare", "snare", lambda m: m["dur"] <= 0.6)
    if snares:
        b, i, _, _ = min(snares, key=lambda h: abs(h[2]["centroid"] - 2500) / 2500 + h[2]["dur"])
        pick["snare"] = (b, i)
    claps = [h for h in of("clap", "snare", lambda m: m["dur"] <= 0.5) if (h[0], h[1]) != pick.get("snare")]
    if claps and any(named(h[3], "clap") for h in claps):
        b, i, _, _ = min(claps, key=lambda h: abs(h[2]["centroid"] - 1600) / 1600 + h[2]["dur"])
        pick["clap"] = (b, i)
    hats = of("hat", "hat")
    if hats:
        b, i, _, _ = min(hats, key=lambda h: h[2]["dur"])
        pick["hat"] = (b, i)
    opens = [h for h in of("openhat", "hat", lambda m: m["dur"] >= 0.15) if (h[0], h[1]) != pick.get("hat")]
    if opens:
        b, i, _, _ = max(opens, key=lambda h: (named(h[3], "openhat"), -abs(h[2]["dur"] - 0.35)))
        pick["openhat"] = (b, i)
    return pick


def write(path: str, x: np.ndarray) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def main() -> int:
    refs = {slot: level(read(os.path.join(bank_dir(b), files(b)[i]))) for slot, (b, i) in REFERENCE.items()}
    kits = []
    for name, (banks, family, borrow) in KITS.items():
        if not all(os.path.isdir(bank_dir(b)) for b in banks):
            print(f"  {name}: a bank is missing, skipped")
            continue
        pick = {**{s: v for s, v in borrow.items()}, **choose(banks)}
        pick = {**pick, **{s: v for s, v in borrow.items() if s not in pick}}
        if "openhat" not in pick:
            pick["openhat"] = ("808oh", 1)
        if "clap" not in pick:
            pick["clap"] = pick.get("snare", ("cp", 0))
        missing = [s for s in SLOTS if s not in pick]
        if missing:
            print(f"  {name}: no {', '.join(missing)}, skipped")
            continue
        out_dir = os.path.join(SAMPLES, f"kit_{name}")
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
        os.makedirs(out_dir)
        sources = {}
        for n, slot in enumerate(SLOTS):
            b, i = pick[slot]
            x = read(os.path.join(bank_dir(b), files(b)[i]))
            on = max(0, int(np.argmax(np.abs(x) > 0.1 * (np.abs(x).max() or 1))) - int(0.002 * SR))
            x = x[on:on + int(1.5 * SR)] * (refs[slot] / level(x))
            if np.abs(x).max() > 0.98:
                x = x * (0.98 / np.abs(x).max())
            fade = min(len(x), int(0.01 * SR))
            x[-fade:] *= np.linspace(1, 0, fade)
            write(os.path.join(out_dir, f"{n}_{slot}.wav"), x)
            sources[slot] = f"{b}:{i} ({files(b)[i]})"
        kits.append({"name": name, "bank": f"kit_{name}", "styles": STYLES[family], "sources": sources})
        print(f"  {name:12s} " + "  ".join(f"{s} {v}" for s, v in sources.items()))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"_about": "eval/drum_kits.py: each kit's bank (0 kick, 1 snare, 2 clap, 3 hat, 4 open hat), "
                             "levelled to the stream's own hits", "kits": kits}, f, indent=1)
    print(f"{len(kits)} kits -> {OUT}; restart the stream so SuperDirt loads samples/kit_*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
