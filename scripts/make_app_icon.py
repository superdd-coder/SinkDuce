#!/usr/bin/env python3
"""Build a macOS Dock icon: white squircle + centered wordmark.

A full-bleed opaque square becomes a square glass plate on Tahoe. An
inset 824/1024 squircle leaves a transparent margin that Tahoe also
wraps in that plate — still looks square. Fill the canvas with a
squircle (transparent corners only) so Dock sees an app-shaped icon.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "public" / "favicon.png"
ICONS = ROOT / "desktop" / "tauri" / "src-tauri" / "icons"

# Full-canvas squircle. Tahoe treats leftover margin as a square glass plate.
MAC_ICON_SHAPE_RATIO = 1.0
SQUIRCLE_EXPONENT = 5.0

ICONSET_SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

TAURI_PNGS = {
    "32x32.png": 32,
    "64x64.png": 64,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
}


def squircle_mask(
    size: int,
    shape_ratio: float = MAC_ICON_SHAPE_RATIO,
    n: float = SQUIRCLE_EXPONENT,
) -> Image.Image:
    """Antialiased superellipse mask, centered on a size×size canvas."""
    if size < 1:
        raise ValueError("size must be positive")
    scale = 4 if size < 64 else 2
    hr = size * scale
    mask = Image.new("L", (hr, hr), 0)
    px = mask.load()
    half = hr * shape_ratio / 2.0
    cx = cy = hr / 2.0
    lo = max(0, int(cx - half - 2))
    hi = min(hr, int(cx + half + 3))
    for y in range(lo, hi):
        ny_n = (abs((y + 0.5) - cy) / half) ** n
        if ny_n > 1.0:
            continue
        for x in range(lo, hi):
            if ((abs((x + 0.5) - cx) / half) ** n) + ny_n <= 1.0:
                px[x, y] = 255
    return mask.resize((size, size), Image.LANCZOS)


def _ink_crop(src: Image.Image) -> Image.Image:
    px = src.load()
    w, h = src.size
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if px[x, y][3] < 16:
                continue
            minx = min(minx, x)
            miny = min(miny, y)
            maxx = max(maxx, x)
            maxy = max(maxy, y)
    if maxx < 0:
        raise SystemExit(f"no ink in {SRC}")
    pad = 2
    return src.crop(
        (
            max(0, minx - pad),
            max(0, miny - pad),
            min(w, maxx + 1 + pad),
            min(h, maxy + 1 + pad),
        )
    )


def _blank_transparent_rgb(im: Image.Image) -> Image.Image:
    """macOS treats (255,255,255,0) as opaque white; fully transparent must be 0,0,0,0."""
    red, green, blue, alpha = im.split()
    empty = Image.new("L", im.size, 0)
    return Image.merge(
        "RGBA",
        (
            Image.composite(red, empty, alpha),
            Image.composite(green, empty, alpha),
            Image.composite(blue, empty, alpha),
            alpha,
        ),
    )


def compose(crop: Image.Image, canvas_size: int, src_w: int) -> Image.Image:
    mask = squircle_mask(canvas_size)
    plate = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    fill = Image.new("RGBA", (canvas_size, canvas_size), (255, 255, 255, 255))
    plate.paste(fill, (0, 0), mask)

    plate_size = canvas_size * MAC_ICON_SHAPE_RATIO
    target_w = max(1, int(round(plate_size * (crop.size[0] / float(src_w)))))
    target_h = max(1, int(round(target_w * crop.size[1] / float(crop.size[0]))))
    mark = crop.resize((target_w, target_h), Image.LANCZOS)
    x = (canvas_size - target_w) // 2
    y = (canvas_size - target_h) // 2
    plate.alpha_composite(mark, (x, y))

    _, _, _, alpha = plate.split()
    plate.putalpha(ImageChops.multiply(alpha, mask))
    return _blank_transparent_rgb(plate)


def main() -> None:
    if not SRC.is_file():
        raise SystemExit(f"missing logo: {SRC}")
    src = Image.open(SRC).convert("RGBA")
    crop = _ink_crop(src)
    ICONS.mkdir(parents=True, exist_ok=True)

    master = compose(crop, 1024, src.size[0])
    master.save(ICONS / "icon-master.png")

    for name, size in TAURI_PNGS.items():
        compose(crop, size, src.size[0]).save(ICONS / name)

    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_images = [compose(crop, s, src.size[0]) for s in ico_sizes]
    ico_images[0].save(
        ICONS / "icon.ico",
        sizes=[(s, s) for s in ico_sizes],
        append_images=ico_images[1:],
    )

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "SinkDuce.iconset"
        iconset.mkdir()
        for name, size in ICONSET_SIZES.items():
            compose(crop, size, src.size[0]).save(iconset / name)
        icns = ICONS / "icon.icns"
        subprocess.check_call(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)])
        shutil.copy2(icns, ICONS / "AppIcon.icns")

    print(f"wrote icons under {ICONS}")


if __name__ == "__main__":
    main()
