"""Review completion is derived from committed outcomes, never model prose."""

from __future__ import annotations

import json
import re


def review_execution(store, sid, batches):
    counts = {
        key: 0 for key in ("applied", "partial", "unchanged", "needs_clarification", "unsaved")
    }
    tasks = []
    for ref in batches:
        batch = store.review_batch(sid, ref["report_id"], ref["id"])
        terminal = batch["status"] in {"completed", "unchanged"}
        if terminal:
            for result in batch.get("results") or []:
                counts[result["status"]] += 1
        else:
            counts["unsaved"] += len(batch["comments"])
        tasks.append(
            {
                "report_id": batch["report_id"],
                "batch_id": batch["id"],
                "base_revision": batch["revision"],
                "status": batch["status"],
                "result_revision": batch.get("result_revision"),
                "comment_ids": [c["id"] for c in batch["comments"]],
                "results": batch.get("results") or [],
            }
        )
    return {**counts, "tasks": tasks}


def completion_text(execution, batches, request):
    chinese = bool(
        re.search(
            r"[\u3400-\u9fff]", request + "".join(c["text"] for b in batches for c in b["comments"])
        )
    )
    labels = (
        {
            "applied": "待审修改",
            "partial": "部分完成",
            "unchanged": "已答复",
            "needs_clarification": "待补充信息",
        }
        if chinese
        else {
            "applied": "proposed revisions",
            "partial": "partially addressed",
            "unchanged": "answered",
            "needs_clarification": "need information",
        }
    )
    parts = [
        f"{execution[key]} 条{label}" if chinese else f"{execution[key]} {label}"
        for key, label in labels.items()
        if execution[key]
    ]
    if execution["unsaved"]:
        n = execution["unsaved"]
        parts.append(f"{n} 条未保存，已保留待重试" if chinese else f"{n} not saved; kept for retry")
    return ("；" if chinese else "; ").join(parts) + ("。" if chinese else ".")


def latest_review_failures(messages):
    """Latest rejected proposal per report; successful saves clear the entry."""
    calls, failures = {}, {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if function.get("name") != "revise_report":
                continue
            try:
                args = json.loads(function.get("arguments") or "{}")
            except (ValueError, TypeError):
                continue
            if isinstance(args, dict) and args.get("report_id"):
                calls[call["id"]] = args["report_id"]
        rid = calls.get(message.get("tool_call_id"))
        if message.get("role") != "tool" or not rid:
            continue
        try:
            result = json.loads(message.get("content") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(result, dict):
            continue
        if result.get("error"):
            failures[rid] = {
                "report_id": rid,
                **{
                    k: result[k]
                    for k in ("error", "saved", "review_issues", "edit_errors", "base_revision")
                    if k in result
                },
            }
        elif result.get("report"):
            failures.pop(rid, None)
    return list(failures.values())


def focus_review_retry(messages):
    """Repair from this turn's exact inputs, rather than imitate rejected plans.

    Keep the latest report read and save attempt per file alongside all current
    evidence tools. This only projects model context; the stored audit trail is
    unchanged. The initial system context carries the frozen review request.
    """
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    if last_user < 0:
        return messages
    current = messages[last_user + 1 :]
    latest, report_calls = {}, {}
    for message in current:
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            name = function.get("name")
            if name not in {"get_report", "revise_report"}:
                continue
            try:
                args = json.loads(function.get("arguments") or "{}")
            except (ValueError, TypeError):
                continue
            if isinstance(args, dict) and args.get("report_id"):
                key = (name, args["report_id"])
                latest[key] = call["id"]
                report_calls[call["id"]] = key
    removed = {tid for tid, key in report_calls.items() if latest[key] != tid}
    out = [m for m in messages[:last_user] if m.get("role") == "system"] + [messages[last_user]]
    for message in current:
        if message.get("role") == "tool" and message.get("tool_call_id") in removed:
            continue
        if message.get("tool_calls"):
            kept = [c for c in message["tool_calls"] if c.get("id") not in removed]
            if kept:
                out.append({**message, "tool_calls": kept})
        else:
            out.append(message)
    return out


def project_report_history(messages):
    """Keep historical execution facts; old task handles are not reusable tools.

    The stored transcript remains intact. Current-turn arguments and results
    remain exact, including failures the model still needs to correct.
    """
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    if last_user < 0:
        return messages
    names = {"create_report", "get_report", "update_report", "revise_report"}
    calls = {
        call["id"]: call["function"]["name"]
        for m in messages[:last_user]
        for call in m.get("tool_calls") or []
        if isinstance(call, dict)
        and isinstance(call.get("function"), dict)
        and call["function"].get("name") in names
        and call.get("id")
    }
    out, facts = [], []
    for i, message in enumerate(messages):
        if i >= last_user:
            out.append(message)
            continue
        role = message.get("role")
        if role == "user":
            facts = []
        if role == "assistant" and message.get("tool_calls"):
            remaining = [
                c
                for c in message["tool_calls"]
                if not isinstance(c, dict) or c.get("id") not in calls
            ]
            if remaining:
                out.append({**message, "tool_calls": remaining})
            elif message.get("content"):
                out.append({k: v for k, v in message.items() if k != "tool_calls"})
            continue
        if role == "tool" and message.get("tool_call_id") in calls:
            try:
                payload = json.loads(message.get("content") or "{}")
            except (ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            name = calls[message["tool_call_id"]]
            if name != "get_report":
                fact = {
                    "operation": name,
                    "saved": isinstance(payload.get("report"), dict) and not payload.get("error"),
                }
                if payload.get("error"):
                    fact["error"] = str(payload["error"])[:300]
                if fact["saved"]:
                    fact["report"] = payload["report"]
                    fact["outcomes"] = (payload.get("review_result") or {}).get("results") or []
                facts.append(fact)
            continue
        if role == "assistant" and facts:
            out.append(
                {
                    **message,
                    "content": json.dumps(
                        {"historical_report_operations": facts}, ensure_ascii=False
                    ),
                }
            )
        else:
            out.append(message)
    return out
