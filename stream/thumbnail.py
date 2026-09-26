#!/usr/bin/env python3
"""
Render a YouTube thumbnail (1280x720 PNG) from the stream page's poster mode:
the current style's visual, "AI agents live-code music" and the track; or,
with --banner, the channel banner (2048x1152, text in YouTube's safe area);
with --logo, the channel logo (800x800).

    python -m stream.thumbnail                       # -> runtime/thumbnail.png
    python -m stream.thumbnail --style trance --out thumb.png
    python -m stream.thumbnail --banner --style trance   # -> runtime/banner.png
    python -m stream.thumbnail --logo --style trance     # -> runtime/logo.png

Needs the UI server running (python -m stream.ui_server, or the stream in
browser mode) and Chromium (same lookup as stream.sh, or YS_CHROMIUM).
Upload the PNG in YouTube Studio as the live stream's thumbnail.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402


def find_chromium() -> str | None:
    for name in (os.getenv("YS_CHROMIUM", ""), "chromium", "chromium-browser", "google-chrome",
                 "google-chrome-stable"):
        if name and shutil.which(name):
            return shutil.which(name)
    for pattern in (os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
                    "/opt/pw-browsers/chromium-*/chrome-linux/chrome"):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--style", help="force a style's look (default: what's playing)")
    parser.add_argument("--banner", action="store_true", help="the 2048x1152 channel banner instead")
    parser.add_argument("--logo", action="store_true", help="the 800x800 channel logo instead")
    parser.add_argument("--out", default=None, help="default: runtime/thumbnail.png (or banner.png)")
    parser.add_argument("--port", default=os.getenv("YS_UI_PORT", "8765"))
    args = parser.parse_args()
    chromium = find_chromium()
    if not chromium:
        print("Chromium not found: install it or set YS_CHROMIUM", file=sys.stderr)
        return 1
    mode = "logo" if args.logo else "banner" if args.banner else "poster"
    w, h = {"logo": (800, 800), "banner": (2048, 1152), "poster": (1280, 720)}[mode]
    args.out = args.out or os.path.join(config.RUNTIME_DIR, {"poster": "thumbnail"}.get(mode, mode) + ".png")
    url = f"http://127.0.0.1:{args.port}/?{mode}=1" + (f"&style={args.style}" if args.style else "")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        # Headless Chrome's viewport is shorter than its window, so render in a
        # taller window (the poster / banner has a fixed size) and crop.
        raw = os.path.join(tmp, "raw.png")
        subprocess.run([chromium, "--headless=new", "--no-sandbox", "--hide-scrollbars", f"--user-data-dir={tmp}/p",
                        f"--window-size={w},{h + 180}", "--virtual-time-budget=4000", f"--screenshot={raw}", url],
                       capture_output=True, timeout=60)
        if not os.path.exists(raw):
            print(f"no thumbnail written (is the UI server running on port {args.port}?)", file=sys.stderr)
            return 1
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", raw, "-vf", f"crop={w}:{h}:0:0",
                        os.path.abspath(args.out)], check=True, timeout=30)
    print(f"{ {'poster': 'thumbnail'}.get(mode, mode)}: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
