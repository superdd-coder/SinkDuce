"""Meeting transcript vector index — packing (retrieve units)."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from src.rag.chunker import _estimate_tokens

TRANSCRIPT_COLLECTION = "__sinkduce_meeting_transcripts__"
INTERNAL_QDRANT_COLLECTIONS = frozenset({"__summaries__", TRANSCRIPT_COLLECTION})

_REF_RE = re.compile(r"\[ref:\s*[^\]]*\]", re.I)
_SPK_RE = re.compile(r"\[spk:[^\]]*\]", re.I)

_FILLERS = frozenset({"嗯", "对", "哦", "啊", "唔", "呃", "嗯嗯", "好", "ok", "OK", "okay"})
_LOCATOR_MAX_TOKENS = 48
_LOCATOR_TRAIL = frozenset({
    "to", "of", "and", "or", "the", "a", "an", "for", "with", "in", "on",
    "at", "by", "from", "as", "into", "onto", "via", "vs",
    "的", "了", "和", "与", "及", "对", "在", "把",
})


def pack_sentences(
    sentences: list[dict[str, Any]],
    *,
    meeting_id: str,
    max_tokens: int = 256,
    buffer_ratio: float = 0.5,
) -> list[dict[str, Any]]:
    """Pack consecutive transcript sentences into retrieve units.

    Never splits a sentence. Allows overflow up to ``max_tokens * (1 + buffer_ratio)``.
    Short fillers attach to the current pack when one is open.
    """
    if not sentences:
        return []
    hard = max(int(max_tokens * (1 + buffer_ratio)), max_tokens)
    packs: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0

    def _flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        packs.append(_pack_from_rows(meeting_id, len(packs), current))
        current = []
        current_tokens = 0

    for i, raw in enumerate(sentences):
        text = str(raw.get("original_text") or raw.get("text") or "").strip()
        if not text:
            continue
        tok = _estimate_tokens(text)
        row = {
            "sentence_id": str(raw.get("sentence_id") or ""),
            "speaker_id": str(raw.get("speaker") or raw.get("speaker_id") or ""),
            "start_time": float(raw.get("start_time") or raw.get("start") or 0.0),
            "end_time": float(raw.get("end_time") or raw.get("end") or 0.0),
            "text": text,
            "ref_n": i + 1,
        }
        is_filler = text in _FILLERS
        if not current:
            current = [row]
            current_tokens = tok
            continue
        if is_filler or current_tokens + tok <= hard:
            current.append(row)
            current_tokens += tok
            continue
        _flush()
        current = [row]
        current_tokens = tok

    _flush()
    return packs


def _pack_from_rows(meeting_id: str, pack_index: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    speakers: list[str] = []
    seen: set[str] = set()
    for r in rows:
        spk = r["speaker_id"]
        if spk and spk not in seen:
            seen.add(spk)
            speakers.append(spk)
    return {
        "meeting_id": meeting_id,
        "pack_index": pack_index,
        "start_time": rows[0]["start_time"],
        "end_time": rows[-1]["end_time"],
        "sentence_ids": [r["sentence_id"] for r in rows],
        "speakers": speakers,
        "sentences": rows,
    }


def sanitize_locator(text: str) -> str:
    """Strip citation / speaker tags from a locator before embedding."""
    cleaned = _SPK_RE.sub(" ", _REF_RE.sub(" ", text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if _estimate_tokens(cleaned) <= _LOCATOR_MAX_TOKENS:
        return cleaned
    parts = cleaned.split()
    acc: list[str] = []
    n = 0
    for p in parts:
        t = _estimate_tokens(p)
        if acc and n + t > _LOCATOR_MAX_TOKENS:
            break
        acc.append(p)
        n += t
    joined = " ".join(acc)
    for sep in (",", ";", "，", "、"):
        i = joined.rfind(sep)
        if i >= max(8, len(joined) // 3):
            joined = joined[:i]
            break
    acc = joined.split()
    while acc:
        tail = acc[-1].lower().rstrip(".,;:，、")
        if tail in _LOCATOR_TRAIL:
            acc.pop()
            continue
        break
    return " ".join(acc).strip(" ,;，、")


def embed_text_for_pack(
    pack: dict[str, Any],
    *,
    title: str = "",
    locator: str = "",
) -> str:
    """Text that goes into dense/sparse vectors — spoken lines only + optional prefixes."""
    lines: list[str] = []
    title = (title or "").strip()
    if title:
        lines.append(title)
    loc = sanitize_locator(locator)
    if loc:
        lines.append(f"Context: {loc}")
    for s in pack.get("sentences") or []:
        body = str(s.get("text") or s.get("original_text") or "").strip()
        if body:
            lines.append(body)
    return "\n".join(lines)


def _seg_from_sentence(raw: dict[str, Any], ref_n: int) -> dict[str, Any] | None:
    text = str(raw.get("original_text") or raw.get("text") or "").strip()
    if not text:
        return None
    return {
        "ref_n": ref_n,
        "speaker_id": raw.get("speaker") or raw.get("speaker_id") or "unknown",
        "text": text,
    }


def _pack_segs_in_range(
    packs: list[dict[str, Any]], lo: int, hi: int
) -> list[dict[str, Any]]:
    segs: list[dict[str, Any]] = []
    for pack in packs:
        for raw in pack.get("sentences") or []:
            rn = int(raw.get("ref_n") or 0)
            if rn < lo or rn > hi:
                continue
            seg = _seg_from_sentence(raw, rn)
            if seg:
                segs.append(seg)
    segs.sort(key=lambda s: int(s["ref_n"]))
    return segs


def stitch_packs(
    all_sentences: list[dict[str, Any]],
    hit_packs: list[dict[str, Any]],
    *,
    glue: int = 2,
    cap_tokens: int = 3000,
) -> str:
    """Merge hit packs in time order, glue ±N sentences, format as [ref:N] windows."""
    from src.chatbox.meeting_context import format_segments_for_chat

    if not hit_packs:
        return ""
    ordered = sorted(hit_packs, key=lambda p: int(p.get("pack_index", 0)))
    islands: list[tuple[int, int]] = []
    for pack in ordered:
        rows = pack.get("sentences") or []
        if not rows:
            continue
        lo = int(rows[0]["ref_n"])
        hi = int(rows[-1]["ref_n"])
        if islands and lo <= islands[-1][1] + 1:
            islands[-1] = (islands[-1][0], max(islands[-1][1], hi))
        else:
            islands.append((lo, hi))

    n_all = len(all_sentences)
    windows: list[str] = []
    used_tokens = 0
    for lo, hi in islands:
        segs: list[dict[str, Any]] = []
        if n_all > 0:
            a = max(1, lo - glue)
            b = min(n_all, hi + glue)
            for ref_n in range(a, b + 1):
                seg = _seg_from_sentence(all_sentences[ref_n - 1], ref_n)
                if seg:
                    segs.append(seg)
        if not segs:
            segs = _pack_segs_in_range(ordered, lo, hi)
        block = format_segments_for_chat(segs)
        if not block:
            continue
        tok = _estimate_tokens(block)
        if windows and used_tokens + tok > cap_tokens:
            break
        windows.append(block)
        used_tokens += tok
    return "\n...\n".join(windows)


def stitch_group_hits(
    hit_packs: list[dict[str, Any]],
    meeting_meta: dict[str, dict[str, Any]],
    *,
    glue: int = 2,
    cap_tokens: int = 3000,
) -> str:
    """Group packs by meeting (date order), stitch inside, never cross meetings."""
    from collections import defaultdict

    if not hit_packs:
        return ""
    by_mid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pack in hit_packs:
        mid = str(pack.get("meeting_id") or "")
        if mid:
            by_mid[mid].append(pack)

    def _sort_key(mid: str) -> tuple:
        meta = meeting_meta.get(mid) or {}
        return (str(meta.get("date") or ""), int(meta.get("n") or 0), mid)

    blocks: list[str] = []
    used = 0
    for mid in sorted(by_mid.keys(), key=_sort_key):
        meta = meeting_meta.get(mid) or {}
        title = str(meta.get("title") or mid)
        header_lines = [
            f"## Meeting: {title}",
            f"n: {int(meta.get('n') or 0)}",
            f"id: {mid}",
        ]
        if meta.get("date"):
            header_lines.append(f"date: {meta['date']}")
        speakers = meta.get("speakers") or {}
        if isinstance(speakers, dict) and speakers:
            names = " · ".join(f"{k} {v}" for k, v in speakers.items() if v)
            if names:
                header_lines.append(f"speakers: {names}")
        sentences = list(meta.get("sentences") or [])
        body = stitch_packs(
            sentences, by_mid[mid], glue=glue, cap_tokens=max(400, cap_tokens - used)
        )
        if not body:
            continue
        if "\n...\n" in body:
            body = body.replace(
                "\n...\n",
                "\n[Note: intermediate sentences omitted — not directly relevant "
                "to the query.]\n",
            )
        block = "\n".join(header_lines) + "\n\n" + body
        tok = _estimate_tokens(block)
        if blocks and used + tok > cap_tokens:
            break
        blocks.append(block)
        used += tok
    return "\n\n".join(blocks)


def group_cites_from_hits(
    hit_packs: list[dict[str, Any]],
    meeting_meta: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every hit sentence in retrieve order. Same *n* may appear many times.

    LLM tokens stay [n] / [n:k]; the UI renumbers chips 1, 2, 3 in
    appearance order. Successive [n] maps to the next sentence here.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for pack in hit_packs:
        mid = str(pack.get("meeting_id") or "")
        n = int((meeting_meta.get(mid) or {}).get("n") or 0)
        if not mid or n <= 0:
            continue
        for raw in pack.get("sentences") or []:
            try:
                ref_n = int(raw.get("ref_n") or 0)
            except (TypeError, ValueError):
                ref_n = 0
            sid = str(raw.get("sentence_id") or "")
            if ref_n and not sid:
                sid = f"stt_{ref_n:04d}"
            if not sid:
                continue
            key = (n, sid)
            if key in seen:
                continue
            seen.add(key)
            meta_row = meeting_meta.get(mid) or {}
            out.append(
                {
                    "n": n,
                    "meeting_id": mid,
                    "ref_n": ref_n,
                    "sentence_id": sid,
                    "title": str(meta_row.get("title") or ""),
                    "date": str(meta_row.get("date") or ""),
                }
            )
    return out


def speaker_ids_from_question(
    question: str, mapping: dict[str, str] | None
) -> list[str]:
    """Resolve speaker_ids whose display name or id appears in *question*."""
    if not (question or "").strip() or not mapping:
        return []
    items: list[tuple[str, str]] = []
    for sid, name in mapping.items():
        sid = str(sid or "").strip()
        if not sid:
            continue
        n = str(name or "").strip()
        if n:
            items.append((sid, n))
        items.append((sid, sid))
    items.sort(key=lambda x: len(x[1]), reverse=True)
    remaining = question
    remaining_l = question.lower()
    found: list[str] = []
    used: set[str] = set()
    for sid, name in items:
        if sid in used:
            continue
        needle = name.lower()
        idx = remaining_l.find(needle)
        if idx < 0:
            continue
        end = idx + len(needle)
        if name.isascii() and any(c.isalnum() for c in name):
            prev = remaining_l[idx - 1] if idx else " "
            nxt = remaining_l[end] if end < len(remaining_l) else " "
            if prev.isalnum() or nxt.isalnum():
                continue
        found.append(sid)
        used.add(sid)
        remaining = remaining[:idx] + (" " * len(name)) + remaining[end:]
        remaining_l = remaining.lower()
    return found


def run_transcript_lookup(
    *,
    meeting_id: str,
    query: str,
    speaker_ids: list[str] | None = None,
    speaker_scope: str = "auto",
    sentences: list[dict[str, Any]] | None = None,
    search_fn=None,
    glue: int = 2,
    cap_tokens: int = 3000,
) -> dict[str, Any]:
    """Search packs then stitch. Empty filtered hits get an unfiltered preview."""
    sentences = sentences or []
    scope_all = (speaker_scope or "auto").lower() == "all"
    applied = None if scope_all else list(speaker_ids or [])
    if applied == []:
        applied = None
    if search_fn is None:
        raise ValueError("search_fn is required")
    hits = search_fn(query, speaker_ids=applied, top_k=10) or []
    out: dict[str, Any] = {
        "meeting_id": meeting_id,
        "hits": hits,
        "filter_applied": {"speaker_ids": applied} if applied else {},
        "context": stitch_packs(sentences, hits, glue=glue, cap_tokens=cap_tokens) if hits else "",
        "preview_unfiltered": "",
    }
    if hits or not applied:
        return out
    preview_hits = search_fn(query, speaker_ids=None, top_k=3, skip_rerank=True) or []
    if preview_hits:
        out["preview_unfiltered"] = stitch_packs(
            sentences, preview_hits, glue=0, cap_tokens=800
        )
    return out


def meeting_search_filter(meeting_id: str, speaker_ids: list[str] | None = None):
    """Qdrant filter: this meeting, optionally packs that contain a speaker."""
    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

    must = [
        FieldCondition(key="meeting_id", match=MatchValue(value=meeting_id)),
    ]
    if speaker_ids:
        must.append(FieldCondition(key="speakers", match=MatchAny(any=list(speaker_ids))))
    return Filter(must=must)


def group_search_filter(meetings: list[dict[str, Any]]):
    """One Qdrant filter for several meetings. Local speaker ids never cross meetings."""
    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

    rows = [m for m in meetings if (m.get("meeting_id") or "").strip()]
    if not rows:
        return Filter(
            must=[FieldCondition(key="meeting_id", match=MatchValue(value="__none__"))]
        )
    if all(not m.get("speaker_ids") for m in rows):
        ids = [str(m["meeting_id"]) for m in rows]
        return Filter(
            must=[FieldCondition(key="meeting_id", match=MatchAny(any=ids))]
        )
    should: list = []
    for m in rows:
        mid = str(m["meeting_id"])
        must = [FieldCondition(key="meeting_id", match=MatchValue(value=mid))]
        spk = m.get("speaker_ids") or None
        if spk:
            must.append(FieldCondition(key="speakers", match=MatchAny(any=list(spk))))
        should.append(Filter(must=must))
    return Filter(should=should)


def purge_meeting_transcripts(meeting_id: str) -> None:
    """Drop this meeting's packs. Safe if Qdrant/collection is missing."""
    try:
        from src.services import services

        if not services.db or not services.db.collection_exists(TRANSCRIPT_COLLECTION):
            return
        services.db.delete_by_filter(TRANSCRIPT_COLLECTION, "meeting_id", meeting_id)
    except Exception:
        logger.warning("purge transcript packs failed meeting=%s", meeting_id, exc_info=True)


