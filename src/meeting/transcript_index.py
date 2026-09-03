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
TOP_PACKS_PER_MEETING = 5
# Shown between two non-adjacent hit windows. Not a page of unread transcript.
HIT_GAP_NOTE = (
    "\n[Note: gap between ranked hits — these sentences did not match this "
    "search. Not unread transcript; do not look them up again.]\n"
)
LOOKUP_NO_NEW_PACKS_MSG = (
    "No new packs. The excerpts below are everything already returned this "
    "turn. Answer from them. Do not call lookup_meeting_transcript again to "
    "fill gaps or repeat this search."
)
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
    text, _shown = stitch_packs_and_keys(
        all_sentences, hit_packs, glue=glue, cap_tokens=cap_tokens
    )
    return text


def stitch_packs_and_keys(
    all_sentences: list[dict[str, Any]],
    hit_packs: list[dict[str, Any]],
    *,
    glue: int = 2,
    cap_tokens: int | None = 3000,
) -> tuple[str, list[dict[str, Any]]]:
    """Like stitch_packs, plus the packs that actually landed in the text."""
    from src.chatbox.meeting_context import format_segments_for_chat

    if not hit_packs:
        return "", []
    ordered = sorted(hit_packs, key=lambda p: int(p.get("pack_index", 0)))
    islands: list[tuple[int, int, list[dict[str, Any]]]] = []
    for pack in ordered:
        rows = pack.get("sentences") or []
        if not rows:
            continue
        lo = int(rows[0]["ref_n"])
        hi = int(rows[-1]["ref_n"])
        if islands and lo <= islands[-1][1] + 1:
            prev = islands[-1]
            islands[-1] = (prev[0], max(prev[1], hi), prev[2] + [pack])
        else:
            islands.append((lo, hi, [pack]))

    n_all = len(all_sentences)
    windows: list[str] = []
    shown: list[dict[str, Any]] = []
    used_tokens = 0
    for lo, hi, members in islands:
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
        if (
            cap_tokens is not None
            and windows
            and used_tokens + tok > cap_tokens
        ):
            break
        windows.append(block)
        shown.extend(members)
        used_tokens += tok
    return HIT_GAP_NOTE.join(windows) if len(windows) > 1 else (windows[0] if windows else ""), shown


def stitch_group_hits(
    hit_packs: list[dict[str, Any]],
    meeting_meta: dict[str, dict[str, Any]],
    *,
    glue: int = 2,
    cap_tokens: int | None = None,
) -> str:
    """Group packs by meeting (date order), stitch inside, never cross meetings."""
    text, _shown = stitch_group_hits_and_keys(
        hit_packs, meeting_meta, glue=glue, cap_tokens=cap_tokens
    )
    return text


