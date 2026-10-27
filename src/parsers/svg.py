"""Rasterize fenced SVG diagrams before Markdown chunking."""

from __future__ import annotations

import math
import re
import uuid
import xml.etree.ElementTree as ET
from functools import lru_cache

from src.parsers.base import ImageInfo
from src.parsers.image_utils import build_image_block

_MAX_SOURCE_BYTES = 256 * 1024
_MAX_SIDE = 2560
_OPEN_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)[\r\n]*$")


@lru_cache(maxsize=1)
def _font_options() -> dict:
    """Reuse the CJK font already shipped with the document viewer."""
    from PIL import ImageFont
    from src.config import get_frontend_dist

    fonts = sorted(get_frontend_dist().glob("**/ppt-font-cjk*.otf"))
    if not fonts:
        return {}
    path = fonts[0]
    family = ImageFont.truetype(str(path), 16).getname()[0]
    return {"font_files": [str(path)], "font_family": family, "sans_serif_family": family}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _validated_root(source: str) -> ET.Element:
    if len(source.encode("utf-8")) > _MAX_SOURCE_BYTES:
        raise ValueError("SVG source exceeds the 256 KiB limit")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", source, re.I):
        raise ValueError("SVG document types and external entities are unsupported")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid SVG XML: {exc}") from exc
    if _local_name(root.tag) != "svg":
        raise ValueError("SVG fence must contain an SVG root")
    for element in root.iter():
        name = _local_name(element.tag)
        if name in {"script", "foreignObject"}:
            raise ValueError("SVG must be a self-contained diagram")
        for key, value in element.attrib.items():
            if (_local_name(key) == "href" and not value.strip().startswith("#")) or key == "{http://www.w3.org/XML/1998/namespace}base":
                raise ValueError("SVG external resources are unsupported")
        styles = list(element.attrib.values())
        if name == "style":
            styles.append("".join(element.itertext()))
        for value in styles:
            if "@import" in value.lower() or "\\" in value:
                raise ValueError("SVG external styles are unsupported")
            for url in re.findall(r"url\s*\((.*?)\)", value, re.I | re.S):
                if not url.strip().strip("\"'").startswith("#"):
                    raise ValueError("SVG external resources are unsupported")
    return root


def _render_size(root: ET.Element) -> tuple[int, int]:
    # Unitless / px lengths and viewBox cover generated diagrams. Physical
    # units use CSS's 96 dpi; percentages fall back to the viewBox.
    units = {"": 1, "px": 1, "pt": 96 / 72, "pc": 16, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4}

    def length(value: str) -> float | None:
        match = re.fullmatch(r"\s*([+\-\d.eE]+)\s*([a-z]*)\s*", value)
        if not match or match[2] not in units:
            return None
        return float(match[1]) * units[match[2]]

    view = re.split(r"[\s,]+", root.get("viewBox", "").strip())
    try:
        vw, vh = (float(view[2]), float(view[3])) if len(view) == 4 else (300, 150)
        width = length(root.get("width", "")) or vw
        height = length(root.get("height", "")) or vh
        if not all(math.isfinite(n) and n > 0 for n in (width, height, vw, vh)):
            raise ValueError("non-positive dimensions")
        scale = min(1, _MAX_SIDE / max(width, height))
        return max(1, round(width * scale)), max(1, round(height * scale))
    except (ValueError, OverflowError) as exc:
        raise ValueError("SVG has invalid dimensions") from exc


def _render_diagram(source: str) -> ImageInfo:
    import resvg_py

    root = _validated_root(source)
    width, height = _render_size(root)
    try:
        png = resvg_py.svg_to_bytes(svg_string=source, width=width, height=height, **_font_options())
    except Exception as exc:
        raise ValueError(f"Unable to render SVG: {exc}") from exc
    labels, descriptions = [], []
    for element in root.iter():
        name = _local_name(element.tag)
        if name in {"text", "title", "desc"}:
            value = re.sub(r"\s+", " ", "".join(element.itertext())).strip()
            target = descriptions if name == "desc" else labels
            if value and value not in target:
                target.append(value)
    return ImageInfo(
        image_id=uuid.uuid4().hex, image_bytes=png, image_format="png",
        ocr_text="\n".join(labels), description="\n".join(descriptions), is_diagram=True,
    )


def extract_svg_diagrams(content: str) -> tuple[str, list[ImageInfo]]:
    """Replace SVG fences only; preserve examples nested in other code fences."""
    lines = content.splitlines(keepends=True)
    output, images = [], []
    i = 0
    while i < len(lines):
        opening = _OPEN_FENCE.match(lines[i])
        if not opening:
            output.append(lines[i])
            i += 1
            continue
        marker, language = opening.groups()
        is_svg = language.strip().lower() == "svg"
        closing = re.compile(r"^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}[ \t]*[\r\n]*$")
        end = i + 1
        while end < len(lines) and not closing.match(lines[end]):
            end += 1
        if is_svg:
            if end == len(lines):
                raise ValueError("SVG code fence is incomplete")
            image = _render_diagram("".join(lines[i + 1:end]).strip())
            images.append(image)
            output.append("\n" + build_image_block(image) + "\n")
        else:
            output.extend(lines[i:min(end + 1, len(lines))])
        i = end + 1
    return "".join(output), images
