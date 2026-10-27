"""One index-text recipe for ingest and sparse rebuilds."""

_IDENTITY_SOURCE_PREFIXES = (
    "__file__:",
    "__note__:",
    "__meeting__:",
    "__url__:",
    "__youtube__:",
)


def source_name(meta: dict) -> str:
    """Human filename for the Source: embedding prefix — never the identity key."""
    for key in ("source_label", "note_title"):
        label = str(meta.get(key) or "").strip()
        if label:
            return label.replace("\\", "/").rsplit("/", 1)[-1]
    source = str(meta.get("source") or "").strip()
    if not source:
        return ""
    if source.startswith(_IDENTITY_SOURCE_PREFIXES):
        return ""
    return source.replace("\\", "/").rsplit("/", 1)[-1]


def build_index_text(chunk) -> str:
    """Build text for embedding/sparse encoding from chunk text + key metadata."""
    parts = []
    filename = source_name(chunk.metadata)
    if filename:
        parts.append(f"Source: {filename}")
    sheet = chunk.metadata.get("sheet_name", "")
    if sheet:
        parts.append(f"Sheet: {sheet}")
    meeting_date = chunk.metadata.get("meeting_date", "")
    if meeting_date:
        parts.append(f"Meeting Date: {meeting_date}")
    # short_summary is stored on the chunk for INFO / consolidate, but is
    # not prepended here — a shared document line homogenizes chunk vectors.
    context = chunk.metadata.get("context", "")
    if context:
        parts.append(f"Context: {context}")
    from src.rag.chunker import (
        _strip_image_fences,
        fence_lexical_text,
        strip_table_source_fences,
    )

    # Table-source fences never enter the vector. Independent figures contribute
    # only their description/OCR, not the :::image markup.
    without_table_src = strip_table_source_fences(chunk.text)
    prose = _strip_image_fences(without_table_src).strip()
    lexical = fence_lexical_text(chunk.text)
    extra: list[str] = []
    for ref in (chunk.metadata or {}).get("images") or []:
        if not isinstance(ref, dict):
            continue
        for key in ("ocr_text", "description"):
            val = str(ref.get(key) or "").strip()
            if val and val not in lexical and val not in extra:
                extra.append(val)
    if extra:
        lexical = "\n".join(p for p in (lexical, *extra) if p)
    body = "\n".join(p for p in (lexical, prose) if p)
    if body.strip():
        parts.append(body)
    return "\n".join(parts)


def build_evidence_text(chunk) -> str:
    """Preserve ingest context for relevance decisions without changing evidence."""
    from src.parsers.image_utils import sanitize_image_readings

    text = sanitize_image_readings(getattr(chunk, "text", "") or "")
    metadata = getattr(chunk, "metadata", {})
    context = metadata.get("context", "") if isinstance(metadata, dict) else ""
    context = str(context or "").strip()
    return ("[Machine-generated relevance hint; verify facts against the original passage or image.]\n"
            f"Context: {context}\n{text}") if context else text