def stitch_group_hits_and_keys(
    hit_packs: list[dict[str, Any]],
    meeting_meta: dict[str, dict[str, Any]],
    *,
    glue: int = 2,
    cap_tokens: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Like stitch_group_hits, plus packs that actually landed in the text."""
    from collections import defaultdict

    if not hit_packs:
        return "", []
    by_mid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pack in hit_packs:
        mid = str(pack.get("meeting_id") or "")
        if mid:
            by_mid[mid].append(pack)
    for mid, packs in list(by_mid.items()):
        ranked = sorted(
            packs,
            key=lambda p: (-(_pack_score(p) or 0.0), int(p.get("pack_index") or 0)),
        )
        by_mid[mid] = ranked[:TOP_PACKS_PER_MEETING]

    def _sort_key(mid: str) -> tuple:
        meta = meeting_meta.get(mid) or {}
        packs = by_mid.get(mid) or []
        scores = [_pack_score(p) for p in packs]
        nums = [s for s in scores if s is not None]
        best = max(nums) if nums else 0.0
        return (-best, str(meta.get("date") or ""), int(meta.get("n") or 0), mid)

    blocks: list[str] = []
    shown: list[dict[str, Any]] = []
    used = 0
    n_meet = max(1, len(by_mid))
    for mid in sorted(by_mid.keys(), key=_sort_key):
        meta = meeting_meta.get(mid) or {}
        title = str(meta.get("title") or mid)
        header_lines = [f"## Meeting: {title}"]
        # Group numbering only exists in Group Chat (cite [n:k]); in Chat /
        # Quick Chat n is always 0 — emitting it just teaches the model a
        # citation format its surface must not use.
        if int(meta.get("n") or 0) > 0:
            header_lines.append(f"n: {int(meta.get('n') or 0)}")
        header_lines.append(f"id: {mid}")
        if meta.get("date"):
            header_lines.append(f"date: {meta['date']}")
        speakers = meta.get("speakers") or {}
        if isinstance(speakers, dict) and speakers:
            names = " · ".join(f"{k} {v}" for k, v in speakers.items() if v)
            if names:
                header_lines.append(f"speakers: {names}")
        sentences = list(meta.get("sentences") or [])
        inner_cap: int | None = None
        if cap_tokens is not None:
            share = max(400, cap_tokens // n_meet)
            inner_cap = min(share, max(400, cap_tokens - used))
        body, used_packs = stitch_packs_and_keys(
            sentences,
            by_mid[mid],
            glue=glue,
            cap_tokens=inner_cap,
        )
        if not body:
            continue
        block = "\n".join(header_lines) + "\n\n" + body
        tok = _estimate_tokens(block)
        if cap_tokens is not None and blocks and used + tok > cap_tokens:
            break
        blocks.append(block)
        shown.extend(used_packs)
        used += tok
    return "\n\n".join(blocks), shown


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


def group_search_filter(
    meetings: list[dict[str, Any]],
    exclude_point_ids: list[str] | None = None,
):
    """One Qdrant filter for several meetings. Local speaker ids never cross meetings."""
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        HasIdCondition,
        MatchAny,
        MatchValue,
    )

    rows = [m for m in meetings if (m.get("meeting_id") or "").strip()]
    if not rows:
        filt = Filter(
            must=[FieldCondition(key="meeting_id", match=MatchValue(value="__none__"))]
        )
    elif all(not m.get("speaker_ids") for m in rows):
        ids = [str(m["meeting_id"]) for m in rows]
        filt = Filter(
            must=[FieldCondition(key="meeting_id", match=MatchAny(any=ids))]
        )
    else:
        should: list = []
        for m in rows:
            mid = str(m["meeting_id"])
            must = [FieldCondition(key="meeting_id", match=MatchValue(value=mid))]
            spk = m.get("speaker_ids") or None
            if spk:
                must.append(FieldCondition(key="speakers", match=MatchAny(any=list(spk))))
            should.append(Filter(must=must))
        filt = Filter(should=should)
    ids = [str(x) for x in (exclude_point_ids or []) if str(x).strip()]
    if not ids:
        return filt
    return Filter(
        must=list(filt.must or []) or None,
        should=list(filt.should or []) or None,
        must_not=[HasIdCondition(has_id=ids)],
    )


def select_diverse_items(
    items: list,
    *,
    meeting_id_of,
    pack_index_of,
    top_k: int = 10,
    max_per_meeting: int = TOP_PACKS_PER_MEETING,
    min_index_gap: int = 3,
) -> list:
    """Prefer spread packs across meetings; fill remaining slots from leftovers."""
    if not items or top_k <= 0:
        return []
    selected: list = []
    per: dict[str, int] = {}
    chosen_idx: dict[str, list[int]] = {}
    deferred: list = []
    for item in items:
        if len(selected) >= top_k:
            break
        mid = str(meeting_id_of(item) or "")
        try:
            idx = int(pack_index_of(item) or 0)
        except (TypeError, ValueError):
            idx = 0
        if per.get(mid, 0) >= max_per_meeting:
            deferred.append(item)
            continue
        prev = chosen_idx.get(mid) or []
        if any(abs(idx - p) < min_index_gap for p in prev):
            deferred.append(item)
            continue
        selected.append(item)
        per[mid] = per.get(mid, 0) + 1
        chosen_idx.setdefault(mid, []).append(idx)
    for item in deferred:
        if len(selected) >= top_k:
            break
        selected.append(item)
    return selected


def select_diverse_hits(
    hits: list[dict[str, Any]],
    *,
    top_k: int = 10,
    max_per_meeting: int = TOP_PACKS_PER_MEETING,
    min_index_gap: int = 3,
) -> list[dict[str, Any]]:
    return select_diverse_items(
        hits,
        meeting_id_of=lambda h: str((h or {}).get("meeting_id") or ""),
        pack_index_of=lambda h: int((h or {}).get("pack_index") or 0),
        top_k=top_k,
        max_per_meeting=max_per_meeting,
        min_index_gap=min_index_gap,
    )


def _point_ids_from_pack_keys(keys: set[str] | list[str] | None) -> list[str]:
    out: list[str] = []
    for key in keys or []:
        raw = str(key or "").strip()
        if ":" not in raw:
            continue
        mid, _, idx_s = raw.rpartition(":")
        try:
            out.append(_point_id(mid, int(idx_s)))
        except (TypeError, ValueError):
            continue
    return out


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


def _rerank_or_truncate(query: str, chunks: list, reranker, top_k: int) -> list:
    """Rerank if possible; never raise — lookup must not kill the Chat stream."""
    if not chunks:
        return []
    if reranker is None:
        return chunks[:top_k]
    try:
        return reranker.rerank(query, chunks, top_k=top_k)
    except Exception as e:
        logger.warning("[TX_INDEX] rerank failed (%s), using retrieve order", e)
        return chunks[:top_k]


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
        chunks = _rerank_or_truncate(query, chunks, reranker, top_k)
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
    exclude_pack_keys: set[str] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """One hybrid retrieve across meetings. ``meetings`` are filter clauses."""
    if retriever is None or not meetings:
        return []
    use_rerank = reranker is not None and not skip_rerank
    n_meet = max(1, len(meetings))
    keep = min(40, max(top_k, TOP_PACKS_PER_MEETING * n_meet))
    search_k = min(50, max(20, 6 * n_meet, keep + 10))
    exclude_keys = {
        str(k).strip() for k in (exclude_pack_keys or []) if str(k).strip()
    }
    if exclude_keys:
        search_k = min(50, search_k + len(exclude_keys))
    filt = group_search_filter(
        meetings,
        exclude_point_ids=_point_ids_from_pack_keys(exclude_keys) or None,
    )
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

    def _chunk_mid(c) -> str:
        meta = getattr(c, "metadata", None) or {}
        return str(meta.get("meeting_id") or "")

    def _chunk_idx(c) -> int:
        meta = getattr(c, "metadata", None) or {}
        try:
            return int(meta.get("pack_index") or 0)
        except (TypeError, ValueError):
            return 0

    pre_k = keep if not use_rerank else min(50, max(keep, 16))
    chunks = select_diverse_items(
        chunks,
        meeting_id_of=_chunk_mid,
        pack_index_of=_chunk_idx,
        top_k=pre_k,
        max_per_meeting=TOP_PACKS_PER_MEETING,
    )
    if use_rerank and chunks:
        chunks = _rerank_or_truncate(query, chunks, reranker, keep)
    else:
        chunks = chunks[:keep]
    packs: list[dict[str, Any]] = []
    for c in chunks:
        meta = dict(getattr(c, "metadata", None) or {})
        if meta.get("sentences"):
            try:
                meta["_score"] = float(getattr(c, "score", 0) or 0)
            except (TypeError, ValueError):
                meta["_score"] = 0.0
            packs.append(meta)
    logger.info(
        "[TX_INDEX] group search q=%r meetings=%d exclude=%d rerank=%s out=%d",
        (query or "")[:80],
        len(meetings),
        len(exclude_keys),
        "yes" if use_rerank else "no",
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


def merge_hit_packs(*groups: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Dedupe packs by meeting_id:pack_index, keeping the first copy."""
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for group in groups:
        for pack in group or []:
            if not isinstance(pack, dict):
                continue
            key = transcript_pack_key(pack)
            if not key or key.startswith(":"):
                continue
            if key not in by_key:
                order.append(key)
                by_key[key] = pack
    return [by_key[k] for k in order]


def _pack_score(pack: dict[str, Any] | None) -> float | None:
    if not pack:
        return None
    raw = pack.get("_score", pack.get("score"))
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def select_new_hits(
    hits: list[dict[str, Any]],
    *,
    prior_hits: list[dict[str, Any]] | None = None,
    min_ratio: float = 0.6,
    max_new: int = 6,
) -> list[dict[str, Any]]:
    """Drop exclude-then-search leftovers that are much weaker than packs already kept."""
    if not hits:
        return []
    if not prior_hits:
        return list(hits)
    prior_scores = [s for s in (_pack_score(p) for p in prior_hits) if s is not None]
    bar: float | None = None
    if prior_scores:
        bar = min(prior_scores) * min_ratio
    else:
        new_scores = [s for s in (_pack_score(h) for h in hits) if s is not None]
        if new_scores:
            bar = max(new_scores) * 0.65
    if bar is None:
        return list(hits)[:max_new]
    kept = [h for h in hits if (_pack_score(h) or 0.0) >= bar]
    return kept[:max_new]


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
    prior_hits: list[dict[str, Any]] | None = None,
) -> tuple[str, set[str], list[dict[str, Any]]]:
    """Lookup JSON for the LLM, plus pack keys found (to mark seen this turn)."""
    import json

    from src.meeting import store
    from src.services import services

    meeting = store.get_meeting(meeting_id)
    # `is True` (not truthiness): meetings can be test mocks whose attributes
    # are Mock objects — only a real boolean True counts as archived.
    if meeting is not None and getattr(meeting, "archived", False) is True:
        body = {
            "meeting_id": meeting_id,
            "context": "",
            "preview_unfiltered": "",
            "hit_count": 0,
            "error": "This meeting is archived; its transcript is not searchable.",
        }
        return json.dumps(body, ensure_ascii=False), set(), []
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
    prior = merge_hit_packs(prior_hits)
    seen = {str(k).strip() for k in (seen_pack_keys or []) if str(k).strip()}
    seen |= {transcript_pack_key(h) for h in prior}
    hits = list(result.get("hits") or [])
    this_keys = {transcript_pack_key(h) for h in hits}
    new_hits = [h for h in hits if transcript_pack_key(h) not in seen]
    merged = merge_hit_packs(prior, new_hits)
    context = (
        stitch_packs(sentences, merged, glue=2, cap_tokens=3000)
        if merged
        else ""
    )
    names = {sid: mapping.get(sid) or sid for sid in (spk or [])}
    body = {
        "meeting_id": meeting_id,
        "filter_applied": {
            "speaker_ids": result.get("filter_applied", {}).get("speaker_ids"),
            "display_names": names,
        },
        "context": context,
        "preview_unfiltered": result.get("preview_unfiltered") or "",
        "hit_count": len(merged),
    }
    found = seen | this_keys
    if prior and not new_hits and body["context"]:
        body["message"] = LOOKUP_NO_NEW_PACKS_MSG
    elif seen and new_hits:
        body["message"] = (
            "New ranked packs for this query; already-returned packs were not repeated."
        )
    elif seen and found and not body["context"] and not body["preview_unfiltered"]:
        body["message"] = LOOKUP_NO_NEW_PACKS_MSG
    elif not body["context"] and not body["preview_unfiltered"]:
        body["message"] = "No matching transcript packs."
    elif not body["context"] and body["preview_unfiltered"]:
        body["message"] = (
            "No packs for the named speaker. preview_unfiltered shows who may "
            "have said this; ask the user before speaker_scope=all."
        )
    return json.dumps(body, ensure_ascii=False), found, merged


