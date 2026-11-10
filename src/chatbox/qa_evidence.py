"""Expand retained Q&A hits and keep historical citations revision-pinned."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException

from src.collections.store import get_collection_meta
from src.file_mgmt import store as files
from src.qa import materials, search_index
from src.rag.chunker import estimate_chunking_tokens
from src.rag.retriever import RetrievedChunk

_THREAD = """
    SELECT l.title,v.id AS version_id,v.number,q.text_revision_id,t.body
    FROM qa_lists l JOIN qa_versions v ON v.id=l.current_version_id
    JOIN qa_version_questions q ON q.version_id=v.id
    JOIN qa_text_revisions t ON t.id=q.text_revision_id
    WHERE l.id=? AND q.question_id=? AND l.archived=0 AND q.included=1
"""
_REPLIES = """
    SELECT c.column_id,c.label,t.id AS text_revision_id,t.body,s.sources_json
    FROM qa_version_cells r
    JOIN qa_version_columns c ON c.version_id=r.version_id AND c.column_id=r.column_id
    JOIN qa_text_revisions t ON t.id=r.text_revision_id
    LEFT JOIN qa_reply_sources s ON s.text_revision_id=t.id
    WHERE r.version_id=? AND r.question_id=? AND c.included=1 AND trim(t.body)!=''
    ORDER BY c.position,c.column_id
