"""Editorial coverage check outside the storage lock, before any commit."""

from __future__ import annotations

import json
import re


class ReviewCoverageError(ValueError):
    def __init__(self, issues):
        super().__init__(
            "Report review coverage is incomplete; finish the requested edits before saving"
        )
        self.review_issues = issues


def review_audit_key(report, batch, proposed, results):
    """Applied reply wording does not change whether the document fulfills a task."""
    from src.reports.blocks import content_hash
    outcomes = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        outcome = {k: result.get(k) for k in ('comment_id', 'status')}
        if result.get('status') != 'applied':
            outcome['summary'] = result.get('summary')
        outcomes.append(outcome)
    return content_hash(json.dumps([report['revision'], batch['id'], proposed,
        sorted(outcomes, key=lambda r: str(r.get('comment_id')))], ensure_ascii=False, sort_keys=True))


def _compact_comments(batch):
    comments = []
    for comment in batch['comments']:
        item = {k: comment[k] for k in ('id', 'text', 'anchor', 'current_text', 'deleted') if k in comment}
        item['discussion_context'] = [{
            'requests': [c['text'] for c in round.get('comments') or [] if c.get('text')],
            'decision': round.get('decision'), 'reply': round.get('summary', ''),
        } for round in comment.get('discussion_history') or []]
        comments.append(item)
    return comments


def _proposal_changes(report, proposed):
    from src.reports.reviews import compute_changes
    return [{k: change[k] for k in ('kind', 'before', 'after', 'old_start', 'new_start')}
            for change in compute_changes(report['markdown'], proposed['markdown'])]


def repair_review(llm, report, batch, edits, results, issues):
    """Produce a corrected proposal in a focused context; never write it here."""
    from src.prompts import (
        CHAT_REPORT_EDIT_SCHEMA,
        CHAT_REPORT_REVIEW_REPAIR_REQUEST,
        CHAT_REPORT_REVIEW_REPAIR_SYSTEM,
    )

    payload = {
        "original": {k: report[k] for k in ("title", "markdown")},
        "blocks": [
            {**b, "text": report["markdown"][b["start"] : b["end"]]} for b in report["blocks"]
        ],
        "comments": _compact_comments(batch),
        "instruction": batch["instruction"],
        "rejected_proposal": {"edits": edits, "results": results},
        "issues": issues,
        "edit_schema": CHAT_REPORT_EDIT_SCHEMA,
    }
    try:
        response = llm.generate(
            CHAT_REPORT_REVIEW_REPAIR_REQUEST.format(
                payload=json.dumps(payload, ensure_ascii=False)
            ),
            system=CHAT_REPORT_REVIEW_REPAIR_SYSTEM,
        )
        text = re.sub(r"\A\s*```(?:json)?\s*|\s*```\s*\Z", "", response.strip())
        proposed = json.loads(text)
        if (
            not isinstance(proposed, dict)
            or not isinstance(proposed.get("edits"), list)
            or not isinstance(proposed.get("results"), list)
        ):
            raise TypeError("Invalid repair proposal")
        return {k: proposed[k] for k in ("edits", "results")}
    except Exception as exc:
        raise ValueError(
            "Report review repair unavailable; the proposal has not been saved"
        ) from exc


def audit_review(llm, report, batch, proposed, results):
    from src.prompts import CHAT_REPORT_REVIEW_AUDIT_REQUEST, CHAT_REPORT_REVIEW_AUDIT_SYSTEM, CHAT_REPORT_REVIEW_CONFIRM_SYSTEM

    comments = _compact_comments(batch)
    changes = _proposal_changes(report, proposed)
    payload = {
        "before": {k: report[k] for k in ("title", "markdown")},
        "comments": comments,
        "instruction": batch["instruction"],
        "outcomes": results,
        "changes": changes,
        "proposed": proposed,
    }
    def decide(context, system):
        response = llm.generate(
            CHAT_REPORT_REVIEW_AUDIT_REQUEST.format(
                payload=json.dumps(context, ensure_ascii=False)
            ),
            system=system,
        )
        text = re.sub(r"\A\s*```(?:json)?\s*|\s*```\s*\Z", "", response.strip())
        decision = json.loads(text)
        if (
            not isinstance(decision, dict)
            or type(decision.get("ok")) is not bool
            or not isinstance(decision.get("issues"), list)
        ):
            raise ValueError("Invalid decision")
        known = {c["id"] for c in batch["comments"]}
        issues = decision["issues"]
        if any(
            not isinstance(i, dict)
            or i.get("comment_id") not in known
            or not isinstance(i.get("required_action"), str)
            or not i["required_action"].strip()
            for i in issues
        ):
            raise ValueError("Invalid coverage feedback")
        if decision['ok'] and not issues:
            return []
        if not issues:
            raise ValueError("Missing correction feedback")
        return issues

    try:
        issues = decide(payload, CHAT_REPORT_REVIEW_AUDIT_SYSTEM)
        if not issues:
            return []
        # A negative editorial verdict is checked once against the candidate alone.
        # Old drafts, citations and prior Agent explanations cannot prove a current gap.
        confirmation = {
            'comments': [{k: c[k] for k in ('id', 'text', 'anchor', 'current_text', 'deleted') if k in c} for c in comments],
            'instruction': batch['instruction'], 'outcomes': results,
            'current_changed_passages': [{'text': c['after'], 'new_start': c['new_start']} for c in changes],
            'issues_to_verify': issues, 'proposed': proposed,
        }
        confirmed = decide(confirmation, CHAT_REPORT_REVIEW_CONFIRM_SYSTEM)
        candidate = ' '.join((proposed['title'] + '\n' + proposed['markdown']).split())
        for issue in confirmed:
            excerpt = issue.get('proposed_excerpt')
            if not isinstance(excerpt, str) or not excerpt.strip() or ' '.join(excerpt.split()) not in candidate:
                raise ValueError('Coverage feedback does not quote the current proposal')
        return confirmed
    except Exception as exc:
        raise ValueError(
            "Report review validation unavailable; the proposal has not been saved"
        ) from exc
