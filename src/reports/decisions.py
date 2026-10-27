"""Persisted, local review decisions with exact source coordinates and discussion lineage."""

from __future__ import annotations

import copy
import uuid
from difflib import SequenceMatcher

from src.reports.blocks import apply_resolved, markdown_lines
from src.reports.locations import discussion_groups, units_for
from src.reports.locations import local_edits as split_location_edits


def identity(report, key):
    return (
        "g_"
        + uuid.uuid5(
            uuid.NAMESPACE_URL, f"{report['session_id']}/{report['id']}/{report['revision']}/{key}"
        ).hex
    )


def diff_edits(parent, body, title):
    edits = []
    for field, after in [("markdown", body), ("title", title)]:
        before = parent[field]
        for tag, a, b, c, d in SequenceMatcher(None, before, after, autojunk=True).get_opcodes():
            if tag != "equal":
                edits.append(
                    {
                        "id": f"edit-{len(edits)}",
                        "operation_id": "rewrite",
                        "field": field,
                        "start": a,
                        "end": b,
                        "before": before[a:b],
                        "after": after[c:d],
                        "comment_ids": [],
                    }
                )
    return edits


def position(value, edits, field="markdown", right=False, *, insertion_right=None):
    """Map an endpoint, with separate affinity for text inserted at that point."""
    shift = 0
    for e in sorted(
        (e for e in edits if e.get("field", "markdown") == field), key=lambda e: e["start"]
    ):
        if e["end"] <= value and e["start"] < value:
            shift += len(e["after"]) - (e["end"] - e["start"])
        elif value < e["start"]:
            break
        elif e["start"] == e["end"] == value and insertion_right is not None:
            return value + shift + (len(e["after"]) if insertion_right else 0)
        elif value == e["start"] and e["start"] < e["end"]:
            return value + shift
        elif e["start"] <= value <= e["end"]:
            return e["start"] + shift + (len(e["after"]) if right else 0)
    return value + shift


def rebase(groups, edits):
    groups = copy.deepcopy(groups)
    for group in groups:
        for patch in [*group["patches"], *([group["focus"]] if group.get("focus") else [])]:
            field = patch.get("field", "markdown")
            start, end = patch["start"], patch["end"]
            # Independent insertions belong outside an existing half-open range.
            # A collapsed deletion anchor follows the insertion as a single point.
            patch["start"], patch["end"] = (
                position(start, edits, field, insertion_right=True),
                position(end, edits, field, True, insertion_right=start == end),
            )
    return groups


def touches(a, b):
    if a.get("field", "markdown") != b.get("field", "markdown"):
        return False
    if a["start"] == a["end"] and b["start"] == b["end"]:
        return a["start"] == b["start"]
    return a["start"] < b["end"] and b["start"] < a["end"]