"""


def _thread(conn, collection, list_id, question_id):
    row = conn.execute(_THREAD, (list_id, question_id)).fetchone()
    if not row:
        return None
    replies = []
    for reply in conn.execute(_REPLIES, (row["version_id"], question_id)):
        item = dict(reply)
        item["sources"] = json.loads(item.pop("sources_json") or "[]")
        replies.append(item)
    return {
        "collection": collection,
        "project_name": (get_collection_meta(collection) or {}).get("name") or collection,
        "list_id": list_id, "list_title": row["title"],
        "version_id": row["version_id"], "version_number": row["number"],
        "question_id": question_id, "question_revision_id": row["text_revision_id"],
        "question": row["body"], "replies": replies,
    }


def _descriptor(thread):
    return {
        **{key: val for key, val in thread.items() if key not in ("question", "replies")},
        "replies": [
            {key: val for key, val in reply.items() if key != "body"} for reply in thread["replies"]
        ],
    }


def _unavailable():
    return HTTPException(404, "Q&A source unavailable")


def _find_reference(conn, snapshot, ref):
    """Return the saved passage and location only if its owning version still verifies."""
    from src.services import services

    file_id, version_id, chunk_id = (str(ref.get(k) or "") for k in ("file_id", "version_id", "chunk_id"))
    collection, list_id = snapshot["collection"], snapshot["list_id"]
    if not (file_id and version_id and chunk_id):
        return None
    if ref.get("kind") == "material":
        row = conn.execute(
            """SELECT m.current_version_id,v.filename,v.content,v.metadata_json
               FROM qa_materials m JOIN qa_material_versions v ON v.material_id=m.id
               WHERE m.list_id=? AND m.id=? AND v.id=? AND v.parse_state='ready'""",
            (list_id, file_id, version_id),
        ).fetchone()
        if not row:
            return None
        materials.source(collection, list_id, file_id, version_id)
        passage = next(
            (item for item in materials._chunks(row["content"], row["metadata_json"])
             if f"{version_id}:{item[0]}" == chunk_id),
            None,
        )
        if not passage or (ref.get("excerpt") and ref["excerpt"] != passage[2]):
            return None
        _, page, excerpt = passage
        return {
            "kind": "material", "file_id": file_id, "version_id": version_id, "chunk_id": chunk_id,
            "filename": row["filename"], "page_number": page, "excerpt": excerpt,
            "historical": row["current_version_id"] != version_id, "available": True,
        }
    if ref.get("kind") != "project":
        return None

    from src.file_mgmt.storage_paths import resolve_version_blob

    row = conn.execute(
        """SELECT f.current_version_id,v.storage_file_id FROM files f
           JOIN file_versions v ON v.file_id=f.file_id
           WHERE f.file_id=? AND v.version_id=?""",
        (file_id, version_id),
    ).fetchone()
    if not row or not resolve_version_blob(collection, file_id, version_id, row["storage_file_id"]):
        return None

    payload = {}
    if services.db:
        try:
            uuid.UUID(chunk_id)
            points = services.db.get_points_by_ids(collection, [chunk_id])
            if points:
                payload = points[0]["payload"]
        except (ValueError, KeyError):
            pass
    excerpt = str(payload.get("text") or "")
    saved_excerpt = str(ref.get("excerpt") or "")
    offset, page = payload.get("char_offset"), payload.get("page_number")
    if (payload.get("file_id") != file_id or payload.get("version_id") != version_id
            or not excerpt or (saved_excerpt and excerpt != saved_excerpt)):
        from src.api.routes.documents import get_extracted_text

        excerpt = saved_excerpt
        if not excerpt.strip():
            return None
        result = get_extracted_text(f"__file__:{file_id}", collection, version_id=version_id)
        text = result.get("text", "") if isinstance(result, dict) else ""
        offset = text.find(excerpt)
        if offset < 0 or text.find(excerpt, offset + 1) >= 0:
            return None
        page = ref.get("page_number")
    return {
        "kind": "project", "file_id": file_id, "version_id": version_id, "chunk_id": chunk_id,
        "filename": Path(row["storage_file_id"]).name, "page_number": page, "char_offset": offset,
        "excerpt": excerpt, "historical": row["current_version_id"] != version_id, "available": True,
    }


def _reference(conn, snapshot, ref):
    if not isinstance(ref, dict):
        return None
    try:
        return _find_reference(conn, snapshot, ref)
    except Exception:
        return None


def open_snapshot(store, session_id, snapshot_id):
    snapshot = store.get_qa_snapshot(session_id, snapshot_id)
    if not snapshot or not search_index.can_read(snapshot["collection"], historical=True):
        raise _unavailable()
    collection, qid = snapshot["collection"], snapshot["question_id"]
    try:
        with closing(files.get_db(collection)) as conn:
            conn.execute("BEGIN")
            belongs = conn.execute(
                """SELECT 1 FROM qa_questions q JOIN qa_versions v ON v.list_id=q.list_id
                   WHERE q.list_id=? AND q.id=? AND v.id=?""",
                (snapshot["list_id"], qid, snapshot["version_id"]),
            ).fetchone()
            if not belongs:
                raise _unavailable()

            def body(revision_id, column_id):
                row = conn.execute(
                    """SELECT body FROM qa_text_revisions
                       WHERE id=? AND question_id=? AND coalesce(column_id,'')=?""",
                    (revision_id, qid, column_id),
                ).fetchone()
                if not row:
                    raise _unavailable()
                return row["body"]

            replies = []
            for reply in snapshot["replies"]:
                refs = [
                    _reference(conn, snapshot, ref) or {
                        "filename": ref.get("filename", ""), "available": False,
                    }
                    for ref in reply["sources"] if isinstance(ref, dict)
                ]
                replies.append({
                    **reply, "sources": refs,
                    "body": body(reply["text_revision_id"], reply["column_id"]),
                })
            return {
                **snapshot, "question": body(snapshot["question_revision_id"], ""), "replies": replies,
            }
    except (FileNotFoundError, sqlite3.OperationalError):
        raise _unavailable() from None


def _matches_current(snapshot):
    collection = snapshot["collection"]
    if not search_index.can_read(collection):
        return False
    try:
        with closing(files.get_db(collection)) as conn:
            conn.execute("BEGIN")
            current = _thread(conn, collection, snapshot["list_id"], snapshot["question_id"])
            return current is not None and _descriptor(current) == snapshot
    except (FileNotFoundError, sqlite3.OperationalError):
        return False


def history_permitted(metadata):
    """An assistant answer may paraphrase Q&A even if it omitted the inline cite."""
    projects = (metadata or {}).get("qa_projects", [])
    try:
        return isinstance(projects, list) and all(
            isinstance(project, str) and search_index.can_read(project, historical=True)
            for project in projects
        )
    except (OSError, ValueError):
        return False


def _part_cost(text):
    return estimate_chunking_tokens(text) + 40


def _selected_parts(thread, hits, budget):
    matched = {}
    for hit in hits:
        matched.setdefault(hit.metadata["qa_column_id"], hit)
    parts = [("", "Question", thread["question"])] + [
        (r["column_id"], r["label"], r["body"]) for r in thread["replies"]
    ]

    if sum(_part_cost(body) for _, _, body in parts) <= budget:
        return parts
    chosen = {}
    index = {cid: i for i, (cid, _, _) in enumerate(parts)}
    preferred = list(dict.fromkeys(
        [0, *(index[cid] for cid in matched if cid in index), len(parts) - 1, *range(len(parts))]
    ))
    for i in preferred:
        cid, label, body = parts[i]
        allowance = budget
        if i == 0 and any(matched):
            allowance -= min(budget // 2, 120)
        if _part_cost(body) > allowance and (i == 0 or cid in matched):
            hit = matched.get(cid)
            body = hit.text if hit else body
            if _part_cost(body) > allowance:
                body = body[:max(0, allowance - 40)].rstrip()
        if body and _part_cost(body) <= budget:
            chosen[i] = (cid, label, body)
            budget -= _part_cost(body)
    if 0 not in chosen or not any(cid in matched for cid, _, _ in chosen.values()):
        return []
    return [chosen[i] for i in sorted(chosen)]


@dataclass
class QaPost:
    snapshot_id: str
    snapshot: dict
    text: dict
    evidence_ids: list[str]
    source: dict
    hits: list[RetrievedChunk]


@dataclass
class QaBundle:
    posts: list[QaPost] = field(default_factory=list)

    def context(self, registry):
        live = []
        for post in self.posts:
            if post.text and _matches_current(post.snapshot):
                live.append(post.text)
            else:
                registry.discard(post.evidence_ids)
        return json.dumps({"qa_posts": live}, ensure_ascii=False) if live else ""


def _reference_key(meta):
    return (meta.get("collection"), meta.get("file_id"), meta.get("version_id"),
            meta.get("chunk_id") or meta.get("id"))


class QaEvidence:
    """Manage one Chat turn's thread budget and pinned source cards."""

    def __init__(self, store, session_id, budget=None, *, llm=None):
        self.store = store
        self.session_id = session_id
        window = getattr(llm, "context_window", None)
        self.window = window if type(window) is int and window > 0 else None
        output = getattr(llm, "_default_max_tokens", None)
        self.output_reserve = output if type(output) is int and output > 0 else 4096
        self.capacity = budget if budget is not None else (24000 if self.window else 12000)
        self.remaining = self.capacity
        self.retried = False
        self.posts = {}
        self.project_ids = set()
        self.linked = {}

    def fit_model_window(self, messages, registry):
        if self.window is None or not self.posts:
            return
        used = 2048 + self.output_reserve
        for message in messages:
            content = message.get("content")
            used += _part_cost(str(message.get("tool_calls") or ""))
            if isinstance(content, str):
                used += _part_cost(content)
            elif isinstance(content, list):
                used += sum(
                    _part_cost(part.get("text", "")) if part.get("type") == "text" else 1024
                    for part in content if isinstance(part, dict)
                )
        self._limit(max(0, self.window - used), registry)

    def retry_context(self, error, registry):
        if self.retried or self.capacity == self.remaining:
            return False
        detail = str(error).lower()
        if getattr(error, "code", None) != "context_length_exceeded" and not any(
            marker in detail for marker in (
                "context_length_exceeded", "maximum context length", "context length exceeded",
                "exceeds the context window",
            )
        ):
            return False
        self.retried = True
        self._limit(self.capacity // 2, registry)
        return True

    def _limit(self, capacity, registry):
        if capacity >= self.capacity:
            return
        used = self.capacity - self.remaining
        self.capacity = capacity
        self.remaining = max(0, capacity - used)
        if used <= capacity:
            return

        self.remaining = capacity
        self.linked.clear()
        for post in sorted(self.posts.values(), key=lambda item: item.source["score"], reverse=True):
            registry.discard(post.evidence_ids)
            post.text = {}
            collection = post.snapshot["collection"]
            if self.remaining <= 80 or not search_index.can_read(collection):
                continue
            try:
                with closing(files.get_db(collection)) as conn:
                    conn.execute("BEGIN")
                    thread = _thread(conn, collection, post.snapshot["list_id"], post.snapshot["question_id"])
            except (FileNotFoundError, sqlite3.OperationalError):
                continue
            if not thread or _descriptor(thread) != post.snapshot:
                continue
            parts = _selected_parts(thread, post.hits, self.remaining)
            if parts:
                fitted = self._make_post(thread, post.snapshot, parts, post.hits, registry)
                post.text, post.evidence_ids, post.source = fitted.text, fitted.evidence_ids, fitted.source
                self.remaining -= sum(_part_cost(body) for _, _, body in parts)

    def capture(self, hits, docs, registry, query, retrieval):
        bundle = QaBundle()
        grouped = defaultdict(list)
        for hit in sorted(hits, key=lambda chunk: chunk.score, reverse=True):
            meta = hit.metadata
            grouped[(meta["collection"], meta["qa_list_id"], meta["qa_question_id"])].append(hit)
        doc_keys = {_reference_key(chunk["metadata"] if isinstance(chunk, dict) else chunk.metadata) for chunk in docs}
        for key in doc_keys.intersection(self.linked):
            post, eid, cost = self.linked.pop(key)
            post.text["linked_passages"] = [
                passage for passage in post.text["linked_passages"]
                if passage["citation"] != f"[cite:{eid}]"
            ]
            post.evidence_ids.remove(eid)
            registry.discard([eid])
            self.remaining += cost
        seen = doc_keys | self.linked.keys()
        for key, group in grouped.items():
            collection, list_id, qid = key
            if self.remaining <= 80 or not search_index.can_read(collection):
                continue
            try:
                with closing(files.get_db(collection)) as conn:
                    conn.execute("BEGIN")
                    current = search_index._current_rows(conn, [hit.metadata["id"] for hit in group])
                    valid = [
                        hit for hit in group
                        if hit.metadata["id"] in current
                        and current[hit.metadata["id"]]["revision_id"] == hit.metadata["qa_text_revision_id"]
                    ]
                    thread = _thread(conn, collection, list_id, qid) if valid else None
                    if not thread:
                        continue
                    old = self.posts.get(key)
                    snapshot = _descriptor(thread)
                    if old and old.snapshot == snapshot:
                        continue
                    parts = _selected_parts(thread, valid, self.remaining)
                    if not parts:
                        continue
                    post = self._make_post(thread, snapshot, parts, valid, registry)
                    self.remaining -= sum(_part_cost(body) for _, _, body in parts)
                    if retrieval and len(self.linked) < 6 and self.remaining > 80:
                        self._add_links(post, parts, conn, seen, registry, query, retrieval, 6 - len(self.linked))
            except (FileNotFoundError, sqlite3.OperationalError):
                continue
            self.project_ids.add(collection)
            self.posts[key] = post
            bundle.posts.append(post)
        return bundle

    def _make_post(self, thread, snapshot, parts, hits, registry):
        snapshot_id = hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:32]
        self.store.save_qa_snapshot(self.session_id, snapshot_id, snapshot)
        replies = {reply["column_id"]: reply for reply in thread["replies"]}
        matched = {}
        for hit in hits:
            matched.setdefault(hit.metadata["qa_column_id"], hit.metadata["qa_fragment"] + 1)
        base = {
            "collection": thread["collection"], "version_id": thread["version_id"],
            "qa_snapshot_id": snapshot_id, "qa_list_id": thread["list_id"],
            "qa_question_id": thread["question_id"],
            "source_label": f"{thread['list_title']} · {thread['question'][:150]}",
        }
        records, blocks = {}, []
        for cid, label, text in parts:
            rid = replies[cid]["text_revision_id"] if cid else thread["question_revision_id"]
            full_text = replies[cid]["body"] if cid else thread["question"]
            record = registry.register(
                text, {**base, "qa_column_id": cid, "qa_text_revision_id": rid}, kind="qa"
            )
            records[cid] = record
            blocks.append({
                "label": label, "text": text, "matched": cid in matched,
                "citation": f"[cite:{record['id']}]",
                **({"excerpt": True, "fragment": matched.get(cid, 1)} if text != full_text else {}),
            })
        primary = next(
            (records[hit.metadata["qa_column_id"]] for hit in hits
             if hit.metadata["qa_column_id"] in records),
            records[""],
        )
        card = {
            "text": primary["quote"], "score": hits[0].score,
            "metadata": {**base, **primary["source"], "qa_evidence_id": primary["id"]},
        }
        return QaPost(
            snapshot_id, snapshot, {
                "project": thread["project_name"], "list": thread["list_title"],
                "version": thread["version_number"], "rounds": blocks,
                "omitted": len(thread["replies"]) + 1 - len(parts),
                "omitted_rounds": [reply["label"] for cid, reply in replies.items() if cid not in records],
            },
            [record["id"] for record in records.values()], card, hits,
        )

    def _add_links(self, post, parts, conn, seen, registry, query, retrieval, limit):
        from src.rag.agent_nodes import node_combined_grade
        from src.rag.agent_state import AgentState

        if not (retrieval.reranker and retrieval.llm):
            return 0
        included = {cid for cid, _, _ in parts}
        chunks = []
        for reply in post.snapshot["replies"]:
            if reply["column_id"] not in included:
                continue
            for ref in reply["sources"]:
                link = _reference(conn, post.snapshot, ref)
                if not link:
                    continue
                meta = {
                    **link, "collection": post.snapshot["collection"],
                    "qa_snapshot_id": post.snapshot_id, "qa_list_id": post.snapshot["list_id"],
                    "qa_column_id": reply["column_id"],
                    "source_label": link["filename"],
                }
                if link["kind"] == "material":
                    meta["qa_material_id"] = link["file_id"]
                else:
                    meta["source"] = "__file__:" + link["file_id"]
                key = _reference_key(meta)
                if key not in seen and not any(_reference_key(c.metadata) == key for c in chunks):
                    chunks.append(RetrievedChunk(link["excerpt"][:4000], 0.0, meta))
        if not chunks:
            return 0
        try:
            ranked = retrieval.reranker.rerank(query, chunks, top_k=min(2, limit))
            state = AgentState(original_query=query, original_user_question=query)
            node_combined_grade(state, ranked, llm=retrieval.llm)
            if state.grade_error:
                return 0
        except Exception:
            return 0

        added = []
        for chunk in state.retained_chunks:
            meta = chunk.metadata
            cost = _part_cost(chunk.text)
            if cost > self.remaining:
                continue
            kind = "qa_material" if meta.get("qa_material_id") else "document"
            record = registry.register(chunk.text, meta, kind=kind)
            key = _reference_key(meta)
            seen.add(key)
            self.linked[key] = (post, record["id"], cost)
            self.remaining -= cost
            post.evidence_ids.append(record["id"])
            added.append({
                "filename": record["title"], "historical": bool(meta.get("historical")),
                "text": chunk.text, "citation": f"[cite:{record['id']}]",
            })
        if added:
            post.text["linked_passages"] = added
        return len(added)

    def finish_search(self, result, docs, registry, query, retrieval, content):
        hits = getattr(result, "qa_hits", None) or []
        bundle = self.capture(hits, docs, registry, query, retrieval) if hits or self.linked else QaBundle()
        if bundle.posts and content == "No relevant information found.":
            content = ""
        errors = getattr(result, "errors", ()) or ()
        if any(error.get("stage", "").startswith("qa_") for error in errors):
            notice = json.dumps({"qa_status": "unavailable"})
            content = (
                [*content, {"type": "text", "text": notice}] if isinstance(content, list)
                else str(content or "") + "\n" + notice
            )
        return bundle, content

    def metadata(self):
        return {"qa_projects": sorted(self.project_ids)} if self.project_ids else {}

    def sources(self, registry):
        live = {record["id"] for record in registry.records()}
        return [
            post.source for post in self.posts.values()
            if post.source["metadata"]["qa_evidence_id"] in live
        ]


def project_qa_tool_messages(messages, registry):
    """Add only still-current threads to ephemeral model input."""
    projected = []
    for message in messages:
        bundle = message.get("_qa_bundle")
        text = bundle.context(registry) if bundle and registry else ""
        if text:
            message = dict(message)
            content = message.get("content") or ""
            message["content"] = (
                [*content, {"type": "text", "text": text}] if isinstance(content, list)
                else content + "\n\n" + text
            )
        projected.append(message)
    return projected
