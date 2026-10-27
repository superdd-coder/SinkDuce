"""Session-scoped evidence identities shared by ordinary answers and reports.

Only callers handling authorized retrieval results may register evidence. The
model selects existing identities; it never supplies paths or quote metadata.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath

MAX_EVIDENCE = 2048  # Conversation cache; an individual report remains capped at 256.
MAX_QUOTE = 4000
_MARKERS = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`|\[cite:([^\]\r\n]{0,200})\]", re.S)
_SOURCE_KEYS = ("collection", "collection_name", "file_id", "version_id", "source",
                "chunk_id", "meeting_id", "attachment_id", "url", "content_hash")
_LOCATION_KEYS = ("page_number", "slide_number", "paragraph_index", "sheet_name",
                  "char_offset", "heading_path", "line", "sentence_id", "start_time", "end_time",
                  "image_index")


def _excerpts(text, boundaries=()):
    """Bound source excerpts while preserving exact offsets into returned text."""
    text = str(text or "")
    start = 0
    while start < len(text):
        end = min(len(text), start + 1600)
        end = min((point for point in boundaries if start < point < end), default=end)
        if end < len(text):
            boundary = text.rfind("\n\n", start + 300, end)
            if boundary > start:
                end = boundary + 2
        if text[start:end].strip():
            yield start, text[start:end]
        start = end


def citation_ids(markdown: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in _MARKERS.finditer(markdown or "") if m.group(1) is not None))


def _meeting_records(payload):
    """Match returned speech against its own meeting, never another meeting's ref."""
    context = str(payload.get("context") or "")
    mid = payload.get("meeting_id")
    sections = {}
    for block in re.split(r"(?m)^## Meeting:", context):
        found = re.search(r"(?m)^id: ([^\n]+)", block)
        if found:
            sections[found.group(1).strip()] = block
    if mid:
        sections[str(mid)] = context
    cites = list(payload.get("cites") or [])
    if not cites and mid:
        cites = [{"meeting_id": mid, "ref_n": int(n)} for n in re.findall(r"(?m)^\[ref:(\d+)\]", context)]
    if not cites and mid:
        summary = payload.get("summary") or context
        if summary:
            yield summary, {"meeting_id": mid, "title": payload.get("title")}
        return
    rows_by_mid = {}
    for cite in cites:
        current_mid = str(cite.get("meeting_id") or mid or "")
        if not current_mid:
            continue
        section = sections.get(current_mid, context if not sections else "")
        meta = {**cite, "meeting_id": current_mid,
                "sentence_id": cite.get("sentence_id") or cite.get("id")}
        quote = cite.get("text") or cite.get("quote") or ""
        if quote:
            yield quote, meta
            continue
        if current_mid not in rows_by_mid:
            from src.meeting.transcript_index import load_sentences_for_meeting
            try:
                rows_by_mid[current_mid] = load_sentences_for_meeting(current_mid)
            except (ValueError, OSError):
                rows_by_mid[current_mid] = []
        rows = rows_by_mid[current_mid]
        try:
            number = int(cite.get("ref_n") or 0)
        except (ValueError, TypeError):
            continue
        row = rows[number - 1] if 1 <= number <= len(rows) else None
        if row:
            original = str(row.get("original_text") or row.get("text") or "").strip()
            if original and original in section:
                meta.update({k: row[k] for k in ("sentence_id", "start_time", "end_time") if row.get(k) is not None})
                yield original, meta
        else:
            match = re.search(r"(?m)^\[ref:" + str(number) + r"\]\s*([^\n]+)", section)
            if match:
                text = match.group(1)
                quote = text.split(": ", 1)[-1]
                meta["sentence_id"] = meta.get("sentence_id") or f"stt_{number:04d}"
                yield quote, meta


