"""Project canonical image references into evidence for one receiving model.

Stored chunks and retrieval text stay unchanged. Only the outgoing request is
projected: original images for vision, explicitly uncertain machine text otherwise.
"""

from __future__ import annotations

from src.parsers.image_utils import iter_image_fence_fields
from src.rag.chunker import iter_image_fence_spans
from src.prompts import (
    IMAGE_TEXT_FALLBACK, IMAGE_ALREADY_ATTACHED, IMAGE_NOT_INCLUDED,
    IMAGE_UNAVAILABLE_REASON, IMAGE_TEXT_ONLY_REASON,
)


def model_supports_vision(llm) -> bool:
    """Use the actual provider instance's checked capability, never a name guess."""
    return getattr(llm, "supports_vision", False) is True


def image_payload(data, *, verify_raster: bool = False) -> dict | None:
    """Reject missing/empty payloads; file loaders separately verify raster bytes."""
    if not isinstance(data, dict):
        return None
    b64 = data.get("base64")
    mime = data.get("mime", "image/png")
    if not isinstance(b64, str) or not b64.strip():
        return None
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return None
    if verify_raster:
        import base64
        from io import BytesIO
        from PIL import Image
        try:
            with Image.open(BytesIO(base64.b64decode(b64, validate=True))) as original:
                original.verify()
        except (ValueError, OSError, SyntaxError):
            return None
    return {"base64": b64, "mime": mime}


def render_image_evidence(
    content: str,
    images: dict[str, dict] | None = None,
    *,
    is_visual: bool,
    skip_ids: set[str] | None = None,
    omitted_ids: set[str] | None = None,
) -> str | list[dict]:
    """Keep prose in order and replace every image fence at the model boundary.

    ``skip_ids`` denotes originals already attached to this same request, not
    an image budget. Such references never fall back to machine readings.
    """
    spans = list(iter_image_fence_spans(content or ""))
    # A document window / legacy synthesis limit can cut the last fence. Treat
    # that tail as image data too, rather than leaking unlabelled machine text.
    trailing_start = (content or "").find(":::image", spans[-1][1] if spans else 0)
    if trailing_start >= 0:
        spans.append((trailing_start, len(content), False))
    if not spans:
        return content or ""
    images = images or {}
    parts: list[dict] = []
    seen = set(skip_ids or ()) if is_visual else set()
    cursor = 0
    for start, end, _ in spans:
        prose = content[cursor:start].strip()
        if prose:
            parts.append({"type": "text", "text": prose})
        block = content[start:end]
        fields = iter_image_fence_fields(block)
        if not fields:
            fields = iter_image_fence_fields(block.rstrip() + "\n:::\n")
        ref = fields[0] if fields else {}
        iid = ref.get("image_id", "")
        payload = image_payload(images.get(iid)) if is_visual else None
        if is_visual and iid in (omitted_ids or ()):
            parts.append({"type": "text", "text": IMAGE_NOT_INCLUDED.format(image_id=iid)})
        elif iid and iid in seen:
            parts.append({"type": "text", "text": IMAGE_ALREADY_ATTACHED.format(image_id=iid)})
        elif payload:
            seen.add(iid)
            parts.append({"type": "image_url", "image_url": {
                "url": f"data:{payload['mime']};base64,{payload['base64']}"}})
        else:
            reason = IMAGE_UNAVAILABLE_REASON if is_visual else IMAGE_TEXT_ONLY_REASON
            parts.append({"type": "text", "text": IMAGE_TEXT_FALLBACK.format(
                image_id=iid or "unknown", reason=reason,
                ocr=ref.get("ocr_text", "").strip() or "(unavailable)",
                description=ref.get("description", "").strip() or "(unavailable)",
            )})
        cursor = end
    tail = content[cursor:].strip()
    if tail:
        parts.append({"type": "text", "text": tail})
    if any(p["type"] == "image_url" for p in parts):
        return parts
    return "\n".join(p["text"] for p in parts)


def evidence_for_model(content: str, chunks: list, llm) -> str | list[dict]:
    """Apply the policy to retrieved evidence, loading images only for vision."""
    if ":::image" not in (content or ""):
        return content
    is_visual = model_supports_vision(llm)
    images = {}
    if is_visual:
        # Lazy import avoids the query/planner/aggregator dependency cycle.
        from src.rag.agentic_query import _stitch_images_from_chunks
        images = _stitch_images_from_chunks(chunks)
    return render_image_evidence(content, images, is_visual=is_visual)
