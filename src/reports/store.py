"""Atomic, session-owned report storage; late workers cannot revive deletion."""
from __future__ import annotations

import json
import copy
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.atomic_io import write_text_atomic
from src.reports.models import FORMAT_TYPES, MAX_CITATIONS, RENDERER_VERSION, validate_content, validate_caption_pages
from src.reports.reviews import HIGHLIGHT_VERSION, ReviewsMixin, compute_changes, compute_verified_changes, text_changes
from src.reports.blocks import content_hash, index_blocks, resolve_edits, apply_resolved, operation_summaries
from src.reports.history import new_journal, append_action, read_node, content_history, journal_records, active_revisions
from src.reports.decisions import DecisionsMixin, evolve, project, diff_edits

_LOCK = threading.RLock()
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


class ReportStore(DecisionsMixin, ReviewsMixin):
    _lock = _LOCK
    def __init__(self, root: Path, *, session_exists):
        self.root = Path(root)
        self.session_exists = session_exists
        self.runtime_id = uuid.uuid4().hex
        self._deleted = set()
        self._session_generations = {}
        self._session_scope = None
        self._block_cache = {}

    def start_session(self, sid):
        """Activate a newly inserted session, preserving revocation of old work."""
        with _LOCK:
            self._session_generations[sid] = uuid.uuid4().hex
            self._deleted.discard(sid)

    def bind_session(self, sid):
        """Freeze this request/worker to the current incarnation of one session."""
        with _LOCK:
            if self._session_scope is not None:
                self._alive(sid)
            bound = copy.copy(self)
            generation = self._session_generations.setdefault(sid, uuid.uuid4().hex)
            bound._session_scope = (sid, generation)
            return bound

    def _dir(self, sid, rid=""):
        if not isinstance(sid, str) or not _ID.fullmatch(sid) or (rid and (not isinstance(rid, str) or not _ID.fullmatch(rid))):
            raise ValueError("Invalid report or session ID")
        return self.root / sid / rid

    def _alive(self, sid):
        self._dir(sid)
        stale = self._session_scope is not None and self._session_scope != (sid, self._session_generations.get(sid))
        if stale or sid in self._deleted or not self.session_exists(sid):
            raise FileNotFoundError("Conversation no longer exists")

    @staticmethod
    def _json(path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write(path, data):
        write_text_atomic(path, json.dumps(data, ensure_ascii=False))

    def list(self, sid):
        with _LOCK:
            directory = self._dir(sid)
            try:
                self._alive(sid)
            except FileNotFoundError:
                return []
            out = []
            for folder in directory.glob('*'):
                if not folder.is_dir():
                    continue
                try:
                    out.append(self._meta(sid, folder.name))
                except (ValueError, OSError, KeyError):
                    continue
            return sorted(out, key=lambda r: r["updated_at"], reverse=True)

    def _meta(self, sid, rid):
        directory = self._dir(sid, rid)
        journal = directory / 'history.json'
        return self._json(journal)['meta'] if journal.is_file() else self._json(directory / 'meta.json')

    def _raw_read(self, sid, rid, revision):
        directory = self._dir(sid, rid)
        journal = directory / 'history.json'
        if journal.is_file():
            return read_node(self._json(journal), revision)
        return self._json(directory / 'revisions' / f'{revision}.json')

    def saved_operation(self, sid, operation_id, report_id=None):
        """Return the original result of an already committed tool operation."""
        key = str(operation_id)[:200]
        if not key:
            return None
        with _LOCK:
            self._alive(sid)
            records = [self._meta(sid, report_id)] if report_id else self.list(sid)
            for meta in records:
                revision = (meta.get('operations') or {}).get(key)
                if revision is not None and revision in self.active_revisions(sid, meta['id']):
                    return self.read(sid, meta['id'], revision)
        return None

    def save(self, sid, title, markdown, citations, *, report_id=None, expected_revision=None, operation_id="", review=None, block_edits=(), restored_from=None, suggestions=None, decision_event=None, historical_replay=False):
        validate_content(title, markdown)
        if len(citations) > MAX_CITATIONS:
            raise ValueError("Too many citations")
        from src.chatbox.citations import EvidenceRegistry
        used = EvidenceRegistry(sid, citations).used(markdown, strict=True)
        with _LOCK:
            self._alive(sid)
            previous = None
            operation_key = str(operation_id)[:200]
            committed = self.saved_operation(sid, operation_id, report_id)
            if committed is not None:
                return committed
            rid = report_id or uuid.uuid4().hex
            directory = self._dir(sid, rid)
            if report_id:
                previous = self._meta(sid, rid)
                if expected_revision != previous["revision"]:
                    raise ValueError("Report version conflict; read the latest revision before editing")
            elif len(self.list(sid)) >= 100:
                raise ValueError("This conversation already has 100 reports")
            now = datetime.now(timezone.utc).isoformat()
            revision = (previous["revision"] if previous else 0) + 1
            meta = {"id": rid, "session_id": sid, "title": title.strip(), "revision": revision,
                    "created_at": previous["created_at"] if previous else now, "updated_at": now}
            record = {**meta, "markdown": markdown, "citations": used}
            parent = None
            if previous:
                parent = self.read(sid, rid, previous["revision"])
                record.update(parent_revision=parent["revision"], changes=compute_verified_changes(parent, markdown, block_edits, title=title.strip()))
            if not historical_replay:
                validate_caption_pages(markdown, used, previous=parent)
            record['blocks'] = index_blocks(markdown, f'{sid}/{rid}/{revision}', parent=parent, edits=block_edits)
            if block_edits:
                record['edit_log'] = block_edits
                record['edit_operations'] = operation_summaries(block_edits)
            if review is not None:
                record["review"] = review
            if restored_from is not None:
                record['restored_from'] = restored_from
                target = self.read(sid, rid, restored_from)
                for field in ('parent_revision', 'changes', 'review', 'edit_log', 'edit_operations', 'blocks'):
                    if field in target:
                        record[field] = copy.deepcopy(target[field])
                    else:
                        record.pop(field, None)
                record['history_revision'] = target['history_revision']
            record['suggestions'] = suggestions if suggestions is not None else evolve(parent, block_edits or diff_edits(parent, markdown, title.strip()), review, revision) if parent else []
            if decision_event is not None:
                record['decision_event'] = decision_event
            operations = dict((previous or {}).get("operations") or {})
            if restored_from is not None:
                retained = self.active_revisions(sid, rid, restored_from)
                operations = {key: value for key, value in operations.items() if value in retained}
            if operation_key:
                operations[operation_key] = revision
            head = {**meta, "operations": operations}
            path = directory / 'history.json'
            if parent:
                if path.is_file():
                    journal = self._json(path)
                else:
                    # Legacy files stay untouched. Future writes use only actions.
                    initial = self.read(sid, rid, 1)
                    journal = new_journal(initial, previous)
                    last = initial
                    for n in range(2, parent['revision'] + 1):
                        current = self.read(sid, rid, n)
                        append_action(journal, last, current, previous)
                        last = current
                # Read projections (legacy blocks / display highlights) may evolve;
                # the action chain always starts with the exact persisted node.
                append_action(journal, read_node(journal, parent['revision']), record, head)
            else:
                journal = new_journal(record, head)
            self._write(path, journal)
            return self.read(sid, rid, revision)

    def save_edits(self, sid, rid, edits, citations, *, expected_revision, operation_id='', title=None):
        with _LOCK:
            committed = self.saved_operation(sid, operation_id, rid)
            if committed is not None:
                return committed
            parent = self.read(sid, rid)
            if type(expected_revision) is not int or parent['revision'] != expected_revision:
                raise ValueError('Report version conflict; read the latest revision before editing')
            resolved = resolve_edits(parent, edits)
            if not resolved and (title is None or title == parent['title']):
                raise ValueError('Provide an actual content or title change')
            body = apply_resolved(parent['markdown'], resolved)
            renamed_title = apply_resolved(parent['title'], resolved, field='title')
            if title is not None and any(e.get('field') == 'title' for e in resolved) and title != renamed_title:
                raise ValueError('Literal replacement also renames the report title; omit title or provide the matching new title')
            proposed_title = title if title is not None else renamed_title
            validate_content(proposed_title, body)
            if proposed_title.strip() != parent['title'] and not any(e.get('field') == 'title' for e in resolved):
                title_id = 'title-' + uuid.uuid4().hex
                resolved.append(dict(id=title_id, operation_id=title_id, field='title',
                                     start=0, end=len(parent['title']), before=parent['title'], after=proposed_title.strip(),
                                     base_revision=parent['revision'], block_ids=[], mode='block', match_count=1))
            return self.save(sid, proposed_title, body, citations,
                             report_id=rid, expected_revision=expected_revision, operation_id=operation_id,
                             block_edits=resolved)

    def _with_blocks(self, sid, rid, record):
        """Project legacy snapshots without writing or recursively walking history."""
        chain, current, parent = [], record, None
        while True:
            key = sid, rid, current['revision'], content_hash(current['markdown'])
            available = current.get('blocks') or self._block_cache.get(key)
            if available:
                current['blocks'] = [dict(b) for b in available]
                parent = current
                break
            chain.append(current)
            if current['revision'] <= 1:
                break
            try:
                current = self._raw_read(sid, rid, current['revision'] - 1)
            except FileNotFoundError:
                break
        for item in reversed(chain):
            edits = []
            if parent and item.get('review', {}).get('edits'):
                try:
                    edits = resolve_edits(parent, item['review']['edits'], require_targets=False)
                    if apply_resolved(parent['markdown'], edits) != item['markdown']:
                        edits = []
                except ValueError:
                    edits = []  # Historical ambiguity never licenses a guessed identity.
            item['blocks'] = index_blocks(item['markdown'], f"{sid}/{rid}/{item['revision']}", parent=parent, edits=edits)
            key = sid, rid, item['revision'], content_hash(item['markdown'])
            self._block_cache[key] = item['blocks']
            if len(self._block_cache) > 128:
                self._block_cache.pop(next(iter(self._block_cache)))
            parent = item
        return record

    def read(self, sid, rid, revision=None):
        with _LOCK:
            self._alive(sid)
            self._dir(sid, rid)
            if revision is None:
                revision = self._meta(sid, rid)["revision"]
            if type(revision) is not int or revision < 1:
                raise ValueError("Invalid report revision")
            record = self._raw_read(sid, rid, revision)
            self._with_blocks(sid, rid, record)
            if revision > 1 and "parent_revision" not in record and 'restored_from' not in record:
                try:
                    parent = self._raw_read(sid, rid, revision-1)
                except FileNotFoundError:
                    pass
                else:
                    record.update(parent_revision=revision-1, changes=compute_changes(parent["markdown"], record["markdown"]))
            for change in record.get("changes", []):
                if change.get("highlight_version") != HIGHLIGHT_VERSION:
                    added, removed = text_changes(change["before"], change["after"])
                    change.update(added=added, removed=removed, highlight_version=HIGHLIGHT_VERSION)
            entries = content_history(self._history_records(sid, rid), revision)
            record['history_revision'] = entries[0]['revision'] if entries else revision
            return project(self, sid, rid, record)

    def versions(self, sid, rid):
        return [{k:record[k] for k in ('id','session_id','title','revision','updated_at')} for record in self.history(sid, rid)]

    def history(self, sid, rid):
        with _LOCK:
            self.read(sid, rid)
            return content_history(self._history_records(sid, rid))

    def _history_records(self, sid, rid):
        path = self._dir(sid, rid) / 'history.json'
        if path.is_file():
            yield from journal_records(self._json(path))
        else:
            for n in range(1, self._meta(sid, rid)['revision'] + 1):
                yield self._raw_read(sid, rid, n)

    def active_revisions(self, sid, rid, revision=None):
        return active_revisions(self._history_records(sid, rid), revision)

    def restore(self, sid, rid, revision, *, expected_revision, operation_id):
        with _LOCK:
            committed = self.saved_operation(sid, 'restore:' + operation_id, rid)
            if committed is not None:
                return committed
            current = self.read(sid, rid)
            if type(expected_revision) is not int or current['revision'] != expected_revision:
                raise ValueError('Report version conflict; read the current report before restoring')
            if any(b['status'] == 'running' and not b.get('archived') for b in self._review_state(sid, rid)['batches'].values()):
                raise ValueError('A report review is running; finish it before restoring history')
            if revision not in self.active_revisions(sid, rid):
                raise FileNotFoundError('History entry is outside the current timeline')
            target = self.read(sid, rid, revision)
            if revision == current['revision']:
                return current
            return self.save(sid, target['title'], target['markdown'], target['citations'],
                             report_id=rid, expected_revision=expected_revision,
                             operation_id='restore:' + operation_id, restored_from=revision,suggestions=target.get('suggestions', []),historical_replay=True)

    def export_path(self, sid, rid, revision, fmt):
        self.read(sid, rid, revision)
        if fmt not in FORMAT_TYPES:
            raise ValueError("Unsupported export format")
        return self._dir(sid, rid) / "exports" / str(revision) / f"report.{fmt}"

    def write_export(self, sid, rid, revision, fmt, data, *, status, error=""):
        with _LOCK:
            path = self.export_path(sid, rid, revision, fmt)
            path.parent.mkdir(parents=True, exist_ok=True)
            if data is not None:
                if len(data) > 50 * 1024 * 1024:
                    raise ValueError("Export exceeds the 50 MiB limit")
                temp = path.with_suffix(path.suffix + ".tmp")
                temp.write_bytes(data)
                temp.replace(path)
            state = {"format": fmt, "revision": revision, "status": status,
                     "error": error[:500], "runtime_id": self.runtime_id, "renderer_version": RENDERER_VERSION}
            self._write(path.with_suffix(path.suffix + ".json"), state)
            return self.export_state(sid, rid, revision, fmt)

    def export_state(self, sid, rid, revision, fmt):
        with _LOCK:
            path = self.export_path(sid, rid, revision, fmt)
            try:
                state = self._json(path.with_suffix(path.suffix + ".json"))
            except FileNotFoundError:
                return {"format": fmt, "revision": revision, "status": "idle", "error": ""}
            if state["status"] in {"queued", "running"} and state.get("runtime_id") != self.runtime_id:
                state.update(status="failed", error="Export was interrupted. Retry this format.")
                self._write(path.with_suffix(path.suffix + ".json"), state)
            if state["status"] == "ready" and not path.is_file():
                state.update(status="failed", error="Export file is unavailable. Retry this format.")
            if state["status"] == "ready" and state.get("renderer_version") != RENDERER_VERSION:
                state.update(status="failed", error="Report rendering has changed. Prepare this export again.")
            return {k: v for k, v in state.items() if k != "runtime_id"}

    def purge(self, sid):
        with _LOCK:
            directory = self._dir(sid)
            self._deleted.add(sid)
            self._session_generations[sid] = uuid.uuid4().hex
            shutil.rmtree(directory, ignore_errors=True)


def for_sessions(session_store):
    from src.config import get_data_dir
    db_path = getattr(session_store, "_db_path", None)
    base = db_path.parent if isinstance(db_path, Path) else get_data_dir()
    root = base / "chat_reports"
    with _LOCK:
        # A reopened database has a new session store and liveness check. Keep
        # its report cache on that instance rather than on the shared disk path.
        reports = getattr(session_store, "_report_store", None)
        if not isinstance(reports, ReportStore) or reports.root != root:
            reports = ReportStore(root, session_exists=lambda sid: session_store.get_session(sid) is not None)
            session_store._report_store = reports
        return reports
