"""Per-chain smart to-do suggestions (debounced enrichment LLM).

Triggered after timeline content stabilizes; results cached in
``todo_suggestion_state`` for Timeline chain-end UI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from src.file_mgmt.store import (
    _ensure_todo_suggestion_state_table,
    get_db,
)

logger = logging.getLogger("file_mgmt.todo_suggestions")

# After last chain content mutation settles, wait this long before LLM.
# Matches product: start the clock only once create/attach/message activity stops.
TODO_SUGGEST_DEBOUNCE_SEC = 5.0

# (collection_id, chain_id) → Timer
_timers: dict[tuple[str, str], threading.Timer] = {}
_timers_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open(collection_id: str):
    from src.file_mgmt.store import init_collection_db

    init_collection_db(collection_id)
    conn = get_db(collection_id)
    _ensure_todo_suggestion_state_table(conn)
    return conn


def _parse_suggestions_json(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("suggestion_id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not sid or not title:
            continue
        body = item.get("body")
        body_s = (str(body).strip() if body is not None else "") or None
        out.append(
            {
                "suggestion_id": sid,
                "title": title,
                "body": body_s or "",
            }
        )
    return out


def get_todo_suggestions(collection_id: str, chain_id: str) -> dict[str, Any]:
    """Return cached suggestion state for a chain (never 404 for missing row)."""
    conn = _open(collection_id)
    try:
        ch = conn.execute(
            "SELECT chain_id FROM chains WHERE chain_id=?", (chain_id,)
        ).fetchone()
        if not ch:
            from fastapi import HTTPException

            raise HTTPException(404, f"Chain '{chain_id}' not found")

        row = conn.execute(
            "SELECT * FROM todo_suggestion_state WHERE chain_id=?",
            (chain_id,),
        ).fetchone()
        if not row:
            return {
                "chain_id": chain_id,
                "status": "idle",
                "suggestions": [],
                "error": None,
                "updated_at": None,
                "generated_at": None,
            }
        items = _parse_suggestions_json(row["suggestions_json"])
        return {
            "chain_id": chain_id,
            "status": row["status"] or "idle",
            "suggestions": [
                {
                    "suggestion_id": i["suggestion_id"],
                    "title": i["title"],
                    "body": i["body"] or None,
                }
                for i in items
            ],
            "error": row["error"],
            "updated_at": row["updated_at"],
            "generated_at": row["generated_at"],
        }
    finally:
        conn.close()


def _upsert_state(
    conn,
    chain_id: str,
    *,
    status: str,
    suggestions: list[dict] | None = None,
    error: str | None = None,
    fingerprint: str | None = None,
    generated_at: str | None = None,
    keep_suggestions: bool = False,
) -> None:
    now = _now_iso()
    existing = conn.execute(
        "SELECT suggestions_json, context_fingerprint, generated_at "
        "FROM todo_suggestion_state WHERE chain_id=?",
        (chain_id,),
    ).fetchone()

    if suggestions is not None:
        sug_json = json.dumps(suggestions, ensure_ascii=False)
    elif keep_suggestions and existing:
        sug_json = existing["suggestions_json"] or "[]"
    else:
        sug_json = existing["suggestions_json"] if existing else "[]"
        if sug_json is None:
            sug_json = "[]"

    fp = fingerprint
    if fp is None and existing:
        fp = existing["context_fingerprint"]
    gen_at = generated_at
    if gen_at is None and existing and keep_suggestions:
        gen_at = existing["generated_at"]

    conn.execute(
        """INSERT INTO todo_suggestion_state
           (chain_id, status, suggestions_json, error, context_fingerprint,
            updated_at, generated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(chain_id) DO UPDATE SET
             status=excluded.status,
             suggestions_json=excluded.suggestions_json,
             error=excluded.error,
             context_fingerprint=excluded.context_fingerprint,
             updated_at=excluded.updated_at,
             generated_at=excluded.generated_at
        """,
        (chain_id, status, sug_json, error, fp, now, gen_at),
    )


def consume_suggestion(
    collection_id: str, chain_id: str, suggestion_id: str
) -> bool:
    """Remove one suggestion by id. Returns True if removed."""
    sid = (suggestion_id or "").strip()
    if not sid:
        return False
    conn = _open(collection_id)
    try:
        with conn:
            row = conn.execute(
                "SELECT * FROM todo_suggestion_state WHERE chain_id=?",
                (chain_id,),
            ).fetchone()
            if not row:
                return False
            items = _parse_suggestions_json(row["suggestions_json"])
            new_items = [i for i in items if i["suggestion_id"] != sid]
            if len(new_items) == len(items):
                return False
            _upsert_state(
                conn,
                chain_id,
                status=row["status"] or "ready",
                suggestions=new_items,
                error=row["error"],
                fingerprint=row["context_fingerprint"],
                generated_at=row["generated_at"],
            )
            return True
    finally:
        conn.close()


def _file_short_summary(collection_id: str, file_id: str) -> str:
    """Best-effort short summary for an attached file."""
    source = f"__file__:{file_id}"
    # 1) Qdrant chunk payload.summary
    try:
        from src import services

        if services.db is not None:
            from qdrant_client.http.models import FieldCondition, Filter, MatchValue

            flt = Filter(
                must=[
                    FieldCondition(
                        key="source", match=MatchValue(value=source)
                    ),
                ]
            )
            # Prefer points that carry a non-empty summary
            points, _ = services.db.scroll_points(
                collection=collection_id,
                scroll_filter=flt,
                limit=8,
                with_payload=True,
                with_vectors=False,
            )
            for p in points or []:
                pl = p.get("payload") or {}
                s = (pl.get("summary") or "").strip()
                if s:
                    return s[:400]
    except Exception:
        logger.debug(
            "short_summary qdrant miss col=%s file=%s",
            collection_id,
            file_id,
            exc_info=True,
        )

    # 2) Structured doc summary
    try:
        from src import services
        from src.rag.summary_manager import SummaryManager

        if services.db is not None:
            sm = SummaryManager(services.db)
            pl = sm.get_doc_summary(collection_id, source)
            if pl:
                for key in ("insights", "facts", "data"):
                    items = pl.get(key) or []
                    if items:
                        line = str(items[0]).strip()
                        if line:
                            return line[:400]
    except Exception:
        logger.debug(
            "short_summary doc_summary miss col=%s file=%s",
            collection_id,
            file_id,
            exc_info=True,
        )
    return ""


def build_chain_context(collection_id: str, chain_id: str) -> dict[str, Any]:
    """Assemble ordered node context + open todo titles for the LLM."""
    conn = _open(collection_id)
    try:
        ch = conn.execute(
            "SELECT chain_id FROM chains WHERE chain_id=?", (chain_id,)
        ).fetchone()
        if not ch:
            from fastapi import HTTPException

            raise HTTPException(404, f"Chain '{chain_id}' not found")

        group_names = {
            r["group_id"]: (r["name"] or "").strip() or "Uncategorized"
            for r in conn.execute(
                "SELECT group_id, name FROM node_groups"
            ).fetchall()
        }

        nodes = conn.execute(
            """SELECT node_id, title, group_id, node_type, "order"
               FROM nodes WHERE chain_id=?
               ORDER BY "order" ASC, created_at ASC""",
            (chain_id,),
        ).fetchall()

        node_list: list[dict[str, Any]] = []
        for n in nodes:
            ntype = (n["node_type"] or "event").lower()
            title = (n["title"] or "").strip()
            # Skip empty system anchors
            if ntype in ("start", "end") and not title:
                continue
            gid = n["group_id"]
            gname = group_names.get(gid, "Uncategorized") if gid else "Uncategorized"

            msg_rows = conn.execute(
                """SELECT body FROM messages
                   WHERE owner_type='node' AND owner_id=?
                   ORDER BY created_at ASC""",
                (n["node_id"],),
            ).fetchall()
            messages = [
                (m["body"] or "").strip()
                for m in msg_rows
                if (m["body"] or "").strip()
            ]

            att_rows = conn.execute(
                """SELECT fn.file_id, fv.storage_file_id
                   FROM file_nodes fn
                   JOIN files f ON f.file_id = fn.file_id
                   LEFT JOIN file_versions fv
                     ON fv.version_id = f.current_version_id
                   WHERE fn.node_id=?""",
                (n["node_id"],),
            ).fetchall()
            attachments = []
            for a in att_rows:
                fid = a["file_id"]
                fname = (a["storage_file_id"] or "").strip() or fid
                short = _file_short_summary(collection_id, fid)
                attachments.append(
                    {
                        "file_id": fid,
                        "filename": fname,
                        "short_summary": short,
                    }
                )

            node_list.append(
                {
                    "node_id": n["node_id"],
                    "title": title or "Untitled",
                    "group": gname,
                    "node_type": ntype,
                    "messages": messages,
                    "attachments": attachments,
                }
            )

        # Open todos resolved to this chain
        main = conn.execute(
            "SELECT chain_id FROM chains WHERE parent_chain_id IS NULL LIMIT 1"
        ).fetchone()
        main_id = main["chain_id"] if main else None
        open_titles: list[str] = []
        for t in conn.execute(
            "SELECT title, target_chain_id FROM todos WHERE done=0"
        ).fetchall():
            target = t["target_chain_id"]
            resolved = target if target else main_id
            if resolved == chain_id:
                tt = (t["title"] or "").strip()
                if tt:
                    open_titles.append(tt)

        return {
            "chain_id": chain_id,
            "nodes": node_list,
            "open_todo_titles": open_titles,
        }
    finally:
        conn.close()


def _format_context_for_prompt(ctx: dict[str, Any]) -> tuple[str, str, str]:
    """Return (chain_text, open_todos_text, fingerprint)."""
    parts: list[str] = []
    for i, n in enumerate(ctx.get("nodes") or [], start=1):
        block = [
            f"### Node {i}: {n.get('title') or 'Untitled'}",
            f"Group: {n.get('group') or 'Uncategorized'}",
        ]
        msgs = n.get("messages") or []
        if msgs:
            block.append("Messages:")
            for m in msgs:
                block.append(f"- {m}")
        else:
            block.append("Messages: (none)")
        atts = n.get("attachments") or []
        if atts:
            block.append("Attachments:")
            for a in atts:
                line = f"- {a.get('filename') or a.get('file_id')}"
                ss = (a.get("short_summary") or "").strip()
                if ss:
                    line += f" — {ss}"
                block.append(line)
        else:
            block.append("Attachments: (none)")
        parts.append("\n".join(block))

    chain_text = "\n\n".join(parts) if parts else "(empty chain)"
    titles = ctx.get("open_todo_titles") or []
    open_text = (
        "\n".join(f"- {t}" for t in titles) if titles else "(none)"
    )
    fp_src = json.dumps(
        {
            "nodes": ctx.get("nodes") or [],
            "open_todo_titles": titles,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(fp_src.encode("utf-8")).hexdigest()
    return chain_text, open_text, fingerprint


def _get_enrichment_llm(collection_id: str):
    """Resolve enrichment LLM (collection override → global → default)."""
    from src.tasks.handlers import _get_enriching_llm
    from src import services

    config: dict = {}
    try:
        if services.db is not None:
            config = services.db.get_collection_config(collection_id) or {}
    except Exception:
        config = {}
    return _get_enriching_llm(config)


def _extract_json_array(text: str) -> list:
    raw = (text or "").strip()
    if not raw:
        return []
    # Strip fences
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("suggestions"), list):
            return data["suggestions"]
    except json.JSONDecodeError:
        pass
    # Find first [...] block
    m = re.search(r"\[[\s\S]*\]", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            return []
    return []


def _normalize_llm_items(raw_items: list) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        body = item.get("body") or item.get("description") or ""
        body_s = str(body).strip() if body is not None else ""
        out.append(
            {
                "suggestion_id": uuid.uuid4().hex,
                "title": title[:80],
                "body": body_s[:2000],
            }
        )
        if len(out) >= 3:
            break
    return out


def generate_todo_suggestions_now(
    collection_id: str, chain_id: str, *, force: bool = False
) -> dict[str, Any]:
    """Synchronously build context and call LLM; update cache row."""
    ctx = build_chain_context(collection_id, chain_id)
    chain_text, open_text, fingerprint = _format_context_for_prompt(ctx)

    conn = _open(collection_id)
    try:
        existing = conn.execute(
            "SELECT * FROM todo_suggestion_state WHERE chain_id=?",
            (chain_id,),
        ).fetchone()
        # Same context fingerprint + ready → skip LLM (including empty after consume)
        if (
            not force
            and existing
            and existing["status"] == "ready"
            and existing["context_fingerprint"] == fingerprint
        ):
            return get_todo_suggestions(collection_id, chain_id)

        with conn:
            _upsert_state(
                conn,
                chain_id,
                status="generating",
                keep_suggestions=True,
                fingerprint=fingerprint,
            )
    finally:
        conn.close()

    from src.prompts import TODO_SUGGEST_SYSTEM_PROMPT, TODO_SUGGEST_USER_PROMPT

    user_prompt = TODO_SUGGEST_USER_PROMPT.format(
        chain_context=chain_text,
        open_todo_titles=open_text,
    )

    try:
        llm = _get_enrichment_llm(collection_id)
        if llm is None:
            raise RuntimeError("No enrichment LLM configured")
        text = llm.generate(
            user_prompt,
            system=TODO_SUGGEST_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=800,
            thinking=False,
        )
        items = _normalize_llm_items(_extract_json_array(text))
        if not items:
            # Soft fallback when chain has content but parse failed
            if ctx.get("nodes"):
                items = [
                    {
                        "suggestion_id": uuid.uuid4().hex,
                        "title": "Follow up",
                        "body": "Review the latest timeline node and capture next actions.",
                    }
                ]
        conn = _open(collection_id)
        try:
            with conn:
                _upsert_state(
                    conn,
                    chain_id,
                    status="ready",
                    suggestions=items,
                    error=None,
                    fingerprint=fingerprint,
                    generated_at=_now_iso(),
                )
        finally:
            conn.close()
    except Exception as e:
        logger.warning(
            "todo suggestion generate failed col=%s chain=%s: %s",
            collection_id,
            chain_id,
            e,
            exc_info=True,
        )
        conn = _open(collection_id)
        try:
            with conn:
                _upsert_state(
                    conn,
                    chain_id,
                    status="error",
                    error=str(e)[:500],
                    keep_suggestions=True,
                    fingerprint=fingerprint,
                )
        finally:
            conn.close()

    return get_todo_suggestions(collection_id, chain_id)


def _timer_fire(collection_id: str, chain_id: str) -> None:
    key = (collection_id, chain_id)
    with _timers_lock:
        _timers.pop(key, None)
    try:
        generate_todo_suggestions_now(collection_id, chain_id)
    except Exception:
        logger.exception(
            "todo suggestion timer failed col=%s chain=%s",
            collection_id,
            chain_id,
        )


def schedule_todo_suggestion_refresh(
    collection_id: str, chain_id: str | None
) -> None:
    """Mark chain pending and reset debounce timer (non-blocking)."""
    if not collection_id or not chain_id:
        return
    try:
        conn = _open(collection_id)
        try:
            ch = conn.execute(
                "SELECT chain_id FROM chains WHERE chain_id=?", (chain_id,)
            ).fetchone()
            if not ch:
                return
            with conn:
                _upsert_state(
                    conn,
                    chain_id,
                    status="pending",
                    keep_suggestions=True,
                )
        finally:
            conn.close()
    except Exception:
        logger.debug(
            "schedule mark pending failed col=%s chain=%s",
            collection_id,
            chain_id,
            exc_info=True,
        )
        return

    key = (collection_id, chain_id)
    with _timers_lock:
        old = _timers.get(key)
        if old is not None:
            try:
                old.cancel()
            except Exception:
                pass
        t = threading.Timer(
            TODO_SUGGEST_DEBOUNCE_SEC,
            _timer_fire,
            args=(collection_id, chain_id),
        )
        t.daemon = True
        _timers[key] = t
        t.start()


def schedule_for_node(collection_id: str, node_id: str | None) -> None:
    """Resolve node's chain and schedule refresh."""
    if not collection_id or not node_id:
        return
    try:
        conn = _open(collection_id)
        try:
            row = conn.execute(
                "SELECT chain_id FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            chain_id = row["chain_id"] if row else None
        finally:
            conn.close()
        if chain_id:
            schedule_todo_suggestion_refresh(collection_id, chain_id)
    except Exception:
        logger.debug(
            "schedule_for_node failed col=%s node=%s",
            collection_id,
            node_id,
            exc_info=True,
        )
