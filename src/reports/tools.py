"""Report tool execution; source metadata is accepted only from the registry."""
from __future__ import annotations

import json
import re

from src.reports.rendering import render_html
from src.reports.models import validate_content
from src.reports.store import for_sessions
from src.reports.blocks import resolve_edits, apply_resolved

TOOL_NAMES = {"create_report", "get_report", "update_report", "revise_report"}


def report_reference(record):
    return {k: record[k] for k in ("id", "session_id", "title", "revision", "updated_at")}


def _edit_result(record):
    return json.dumps({'report': report_reference(record), 'operations': record.get('edit_operations', [])}, ensure_ascii=False)


def execute_report_tool(sessions, sid, name, args, registry, operation_id="", review_batches=None, review_validator=None, review_repairer=None, *, report_store=None):
    store = report_store if report_store is not None else for_sessions(sessions).bind_session(sid)
    try:
        if name == "get_report":
            record = store.read(sid, str(args.get("report_id") or ""), args.get("revision"))
            # Pair exact source with its identity so the editor can verify range
            # boundaries without matching a separate offset-only index by eye.
            record['blocks'] = [{**block, 'text': record['markdown'][block['start']:block['end']]}
                                for block in record['blocks']]
            # Source identities in an immutable report remain valid when revising it.
            registry.include(record["citations"])
            active = next((b for b in review_batches or [] if b['report_id'] == record['id']), None)
            if active:
                current = store.review_batch(sid, record['id'], active['id'])
                record['submitted_review'] = {k:v for k,v in current.items() if k not in {'runtime_id','result'}}
            return json.dumps(record, ensure_ascii=False)
        rid = str(args.get("report_id") or "")
        active = next((b for b in review_batches or [] if b["report_id"] == rid), None)
        if name == "revise_report":
            if not active:
                raise ValueError("This report has no submitted review in the current turn")
            previous = store.read(sid, rid, active["revision"])
            registry.include(previous["citations"])
            task = store.review_batch(sid, rid, active['id'])
            if args.get('output_report_id'):
                results = args.get('results')
                if args.get('edits') != [] or not isinstance(results, list) or any(
                    not isinstance(result, dict) or result.get('status') != 'unchanged' or result.get('edit_ids') != []
                    for result in results
                ):
                    raise ValueError('A separate-report review keeps source edits empty and uses unchanged source outcomes')
                output = store.read(sid, str(args['output_report_id']))
                if output['id'] == rid:
                    raise ValueError('A separate deliverable needs its own report identity')
                if review_validator and task['status'] == 'running':
                    issues = review_validator(previous, task, {k: output[k] for k in ('title', 'markdown')}, results)
                    if issues:
                        from src.reports.validation import ReviewCoverageError
                        raise ReviewCoverageError(issues)
                registry.include(output['citations'])
                record = store.apply_review(sid, rid, active['id'], [], results, registry.records(), require_block_targets=True)
                return json.dumps({'report': report_reference(output), 'review_result': record.get('review_result', record.get('review'))}, ensure_ascii=False)
            if review_validator and task['status'] == 'running':
                for attempt in range(3):
                    resolved = resolve_edits(previous, args.get('edits'), comments=task['comments'])
                    proposed = dict(title=apply_resolved(previous['title'],resolved,field='title'), markdown=apply_resolved(previous['markdown'],resolved))
                    issues = review_validator(previous, task, proposed, args.get('results'))
                    if not issues:
                        break
                    if review_repairer is None or attempt == 2:
                        from src.reports.validation import ReviewCoverageError
                        raise ReviewCoverageError(issues)
                    corrected = review_repairer(previous, task, args.get('edits'), args.get('results'), issues)
                    args = {**args, 'edits': corrected['edits'], 'results': corrected['results']}
            # apply_review validates exact edits and the final citation relationships.
            record = store.apply_review(sid, rid, active["id"], args.get("edits"), args.get("results"), registry.records(), require_block_targets=True)
            return json.dumps({"report": report_reference(record), "review_result": record.get("review", record.get("review_result"))}, ensure_ascii=False)
        if name == "update_report" and active:
            raise ValueError("Use revise_report to save this batch's edits and per-comment outcomes together")
        if name not in {"create_report", "update_report"}:
            raise ValueError("Unknown report tool")
        rid = str(args.get("report_id") or "") if name == "update_report" else None
        if name == 'update_report' and not rid:
            raise ValueError('Provide the report ID returned by get_report')
        rewrite_copy = name == 'update_report' and args.get('mode') == 'rewrite'
        committed = store.saved_operation(sid, operation_id, None if rewrite_copy else rid)
        if committed is not None:
            registry.include(committed['citations'])
            return _edit_result(committed)
        previous = store.read(sid, rid) if rid else None
        title = args.get("title") or (previous or {}).get("title", "")
        if previous:
            if type(args.get('expected_revision')) is not int or args['expected_revision'] != previous['revision']:
                raise ValueError('Report version conflict; read the latest revision before editing')
            registry.include(previous['citations'])
            if 'edits' in args:
                if args.get('mode') == 'rewrite' or 'markdown' in args:
                    raise ValueError('Choose block edits or explicit whole-document rewrite')
                resolved = resolve_edits(previous, args['edits'])
                body = apply_resolved(previous['markdown'], resolved)
                if not args.get('title'):
                    title = apply_resolved(previous['title'], resolved, field='title')
                validate_content(title, body)
                citations = registry.used(body, strict=True)
                render_html({'title': title, 'markdown': body, 'citations': citations})
                record = store.save_edits(sid, rid, args['edits'], citations, title=title,
                    expected_revision=args['expected_revision'], operation_id=operation_id)
                return _edit_result(record)
            if args.get('mode') != 'rewrite':
                raise ValueError('Local changes require block-addressed edits. Use mode=rewrite for an intentional whole-document rewrite.')
        markdown = args.get("markdown", "")
        validate_content(title, markdown)
        if isinstance(markdown, str) and isinstance(title, str):
            body = re.sub(r"\A[\r\n]* {0,3}#\s+" + re.escape(title.strip()) + r"\s*\r?\n+", "", markdown, count=1)
            if body.strip():
                markdown = body
        citations = registry.used(markdown, strict=True)
        render_html({"title": title, "markdown": markdown, "citations": citations})
        record = store.save(sid, title, markdown, citations, report_id=None if rewrite_copy else rid,
            expected_revision=None if rewrite_copy else args.get("expected_revision"), operation_id=operation_id)
        return json.dumps({"report": report_reference(record)}, ensure_ascii=False)
    except (ValueError, TypeError, FileNotFoundError) as exc:
        error = {"error": str(exc) if isinstance(exc, ValueError) else "Report version is unavailable in this conversation", "saved": False}
        if hasattr(exc, 'review_issues'):
            error['review_issues'] = exc.review_issues
        if name in {'revise_report', 'update_report'} and isinstance(args.get('edits'), list):
            try:
                from src.reports.edit_feedback import edit_feedback
                current = store.read(sid, str(args.get('report_id') or ''))
                batch = next((b for b in review_batches or [] if b['report_id'] == current['id']), None)
                base = batch['revision'] if batch else args.get('expected_revision')
                if current['revision'] == base:
                    error['edit_errors'] = edit_feedback(current, args['edits'], comments=(batch or {}).get('comments', []))
                    error['base_revision'] = base
            except (ValueError, FileNotFoundError, TypeError):
                pass
        return json.dumps(error, ensure_ascii=False)


def changes_from_result(name, content):
    if name not in {"create_report", "update_report", "revise_report"} or not isinstance(content, str):
        return []
    try:
        payload = json.loads(content)
        report = payload.get("report")
        return [report] if isinstance(report, dict) and all(k in report for k in ("id", "session_id", "title", "revision")) else []
    except (ValueError, TypeError, AttributeError):
        return []


def merge_report_changes(changes, incoming):
    for report in incoming or []:
        for index, existing in enumerate(changes):
            if existing["id"] == report["id"]:
                changes[index] = report
                break
        else:
            changes.append(report)