def evolve(parent, edits, review, result_revision):
    """Compose each new group with overlapping unaccepted ancestors only.

    Coordinates always address the parent source. Rollback text is composed
    inside verified regions, preserving every unrelated edit in the file.
    """
    edits, units = split_location_edits(parent['markdown'], edits)
    old = parent.get("suggestions") or []
    groups = rebase(old, edits)
    if not edits:
        return groups
    comments = (review or {}).get("comments") or []
    outcomes = (review or {}).get("results") or []
    by_comment = {c["id"]: c for c in comments}
    # Connected edits share a comment, a real operation, or a pending ancestor.
    components = [{i} for i in range(len(edits))]
    pending = [g for g in old if g["decision"] == "pending"]
    inherited = []
    for edit in edits:
        cids = edit.get("comment_ids") or [
            r["comment_id"]
            for r in outcomes
            if edit.get("operation_id", edit["id"]) in r.get("edit_ids", [])
        ]
        inherited.append(
            {
                g["id"]
                for g in pending
                if any(touches(edit, p) for p in g["patches"])
                or any(by_comment.get(cid, {}).get("parent_group_id") == g["id"] for cid in cids)
            }
        )

    def linked(i, j):
        a, b = edits[i], edits[j]
        ca = set(
            a.get("comment_ids")
            or [
                r["comment_id"]
                for r in outcomes
                if a.get("operation_id", a["id"]) in r.get("edit_ids", [])
            ]
        )
        cb = set(
            b.get("comment_ids")
            or [
                r["comment_id"]
                for r in outcomes
                if b.get("operation_id", b["id"]) in r.get("edit_ids", [])
            ]
        )
        return bool(
            ca & cb
            or inherited[i] & inherited[j]
            or a.get("operation_id", a["id"]) == b.get("operation_id", b["id"])
        )

    merged = True
    while merged:
        merged = False
        for i, a in enumerate(components):
            found = next(
                (
                    j
                    for j in range(i + 1, len(components))
                    if any(linked(x, y) for x in a for y in components[j])
                ),
                None,
            )
            if found is not None:
                a.update(components.pop(found))
                merged = True
                break
    # Keep instruction membership separate from the local decision boundary.
    locations = []
    for component in components:
        instruction = identity({**parent, 'revision': result_revision},
                               ','.join(sorted({edits[i].get('operation_id', edits[i]['id']) for i in component})))
        parts = [{i} for i in sorted(component)]
        changed = True
        while changed:
            changed = False
            for index, part in enumerate(parts):
                match = next((j for j in range(index + 1, len(parts))
                              if any(inherited[a] & inherited[b] or
                                     units_for(edits[a], units) & units_for(edits[b], units)
                                     for a in part for b in parts[j])), None)
                if match is not None:
                    part.update(parts.pop(match))
                    changed = True
                    break
        locations.extend((part, instruction, len(parts) > 1) for part in parts)
    for component, instruction, multiple in locations:
        chosen = [edits[i] for i in sorted(component)]
        ancestors = {gid for i in component for gid in inherited[i]}
        prior = [g for g in pending if g["id"] in ancestors]
        opids = {e.get("operation_id", e["id"]) for e in chosen}
        results = [r for r in outcomes if opids & set(r.get("edit_ids") or [])]
        selected = [by_comment[r["comment_id"]] for r in results]
        patches = []
        for field in ("markdown", "title"):
            actual = [e for e in chosen if e.get("field", "markdown") == field]
            previous = [
                p for g in prior for p in g["patches"] if p.get("field", "markdown") == field
            ]
            ranges = sorted((e["start"], e["end"]) for e in [*actual, *previous])
            regions = []
            for lo, hi in ranges:
                if regions and lo <= regions[-1][1]:
                    regions[-1] = (regions[-1][0], max(hi, regions[-1][1]))
                else:
                    regions.append((lo, hi))
            for lo, hi in regions:
                before = parent[field][lo:hi]
                after, rollback = before, before
                for e in sorted(
                    (e for e in actual if lo <= e["start"] and e["end"] <= hi),
                    key=lambda e: e["start"],
                    reverse=True,
                ):
                    after = after[: e["start"] - lo] + e["after"] + after[e["end"] - lo :]
                for p in sorted(
                    (p for p in previous if lo <= p["start"] and p["end"] <= hi),
                    key=lambda p: p["start"],
                    reverse=True,
                ):
                    if parent[field][p["start"] : p["end"]] != p["after"]:
                        raise ValueError("Pending revision changed; reload before editing")
                    rollback = (
                        rollback[: p["start"] - lo]
                        + p.get("rollback", p["before"])
                        + rollback[p["end"] - lo :]
                    )
                start = position(lo, edits, field)
                from src.reports.reviews import text_changes

                added, removed = text_changes(before, after)
                opcodes = SequenceMatcher(None, before, after, autojunk=True).get_opcodes()
                only_deletions = all(kind in {"equal", "delete"} for kind, *_ in opcodes)
                display_patches = (
                    [
                        {"start": c, "end": d, "before": before[a:b], "after": after[c:d]}
                        for kind, a, b, c, d in opcodes
                        if kind == "delete"
                    ]
                    if only_deletions
                    else None
                )
                patches.append(
                    {
                        "field": field,
                        "start": start,
                        "end": start + len(after),
                        "before": before,
                        "after": after,
                        "rollback": rollback,
                        "added": added,
                        "removed": removed,
                        **(
                            {"display_patches": display_patches}
                            if display_patches is not None
                            else {}
                        ),
                    }
                )
        key = ','.join(sorted(opids))
        if multiple:
            key += ':location:' + ','.join(str(i) for i in sorted(component))
        gid = identity({**parent, "revision": result_revision}, key)
        threads = list(
            dict.fromkeys(
                [c.get("thread_id", c["id"]) for c in selected]
                + [t for g in prior for t in g["thread_ids"]]
            )
        ) or [gid]
        parent_ids = list(
            dict.fromkeys(
                [c["parent_group_id"] for c in selected if c.get("parent_group_id")]
                + list(ancestors)
            )
        )
        group = {
            "id": gid,
            'instruction_id': prior[0].get('instruction_id', instruction) if len(prior) == 1 else instruction,
            'location_id': prior[0].get('location_id', prior[0]['id']) if len(prior) == 1 else gid,
            "thread_ids": threads,
            "parent_ids": parent_ids,
            "decision": "pending",
            "base_revision": parent["revision"],
            "result_revision": result_revision,
            "comments": [
                {k: copy.deepcopy(v) for k, v in c.items() if k != "discussion_history"}
                for c in selected
            ],
            "results": copy.deepcopy(results),
            "summary": next((r["summary"] for r in results), ""),
            "patches": patches,
            "citations": copy.deepcopy(parent.get("citations") or []),
        }
        anchor_line = next((c["anchor"]["start_line"] for c in selected), 1)
        source_at = sum(
            len(line)
            for line in markdown_lines(parent["markdown"], keepends=True)[: anchor_line - 1]
        )
        selected_ranges = next(
            (
                c["anchor"].get("source_ranges")
                for c in selected
                if c["anchor"].get("source_ranges")
            ),
            [],
        )
        if selected_ranges:
            source_at = selected_ranges[0]["start"]
        target_at = position(source_at, edits)
        local_edits = [e for e in chosen if e.get("field", "markdown") == "markdown"]
        if local_edits:
            focused = min(
                local_edits,
                key=lambda e: (
                    0
                    if e["start"] <= source_at <= e["end"]
                    else min(abs(source_at - e["start"]), abs(source_at - e["end"]))
                ),
            )
            focus_start = position(focused["start"], edits)
            group["focus"] = {
                "field": "markdown",
                "start": focus_start,
                "end": focus_start + len(focused["after"]),
                "before": focused["before"],
                "after": focused["after"],
            }
            comparison = SequenceMatcher(
                None, focused["before"], focused["after"], autojunk=True
            ).get_opcodes()
            if all(kind in {"equal", "delete"} for kind, *_ in comparison):
                for kind, a, b, c, d in comparison:
                    if (
                        kind == "delete"
                        and focused["start"] + a <= source_at < focused["start"] + b
                    ):
                        group["focus"] = {
                            "field": "markdown",
                            "start": focus_start + c,
                            "end": focus_start + d,
                            "before": focused["before"][a:b],
                            "after": "",
                        }
                        break
        body_patches = [(i, p) for i, p in enumerate(patches) if p["field"] == "markdown"]
        group["primary_patch"] = (
            min(
                body_patches,
                key=lambda item: (
                    0
                    if item[1]["start"] <= target_at <= item[1]["end"]
                    else min(abs(target_at - item[1]["start"]), abs(target_at - item[1]["end"]))
                ),
            )[0]
            if body_patches
            else 0
        )
        if multiple and local_edits:
            edited_at = local_edits[0]['start']
            unit = next(((a, b) for a, b in units if a <= edited_at < b), None)
            if unit:
                a, b = unit
                group['location_quote'] = parent['markdown'][a:b].strip()[:8000]
                if any(e['after'].strip() for e in local_edits):
                    updated_body = apply_resolved(parent['markdown'], edits)
                    start = position(a, edits)
                    end = position(b, edits, right=True, insertion_right=False)
                    group['focus'] = {'field': 'markdown', 'start': start, 'end': end,
                                      'before': parent['markdown'][a:b], 'after': updated_body[start:end]}
        for g in groups:
            if g["id"] in ancestors:
                g["decision"] = "superseded"
        groups.append(group)
    return groups