def ensure_transcript_collection(db, vector_size: int) -> None:
    """Create the hidden transcript collection + payload indexes if needed."""
    if not db.collection_exists(TRANSCRIPT_COLLECTION):
        db.create_collection(TRANSCRIPT_COLLECTION, vector_size=vector_size)
    _ensure_payload_indexes(db)


def _ensure_payload_indexes(db) -> None:
    from qdrant_client.http.models import PayloadSchemaType

    client = getattr(db, "client", None)
    if client is None:
        return
    specs = [
        ("meeting_id", PayloadSchemaType.KEYWORD),
        ("speakers", PayloadSchemaType.KEYWORD),
        ("sentence_ids", PayloadSchemaType.KEYWORD),
        ("start_time", PayloadSchemaType.FLOAT),
        ("end_time", PayloadSchemaType.FLOAT),
    ]
    for field, schema in specs:
        try:
            client.create_payload_index(
                collection_name=TRANSCRIPT_COLLECTION,
                field_name=field,
                field_schema=schema,
            )
        except Exception:
            pass


def apply_locator_batch(
    packs: list[dict[str, Any]],
    contexts: dict[int, str],
    *,
    offset: int = 0,
) -> None:
    """Write sanitized locators onto packs[offset + id]."""
    for local_id, raw in (contexts or {}).items():
        idx = offset + int(local_id)
        if 0 <= idx < len(packs):
            packs[idx]["context"] = sanitize_locator(str(raw or ""))


