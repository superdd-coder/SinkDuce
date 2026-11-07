"""Bounded, cached AI stages. Original cells and validated relationships are authoritative."""

import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from src.prompts import QA_IDENTITY_SYSTEM, QA_STRUCTURE_SYSTEM

from . import imports, metadata, store
from .workbook_reader import classify_rows


class StructureRow(BaseModel):
    key: str
    kind: Literal["question", "child", "section", "note", "header", "continuation", "review"]
    parent_key: str = ""
    needs_evidence: bool = False


class StructureResult(BaseModel):
    rows: list[StructureRow]


class IdentityRow(BaseModel):
    key: str
    target: str
    certainty: Literal["high", "review"]
    reason: str = Field(default="", max_length=1000)
    needs_evidence: bool = False


class IdentityResult(BaseModel):
    rows: list[IdentityRow]


class ColumnResult(BaseModel):
    columns: list[IdentityRow]


def _unique_assignments(assignments, manual):
    result = dict(assignments)
    counts = Counter(value for value in result.values() if value and value != "new")
    for key, target in assignments.items():
        if counts[target] > 1 and manual.get(key) != target:
            result[key] = None
    return result


def batches(rows, budget=24000, limit=24):
    batch, size, group = [], 0, None
    for row in rows:
        cost = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
        next_group = row.get("group_key", group)
        if batch and (size + cost > budget or len(batch) >= limit or next_group != group):
            yield batch
            batch, size = [], 0
        batch.append(row)
        size += cost
        group = next_group
    if batch:
        yield batch


def cached_calls(jobs, cache, saved=None):
    """At most two provider calls at once; persist validated successful batches."""
    from .intelligence import generate_json

    def execute(job):
        prompt, packet, schema = job
        digest = hashlib.sha256(
            (prompt + json.dumps(packet, sort_keys=True, ensure_ascii=False)).encode()
        ).hexdigest()
        if digest in cache:
            return digest, schema.model_validate(cache[digest]), None
        try:
            if len(json.dumps(packet, ensure_ascii=False).encode("utf-8")) > 100000:
                raise ValueError("ai_input_limit")
            output = generate_json(prompt, packet, schema)
            field = "columns" if packet.get("stage") == "columns" else "rows"
            expected = {
                r["key"] for r in packet.get("incoming" if field == "columns" else "rows", [])
            }
            returned = [r.key for r in getattr(output, field)]
            if len(set(returned)) != len(returned) or set(returned) != expected:
                raise ValueError("incomplete_analysis")
            return digest, output, None
        except Exception:  # noqa: BLE001 — provider failures leave a resumable manual path
            return digest, None, "analysis_unavailable"

    results = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {pool.submit(execute, job): i for i, job in enumerate(jobs)}
        for future in as_completed(pending):
            key, value, error = future.result()
            results[pending[future]] = value
            if not error:
                cache[key] = value.model_dump()
                if saved:
                    saved(cache)
    return results