class EvidenceRegistry:
    def __init__(self, session_id: str, records=()):
        self.session_id = session_id
        self._records: dict[str, dict] = {}
        self._active: dict[str, bool] = {}
        for record in records or ():
            if not isinstance(record, dict) or not isinstance(record.get("source"), dict):
                continue
            previous = set(self._records)
            restored = self.register(record.get("quote", ""), {
                **record["source"], **(record.get("location") or {}),
                "source_label": record.get("title", ""),
            }, kind=record.get("kind", "document"))
            # Recompute identities: corrupted history cannot introduce fake IDs.
            if restored and restored["id"] != record.get("id") and restored["id"] not in previous:
                self._records.pop(restored["id"], None)
        self._active = dict.fromkeys(list(self._records)[-32:], True)

    def register(self, text: str, metadata: dict, *, kind: str | None = None) -> dict | None:
        text = str(text or "")[:MAX_QUOTE]
        if not text.strip() and kind != "image":
            return None
        meta = dict(metadata or {})
        source = {k: meta[k] for k in _SOURCE_KEYS if meta.get(k) is not None}
        location = {k: meta[k] for k in _LOCATION_KEYS if meta.get(k) is not None}
        kind = kind or ("attachment" if source.get("attachment_id") else "meeting" if source.get("meeting_id")
                        else "web" if source.get("url") else "document")
        identity = json.dumps([self.session_id, kind, source, location, text],
                              sort_keys=True, ensure_ascii=False, default=str)
        eid = "e_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        self._active.pop(eid, None)
        self._active[eid] = True
        if len(self._active) > 256:
            self._active.pop(next(iter(self._active)))
        if eid in self._records:
            return self._records[eid]
        if len(self._records) >= MAX_EVIDENCE:
            self._records.pop(next(iter(self._records)))
        title = str(meta.get("source_label") or meta.get("filename") or meta.get("title") or
                    meta.get("source") or meta.get("url") or "Source")
        if title.startswith(("/", "file:")):
            title = PurePosixPath(title).name
        evidence = {"id": eid, "kind": kind, "title": title[:300], "quote": text,
                    "source": source, "location": location}
        self._records[eid] = evidence
        return evidence

    def records(self) -> list[dict]:
        return list(self._records.values())

    def live_records(self) -> list[dict]:
        return [self._records[eid] for eid in self._active if eid in self._records]

    def include(self, records):
        """Reactivate trusted report evidence without duplicating the history cache."""
        restored = EvidenceRegistry(self.session_id, records)
        for e in restored.records():
            self.register(e["quote"], {**e["source"], **e["location"], "source_label": e["title"]}, kind=e["kind"])

    def used(self, markdown: str, *, strict=False) -> list[dict]:
        ids = citation_ids(markdown)
        missing = [eid for eid in ids if eid not in self._records]
        if strict and missing:
            raise ValueError("Unknown citation: " + ", ".join(missing[:5]))
        return [self._records[eid] for eid in ids if eid in self._records]

    def capture(self, tool: str, content, *, sources=(), args=None):
        """Annotate a result with IDs while keeping multimodal images untouched."""
        args = args or {}
        selected = []

        def add(text, meta, kind=None):
            e = self.register(text, meta, kind=kind)
            if e and e not in selected:
                selected.append(e)

        def snippets(text, meta, kind=None, positions=()):
            base = meta.get("char_offset")
            points = sorted((p for p in positions if isinstance(p, dict) and
                             isinstance(p.get("char_offset"), int)), key=lambda p: p["char_offset"]) if isinstance(base, int) else []
            boundaries = [p["char_offset"] - base for p in points]
            for offset, excerpt in _excerpts(text, boundaries):
                position = dict(meta)
                if isinstance(meta.get("char_offset"), int):
                    position["char_offset"] += offset
                if isinstance(meta.get("line"), int):
                    position["line"] += str(text)[:offset].count("\n")
                if offset:
                    position.pop("page_number", None)  # Only the chunk's starting page is known.
                    position.pop("paragraph_index", None)
                if points:
                    located = next((p for p in reversed(points) if p["char_offset"] <= base + offset), {})
                    position.update({k: located[k] for k in ("page_number", "slide_number", "paragraph_index", "sheet_name") if k in located})
                add(excerpt, position, kind)

        for source in sources or ():
            if isinstance(source, dict):
                snippets(source.get("text", ""), source.get("metadata") or {})
        texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"] if isinstance(content, list) else [content]
        for text in texts:
            if not isinstance(text, str):
                continue
            try:
                payload = json.loads(text)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict) or payload.get("error"):
                continue
            if tool in {"read_chat_attachment", "search_chat_attachment"}:
                meta = {"attachment_id": payload.get("attachment_id"),
                        "filename": payload.get("filename") or payload.get("attachment_id"),
                        "line": payload.get("start_line", 1)}
                meta.update({k: v for k, v in (payload.get("location") or {}).items() if k in _LOCATION_KEYS})
                if isinstance(payload.get("char_offset"), int):
                    meta["char_offset"] = payload["char_offset"]
                snippets(payload.get("content", ""), meta, "attachment", payload.get("position_map") or ())
                for match in payload.get("matches") or []:
                    add(match.get("text", ""), {**meta, "line": match.get("line")}, "attachment")
                for image in payload.get("images") or []:
                    if image.get("image_index") in (payload.get("attached_image_indices") or []):
                        add("", {**meta, **{k: image[k] for k in _LOCATION_KEYS if k in image}}, "image")
            elif tool in {"lookup_meeting_transcript", "read_meeting_summary"}:
                for quote, meta in _meeting_records(payload):
                    add(quote, meta, "meeting")
            elif tool in {"get_document_text", "get_file_chunks", "get_note"}:
                meta = {**args, **{k: payload[k] for k in _SOURCE_KEYS if k in payload},
                        "char_offset": payload.get("offset", 0), "title": payload.get("title")}
                if tool == "get_note" and (note_id := payload.get("id") or args.get("note_id")):
                    meta["source"] = "__note__:" + str(note_id)
                snippets(payload.get("content") or payload.get("text") or "", meta)
                for chunk in payload.get("chunks") or []:
                    snippets(chunk.get("text", ""), {**meta, **(chunk.get("metadata") or {})})
        if not selected:
            return content
        catalog = json.dumps({"evidence": [{**e, "quote": e["quote"][:500]} for e in selected]}, ensure_ascii=False)
        def annotate(text):
            try:
                payload = json.loads(text)
            except (ValueError, TypeError):
                return str(text or "") + "\n\n" + catalog
            if isinstance(payload, dict):
                payload["evidence"] = json.loads(catalog)["evidence"]
                return json.dumps(payload, ensure_ascii=False)
            return str(text or "") + "\n\n" + catalog
        if isinstance(content, list):
            out = [dict(p) for p in content]
            for part in out:
                if part.get("type") == "text":
                    part["text"] = annotate(part.get("text", ""))
                    break
            else:
                out.append({"type": "text", "text": catalog})
            return out
        return annotate(content)