def execute_lookup_json(
    meeting_id: str,
    query: str,
    *,
    speaker_scope: str = "auto",
    user_question: str = "",
) -> str:
    """Tool payload for lookup_meeting_transcript (MCP / single-shot)."""
    raw, _found, _hits = lookup_json_and_keys(
        meeting_id,
        query,
        speaker_scope=speaker_scope,
        user_question=user_question,
    )
    return raw


def execute_meetings_lookup_json(
    target_ids: list[str],
    query: str,
    *,
    speaker_scope: str = "auto",
    user_question: str = "",
    group_n: dict[str, int] | None = None,
    group_id: str | None = None,
    seen_pack_keys: set[str] | None = None,
) -> str:
    """Search spoken transcripts for an explicit meeting id list."""
    raw, _found, _hits = execute_meetings_lookup_json_and_keys(
        target_ids,
        query,
        speaker_scope=speaker_scope,
        user_question=user_question,
        group_n=group_n,
        group_id=group_id,
        seen_pack_keys=seen_pack_keys,
    )
    return raw


def execute_meetings_lookup_json_and_keys(
    target_ids: list[str],
    query: str,
    *,
    speaker_scope: str = "auto",
    user_question: str = "",
    group_n: dict[str, int] | None = None,
    group_id: str | None = None,
    seen_pack_keys: set[str] | None = None,
    prior_hits: list[dict[str, Any]] | None = None,
    cap_tokens: int | None = None,
) -> tuple[str, set[str], list[dict[str, Any]]]:
    """Search transcripts; return JSON, keys to exclude next, accumulated packs."""
    import json

    from src.meeting import store
    from src.services import services

    target = [str(x).strip() for x in target_ids if str(x).strip()]
    by_n = group_n or {}
    unindexed: list[str] = []
    clauses: list[dict[str, Any]] = []
    meta: dict[str, dict[str, Any]] = {}
    scope_all = (speaker_scope or "auto").lower() == "all"
    question = user_question or query
    prior = merge_hit_packs(prior_hits)
    seen = {str(k).strip() for k in (seen_pack_keys or []) if str(k).strip()}
    seen |= {transcript_pack_key(h) for h in prior}

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
        }
        clauses.append({"meeting_id": mid, "speaker_ids": spk})

    hits: list[dict[str, Any]] = []
    if clauses and services.retriever:
        hits = search_group_transcript_packs(
            query,
            meetings=clauses,
            retriever=services.retriever,
            reranker=services.reranker,
            skip_rerank=False,
            exclude_pack_keys=seen or None,
        )
    new_hits = [h for h in hits if transcript_pack_key(h) not in seen]
    new_hits = select_new_hits(new_hits, prior_hits=prior)
    merged = merge_hit_packs(prior, new_hits)
    stitch_hits = merged
    hit_mids = {
        str(h.get("meeting_id") or "").strip()
        for h in stitch_hits
        if str(h.get("meeting_id") or "").strip()
    }
    for mid in hit_mids:
        if mid not in meta:
            meta[mid] = {"n": 0, "title": mid, "date": "", "speakers": {}}
        meta[mid]["sentences"] = load_sentences_for_meeting(mid)
    glue = 2 if (prior and new_hits) else (0 if seen else 2)
    context, shown = (
        stitch_group_hits_and_keys(
            stitch_hits,
            meta,
            glue=glue,
            cap_tokens=cap_tokens,
        )
        if stitch_hits
        else ("", [])
    )
    found = {transcript_pack_key(h) for h in merged}
    cites = group_cites_from_hits(shown, meta)
    searched = [c["meeting_id"] for c in clauses]
    titles = {mid: (meta.get(mid) or {}).get("title") or mid for mid in searched}
    body: dict[str, Any] = {
        "context": context,
        "hit_count": len(shown),
        "unindexed": unindexed,
        "meetings_searched": searched,
        "meeting_titles": titles,
        "cites": cites,
    }
    if group_id:
        body["group_id"] = group_id
        body["cite_as"] = (
            "[n:k] — n is the meeting header n, k is [ref:k] in the excerpts. "
            "The UI renumbers chips 1,2,3 in appearance order. Do not paste excerpt lines."
        )
    if prior and new_hits:
        body["message"] = "Merged with earlier transcript hits this turn."
    elif seen and new_hits:
        body["message"] = (
            "New ranked packs for this query; already-returned packs were not repeated."
        )
    elif prior and not new_hits:
        body["message"] = LOOKUP_NO_NEW_PACKS_MSG
    elif seen and not new_hits:
        body["message"] = LOOKUP_NO_NEW_PACKS_MSG
    elif unindexed:
        body["message"] = (
            "Some meetings have no transcript index yet: " + ", ".join(unindexed)
        )
    elif not context:
        body["message"] = "No matching transcript packs."
    return json.dumps(body, ensure_ascii=False), found, merged


def execute_group_lookup_json(
    group_id: str,
    query: str,
    *,
    meeting_ids: list[str] | None = None,
    speaker_scope: str = "auto",
    user_question: str = "",
) -> str:
    """Group search. meeting_ids must be members; unindexed meetings are skipped."""
    import json

    from src.meeting.catalog import select_lookup_targets
    from src.meeting.group_store import get_group

    group = get_group(group_id)
    if group is None:
        return json.dumps({"error": "Group not found", "context": "", "hit_count": 0})
    member_ids = [m.meeting_id for m in group.members]
    by_n = {m.meeting_id: m.n for m in group.members}
    requested = [str(x).strip() for x in (meeting_ids or []) if str(x).strip()]
    target, err = select_lookup_targets(member_ids, requested or None)
    if err:
        return json.dumps(
            {
                "error": "meeting_id not in this group",
                "unknown": err.split(": ", 1)[-1].split(", "),
                "context": "",
                "hit_count": 0,
            },
            ensure_ascii=False,
        )
    return execute_meetings_lookup_json(
        target or [],
        query,
        speaker_scope=speaker_scope,
        user_question=user_question,
        group_n=by_n,
        group_id=group_id,
    )


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
