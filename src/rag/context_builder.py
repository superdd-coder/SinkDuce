"""ContextBuilder — cluster-formatted context string for LLM consumption.

Pure function with zero external dependencies.  Groups RetrievedChunks by
collection → source → chunk_index and inserts gap indicators where chunks
are missing from a contiguous range.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_context(chunks: list, gap_indicators: bool = True) -> str:
    """Build a cluster-formatted context string from *chunks*.

    Chunks can be ``RetrievedChunk`` objects (with ``.text``, ``.score``,
    ``.metadata``) or plain dicts.

    Format::

        ## Database: {collection_name}
        ### Source: {source_filename}
        Uploaded: {upload_date}
        Document summary: {chunk.metadata["summary"]}

        [Chunk #1 · score: 0.92 · id: {point_id}]
        [Context: {chunk.metadata["context"]}]
        {chunk.text}

        [Note: {n} intermediate chunks (#X–#Y) from {source} were omitted ...]

    When *gap_indicators* is False, the ``[Note: ...]`` lines are skipped.
    """
    logger.debug("[Context] %d chunks, gap_indicators=%s", len(chunks), gap_indicators)

    if not chunks:
        logger.debug("[Context] empty input → empty output")
        return ""

    # ── Normalise to a uniform intermediate representation ──────────
    entries = []
    for c in chunks:
        if hasattr(c, "text"):
            text = c.text
            meta = c.metadata if hasattr(c, "metadata") else {}
            score = getattr(c, "score", 0.0)
        elif isinstance(c, dict):
            text = c.get("text", "")
            meta = c.get("metadata", {})
            score = c.get("score", 0.0)
        else:
            continue

        collection = meta.get("collection", "unknown")
        source = meta.get("source", meta.get("filename", "unknown"))
        chunk_index = meta.get("chunk_index", 0)
        point_id = meta.get("id", "")
        context = meta.get("context", "")
        summary = meta.get("summary", "")
        uploaded_at = meta.get("uploaded_at", "")
        chunk_type = meta.get("chunk_type", "")

        entries.append({
            "text": text,
            "score": score,
            "collection": str(collection),
            "source": str(source),
            "chunk_index": int(chunk_index) if chunk_index else 0,
            "point_id": str(point_id),
            "context": str(context) if context else "",
            "summary": str(summary) if summary else "",
            "uploaded_at": str(uploaded_at) if uploaded_at else "",
            "chunk_type": str(chunk_type) if chunk_type else "",
        })

    if not entries:
        return ""

    # ── Cluster: collection → source → sorted-by-chunk_index ────────
    from collections import defaultdict
    clusters: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for e in entries:
        clusters[e["collection"]][e["source"]].append(e)

    # Sort each source's chunks by chunk_index
    for col_sources in clusters.values():
        for src in col_sources:
            col_sources[src].sort(key=lambda e: e["chunk_index"])

    # ── Detect how many distinct collections are present ────────────
    distinct_collections = sorted(clusters.keys())

    # ── Render ──────────────────────────────────────────────────────
    parts: list[str] = []

    # Multi-collection hint
    if len(distinct_collections) > 1:
        col_list = ", ".join(distinct_collections)
        parts.append(
            f"[IMPORTANT: The following context comes from {len(distinct_collections)} "
            f"DIFFERENT collections: {col_list}. These are separate knowledge bases "
            f"that may contain overlapping or conflicting information. When answering, "
            f"be mindful of collection boundaries — note which collection each key "
            f"fact originates from, and do not assume consistency across collections.]"
        )

    for collection_name in distinct_collections:
        parts.append(f"## Database: {collection_name}")
        sources = clusters[collection_name]

        for source_name in sorted(sources.keys()):
            source_chunks = sources[source_name]
            if not source_chunks:
                continue

            first = source_chunks[0]

            parts.append(f"### Source: {source_name}")

            if first.get("uploaded_at"):
                parts.append(f"Uploaded: {first['uploaded_at']}")

            if first.get("summary"):
                # Summary shown once per source
                parts.append(f"Document summary: {first['summary']}")
                parts.append("")

            # ── Emit chunks with gap indicators ─────────────────────
            indices = [e["chunk_index"] for e in source_chunks]
            prev_idx: int | None = None
            last_emitted_idx: int | None = None
            gap_count = 0
            gap_start: int | None = None

            for i, entry in enumerate(source_chunks):
                cur_idx = entry["chunk_index"]

                # Check for gap from previous emitted chunk
                if gap_indicators and prev_idx is not None and cur_idx > prev_idx + 1:
                    # There is a gap between prev_idx+1 and cur_idx-1
                    n_gap = cur_idx - prev_idx - 1
                    gap_hint = (
                        f"[Note: {n_gap} intermediate chunk{'s' if n_gap > 1 else ''} "
                        f"(#{prev_idx + 1}–#{cur_idx - 1}) from {source_name} were omitted "
                        f"— content not directly relevant to the query.]"
                    )
                    parts.append(gap_hint)
                    parts.append("")  # blank line after gap note

                prev_idx = cur_idx

                # Emit the chunk
                chunk_header = (
                    f"[Chunk #{cur_idx} · score: {entry['score']:.2f}"
                )
                if entry["point_id"]:
                    chunk_header += f" · id: {entry['point_id']}"
                chunk_header += "]"
                parts.append(chunk_header)

                if entry["context"]:
                    parts.append(f"[Context: {entry['context']}]")

                parts.append(entry["text"])
                parts.append("")  # blank line between chunks

    result = "\n".join(parts).rstrip()
    logger.debug("[Context] done — %d chars output", len(result))
    return result