def project(store, sid, rid, record):
    """Read-only compatibility for reports created before review decisions."""
    if "suggestions" not in record:
        chain, current = [], record
        while "suggestions" not in current and current["revision"] > 1:
            try:
                parent = store._raw_read(sid, rid, current["revision"] - 1)
            except FileNotFoundError:
                break
            chain.append((parent, current))
            current = parent
        groups = copy.deepcopy(current.get("suggestions") or [])
        for parent, step in reversed(chain):
            edits = step.get("edit_log") or step.get("review", {}).get("edits")
            if not edits or any("start" not in e for e in edits):
                edits = diff_edits(parent, step["markdown"], step["title"])
            if apply_resolved(parent["markdown"], edits) != step["markdown"]:
                raise ValueError("Revision suggestion source does not match its saved edits")
            groups = evolve(
                {**parent, "suggestions": groups}, edits, step.get("review"), step["revision"]
            )
        record["suggestions"] = groups
    try:
        state = store._json(store._dir(sid, rid) / "reviews.json")
    except FileNotFoundError:
        return record
    known = {c["id"] for g in record["suggestions"] for c in g["comments"]}
    active = store.active_revisions(sid, rid, record['revision'])
    for batch in state["batches"].values():
        if batch["status"] != "unchanged" or batch["revision"] not in active:
            continue
        for c in batch["comments"]:
            if c["id"] in known:
                continue
            result = next(
                (r for r in batch.get("results") or [] if r["comment_id"] == c["id"]), None
            )
            if result:
                parent_group = next((g for g in record['suggestions'] if g['id'] == c.get('parent_group_id')), {})
                record["suggestions"].append(
                    {
                        "id": identity(
                            {**record, "revision": batch["revision"]}, "answer:" + c["id"]
                        ),
                        "thread_ids": [c.get("thread_id", c["id"])],
                        "parent_ids": [c["parent_group_id"]] if c.get("parent_group_id") else [],
                        **({k: parent_group[k] for k in ('location_id', 'instruction_id') if k in parent_group}),
                        "decision": "answered",
                        "base_revision": batch["revision"],
                        "result_revision": batch["revision"],
                        "comments": [copy.deepcopy(c)],
                        "results": [copy.deepcopy(result)],
                        "summary": result["summary"],
                        "patches": [],
                        "citations": [],
                    }
                )
                known.add(c["id"])
    record["suggestions"].sort(key=lambda g: g["result_revision"])
    return record


