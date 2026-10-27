"""Session-authorized report reads and explicit export/download actions."""
from __future__ import annotations

import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse

from src.api.routes.identity import require_module
from src.api.routes.sessions import _get_store, require_session_access
from src.reports.models import ExportRequest, RestoreRequest, FORMAT_TYPES, CommentCreate, CommentText, DecisionRequest, FollowUpRequest
from src.reports.service import schedule_export
from src.reports.store import for_sessions

router = APIRouter(dependencies=[Depends(require_module("chat"))])


def _store(sid):
    require_session_access(sid)
    sessions = _get_store()
    reports = for_sessions(sessions)
    with reports._lock:
        if sessions.get_session(sid) is None:
            raise HTTPException(404, "Conversation not found")
        return reports.bind_session(sid)


def _read(sid, rid, revision):
    store = _store(sid)
    try:
        report = store.read(sid, rid, revision)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Report version not found") from None
    return store, report


@router.get("/sessions/{session_id}/reports")
def list_reports(session_id: str):
    return [{k: v for k, v in report.items() if k != "operations"} for report in _store(session_id).list(session_id)]


@router.get("/sessions/{session_id}/reports/{report_id}/revisions")
def list_revisions(session_id: str, report_id: str):
    store, _ = _read(session_id, report_id, None)
    return store.versions(session_id, report_id)


@router.get("/sessions/{session_id}/reports/{report_id}/revisions/{revision}")
def read_revision(session_id: str, report_id: str, revision: int):
    return _read(session_id, report_id, revision)[1]


@router.get('/sessions/{session_id}/reports/{report_id}')
def read_current_report(session_id: str, report_id: str):
    return _read(session_id, report_id, None)[1]


@router.get('/sessions/{session_id}/reports/{report_id}/history')
def report_history(session_id: str, report_id: str):
    store, _ = _read(session_id, report_id, None)
    return store.history(session_id, report_id)


@router.post('/sessions/{session_id}/reports/{report_id}/history/{revision}/restore')
def restore_history(session_id: str, report_id: str, revision: int, body: RestoreRequest):
    store = _store(session_id)
    try:
        return store.restore(session_id, report_id, revision,
                             expected_revision=body.expected_revision, operation_id=body.operation_id)
    except FileNotFoundError:
        raise HTTPException(404, 'Report history entry not found') from None
    except ValueError as exc:
        if str(exc).startswith('Report version conflict'):
            raise HTTPException(409, {'code': 'report_version_conflict', 'message': str(exc)}) from exc
        raise HTTPException(409, str(exc)) from exc


@router.get("/sessions/{session_id}/reports/{report_id}/reviews")
def read_review_history(session_id: str, report_id: str):
    store, _ = _read(session_id, report_id, None)
    return store.review_history(session_id, report_id)


@router.get("/sessions/{session_id}/report-comments")
def pending_comments(session_id: str):
    return _store(session_id).pending_comments(session_id)


@router.post('/sessions/{session_id}/reports/{report_id}/suggestions/{group_id}/decision')
def decide_suggestion(session_id: str, report_id: str, group_id: str, body: DecisionRequest):
    store = _store(session_id)
    try:
        return store.decide_suggestion(session_id, report_id, group_id, body.decision,
            expected_revision=body.expected_revision, operation_id=body.operation_id, scope=body.scope)
    except FileNotFoundError:
        raise HTTPException(404, 'Revision suggestion not found') from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/sessions/{session_id}/reports/{report_id}/suggestions/{group_id}/follow-up', status_code=201)
def follow_up_suggestion(session_id: str, report_id: str, group_id: str, body: FollowUpRequest):
    from src.identity import get_actor
    store = _store(session_id)
    try:
        return store.follow_up(session_id, report_id, group_id, text=body.text, author=get_actor().id,
            expected_revision=body.expected_revision, operation_id=body.operation_id)
    except FileNotFoundError:
        raise HTTPException(404, 'Revision suggestion not found') from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/sessions/{session_id}/reports/{report_id}/suggestions/{group_id}/follow-up-context')
def follow_up_context(session_id: str, report_id: str, group_id: str, expected_revision: int):
    try:
        return _store(session_id).follow_up_context(session_id,report_id,group_id,expected_revision=expected_revision)
    except FileNotFoundError:
        raise HTTPException(404, 'Revision suggestion not found') from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/sessions/{session_id}/reports/{report_id}/revisions/{revision}/comments")
def read_comments(session_id: str, report_id: str, revision: int):
    store, _ = _read(session_id, report_id, revision)
    return store.comments(session_id, report_id, revision)


@router.post("/sessions/{session_id}/reports/{report_id}/revisions/{revision}/comments", status_code=201)
def create_comment(session_id: str, report_id: str, revision: int, body: CommentCreate):
    from src.identity import get_actor
    store, _ = _read(session_id, report_id, revision)
    try:
        return store.create_comment(session_id, report_id, revision, text=body.text,
                                    anchor=body.anchor.model_dump(), author=get_actor().id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/sessions/{session_id}/reports/{report_id}/comments/{comment_id}")
def edit_comment(session_id: str, report_id: str, comment_id: str, body: CommentText):
    store, _ = _read(session_id, report_id, None)
    try:
        return store.edit_comment(session_id, report_id, comment_id, body.text)
    except FileNotFoundError:
        raise HTTPException(404, "Comment not found") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/sessions/{session_id}/reports/{report_id}/comments/{comment_id}", status_code=204)
def delete_comment(session_id: str, report_id: str, comment_id: str):
    store, _ = _read(session_id, report_id, None)
    store.delete_comment(session_id, report_id, comment_id)


@router.post("/sessions/{session_id}/reports/{report_id}/revisions/{revision}/exports", status_code=202)
def create_export(session_id: str, report_id: str, revision: int, body: ExportRequest, background: BackgroundTasks):
    store, _ = _read(session_id, report_id, revision)
    return schedule_export(background, store, session_id, report_id, revision, body.format)


@router.get("/sessions/{session_id}/reports/{report_id}/revisions/{revision}/exports/{fmt}")
def export_status(session_id: str, report_id: str, revision: int, fmt: str):
    store, _ = _read(session_id, report_id, revision)
    if fmt not in FORMAT_TYPES:
        raise HTTPException(400, "Unsupported export format")
    return store.export_state(session_id, report_id, revision, fmt)


@router.get("/sessions/{session_id}/reports/{report_id}/revisions/{revision}/exports/{fmt}/download")
def download_export(session_id: str, report_id: str, revision: int, fmt: str):
    store, report = _read(session_id, report_id, revision)
    if fmt not in FORMAT_TYPES:
        raise HTTPException(400, "Unsupported export format")
    if store.export_state(session_id, report_id, revision, fmt)["status"] != "ready":
        raise HTTPException(409, "Prepare this export before downloading")
    filename = re.sub(r'[\\/\x00-\x1f]', "_", report["title"])[:80] + f".{fmt}"
    return FileResponse(store.export_path(session_id, report_id, revision, fmt), filename=filename, media_type=FORMAT_TYPES[fmt])