def _pack_card(pack: dict[str, Any], idx: int) -> str:
    body = "\n".join(
        str(s.get("text") or "").strip()
        for s in (pack.get("sentences") or [])
        if str(s.get("text") or "").strip()
    )
    return f"[{idx}]\n{body}"


def locate_packs(
    packs: list[dict[str, Any]],
    *,
    transcript: str,
    llm,
    batch_size: int = 10,
) -> None:
    """Fill pack['context'] via Meeting Summary LLM, 10 packs per call."""
    if not packs or llm is None:
        return
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from src.prompts import MEETING_BLUEPRINT_SYSTEM, MEETING_TRANSCRIPT_LOCATOR_PROMPT
    from src.rag.contextual import (
        _parse_context_batch,
        ingest_parallel_limit,
        ingest_request_limiter,
    )

    batches: list[tuple[int, list[int]]] = []
    for start in range(0, len(packs), batch_size):
        ids = list(range(min(batch_size, len(packs) - start)))
        batches.append((start, ids))

    def _run(start: int, ids: list[int]) -> tuple[int, dict[int, str]]:
        cards = "\n\n".join(
            _pack_card(packs[start + i], i) for i in ids
        )
        prompt = MEETING_TRANSCRIPT_LOCATOR_PROMPT.format(
            transcript=transcript, packs=cards
        )
        with ingest_request_limiter:
            raw = (llm.generate(
                prompt,
                system=MEETING_BLUEPRINT_SYSTEM,
                temperature=0.0,
                thinking=False,
                response_format={"type": "json_object"},
                max_tokens=800,
            ) or "").strip()
        parsed = _parse_context_batch(raw, ids)
        return start, parsed

    workers = min(ingest_parallel_limit(), max(1, len(batches)))
    logger.info(
        "[TX_INDEX] locator start packs=%d batches=%d workers=%d transcript_chars=%d",
        len(packs),
        len(batches),
        workers,
        len(transcript or ""),
    )
    ok_batches = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_run, start, ids) for start, ids in batches]
        for fut in as_completed(futs):
            try:
                start, parsed = fut.result()
            except Exception:
                logger.warning("[TX_INDEX] locator batch failed", exc_info=True)
                continue
            apply_locator_batch(packs, parsed, offset=start)
            ok_batches += 1
    n_ctx = sum(1 for p in packs if str(p.get("context") or "").strip())
    logger.info(
        "[TX_INDEX] locator done batches_ok=%d/%d packs_with_locator=%d/%d",
        ok_batches,
        len(batches),
        n_ctx,
        len(packs),
    )