class DecisionsMixin:
    def decide_suggestion(self, sid, rid, gid, decision, *, expected_revision, operation_id, scope='location'):
        with self._lock:
            key = "decision:" + operation_id
            committed = self.saved_operation(sid, key, rid)
            if committed:
                g = next((g for g in committed["suggestions"] if g["id"] == gid), None)
                event = committed.get('decision_event') or {}
                if not g or event.get('group_id') != gid or event.get('scope', 'location') != scope or event.get('decision') != decision:
                    raise ValueError("Decision operation changed")
                return committed
            report = self.read(sid, rid)
            if report["revision"] != expected_revision:
                raise ValueError("Report version conflict; reload before deciding")
            if any(
                b["status"] == "running" for b in self._review_state(sid, rid)["batches"].values()
            ):
                raise ValueError("A report review is running; finish it before deciding")
            group = next((g for g in report["suggestions"] if g["id"] == gid), None)
            if not group:
                raise FileNotFoundError("Revision suggestion not found")
            if scope not in {'location', 'instruction'}:
                raise ValueError('Unknown revision decision scope')
            selected = [g for g in report['suggestions'] if g['decision'] == 'pending' and
                        (g['id'] == gid or scope == 'instruction' and group.get('instruction_id') and
                         g.get('instruction_id') == group['instruction_id'])]
            if decision not in {"accepted", "rejected"} or not selected:
                raise ValueError("This revision is no longer awaiting a decision")
            edits = []
            if decision == "rejected":
                for p in [p for g in selected for p in g['patches']]:
                    field = p.get("field", "markdown")
                    if report[field][p["start"] : p["end"]] != p["after"]:
                        raise ValueError("Revision content changed; reload before deciding")
                    edits.append(
                        {
                            "id": f"reject-{len(edits)}",
                            "field": field,
                            "start": p["start"],
                            "end": p["end"],
                            "before": p["after"],
                            "after": p.get("rollback", p["before"]),
                        }
                    )
            groups = rebase(report["suggestions"], edits)
            selected_ids = {g['id'] for g in selected}
            for g in groups:
                if g['id'] in selected_ids:
                    g['decision'] = decision
            citations = {e["id"]: e for e in [*report["citations"], *(e for g in selected for e in g['citations'])]}
            return self.save(
                sid,
                apply_resolved(report["title"], edits, field="title"),
                apply_resolved(report["markdown"], edits),
                list(citations.values()),
                report_id=rid,
                expected_revision=expected_revision,
                operation_id=key,
                block_edits=edits,
                suggestions=groups,
                decision_event={"group_id": gid, "decision": decision, 'scope': scope,
                                'group_ids': [g['id'] for g in selected]},
                historical_replay=True,
            )

    def follow_up_context(self, sid, rid, gid, *, expected_revision):
        with self._lock:
            report = self.read(sid, rid)
            if report["revision"] != expected_revision:
                raise ValueError("Report version conflict; reload before continuing")
            group = next((g for g in report["suggestions"] if g["id"] == gid), None)
            if group is None:
                raise FileNotFoundError("Revision suggestion not found")
            history = discussion_groups(report['suggestions'], group)
            current = history[-1]
            latest_edit = next(
                (
                    g
                    for g in reversed(history)
                    if any(p.get("field", "markdown") == "markdown" for p in g["patches"])
                ),
                None,
            )
            patches = latest_edit["patches"] if latest_edit else []
            target = (
                latest_edit.get("focus") or patches[latest_edit.get("primary_patch", 0)]
                if latest_edit
                else None
            )
            blocks = report["blocks"]
            line = next((c["anchor"].get("start_line", 1) for c in current["comments"]), 1)
            at = (
                target["start"]
                if target
                else next(
                    (b["start"] for b in blocks if b["start_line"] <= line <= b["end_line"]), 0
                )
            )
            block = (
                next((b for b in blocks if b["start"] <= at < b["end"]), None)
                or next((b for b in blocks if b["start"] >= at), None)
                or blocks[-1]
            )
            fragment = "\n".join(
                markdown_lines(report["markdown"])[block["start_line"] - 1 : block["end_line"]]
            )
            deleted = bool(target and target["start"] == target["end"])
            return {
                "revision": report["revision"],
                "thread_id": next(iter(group["thread_ids"])),
                "parent_group_id": current["id"],
                "anchor": {
                    "kind": "block",
                    "start_line": block["start_line"],
                    "end_line": block["end_line"],
                    "quote": fragment[:8000],
                },
                "current_text": ""
                if deleted
                else report["markdown"][target["start"] : target["end"]]
                if target
                else fragment,
                "deleted": deleted,
                "discussion_history": copy.deepcopy(history),
            }

    def follow_up(self, sid, rid, gid, *, text, author, expected_revision, operation_id):
        with self._lock:
            state = self._review_state(sid, rid)
            existing = next(
                (c for c in state["comments"] if c.get("followup_operation") == operation_id and not c.get('archived')), None
            )
            if existing:
                if existing.get("followup_from") != gid or existing["text"] != text.strip():
                    raise ValueError("Follow-up operation changed")
                return copy.deepcopy(existing)
            context = self.follow_up_context(sid, rid, gid, expected_revision=expected_revision)
            from src.reports.blocks import anchor_blocks
            from src.reports.reviews import now, validate_anchor

            report = self.read(sid, rid)
            text = str(text).strip()
            if not text or len(text) > 4000:
                raise ValueError("Comment must contain 1–4000 characters")
            if len(state["comments"]) >= 1000:
                raise ValueError("This report already has 1000 comments")
            anchor = {
                **validate_anchor(report["markdown"], context["anchor"]),
                **anchor_blocks(report, context["anchor"]),
            }
            c = {
                "id": uuid.uuid4().hex,
                "session_id": sid,
                "report_id": rid,
                "title": report["title"],
                "revision": expected_revision,
                "created_revision": expected_revision,
                "text": text,
                "anchor": anchor,
                "author": author,
                "state": "pending",
                "created_at": now(),
                "updated_at": now(),
            }
            c.update({k: v for k, v in context.items() if k not in {"anchor", "revision"}})
            c.update(followup_operation=operation_id, followup_from=gid)
            state["comments"].append(c)
            self._write_reviews(sid, rid, state)
            return copy.deepcopy(c)