def recognize_structure(parsed, mapping, cache=None, manual=None, manual_parents=None, saved=None):
    cache = cache if cache is not None else {}
    manual, manual_parents = manual or {}, manual_parents or {}
    original = [
        {key: row.get(key) for key in ("key", "values", "bold", "merges", "indents")}
        for row in parsed["rows"]
    ]
    signature = hashlib.sha256(
        json.dumps([3, original, mapping, manual, manual_parents], sort_keys=True).encode()
    ).hexdigest()
    if parsed.get("structure_status") == "ready" and parsed.get("structure_signature") == signature:
        return parsed
    initial, _ = classify_rows(
        parsed, mapping, parsed.get("row_actions", {}), parsed.get("parents", {})
    )
    source = {r["key"]: r for r in parsed["rows"]}
    safe = []
    for row in initial:
        # Reply values are fetched only for explicit evidence requests.
        safe.append(
            {
                "key": row["key"],
                "values": {
                    str(i): v for i, v in enumerate(row["values"]) if i not in mapping["replies"]
                },
                "reply_populated": [bool(row["values"][i].strip()) for i in mapping["replies"]],
                "bold": row.get("bold", []),
                "merges": row.get("merges", []),
                "indents": row.get("indents", {}),
                "group_key": row.get("group_key", ""),
                "proposed_kind": row["kind"],
            }
        )
    jobs = []
    for batch in batches(safe):
        first = next(i for i, row in enumerate(safe) if row["key"] == batch[0]["key"])
        context = (
            safe[max(0, first - 2) : first] + safe[first + len(batch) : first + len(batch) + 2]
        )
        groups = [r for r in safe[:first] if r["proposed_kind"] == "section"][-1:]
        parents = [r for r in safe[:first] if r["proposed_kind"] in ("question", "child")][-4:]
        jobs.append(
            (
                QA_STRUCTURE_SYSTEM,
                {
                    "stage": "structure",
                    "headers": parsed["headers"],
                    "mapping": mapping,
                    "rows": batch,
                    "context": context + groups + parents,
                },
                StructureResult,
            )
        )
    actions = dict(parsed.get("row_actions", {}))
    parents = dict(parsed.get("parents", {}))
    failed, evidence_jobs = False, []
    results = cached_calls(jobs, cache, saved)
    for job, result in zip(jobs, results):
        targets = {r["key"] for r in job[1]["rows"]}
        if result is None:
            failed = True
            continue
        counts = Counter(r.key for r in result.rows)
        for key in targets:
            row = next((r for r in result.rows if r.key == key), None)
            if row is None or counts[key] != 1:
                actions[key] = "review"
                failed = True
                continue
            actions[key], parents[key] = row.kind, row.parent_key
            if row.needs_evidence:
                evidence_jobs.append(
                    (
                        QA_STRUCTURE_SYSTEM,
                        {
                            **job[1],
                            "stage": "structure_evidence",
                            "rows": [next(r for r in job[1]["rows"] if r["key"] == key)],
                            "reply_evidence": {
                                key: [source[key]["values"][i] for i in mapping["replies"]]
                            },
                        },
                        StructureResult,
                    )
                )
    for job, result in zip(evidence_jobs, cached_calls(evidence_jobs, cache, saved)):
        key = job[1]["rows"][0]["key"]
        row = next((r for r in result.rows if r.key == key), None) if result else None
        if row is None or row.needs_evidence:
            actions[key] = "review"
        else:
            actions[key], parents[key] = row.kind, row.parent_key
    # Evidence can clarify structure, but loss of question/reply content still needs a human choice.
    for key, kind in list(actions.items()):
        source_row = source[key]
        if (
            (
                kind in ("section", "note", "header")
                or kind == "continuation" and source_row["values"][mapping["question"]].strip()
            )
            and any(source_row["values"][i].strip() for i in mapping["replies"])
        ):
            actions[key], parents[key] = "review", ""
    actions.update(manual)
    parents.update(manual_parents)
    # Providers sometimes put the enclosing section in parent_key. That is group
    # membership, already derived from source order, not a child-question edge.
    group_key = ""
    for source_row in parsed["rows"]:
        key = source_row["key"]
        if actions.get(key) == "section":
            group_key = key
        elif (
            group_key
            and actions.get(key) == "question"
            and parents.get(key) == group_key
            and key not in manual_parents
        ):
            parents[key] = ""
    # Reject unknown/cyclic parents locally, keeping original rows available for review.
    for key, parent in list(parents.items()):
        seen, current = {key}, parent
        while current:
            if (
                current not in source
                or current in seen
                or actions.get(current, "question") not in ("question", "child")
            ):
                actions[key], parents[key] = "review", ""
                break
            seen.add(current)
            current = parents.get(current, "")
    candidate = {**parsed, "row_actions": actions, "parents": parents}
    try:
        classify_rows(candidate, mapping, actions, parents)
    except HTTPException as exc:
        if exc.detail.get("code") != "invalid_parent":
            raise
        for key, parent in list(parents.items()):
            if parent and key not in manual_parents:
                actions[key], parents[key] = "review", ""
        classify_rows(candidate, mapping, actions, parents)
    candidate.update(
        pipeline_cache=cache,
        structure_status="partial" if failed else "ready",
        structure_signature=signature,
    )
    return candidate


