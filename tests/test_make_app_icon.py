"""macOS Dock icon must be a full-bleed squircle, not a square or inset plate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_app_icon.py"


def _load():
    spec = importlib.util.spec_from_file_location("make_app_icon", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _blank_mark() -> Image.Image:
    return Image.new("RGBA", (8, 8), (0, 0, 0, 0))


def _alpha_bbox(img: Image.Image, threshold: int = 128) -> tuple[int, int, int, int]:
    px = img.load()
    w, h = img.size
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if px[x, y][3] < threshold:
                continue
            minx = min(minx, x)
            miny = min(miny, y)
            maxx = max(maxx, x)
            maxy = max(maxy, y)
    assert maxx >= 0, "icon has no opaque pixels"
    return minx, miny, maxx, maxy


def test_script_exists():
    assert SCRIPT.is_file()


def test_compose_corners_are_transparent():
    mod = _load()
    img = mod.compose(_blank_mark(), 128, src_w=64)
    for xy in ((0, 0), (127, 0), (0, 127), (127, 127)):
        assert img.getpixel(xy)[3] == 0, xy


def test_compose_transparent_pixels_are_blank_not_white():
    # macOS paints (255,255,255,0) as an opaque white square.
    mod = _load()
    img = mod.compose(_blank_mark(), 128, src_w=64)
    for xy in ((0, 0), (127, 0), (0, 127), (127, 127)):
        assert img.getpixel(xy) == (0, 0, 0, 0), xy


def test_compose_edge_midpoints_fill_the_dock_slot():
    # Tahoe wraps leftover canvas margin in a square glass plate.
    # The squircle must touch the canvas edges.
    mod = _load()
    img = mod.compose(_blank_mark(), 128, src_w=64)
    for xy in ((64, 0), (0, 64), (127, 64), (64, 127)):
        assert img.getpixel(xy)[3] > 128, xy


def test_compose_center_is_opaque_white():
    mod = _load()
    img = mod.compose(_blank_mark(), 128, src_w=64)
    r, g, b, a = img.getpixel((64, 64))
    assert a > 250
    assert r > 250 and g > 250 and b > 250


def test_compose_fills_the_canvas():
    mod = _load()
    size = 256
    img = mod.compose(_blank_mark(), size, src_w=64)
    minx, miny, maxx, maxy = _alpha_bbox(img)
    width_ratio = (maxx - minx + 1) / size
    height_ratio = (maxy - miny + 1) / size
    assert width_ratio >= 0.96
    assert height_ratio >= 0.96


def test_wordmark_stays_inside_the_plate():
    mod = _load()
    crop = Image.new("RGBA", (40, 16), (0, 120, 60, 255))
    size = 256
    img = mod.compose(crop, size, src_w=40)
    mask = mod.squircle_mask(size)
    px = img.load()
    m = mask.load()
    leaked = 0
    for y in range(size):
        for x in range(size):
            r, g, b, a = px[x, y]
            if a < 16:
                continue
            if m[x, y] < 16 and (g > r + 20):
                leaked += 1
    assert leaked == 0


def test_ink_crop_trims_transparent_padding():
    mod = _load()
    src = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for x in range(10, 20):
        for y in range(12, 18):
            src.putpixel((x, y), (10, 20, 30, 255))
    crop = mod._ink_crop(src)
    assert crop.size[0] <= 14
    assert crop.size[1] <= 10