def _point_id(meeting_id: str, pack_index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sinkduce-tx:{meeting_id}:{pack_index}"))


def index_meeting_transcripts(
    *,
    meeting_id: str,
    sentences: list[dict[str, Any]],
    title: str = "",
    transcript: str = "",
    db,
    embedding,
    llm=None,
    sparse_encoder=None,
) -> int:
    """Replace this meeting's packs in Qdrant. Returns pack count."""
    dim = int(getattr(embedding, "dimensions", 0) or 0)
    if dim <= 0:
        raise ValueError("Embedding dimensions unknown")
    ensure_transcript_collection(db, dim)
    try:
        db.delete_by_filter(TRANSCRIPT_COLLECTION, "meeting_id", meeting_id)
    except Exception:
        logger.warning("delete old transcript packs failed meeting=%s", meeting_id, exc_info=True)

    packs = pack_sentences(sentences, meeting_id=meeting_id)
    n_sent = len(sentences)
    if not packs:
        logger.info("[TX_INDEX] meeting=%s sentences=%d → 0 packs", meeting_id, n_sent)
        return 0
    tok_per = [
        _estimate_tokens("".join(str(s.get("text") or "") for s in p.get("sentences") or []))
        for p in packs
    ]
    n_per = [len(p.get("sentences") or []) for p in packs]
    logger.info(
        "[TX_INDEX] meeting=%s sentences=%d packs=%d "
        "sents/pack min=%d avg=%.1f max=%d  tokens/pack min=%d avg=%.0f max=%d  llm=%s",
        meeting_id,
        n_sent,
        len(packs),
        min(n_per),
        sum(n_per) / len(n_per),
        max(n_per),
        min(tok_per),
        sum(tok_per) / len(tok_per),
        max(tok_per),
        type(llm).__name__ if llm else "none",
    )
    for p, tok in zip(packs, tok_per):
        rows = p.get("sentences") or []
        t0 = p.get("start_time")
        t1 = p.get("end_time")
        logger.info(
            "[TX_INDEX]   pack=%d sents=%d tok=%d t=%.1f-%.1f speakers=%s refs=%s-%s",
            p.get("pack_index"),
            len(rows),
            tok,
            float(t0 or 0),
            float(t1 or 0),
            ",".join(p.get("speakers") or []) or "-",
            rows[0].get("ref_n") if rows else "-",
            rows[-1].get("ref_n") if rows else "-",
        )
    if llm and transcript:
        try:
            locate_packs(packs, transcript=transcript, llm=llm)
        except Exception:
            logger.warning("[TX_INDEX] locator batches failed meeting=%s", meeting_id, exc_info=True)
    else:
        logger.info(
            "[TX_INDEX] skip locator meeting=%s reason=%s",
            meeting_id,
            "no_llm" if not llm else "empty_transcript",
        )

    texts = [
        embed_text_for_pack(p, title=title, locator=str(p.get("context") or ""))
        for p in packs
    ]
    vectors = embedding.embed_texts(texts)
    ids = [_point_id(meeting_id, p["pack_index"]) for p in packs]
    payloads = []
    for p, text in zip(packs, texts):
        pl = dict(p)
        pl["chunk_type"] = "meeting_tx"
        pl["text"] = text
        pl["archived"] = False
        pl["is_current"] = True
        payloads.append(pl)
    db.upsert_points(
        collection=TRANSCRIPT_COLLECTION,
        ids=ids,
        vectors=vectors,
        payloads=payloads,
    )
    if sparse_encoder is not None:
        try:
            sparse_encoder.load(db, TRANSCRIPT_COLLECTION)
            sv = sparse_encoder.encode(texts)
            db.upsert_sparse_vectors(TRANSCRIPT_COLLECTION, ids, sv)
            sparse_encoder.save(db, TRANSCRIPT_COLLECTION)
        except Exception:
            logger.warning("sparse upsert failed meeting=%s", meeting_id, exc_info=True)
        else:
            logger.info("[TX_INDEX] sparse upserted %d vectors meeting=%s", len(ids), meeting_id)
    logger.info(
        "[TX_INDEX] upserted %d packs meeting=%s collection=%s dim=%d",
        len(packs),
        meeting_id,
        TRANSCRIPT_COLLECTION,
        dim,
    )
    return len(packs)


def _spoken_text_from_chunk(chunk: Any) -> str:
    """Reranker drops empty docs; recover spoken lines from pack payload."""
    text = str(getattr(chunk, "text", "") or "").strip()
    if text:
        return text
    meta = getattr(chunk, "metadata", None) or {}
    lines: list[str] = []
    for s in meta.get("sentences") or []:
        body = str(s.get("text") or s.get("original_text") or "").strip()
        if body:
            lines.append(body)
    return "\n".join(lines)


def search_transcript_packs(
    query: str,
    *,
    meeting_id: str,
    speaker_ids: list[str] | None = None,
    top_k: int = 10,
    retriever=None,
    reranker=None,
    skip_rerank: bool = False,
) -> list[dict[str, Any]]:
    """Hybrid (+ optional rerank) search, returning pack payloads."""
    if retriever is None:
        return []
    use_rerank = reranker is not None and not skip_rerank
    search_k = 20 if use_rerank else max(top_k, 10)
    filt = meeting_search_filter(meeting_id, speaker_ids)
    chunks = retriever.retrieve(
        query,
        collection=TRANSCRIPT_COLLECTION,
        top_k=search_k,
        search_mode="hybrid",
        filter_condition=filt,
    ) or []
    n_retrieve = len(chunks)
    n_empty = 0
    for c in chunks:
        if not str(getattr(c, "text", "") or "").strip():
            n_empty += 1
        filled = _spoken_text_from_chunk(c)
        if filled:
            c.text = filled
    if use_rerank and chunks:
        chunks = reranker.rerank(query, chunks, top_k=top_k)
    else:
        chunks = chunks[:top_k]
    packs: list[dict[str, Any]] = []
    for c in chunks:
        meta = dict(getattr(c, "metadata", None) or {})
        if meta.get("sentences"):
            packs.append(meta)
    logger.info(
        "[TX_INDEX] search meeting=%s q=%r speakers=%s retrieve=%d empty_text=%d "
        "rerank=%s out=%d",
        meeting_id,
        (query or "")[:80],
        ",".join(speaker_ids or []) or "-",
        n_retrieve,
        n_empty,
        "yes" if use_rerank else "no",
        len(packs),
    )
    if n_retrieve and not packs:
        logger.warning(
            "[TX_INDEX] search dropped all hits meeting=%s retrieve=%d empty_text=%d",
            meeting_id,
            n_retrieve,
            n_empty,
        )
    return packs


def search_group_transcript_packs(
    query: str,
    *,
    meetings: list[dict[str, Any]],
    top_k: int = 10,
    retriever=None,
    reranker=None,
    skip_rerank: bool = False,
) -> list[dict[str, Any]]:
    """One hybrid retrieve across meetings. ``meetings`` are filter clauses."""
    if retriever is None or not meetings:
        return []
    use_rerank = reranker is not None and not skip_rerank
    search_k = 20 if use_rerank else max(top_k, 10)
    filt = group_search_filter(meetings)
    chunks = retriever.retrieve(
        query,
        collection=TRANSCRIPT_COLLECTION,
        top_k=search_k,
        search_mode="hybrid",
        filter_condition=filt,
    ) or []
    for c in chunks:
        filled = _spoken_text_from_chunk(c)
        if filled:
            c.text = filled
    if use_rerank and chunks:
        chunks = reranker.rerank(query, chunks, top_k=top_k)
    else:
        chunks = chunks[:top_k]
    packs: list[dict[str, Any]] = []
    for c in chunks:
        meta = dict(getattr(c, "metadata", None) or {})
        if meta.get("sentences"):
            packs.append(meta)
    logger.info(
        "[TX_INDEX] group search q=%r meetings=%d out=%d",
        (query or "")[:80],
        len(meetings),
        len(packs),
    )
    return packs


def format_transcript_for_locator(meeting_id: str) -> str:
    """Same [N] [spk:ID] lines General Summary uses (prefix-cache)."""
    from src.meeting import store
    from src.meeting.service import _num_id

    meeting = store.get_meeting(meeting_id)
    sentences_data = store.get_sentences(meeting_id)
    if not sentences_data:
        t = store.get_transcript(meeting_id)
        return (t.text if t else "") or ""
    speaker_names = getattr(meeting, "speaker_names", None) or {} if meeting else {}
    lines = []
    for s in sentences_data:
        sid = s.get("sentence_id", "")
        speaker = s.get("speaker", "")
        text = s.get("original_text", "") or ""
        spk_name = speaker_names.get(speaker, "")
        if spk_name:
            text = text.removeprefix(spk_name).strip().removeprefix(":").strip()
        spk_part = f"[spk:{speaker}] " if speaker else ""
        lines.append(f"[{_num_id(sid)}] {spk_part}{text}")
    return "\n".join(lines)


def load_sentences_for_meeting(meeting_id: str) -> list[dict[str, Any]]:
    """Same sentence source for indexing and lookup (sentences.json, else STT segments)."""
    from src.meeting import store

    sentences = store.get_sentences(meeting_id) or []
    if sentences:
        return list(sentences)
    t = store.get_transcript(meeting_id)
    if not (t and t.segments):
        return []
    return [
        {
            "sentence_id": f"stt_{i + 1:04d}",
            "speaker": getattr(seg, "speaker_id", "") or "",
            "original_text": getattr(seg, "text", "") or "",
            "start_time": getattr(seg, "start", 0) or 0,
            "end_time": getattr(seg, "end", 0) or 0,
        }
        for i, seg in enumerate(t.segments)
    ]


def index_from_store(meeting_id: str) -> int:
    """Load meeting from disk, locate, upsert. Updates transcript_index_status."""
    from src.meeting import store
    from src.meeting.service import _resolve_meeting_llm
    from src.rag.sparse_encoder import SparseEncoder
    from src.services import services

    store.update_meeting(
        meeting_id, transcript_index_status="building", transcript_index_error=""
    )
    logger.info("[TX_INDEX] index_from_store start meeting=%s", meeting_id)
    try:
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(meeting_id)
        sentences = load_sentences_for_meeting(meeting_id)
        llm = None
        try:
            llm = _resolve_meeting_llm()
        except Exception:
            llm = None
        n = index_meeting_transcripts(
            meeting_id=meeting_id,
            sentences=sentences,
            title=(meeting.title or "") if meeting else "",
            transcript=format_transcript_for_locator(meeting_id),
            db=services.db,
            embedding=services.embedding,
            llm=llm,
            sparse_encoder=SparseEncoder(),
        )
        store.update_meeting(
            meeting_id,
            transcript_index_status="ready" if n else "failed",
            transcript_index_error="" if n else "no packs",
        )
        logger.info(
            "[TX_INDEX] index_from_store done meeting=%s status=%s packs=%d title=%r",
            meeting_id,
            "ready" if n else "failed",
            n,
            (meeting.title or "") if meeting else "",
        )
        return n
    except Exception as exc:
        logger.exception("transcript index failed meeting=%s", meeting_id)
        try:
            store.update_meeting(
                meeting_id,
                transcript_index_status="failed",
                transcript_index_error=str(exc)[:500],
            )
        except Exception:
            pass
        raise


def transcript_pack_key(pack: dict[str, Any]) -> str:
    return f"{pack.get('meeting_id') or ''}:{int(pack.get('pack_index') or 0)}"


def apply_turn_pack_dedupe(
    result: dict[str, Any],
    sentences: list[dict[str, Any]],
    seen_pack_keys: set[str],
    *,
    glue: int = 2,
    cap_tokens: int = 3000,
) -> tuple[dict[str, Any], set[str]]:
    """Drop packs already returned this turn; restitch context from the rest."""
    hits = list(result.get("hits") or [])
    found = {transcript_pack_key(h) for h in hits}
    if not seen_pack_keys:
        return result, found
    new_hits = [h for h in hits if transcript_pack_key(h) not in seen_pack_keys]
    out = dict(result)
    out["hits"] = new_hits
    out["context"] = (
        stitch_packs(sentences, new_hits, glue=glue, cap_tokens=cap_tokens)
        if new_hits
        else ""
    )
    return out, found


def lookup_json_and_keys(
    meeting_id: str,
    query: str,
    *,
    speaker_scope: str = "auto",
    user_question: str = "",
    seen_pack_keys: set[str] | None = None,
) -> tuple[str, set[str]]:
    """Lookup JSON for the LLM, plus pack keys found (to mark seen this turn)."""
    import json

    from src.meeting import store
    from src.services import services

    meeting = store.get_meeting(meeting_id)
    mapping = (meeting.speaker_names if meeting else None) or {}
    scope = (speaker_scope or "auto").lower()
    spk = None
    if scope != "all":
        spk = speaker_ids_from_question(user_question or query, mapping)
    sentences = load_sentences_for_meeting(meeting_id)

    def search_fn(q, *, speaker_ids, top_k, skip_rerank=False):
        return search_transcript_packs(
            q,
            meeting_id=meeting_id,
            speaker_ids=speaker_ids,
            top_k=top_k,
            retriever=services.retriever,
            reranker=services.reranker,
            skip_rerank=skip_rerank,
        )

    result = run_transcript_lookup(
        meeting_id=meeting_id,
        query=query,
        speaker_ids=spk,
        speaker_scope=scope,
        sentences=sentences,
        search_fn=search_fn,
    )
    seen = seen_pack_keys or set()
    result, found = apply_turn_pack_dedupe(result, sentences, seen)
    names = {sid: mapping.get(sid) or sid for sid in (spk or [])}
    body = {
        "meeting_id": meeting_id,
        "filter_applied": {
            "speaker_ids": result.get("filter_applied", {}).get("speaker_ids"),
            "display_names": names,
        },
        "context": result.get("context") or "",
        "preview_unfiltered": result.get("preview_unfiltered") or "",
        "hit_count": len(result.get("hits") or []),
    }
    if seen and body["context"]:
        body["message"] = "Additional packs this turn."
    elif seen and found and not body["context"] and not body["preview_unfiltered"]:
        body["message"] = "No additional packs this turn."
    elif not body["context"] and not body["preview_unfiltered"]:
        body["message"] = "No matching transcript packs."
    elif not body["context"] and body["preview_unfiltered"]:
        body["message"] = (
            "No packs for the named speaker. preview_unfiltered shows who may "
            "have said this; ask the user before speaker_scope=all."
        )
    return json.dumps(body, ensure_ascii=False), found


def execute_lookup_json(
    meeting_id: str,
    query: str,
    *,
    speaker_scope: str = "auto",
    user_question: str = "",
) -> str:
    """Tool payload for lookup_meeting_transcript (MCP / single-shot)."""
    raw, _found = lookup_json_and_keys(
        meeting_id,
        query,
        speaker_scope=speaker_scope,
        user_question=user_question,
    )
    return raw


def execute_group_lookup_json(
    group_id: str,
    query: str,
    *,
    meeting_ids: list[str] | None = None,
    speaker_scope: str = "auto",
    user_question: str = "",
) -> str:
    """One group search. meeting_ids must be members; unindexed meetings are skipped."""
    import json

    from src.meeting.group_store import get_group
    from src.meeting import store
    from src.services import services

    group = get_group(group_id)
    if group is None:
        return json.dumps({"error": "Group not found", "context": "", "hit_count": 0})
    member_ids = [m.meeting_id for m in group.members]
    by_n = {m.meeting_id: m.n for m in group.members}
    requested = [str(x).strip() for x in (meeting_ids or []) if str(x).strip()]
    if requested:
        unknown = [mid for mid in requested if mid not in member_ids]
        if unknown:
            return json.dumps(
                {
                    "error": "meeting_id not in this group",
                    "unknown": unknown,
                    "context": "",
                    "hit_count": 0,
                },
                ensure_ascii=False,
            )
        target = requested
    else:
        target = list(member_ids)

    unindexed: list[str] = []
    clauses: list[dict[str, Any]] = []
    meta: dict[str, dict[str, Any]] = {}
    scope_all = (speaker_scope or "auto").lower() == "all"
    question = user_question or query

    for mid in target:
        meeting = store.get_meeting(mid)
        if meeting is None:
            continue
        title = meeting.title or mid
        if (meeting.transcript_index_status or "") != "ready":
            unindexed.append(title)
            continue
        mapping = (meeting.speaker_names if meeting else None) or {}
        spk = None if scope_all else speaker_ids_from_question(question, mapping)
        if spk == []:
            spk = None
        created = meeting.created_at
        date = created.date().isoformat() if created is not None else ""
        meta[mid] = {
            "n": by_n.get(mid, 0),
            "title": title,
            "date": date,
            "speakers": mapping,
            "sentences": load_sentences_for_meeting(mid),
        }
        clauses.append({"meeting_id": mid, "speaker_ids": spk})

    hits: list[dict[str, Any]] = []
    if clauses and services.retriever:
        hits = search_group_transcript_packs(
            query,
            meetings=clauses,
            retriever=services.retriever,
            reranker=services.reranker,
        )
    context = stitch_group_hits(hits, meta) if hits else ""
    cites = group_cites_from_hits(hits, meta)
    body = {
        "group_id": group_id,
        "context": context,
        "hit_count": len(hits),
        "unindexed": unindexed,
        "meetings_searched": [c["meeting_id"] for c in clauses],
        "cites": cites,
        "cite_as": (
            "[n:k] — n is the meeting header n, k is [ref:k] in the excerpts. "
            "The UI renumbers chips 1,2,3 in appearance order. Do not paste excerpt lines."
        ),
    }
    if unindexed:
        body["message"] = (
            "Some meetings in this group have no transcript index yet: "
            + ", ".join(unindexed)
        )
    elif not context:
        body["message"] = "No matching transcript packs."
    return json.dumps(body, ensure_ascii=False)


def hit_count_from_lookup_json(raw: str) -> int:
    """Parse hit_count from lookup_meeting_transcript JSON (0 if missing)."""
    import json

    try:
        data = json.loads(raw or "")
    except (TypeError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    try:
        return max(0, int(data.get("hit_count") or 0))
    except (TypeError, ValueError):
        return 0
