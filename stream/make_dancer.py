#!/usr/bin/env python3
"""
Cut a dancer out of a studio clip for the stream page.

    python -m stream.make_dancer dance/ComfyUI_00011_.webp stream/ui/dancer/groove.webm
    python -m stream.make_dancer clip.webm out.webm --box 20,8,460,830   # one crop for a set of clips

Reads an animated WebP (or any video ffmpeg can read), makes the light,
grey, unsaturated backdrop transparent (soft edges, so hair stays hair),
crops to the dancer across all frames, scales to --height and writes a VP9
WebM with alpha that Chromium plays over the page. Done once, offline, so
the stream never keys video live.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageFilter


def frames_of(path: str) -> tuple[list[Image.Image], float]:
    """The clip's frames (RGB) and its frame rate."""
    if path.lower().endswith(".webp"):
        im = Image.open(path)
        frames = []
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            frames.append(im.convert("RGB").copy())
        return frames, 16.0                     # ComfyUI writes 16 fps (its WebP carries no timing)
    tmp = tempfile.mkdtemp()
    subprocess.run(["ffmpeg", "-v", "error", "-i", path, os.path.join(tmp, "f%04d.png")], check=True)
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=r_frame_rate", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    num, _, den = rate.strip().partition("/")
    names = sorted(os.listdir(tmp))
    return [Image.open(os.path.join(tmp, n)).convert("RGB") for n in names], float(num) / float(den or 1)


def alpha_of(frame: Image.Image, bright: float, sat: float) -> np.ndarray:
    """0 where the frame is backdrop (light and grey), 255 on the dancer:
    dark (clothes, hair, boots) or coloured (skin), with a soft ramp between."""
    rgb = np.asarray(frame, dtype=np.float32)
    hi, lo = rgb.max(axis=2), rgb.min(axis=2)
    saturation = (hi - lo) / np.maximum(hi, 1)
    lightness = rgb.mean(axis=2)
    dark = np.clip((bright - lightness) / 30.0, 0, 1)
    colour = np.clip((saturation - sat) / 0.06, 0, 1)
    alpha = np.maximum(dark, colour)
    img = Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(5))
    return np.asarray(img.filter(ImageFilter.GaussianBlur(1.2)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source")
    ap.add_argument("out")
    ap.add_argument("--height", type=int, default=640, help="output height in pixels")
    ap.add_argument("--bright", type=float, default=138, help="backdrop is lighter than this")
    ap.add_argument("--sat", type=float, default=0.11, help="and less saturated than this")
    ap.add_argument("--box", help="crop to x0,y0,x1,y1 instead of the dancer's box in this clip: give clips "
                                   "of one camera the same box and she stays the same size in all of them")
    ap.add_argument("--glow", default="#7cf2ff", help="a rim of light around her, so dark clothes read on a dark "
                                                      "page ('none' for none)")
    args = ap.parse_args()

    frames, fps = frames_of(args.source)
    alphas = [alpha_of(f, args.bright, args.sat) for f in frames]
    # One crop for the whole clip (the dancer's box over every frame, a margin
    # around it), so she doesn't jump around inside it.
    union = np.maximum.reduce([a > 128 for a in alphas])
    ys, xs = np.nonzero(union)
    if not len(xs):
        sys.exit("found no dancer in the clip (try --bright / --sat)")
    m = 12
    box = (max(0, xs.min() - m), max(0, ys.min() - m), min(frames[0].width, xs.max() + m),
           min(frames[0].height, ys.max() + m))
    if args.box:
        box = tuple(int(v) for v in args.box.split(","))
    scale = args.height / (box[3] - box[1])
    size = (int(round((box[2] - box[0]) * scale / 2)) * 2, args.height)
    glow = None if args.glow == "none" else tuple(int(args.glow.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    tmp = tempfile.mkdtemp()
    for i, (frame, alpha) in enumerate(zip(frames, alphas)):
        rgba = frame.convert("RGBA")
        rgba.putalpha(Image.fromarray(alpha))
        rgba = rgba.crop(box).resize(size, Image.LANCZOS)
        if glow:
            # The rim: her outline grown a little, blurred, in the glow colour, behind her.
            edge = rgba.getchannel("A").filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(5))
            halo = Image.new("RGBA", size, glow + (0,))
            halo.putalpha(edge.point(lambda v: int(v * 0.75)))
            halo.alpha_composite(rgba)
            rgba = halo
        rgba.save(os.path.join(tmp, f"f{i:04d}.png"))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", f"{fps:g}", "-i", os.path.join(tmp, "f%04d.png"),
                    "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "34", "-auto-alt-ref", "0",
                    "-an", args.out], check=True)
    print(f"{args.out}: {len(frames)} frames at {fps:g} fps, {size[0]}x{size[1]}, "
          f"{os.path.getsize(args.out) // 1024} KB")


if __name__ == "__main__":
    main()