def analyze(collection, lid, iid, revision):
    with store.connection(collection, "update") as conn:
        plan = imports.get_plan(conn, lid, iid)
        listing = store.get_list(conn, lid)
        if (
            plan.get("cancelled")
            or plan["committed_version_id"]
            or plan["plan_revision"] != revision
            or listing["revision"] != plan["base_revision"]
        ):
            store.fail("stale_import")
        original_matches = dict(plan["matches"])
        original_columns = dict(plan["column_matches"])
        histories = {}
        for row in conn.execute(
            """SELECT t.question_id,t.body FROM qa_text_revisions t JOIN qa_questions q ON q.id=t.question_id
            WHERE q.list_id=? AND t.column_id IS NULL AND NOT EXISTS(SELECT 1 FROM qa_revoked_associations a WHERE a.text_revision_id=t.id)""",
            (lid,),
        ):
            histories.setdefault(row[0], set()).add(row[1])
        meta = (
            metadata.read(conn, listing["current_version_id"])
            if listing["current_version_id"]
            else metadata.defaults()
        )
        old_structure = {i["id"]: i for i in meta.get("outline", [])}
        history = [
            {
                "id": qid,
                "texts": sorted(texts),
                "group": meta["groups"].get(qid, ""),
                "parent_id": old_structure.get(qid, {}).get("parent_id", ""),
            }
            for qid, texts in histories.items()
        ]
        columns = [
            dict(r)
            for r in conn.execute(
                "SELECT column_id AS id,label FROM qa_version_columns WHERE version_id=?",
                (listing["current_version_id"],),
            )
        ]
        samples = {
            (r[0], r[1]): r[2]
            for r in conn.execute(
                """SELECT c.question_id,c.column_id,t.body FROM qa_version_cells c
            JOIN qa_text_revisions t ON t.id=c.text_revision_id WHERE c.version_id=?""",
                (listing["current_version_id"],),
            )
        }

    cache = plan.get("pipeline_cache", {})

    def checkpoint(updated):
        with store.connection(collection, "update") as conn, conn:
            current = imports.get_plan(conn, lid, iid)
            if (
                current.get("cancelled")
                or current["committed_version_id"]
                or current["plan_revision"] != revision
            ):
                store.fail("stale_import")
            current["pipeline_cache"] = updated
            conn.execute("UPDATE qa_imports SET plan_json=? WHERE id=?", (json.dumps(current), iid))

    parsed = {**plan, "rows": plan.get("table_rows", plan["rows"])}
    parsed = recognize_structure(
        parsed,
        plan["mapping"],
        cache,
        plan.get("manual_row_actions"),
        plan.get("manual_parents"),
        checkpoint,
    )
    with store.connection(collection) as conn:
        rebuilt = imports.build_plan(
            conn, lid, parsed, plan["mapping"], validate=plan["mapping_valid"]
        )
    for key in ("matches", "column_matches"):
        for source_key, target in plan.get("manual_" + key, {}).items():
            if source_key in rebuilt[key]:
                rebuilt[key][source_key] = target
    plan.update(rebuilt)
    plan["pipeline_cache"] = cache
    pending = [r for r in plan["rows"] if plan["matches"].get(r["key"]) is None]
    safe_rows = [
        {
            "key": r["key"],
            "text": r["text"],
            "group": r.get("group", ""),
            "group_key": r.get("group_key", ""),
            "parent_key": r.get("parent_key", ""),
        }
        for r in pending
    ]
    jobs, narrowed = [], set()
    for batch in batches(safe_rows, limit=16):
        candidates = history
        if len(json.dumps(history).encode()) > 45000:
            candidates = sorted(
                history,
                key=lambda h: max(
                    SequenceMatcher(None, r["text"], old).ratio()
                    for r in batch
                    for old in h["texts"]
                ),
                reverse=True,
            )[:16]
            narrowed.update(r["key"] for r in batch)
        jobs.append(
            (
                QA_IDENTITY_SYSTEM,
                {
                    "stage": "identity",
                    "rows": batch,
                    "history": candidates,
                    "complete_history": candidates is history,
                    "existing_matches": plan["matches"],
                },
                IdentityResult,
            )
        )
    results = cached_calls(jobs, cache, checkpoint)
    suggestions, evidence_jobs = {}, []
    for job, result in zip(jobs, results):
        if not result:
            continue
        allowed = {r["key"] for r in job[1]["rows"]}
        counts = Counter(r.key for r in result.rows)
        for row in result.rows:
            if (
                row.key not in allowed
                or counts[row.key] != 1
                or row.target not in histories.keys() | {"new"}
            ):
                continue
            suggestions[row.key] = row.model_dump()
            if row.needs_evidence:
                incoming = next(r for r in pending if r["key"] == row.key)
                evidence = {
                    "incoming": [incoming["values"][i] for i in plan["mapping"]["replies"]],
                    "historical": {
                        h["id"]: [v for (qid, _), v in samples.items() if qid == h["id"] and v][:3]
                        for h in job[1]["history"]
                    },
                }
                evidence_jobs.append(
                    (
                        QA_IDENTITY_SYSTEM,
                        {
                            **job[1],
                            "stage": "identity_evidence",
                            "rows": [next(r for r in job[1]["rows"] if r["key"] == row.key)],
                            "reply_evidence": evidence,
                        },
                        IdentityResult,
                    )
                )
    for job, result in zip(evidence_jobs, cached_calls(evidence_jobs, cache, checkpoint)):
        key = job[1]["rows"][0]["key"]
        row = (
            next(
                (r for r in result.rows if r.key == key and r.target in histories.keys() | {"new"}),
                None,
            )
            if result
            else None
        )
        if row:
            suggestions[key] = row.model_dump()
    for key, result in suggestions.items():
        if key in narrowed or result.get("needs_evidence"):
            result["certainty"] = "review"
        if result["certainty"] != "high":
            continue
        text = next(r["text"] for r in pending if r["key"] == key)
        scores = {
            qid: max(SequenceMatcher(None, text, old).ratio() for old in texts)
            for qid, texts in histories.items()
        }
        target = result["target"]
        supported = (
            max(scores.values(), default=0) < 0.45
            if target == "new"
            else scores[target] >= 0.6
            and scores[target] - max((s for q, s in scores.items() if q != target), default=0)
            >= 0.12
        )
        if supported:
            plan["matches"][key] = target
    plan["matches"] = _unique_assignments(plan["matches"], plan.get("manual_matches", {}))
    plan["suggestions"] = suggestions
    with store.connection(collection) as conn:
        imports.resolve_empty_rounds(conn, lid, plan)
    if history and all(plan["matches"].values()):
        from .column_alignment import align

        align(plan, meta, columns, samples, cache, checkpoint)
    unique_columns = _unique_assignments(plan["column_matches"], plan.get("manual_column_matches", {}))
    # Independent new rounds preserve old correspondence when a reuse cannot be proven.
    plan["column_matches"] = {key: value or "new" for key, value in unique_columns.items()}
    if plan["matches"] != original_matches or plan["column_matches"] != original_columns:
        plan.pop("content_choices", None)
    matched = {target for target in plan["matches"].values() if target and target != "new"}
    for field in ("retained_questions", "removed_questions"):
        if field in plan:
            plan[field] = [qid for qid in plan[field] if qid not in matched]
    if "blank_columns" in plan:
        active_columns = {str(index) for index in plan["mapping"]["replies"]}
        plan["blank_columns"] = [key for key in plan["blank_columns"] if key in active_columns]
    complete = plan.get("structure_status") == "ready" and all(r is not None for r in results)
    plan["ai_status"] = "ready" if complete else "partial"
    plan["analysis_complete"] = (
        complete and all(plan["matches"].values()) and all(plan["column_matches"].values())
    )
    with store.connection(collection, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        current = imports.get_plan(conn, lid, iid)
        if (
            current.get("cancelled")
            or current["committed_version_id"]
            or current["plan_revision"] != revision
            or store.get_list(conn, lid)["revision"] != plan["base_revision"]
        ):
            store.fail("stale_import")
        imports.review_plan(conn, lid, plan)
        plan["plan_revision"] += 1
        conn.execute(
            "UPDATE qa_imports SET plan_json=? WHERE id=?",
            (json.dumps(plan, ensure_ascii=False), iid),
        )
    return plan


def summarize(collection, lid, revision):
    from .enrichment import summarize as run

    return run(collection, lid, revision)
