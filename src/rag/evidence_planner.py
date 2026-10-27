"""Pure provenance checks for preliminary evidence used by query planning."""

from __future__ import annotations

import copy
import hashlib
import html
import json
import re

from src.rag.context_builder import build_context
from src.rag.evidence_integrity import primary_passage
from src.rag.retriever import RetrievedChunk

_IDENTITY = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def select_seed_chunks(
    rows: list[RetrievedChunk], collections: list[str], *, max_chars: int = 16000,
) -> list[RetrievedChunk]:
    """Keep bounded complete current sources, excluding conflicting identities."""
    by_key = {}
    fingerprints = {}
    conflicts = set()
    for chunk in rows:
        meta = getattr(chunk, "metadata", None)
        body = getattr(chunk, "text", None)
        if not isinstance(meta, dict) or not isinstance(body, str) or not body.strip():
            continue
        values = [meta.get(k) for k in ("collection", "id", "file_id", "version_id")]
        if meta.get("is_current") is not True or not all(
            isinstance(v, str) and _IDENTITY.fullmatch(v) for v in values
        ):
            continue
        col, cid, fid, version = values
        if col not in collections or meta.get("source") != "__file__:" + fid:
            continue
        key = (col, cid)
        fingerprint = hashlib.sha256(json.dumps(
            {"text": body, "metadata": meta}, sort_keys=True, ensure_ascii=False, default=str,
        ).encode()).hexdigest()
        if key in fingerprints and fingerprints[key] != fingerprint:
            conflicts.add(key)
        else:
            by_key[key] = chunk
            fingerprints[key] = fingerprint
    chunks = [copy.deepcopy(c) for key, c in by_key.items() if key not in conflicts][:5]
    if chunks and len(build_context(chunks)) > max_chars:
        return []
    return chunks


def covered_evidence(
    need: dict, seeds: list[RetrievedChunk],
) -> tuple[list[RetrievedChunk], list[str]] | None:
    """Check quoted provenance; semantic support remains the planner's decision."""
    refs = need.get("evidence")
    if not isinstance(refs, list) or not refs or len(refs) > 5:
        return None
    sources = {(c.metadata["collection"], c.metadata["id"]): c for c in seeds}
    selected = {}
    quotes = []
    for ref in refs:
        if not isinstance(ref, dict):
            return None
        collection, cid = ref.get("collection"), ref.get("id")
        if not isinstance(collection, str) or not isinstance(cid, str):
            return None
        chunk = sources.get((collection, cid))
        quote = ref.get("quote")
        if chunk is None or not isinstance(quote, str) or len(quote.strip()) < 12:
            return None
        quote = html.unescape(quote).strip()
        if normalized_text(quote) not in normalized_text(primary_passage(chunk)):
            return None
        selected[(collection, cid)] = copy.deepcopy(chunk)
        quotes.append(quote)
    return list(selected.values()), quotes
