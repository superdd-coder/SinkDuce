"""Shared image utilities: filtering, Vision LLM description, path resolution."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from src.parsers.base import ImageInfo

logger = logging.getLogger(__name__)

# ── filtering thresholds ────────────────────────────────────────────────

_MIN_WIDTH = 150       # px
_MIN_HEIGHT = 150      # px
_MIN_AREA = 20_000     # px²
_MAX_REPEAT_PAGES = 3  # same hash on >= N pages → logo / template element
_SAFE_RASTER = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp"})
_OFFICE_VECTOR_EXTS = frozenset({"wmf", "emf", "emz", "wmz"})
# Placeable WMF header. Standard WMF/EMF magics overlap other formats.
_PLACEABLE_WMF = b"\xd7\xcd\xc6\x9a"
OCR_TIMEOUT_SEC = 20
# RapidOCR / packing: 1600px is enough to classify text vs visual.
OCR_MAX_SIDE = 1600


def _format_ext(declared: str) -> str:
    ext = (declared or "").lower().lstrip(".")
    if ext.startswith("image/"):
        ext = ext.split("/", 1)[1]
    if ext.startswith("x-"):
        ext = ext[2:]
    return re.sub(r"[^a-z0-9]+", "", ext)


def is_office_vector_format(declared: str) -> bool:
    return _format_ext(declared) in _OFFICE_VECTOR_EXTS


def sniff_raster_format(image_bytes: bytes) -> str | None:
    """Return png/jpeg/gif/webp/bmp from magic bytes, else None."""
    if not image_bytes or len(image_bytes) < 12:
        return None
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if image_bytes.startswith(b"GIF8"):
        return "gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "webp"
    if image_bytes.startswith(b"BM"):
        return "bmp"
    return None


def _skip_pillow_decode(image_bytes: bytes, declared_format: str) -> bool:
    """True for Office vectors Pillow often hangs on (no raster magic)."""
    if sniff_raster_format(image_bytes) is not None:
        return False
    return is_office_vector_format(declared_format) or image_bytes.startswith(_PLACEABLE_WMF)


def normalize_raster_image(
    image_bytes: bytes,
    declared_format: str = "",
) -> tuple[bytes, str] | None:
    """Re-encode Office/raw bytes as PNG or JPEG that Vision APIs can open.

    PPTX/Excel often embed WMF/EMF (``image/x-wmf``). DashScope rejects those
    with 400 ``image format is illegal``. Returns None if Pillow cannot read
    the payload. Office vectors with no raster magic are skipped — Pillow
    (libwmf) can hang and would stall every other ingest on the OCR/parse
    pools.
    """
    if not image_bytes:
        return None
    declared = _format_ext(declared_format)
    if _skip_pillow_decode(image_bytes, declared_format):
        logger.info(
            "[ImageNorm] skip Office vector fmt=%s (%d bytes) — not a raster",
            declared or "unknown",
            len(image_bytes),
        )
        return None
    try:
        from PIL import Image
        import io

        with Image.open(io.BytesIO(image_bytes)) as im:
            im.load()
            src_fmt = (im.format or "").upper()
            if src_fmt == "JPEG" and declared in ("", "jpg", "jpeg"):
                return image_bytes, "jpeg"
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                converted = im.convert("RGBA")
            elif im.mode != "RGB":
                converted = im.convert("RGB")
            else:
                converted = im
            buf = io.BytesIO()
            converted.save(buf, format="PNG")
            return buf.getvalue(), "png"
    except Exception:
        logger.info(
            "[ImageNorm] cannot rasterize fmt=%s (%d bytes)",
            declared or "unknown",
            len(image_bytes),
        )
        return None


def _image_hash(image_bytes: bytes) -> str:
    """SHA-256 of raw image bytes."""
    return hashlib.sha256(image_bytes).hexdigest()


def _is_too_small(
    bbox: tuple | None,
    image_bytes: bytes | None = None,
    image_format: str = "",
) -> bool:
    """Check if image dimensions or area are below thresholds.

    If bbox is None, try to infer from image_bytes via Pillow.
    Returns True (too small → skip) or False (keep).
    """
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        w = abs(x1 - x0)
        h = abs(y1 - y0)
        if w < _MIN_WIDTH and h < _MIN_HEIGHT:
            return True
        if w * h < _MIN_AREA:
            return True
        return False

    # No bbox — try image bytes. Never Image.open Office vectors (can hang).
    if image_bytes is not None and not _skip_pillow_decode(image_bytes, image_format):
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(image_bytes)) as img:
                w, h = img.size
                if w < _MIN_WIDTH and h < _MIN_HEIGHT:
                    return True
                if w * h < _MIN_AREA:
                    return True
        except Exception:
            pass

    return False


def filter_images(images: list[ImageInfo]) -> list[ImageInfo]:
    """Apply size, repetition, and data-availability filters. Returns kept images.

    Filters applied (return True means *skip*):
    0. No bytes: image_bytes is None or empty → skip (can't save or describe)
    1. Size: bbox or image_bytes dimensions below thresholds
    2. Repetition: same hash appears on >= MAX_REPEAT_PAGES pages
    """
    if not images:
        return []

    # 0. Skip images without byte data (e.g. vector elements pdfplumber can't render)
    kept = []
    for img in images:
        if img.image_bytes and len(img.image_bytes) > 0:
            kept.append(img)
        else:
            logger.debug(
                "[ImageFilter] Skipped (no bytes): img_id=%s page=%s slide=%s",
                img.image_id, img.page_number, img.slide_number,
            )

    # 1. Size filter
    kept2 = []
    for img in kept:
        if not _is_too_small(img.bbox, img.image_bytes, getattr(img, "image_format", "") or ""):
            kept2.append(img)
        else:
            logger.debug(
                "[ImageFilter] Skipped (too small): img_id=%s page=%s slide=%s",
                img.image_id, img.page_number, img.slide_number,
            )

    # 2. Repetition filter
    hash_page_map: dict[str, set[int]] = {}
    for img in kept2:
        if img.image_bytes is None:
            continue
        h = _image_hash(img.image_bytes)
        page = img.page_number or img.slide_number or 0
        hash_page_map.setdefault(h, set()).add(page)

    result = []
    for img in kept2:
        if img.image_bytes is None:
            result.append(img)
            continue
        h = _image_hash(img.image_bytes)
        if len(hash_page_map.get(h, set())) >= _MAX_REPEAT_PAGES:
            logger.debug(
                "[ImageFilter] Skipped (repeated %d pages): img_id=%s hash=%s",
                len(hash_page_map[h]), img.image_id, h[:16],
            )
            continue
        result.append(img)

    return result


# ── OCR classification ───────────────────────────────────────────────────

_OCR_MIN_CHARS = 30        # min chars: below this → visual (Vision LLM)
_OCR_TEXT_ONLY_CHARS = 200 # above this + clean → pure text (OCR only, skip Vision LLM)
_OCR_MIN_CONFIDENCE = 60   # min mean confidence


def _ocr_text_is_garbage(text: str) -> bool:
    """Heuristic: OCR text is likely garbage if it contains too many short
    fragments (1-2 char tokens) or non-word symbols — typical of OCR
    hallucinating on flowcharts, diagrams, and photos.

    Real English/Chinese text has mostly 3+ character words; garbage OCR
    from graphics produces scattered single letters, digits, and symbols
    (e.g. "a 7 é 5 4 3 2 1 us wunimasway ...").
    """
    if not text:
        return True
    tokens = text.split()
    if not tokens:
        return True
    # Count short fragments (1-2 chars) that are NOT CJK (single CJK char
    # can be a valid word in Chinese)
    short = 0
    for t in tokens:
        if len(t) <= 2 and not ("一" <= t <= "鿿") and not t.isalpha():
            short += 1
    ratio = short / len(tokens)
    return ratio > 0.3


def _image_for_ocr(image_bytes: bytes, image_format: str = ""):
    """Rasterize and downscale for OCR. None if not a readable raster."""
    normalized = normalize_raster_image(image_bytes, image_format)
    if normalized is None:
        return None
    data, _fmt = normalized
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(data))
    img.load()
    w, h = img.size
    longest = max(w, h)
    if longest > OCR_MAX_SIDE:
        scale = OCR_MAX_SIDE / float(longest)
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.BILINEAR,
        )
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def _ocr_image(
    image_bytes: bytes,
    lang: str = "",
    image_format: str = "",
) -> tuple[str, float]:
    """Run bundled RapidOCR. Returns (text, mean_confidence 0–100).

    Non-raster Office vectors are skipped. Images are downscaled.
    ``lang`` is ignored (PP-OCRv6 small is multilingual).
    """
    try:
        img = _image_for_ocr(image_bytes, image_format)
    except Exception:
        logger.debug("[OCR] failed to prepare raster", exc_info=True)
        return "", 0.0
    if img is None:
        return "", 0.0
    from src.parsers.rapid_ocr import ocr_array

    return ocr_array(img)


def _classify_image(
    image_bytes: bytes,
    image_format: str = "",
    lang: str = "eng",
) -> tuple[str, str]:
    """Classify image as 'text', 'mixed', or 'visual' based on OCR quality.

    Returns (classification, ocr_text).
    - 'text': dense high-confidence clean text → OCR only, skip Vision LLM
    - 'mixed': moderate text or text with garbage characters → OCR + Vision LLM
    - 'visual': little or low-quality text → Vision LLM only
    """
    ocr_text, confidence = _ocr_image(
        image_bytes, lang=lang, image_format=image_format
    )
    chars = len(ocr_text)

    if chars >= _OCR_TEXT_ONLY_CHARS and confidence >= _OCR_MIN_CONFIDENCE:
        if _ocr_text_is_garbage(ocr_text):
            logger.debug("[OCR] TEXT→MIXED (garbage ratio): %d chars, conf=%.0f%%", chars, confidence)
            return "mixed", ocr_text
        logger.debug("[OCR] Classified as TEXT: %d chars, conf=%.0f%%", chars, confidence)
        return "text", ocr_text
    if chars >= _OCR_MIN_CHARS and confidence >= _OCR_MIN_CONFIDENCE:
        logger.debug("[OCR] Classified as MIXED: %d chars, conf=%.0f%%", chars, confidence)
        return "mixed", ocr_text
    logger.debug("[OCR] Classified as VISUAL: %d chars, conf=%.0f%%", chars, confidence)
    return "visual", ocr_text


# ── Vision LLM description ──────────────────────────────────────────────

def _describe_one(
    img: ImageInfo,
    visual_llm,
    prompt: str,
    retries: int = 3,
) -> str:
    """Describe one image via Vision LLM. Returns description or empty string on failure."""
    if img.image_bytes is None:
        return ""

    normalized = normalize_raster_image(img.image_bytes, img.image_format)
    if normalized is None:
        logger.warning(
            "[ImageDescribe] img_id=%s fmt=%s not a readable raster — skip Vision",
            img.image_id, img.image_format,
        )
        return ""
    raster, fmt = normalized
    image_base64 = base64.b64encode(raster).decode("utf-8")
    mime = "image/jpeg" if fmt in ("jpg", "jpeg") else f"image/{fmt}"

    from src.providers.retry import (
        is_rate_limit_error,
        is_timeout_error,
        is_unretryable_image_error,
        retry_delay,
    )
    from src.rag.contextual import ingest_request_limiter

    for attempt in range(1, retries + 1):
        try:
            # Same 50-request cap as Summary/Context so one file's Vision
            # fan-out cannot 429 / stall every other ingest.
            with ingest_request_limiter:
                description = visual_llm.describe_image(image_base64, mime, prompt=prompt)
            logger.info(
                "[ImageDescribe] img_id=%s attempt=%d/%d len=%d",
                img.image_id, attempt, retries, len(description),
            )
            return description.strip()
        except Exception as exc:
            if is_unretryable_image_error(exc) or is_timeout_error(exc):
                logger.warning(
                    "[ImageDescribe] img_id=%s skipped (%s)",
                    img.image_id, type(exc).__name__,
                )
                return ""
            if is_rate_limit_error(exc) and attempt < retries:
                delay = retry_delay(attempt)
                logger.warning(
                    "[ImageDescribe] img_id=%s rate-limited attempt %d/%d, sleeping %.1fs",
                    img.image_id, attempt, retries, delay,
                )
                time.sleep(delay)
                continue
            logger.warning(
                "[ImageDescribe] img_id=%s attempt=%d/%d failed",
                img.image_id, attempt, retries, exc_info=True,
            )

    logger.error("[ImageDescribe] img_id=%s all %d attempts failed, skipping", img.image_id, retries)
    return ""


def describe_images(
    images: list[ImageInfo],
    provider,
    model_id: str,
    prompt: str,
    max_workers: int = 5,
    on_image_done: Callable[[], None] | None = None,
) -> list[ImageInfo]:
    """Concurrently describe images using a Vision LLM.

    Args:
        images: Images to describe (filtered list).
        provider: The LLM provider config object (has visual_model_ids).
        model_id: The specific vision model ID to use.
        prompt: The system/user prompt for image description.
        max_workers: Max concurrent Vision LLM calls (also gated by the
            process-wide ingest request limiter).
        on_image_done: Called once per image when its describe attempt finishes
            (success or failure) — for completed-work progress tracking.

    Returns:
        Images with ``description`` filled in. Only images that got a
        non-empty description are included.
    """
    if not images:
        return []

    from src.providers.llm import create_llm_for_provider

    try:
        visual_llm = create_llm_for_provider(provider, model=model_id)
    except Exception as e:
        logger.warning("[ImageDescribe] Failed to create vision LLM: %s", e)
        # Count all as done so progress does not stall
        if on_image_done:
            for _ in images:
                try:
                    on_image_done()
                except Exception:
                    pass
        return []

    results: list[ImageInfo] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(images))) as executor:
        futures = {
            executor.submit(_describe_one, img, visual_llm, prompt): img
            for img in images
        }
        for future in as_completed(futures):
            img = futures[future]
            try:
                description = future.result()
                if description:
                    img.description = description
                    results.append(img)
                else:
                    logger.debug("[ImageDescribe] img_id=%s no description, skipped", img.image_id)
            except Exception:
                logger.exception("[ImageDescribe] img_id=%s unexpected error", img.image_id)
            finally:
                if on_image_done:
                    try:
                        on_image_done()
                    except Exception:
                        pass

    logger.info("[ImageDescribe] %d/%d images described successfully", len(results), len(images))
    return results


# ── path resolution ──────────────────────────────────────────────────────

def resolve_image_path(file_id: str, image_id: str) -> Path | None:
    """Find image file on disk from file_id + image_id.

    Searches ``files/{file_id}/images/`` (legacy) and
    ``files/{file_id}/{version_id}/images/`` across collections.
    """
    from src.file_mgmt.storage_paths import find_image_file

    return find_image_file(None, file_id, image_id)


def _resolve_image_path_direct(file_id: str, image_id: str, collection: str) -> Path | None:
    """Direct path resolution when collection is known — no full collection scan."""
    from src.file_mgmt.storage_paths import find_image_file

    path = find_image_file(collection, file_id, image_id)
    if path is None:
        logger.warning(
            "[ImageStitch] image not found: file_id=%s image_id=%s col=%s",
            file_id,
            image_id,
            collection,
        )
    return path


def encode_image_base64(image_id: str, file_id: str) -> tuple[str, str] | None:
    """Encode an image as base64. Returns (base64_string, mime_type) or None."""
    path = resolve_image_path(file_id, image_id)
    if path is None:
        return None
    try:
        data = path.read_bytes()
        normalized = normalize_raster_image(data, path.suffix.lstrip("."))
        if normalized is None:
            logger.warning("[ImageStitch] cannot open %s", path)
            return None
        raster, fmt = normalized
        mime = "image/jpeg" if fmt in ("jpg", "jpeg") else f"image/{fmt}"
        return base64.b64encode(raster).decode("utf-8"), mime
    except Exception:
        logger.exception("[ImageStitch] failed to encode: %s", path)
        return None


def _encode_image_base64_direct(image_id: str, file_id: str, collection: str) -> tuple[str, str] | None:
    """Encode image using direct path (collection known) — no directory scan."""
    path = _resolve_image_path_direct(file_id, image_id, collection)
    if path is None:
        # Fallback to full scan
        path = resolve_image_path(file_id, image_id)
    if path is None:
        logger.warning("[ImageStitch] image not found: file_id=%s image_id=%s col=%s", file_id, image_id, collection)
        return None

    try:
        data = path.read_bytes()
        normalized = normalize_raster_image(data, path.suffix.lstrip("."))
        if normalized is None:
            logger.warning("[ImageStitch] cannot open %s", path)
            return None
        raster, fmt = normalized
        mime = "image/jpeg" if fmt in ("jpg", "jpeg") else f"image/{fmt}"
        logger.debug("[ImageStitch] encoded %s (%d bytes) from %s", image_id[:12], len(raster), path)
        return base64.b64encode(raster).decode("utf-8"), mime
    except Exception:
        logger.exception("[ImageStitch] failed to encode: %s", path)
        return None


# ── block helpers ─────────────────────────────────────────────────────────

IMAGE_BLOCK_PATTERN = ":::image"

# Matches a :::image block. Uses negative-lookahead to avoid crossing ::: boundaries.
# Note: no \s* before \n — when value is empty, \s* would consume the newline
# and break the match. Each line ends with the value (possibly empty) then \n.
_IMAGE_BLOCK_RE = re.compile(
    r":::image[ \t]*\n"
    r"image_id:[ \t]*([a-f0-9]+)[ \t]*\n"
    r"file_id:[ \t]*([^\n]*)\n"
    r"(?:ocr_text:[ \t]*((?:(?!:::).)*?)\n)?"        # optional — absent when ocr_text is empty
    r"(?:description:[ \t]*((?:(?!:::).)*?)\n)?"      # optional — absent when description is empty
    r":::",
    re.DOTALL,
)


def _sanitize_fence_field(value: str) -> str:
    """Keep ocr/description as a single fence line (no leaked body paragraphs)."""
    return re.sub(r"\s+", " ", (value or "").replace("\r", " ")).strip()


def build_image_block(img: ImageInfo) -> str:
    """Build a :::image fenced block string.
    Empty ocr_text / description lines are omitted to keep the block clean.
    Newlines inside fields are collapsed so they cannot close or escape the fence.
    """
    lines = [
        ":::image",
        f"image_id: {img.image_id}",
        f"file_id: {img.file_id}",
    ]
    ocr = _sanitize_fence_field(img.ocr_text)
    desc = _sanitize_fence_field(img.description)
    if ocr:
        lines.append(f"ocr_text: {ocr}")
    if desc:
        lines.append(f"description: {desc}")
    lines.append(":::")
    return "\n".join(lines) + "\n"


def iter_image_fence_fields(text: str) -> list[dict[str, str]]:
    """Parse ``:::image`` fences line-by-line (CRLF-safe, field order free)."""
    if not text or ":::image" not in text:
        return []
    out: list[dict[str, str]] = []
    i = 0
    n = len(text)
    while i < n:
        idx = text.find(":::image", i)
        if idx < 0:
            break
        pos = idx
        fields = {"image_id": "", "file_id": "", "ocr_text": "", "description": ""}
        close_end: int | None = None
        first = True
        while pos < n:
            nl = text.find("\n", pos)
            line_end = n if nl < 0 else nl
            stripped = text[pos:line_end].strip()
            next_pos = n if nl < 0 else nl + 1
            if not first and stripped == ":::":
                close_end = next_pos
                break
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip().lower()
                if key in fields:
                    fields[key] = val.strip()
            first = False
            if nl < 0:
                break
            pos = next_pos
        if close_end is None:
            break
        if fields["image_id"]:
            out.append(fields)
        i = close_end
    return out


def refresh_chunk_image_refs(
    text: str,
    existing: list | None = None,
    img_map: dict | None = None,
) -> list[dict]:
    """Build ``metadata.images`` from fences, merged with ImageInfo / stored refs."""
    by_id: dict[str, dict] = {}
    for raw in existing or []:
        if isinstance(raw, dict) and raw.get("image_id"):
            by_id[str(raw["image_id"])] = dict(raw)
    img_map = img_map or {}
    for fence in iter_image_fence_fields(text or ""):
        img_id = fence["image_id"]
        rec = by_id.get(img_id, {"image_id": img_id})
        img = img_map.get(img_id)
        file_id = ""
        if img is not None:
            file_id = getattr(img, "file_id", "") or ""
            if getattr(img, "page_number", None) is not None:
                rec["page_number"] = img.page_number
            if getattr(img, "slide_number", None) is not None:
                rec["slide_number"] = img.slide_number
        rec["file_id"] = file_id or fence.get("file_id") or rec.get("file_id") or ""
        desc = ""
        if img is not None:
            desc = (getattr(img, "description", None) or "").strip()
        rec["description"] = desc or fence.get("description") or rec.get("description") or ""
        ocr = ""
        if img is not None:
            ocr = (getattr(img, "ocr_text", None) or "").strip()
        rec["ocr_text"] = ocr or fence.get("ocr_text") or rec.get("ocr_text") or ""
        by_id[img_id] = rec
    return list(by_id.values())


def _find_image_block_spans(content: str, image_id: str) -> list[tuple[int, int]]:
    """Byte spans of every ``:::image`` fence whose ``image_id`` matches.

    Walks the original string so ``\\r\\n`` fences are not shortened by
    rejoining on ``\\n`` (that used to leave ``description:`` outside the fence).
    """
    if not content or not image_id or ":::image" not in content:
        return []
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(content)
    needle = ":::image"
    while i < n:
        idx = content.find(needle, i)
        if idx < 0:
            break
        pos = idx
        found_id = ""
        close_end: int | None = None
        first_line = True
        while pos < n:
            nl = content.find("\n", pos)
            line_end = n if nl < 0 else nl
            line = content[pos:line_end]
            next_pos = n if nl < 0 else nl + 1
            stripped = line.strip()
            if not first_line and stripped == ":::":
                close_end = next_pos
                break
            if stripped.lower().startswith("image_id:"):
                found_id = stripped.split(":", 1)[1].strip()
            first_line = False
            if nl < 0:
                break
            pos = next_pos
        if close_end is None:
            break
        if found_id == image_id:
            spans.append((idx, close_end))
        i = close_end
    return spans


# ── document-level image processing ─────────────────────────────────────

def _safe_image_ext(declared: str) -> str:
    ext = (declared or "bin").lower().lstrip(".")
    if ext.startswith("x-"):
        ext = ext[2:]
    ext = re.sub(r"[^a-z0-9]+", "", ext) or "bin"
    return ext[:8]


def _save_image_to_disk(file_dir: Path, img: ImageInfo) -> bool:
    """Save image bytes to disk. True only when the file is a displayable raster.

    Unreadable Office vectors are still written for debugging, but the
    caller must drop their ``:::image`` fence so ImageStitch / the UI
    do not request a file they cannot open.
    """
    if img.image_bytes is None:
        return False
    images_dir = file_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    normalized = normalize_raster_image(img.image_bytes, img.image_format)
    displayable = normalized is not None
    if normalized is not None:
        data, fmt = normalized
        img.image_bytes = data
        img.image_format = fmt
    else:
        data = img.image_bytes
        fmt = _safe_image_ext(img.image_format)
        img.image_format = fmt
        logger.warning(
            "[ImageSave] could not rasterize %s (fmt=%s) — saved original, dropping fence",
            img.image_id, fmt,
        )
    img_path = images_dir / f"{img.image_id}.{fmt}"
    try:
        img_path.write_bytes(data)
        return displayable
    except Exception:
        logger.exception("[ImageSave] Failed to save %s", img_path)
        return False


def _remove_image_block_from_content(content: str, image_id: str) -> str:
    """Remove every :::image block for a specific image_id from content."""
    spans = _find_image_block_spans(content, image_id)
    if not spans:
        return content
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(content[cursor:start])
        cursor = end
    out.append(content[cursor:])
    return "".join(out)


def _update_description_in_content(content: str, img: ImageInfo) -> str:
    """Rebuild every ``:::image`` fence for this image from current ImageInfo.

    Replaces by scanned span (not a fragile regex) so multiline OCR / a
    leftover empty ``description:`` line cannot push the new description
    outside the fence. Replacement is concatenated, not ``re.sub``, so
    backslashes in the description stay literal.
    """
    if not content or not img.image_id:
        return content or ""
    spans = _find_image_block_spans(content, img.image_id)
    if not spans:
        return content
    block = build_image_block(img)
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(content[cursor:start])
        out.append(block)
        cursor = end
    out.append(content[cursor:])
    return "".join(out)


def _split_images_by_ocr_class(
    images: list[ImageInfo],
    *,
    classified: bool = True,
) -> tuple[list[ImageInfo], list[ImageInfo], list[ImageInfo], list[ImageInfo]]:
    """Split into text / mixed / visual / table-source.

    When *classified* is False, non-table images are all treated as visual
    (pending OCR) so they are not dropped.
    """
    table_source: list[ImageInfo] = []
    text_images: list[ImageInfo] = []
    mixed_images: list[ImageInfo] = []
    visual_images: list[ImageInfo] = []
    for img in images:
        if img.is_table_source:
            table_source.append(img)
            continue
        if not classified:
            visual_images.append(img)
            continue
        kind = getattr(img, "_ocr_kind", None)
        if kind == "text":
            text_images.append(img)
        elif kind == "mixed":
            mixed_images.append(img)
        else:
            visual_images.append(img)
    return text_images, mixed_images, visual_images, table_source


def rewrite_image_fences_in_document(doc) -> None:
    """Rewrite every :::image fence from current ImageInfo fields."""
    if not getattr(doc, "images", None):
        return
    for img in doc.images:
        doc.content = _update_description_in_content(doc.content, img)


def ocr_classify_document_images(doc, *, write_content: bool = True) -> None:
    """OCR-classify kept images and write ``ocr_text`` back into fences.

    Table-source images are skipped. Mutates *doc* in place.
    When *write_content* is False, only ImageInfo fields are updated so OCR
    can run beside Vision without racing on ``doc.content``.
    """
    if not doc.images:
        return
    from src.parsers.rapid_ocr import (
        OCR_ENGINE_COUNT,
        backlog_add,
        backlog_done,
        target_engine_count,
    )

    jobs: list = []
    text_n = mixed_n = visual_n = table_n = 0
    for img in doc.images:
        if img.is_table_source:
            table_n += 1
            continue
        if img.image_bytes is None:
            visual_n += 1
            continue
        jobs.append(img)

    def _apply(img, img_type: str, ocr_text: str) -> None:
        nonlocal text_n, mixed_n, visual_n
        img._ocr_kind = img_type
        usable = (ocr_text or "").strip() and not _ocr_text_is_garbage(ocr_text)
        if img_type == "text":
            img.ocr_text = ocr_text
            text_n += 1
        elif img_type == "mixed":
            img.ocr_text = ocr_text
            mixed_n += 1
        else:
            img.ocr_text = ocr_text if usable else ""
            visual_n += 1

    accounted = 0
    if jobs:
        pending = backlog_add(len(jobs))

        def _work(img):
            try:
                kind, text = _classify_image(
                    img.image_bytes,
                    getattr(img, "image_format", "") or "",
                )
                return img, kind, text
            finally:
                backlog_done(1)

        try:
            workers = min(OCR_ENGINE_COUNT, target_engine_count(), len(jobs))
            logger.info(
                "[OCR] classify %d images with %d engine(s) (backlog=%d)",
                len(jobs),
                workers,
                pending,
            )
            if workers <= 1:
                results = [_work(img) for img in jobs]
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    results = list(pool.map(_work, jobs))
            for img, kind, text in results:
                accounted += 1
                _apply(img, kind, text)
                if write_content and img.ocr_text:
                    doc.content = _update_description_in_content(doc.content, img)
        finally:
            leftover = len(jobs) - accounted
            if leftover > 0:
                backlog_done(leftover)

    logger.info(
        "[ImageProcess] OCR classification: %d text, %d mixed, %d visual, %d table-source",
        text_n, mixed_n, visual_n, table_n,
    )


def process_document_images(
    doc: ParsedDocument,
    file_id: str,
    file_dir: Path,
    *,
    vision_provider=None,
    vision_model_id: str = "",
    vision_prompt: str = "",
    on_image_done: Callable[[], None] | None = None,
    on_describe_planned: Callable[[int], None] | None = None,
    describe: bool = True,
    ocr: bool = True,
) -> ParsedDocument:
    """Post-process a ParsedDocument's images: filter, save, describe, update content.

    Modifies ``doc`` in-place and returns it.

    1. Sets ``file_id`` on all images
    2. Filters out small / repeated images → removes their :::image blocks from content
    3. Saves remaining images to disk
    4. Optionally OCR-classifies (skipped when *ocr* is False so Summary can start)
    5. If Vision LLM configured and *describe* is True, describes images concurrently
    6. Updates :::image blocks in content with descriptions

    When *ocr* is False, images are only filtered and saved — no RapidOCR.
    When *describe* is False, mixed/visual images keep ``image_bytes`` so a
    later ``describe_document_images`` call can run in parallel with chunking.

    Args:
        doc: Parsed document with images.
        file_id: The file's unique ID (used for storage path).
        file_dir: Directory where the file's data lives (parent of images/ dir).
        vision_provider: LLM provider config with visual_model_ids.
        vision_model_id: The vision model to use. If empty, skip description.
        vision_prompt: Prompt for image description.
        on_image_done: Per-image completion callback (after each describe finishes).
        on_describe_planned: Called with N before describing, so callers can
            register N work units for progress tracking.
    """
    if not doc.images:
        return doc

    # 1. Set file_id on all images
    for img in doc.images:
        img.file_id = file_id

    # 2. Filter
    before_count = len(doc.images)
    kept = filter_images(list(doc.images))

    # Remove filtered-out images' blocks from content
    kept_ids = {img.image_id for img in kept}
    for img in doc.images:
        if img.image_id not in kept_ids:
            doc.content = _remove_image_block_from_content(doc.content, img.image_id)

    doc.images = kept
    logger.info(
        "[ImageProcess] Filter: %d → %d images kept for %s",
        before_count, len(kept), file_id,
    )

    if not doc.images:
        return doc

    if ocr:
        ocr_classify_document_images(doc)
        kept = list(doc.images)

    text_images, mixed_images, visual_images, table_source_images = _split_images_by_ocr_class(
        kept, classified=ocr
    )
    pending_ocr = [
        i for i in kept if not i.is_table_source
    ] if not ocr else []

    logger.info(
        "[ImageProcess] classes: %d text, %d mixed, %d visual, %d table-source, %d pending-ocr (of %d total)",
        len(text_images), len(mixed_images), len(visual_images),
        len(table_source_images), len(pending_ocr), len(kept),
    )

    # 4. Describe MIXED + VISUAL images with Vision LLM (text-only images skip this)
    needs_description = mixed_images + visual_images
    if describe and vision_provider and vision_model_id and needs_description:
        logger.info("[ImageProcess] Describing %d images (mixed+visual) with %s",
                    len(needs_description), vision_model_id)
        if on_describe_planned:
            try:
                on_describe_planned(len(needs_description))
            except Exception:
                pass
        described = describe_images(
            needs_description,
            vision_provider,
            vision_model_id,
            vision_prompt,
            on_image_done=on_image_done,
        )
    else:
        described = []

    described_ids = {img.image_id for img in described}

    # Text images (always keep) + successfully described mixed/visual images
    # + table source images (always keep, no OCR/description)
    if describe and vision_provider and vision_model_id:
        final_images = text_images + described + table_source_images
        needs_ids = {img.image_id for img in needs_description}
        for image_id in (needs_ids - described_ids):
            doc.content = _remove_image_block_from_content(doc.content, image_id)
            logger.warning("[ImageProcess] Failed to describe image %s, block removed", image_id[:16])
    else:
        if needs_description and not describe:
            logger.info(
                "[ImageProcess] Deferring Vision for %d mixed+visual images",
                len(needs_description),
            )
        elif needs_description:
            logger.info(
                "[ImageProcess] No Vision LLM — keeping %d mixed+visual image blocks without descriptions",
                len(needs_description),
            )
        final_images = (
            list(kept) if not ocr
            else text_images + needs_description + table_source_images
        )

    doc.images = final_images

    # Update content blocks with OCR text and descriptions
    for img in final_images:
        doc.content = _update_description_in_content(doc.content, img)

    n_text = len([i for i in final_images if i.ocr_text and not i.description])
    n_mixed = len([i for i in final_images if i.ocr_text and i.description])
    n_visual = len([i for i in final_images if i.description and not i.ocr_text])
    n_table_src = len([i for i in final_images if i.is_table_source])
    logger.info(
        "[ImageProcess] %d images final: %d text-only, %d mixed, %d visual-only, %d table-source for %s",
        len(final_images), n_text, n_mixed, n_visual, n_table_src, file_id,
    )

    # Save images to disk. Clear bytes unless a later OCR/describe pass needs them.
    if not ocr:
        keep_bytes = {img.image_id for img in doc.images if not img.is_table_source}
    elif not describe:
        keep_bytes = {img.image_id for img in needs_description}
    else:
        keep_bytes = set()
    saved_count = 0
    saved_ids: set[str] = set()
    for img in doc.images:
        if _save_image_to_disk(file_dir, img):
            saved_count += 1
            saved_ids.add(img.image_id)
        if img.image_id not in keep_bytes:
            img.image_bytes = None

    if saved_count != len(doc.images):
        for img in list(doc.images):
            if img.image_id in saved_ids:
                continue
            doc.content = _remove_image_block_from_content(doc.content, img.image_id)
            logger.warning(
                "[ImageProcess] dropped unsaved image %s so fences do not dangle",
                img.image_id,
            )
        doc.images = [img for img in doc.images if img.image_id in saved_ids]

    if saved_count:
        logger.info("[ImageProcess] Saved %d/%d images to disk for %s",
                    saved_count, len(doc.images), file_id)

    return doc


def describe_document_images(
    doc,
    *,
    vision_provider=None,
    vision_model_id: str = "",
    vision_prompt: str = "",
    on_image_done: Callable[[], None] | None = None,
    on_describe_planned: Callable[[int], None] | None = None,
    write_content: bool = True,
    clear_bytes: bool = True,
):
    """Run Vision descriptions on images that still need them and write fences.

    Does not run OCR — that is a separate parallel step. Table-source
    images are skipped. Images already classified as text-only are
    skipped; unclassified images are described so Vision need not wait
    for RapidOCR.

    Failures keep the empty fence (ingest is not blocked).
    """
    if not doc.images or not vision_provider or not vision_model_id:
        return doc

    pending = [
        img for img in doc.images
        if not img.is_table_source
        and not img.description
        and img.image_bytes
        and getattr(img, "_ocr_kind", None) != "text"
    ]
    if not pending:
        return doc

    logger.info("[ImageProcess] Describing %d deferred images with %s",
                len(pending), vision_model_id)
    if on_describe_planned:
        try:
            on_describe_planned(len(pending))
        except Exception:
            pass
    described = describe_images(
        pending,
        vision_provider,
        vision_model_id,
        vision_prompt,
        on_image_done=on_image_done,
    )
    described_ids = {img.image_id for img in described}
    for img in doc.images:
        if write_content and img.image_id in described_ids:
            doc.content = _update_description_in_content(doc.content, img)
        if clear_bytes:
            img.image_bytes = None
    return doc


def apply_image_updates_to_chunks(chunks: list, doc_images: list[ImageInfo]) -> None:
    """Rewrite :::image fences in chunk text (file_id, OCR, description).

    Always rewrite when the image_id is present — not only after OCR/Vision —
    so empty visual fences get a real ``file_id`` and normalized newlines.
    """
    if not chunks or not doc_images:
        return
    for img in doc_images:
        if not img.image_id:
            continue
        for chunk in chunks:
            text = getattr(chunk, "text", "") or ""
            if img.image_id in text:
                chunk.text = _update_description_in_content(text, img)
    annotate_chunks_with_images(chunks, doc_images)


# ── chunk annotation ──────────────────────────────────────────────────────

def annotate_chunks_with_images(chunks: list, doc_images: list[ImageInfo]) -> list:
    """Scan chunk text for :::image blocks and add image refs to chunk metadata.

    For each chunk, extracts image_id and file_id from :::image blocks in
    its text, then looks up the matching ImageInfo to add page/slide info.

    Modifies chunks in-place. Returns the modified list.
    """
    if not doc_images:
        return chunks

    img_map: dict[str, ImageInfo] = {img.image_id: img for img in doc_images if img.image_id}

    for chunk in chunks:
        chunk_text = chunk.text if hasattr(chunk, "text") else ""
        meta = chunk.metadata if hasattr(chunk, "metadata") else {}
        refs = refresh_chunk_image_refs(
            chunk_text,
            existing=meta.get("images") if isinstance(meta.get("images"), list) else None,
            img_map=img_map,
        )
        if refs or "images" in meta:
            meta["images"] = refs

    return chunks