def registry_from_history(store, session_id: str) -> EvidenceRegistry:
    records = []
    for message in store.get_messages(session_id):
        metadata = getattr(message, "metadata", None) or {}
        if isinstance(metadata.get("citations"), list):
            records.extend(metadata["citations"])
    return EvidenceRegistry(session_id, records)


def annotate_attachment_parts(registry, parts, store, sid):
    """Register exactly the bounded excerpts/pages actually shown to the model."""
    out = []
    for part in parts:
        if part.get("type") != "text":
            out.append(part)
            continue
        text = part.get("text", "")
        match = re.match(r"\[Conversation attachment (\{.*\})\]\n(.*)\n\[End of attachment excerpt\]\Z", text, re.S)
        if not match:
            out.append(part)
            continue
        manifest = json.loads(match.group(1))
        aid = manifest["attachment_id"]
        images = store.parsed(sid, aid)["images"]
        payload = {**manifest, "content": match.group(2), "images": [
            {**{k: v for k, v in image.items() if k in _LOCATION_KEYS}, "image_index": i}
            for i, image in enumerate(images) if i in manifest.get("attached_image_indices", [])]}
        annotated = registry.capture("read_chat_attachment", json.dumps(payload, ensure_ascii=False))
        catalog = json.loads(annotated).get("evidence", [])
        out.append({**part, "text": text + "\n" + json.dumps({"evidence": [
            {**e, "quote": e["quote"][:160]} for e in catalog]}, ensure_ascii=False)})
    return out
