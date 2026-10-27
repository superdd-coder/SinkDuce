"""Snapshot saved minutes and render downloads with the report document engine."""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from urllib.parse import unquote, urlsplit

from src.meeting import store
from src.notes.service import prepare_meeting_summary_for_note
from src.reports import rendering
from src.reports.models import MAX_MARKDOWN

CONTENT_TYPES = {
    'md': 'text/markdown; charset=utf-8',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'pdf': 'application/pdf',
}
_SLOTS = threading.BoundedSemaphore(2)
_OPEN_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})([^\r\n]*)[\r\n]*$')


class ExportBusy(Exception):
    pass


@contextmanager
def conversion_slot():
    if not _SLOTS.acquire(blocking=False):
        raise ExportBusy('Other documents are exporting. Please retry shortly.')
    try:
        yield
    finally:
        _SLOTS.release()


def clean_summary(markdown, speaker_names):
    """Keep code/diagram fences verbatim while cleaning human-facing prose."""
    chunks, prose, code, fence = [], [], [], None

    def flush():
        if prose:
            cleaned = prepare_meeting_summary_for_note(''.join(prose), speaker_names, resolve_speakers=True)
            if cleaned.strip():
                chunks.append(cleaned.strip())
            prose.clear()

    for line in markdown.splitlines(keepends=True):
        if fence:
            code.append(line)
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + r'{' + str(len(fence)) + r',}[ \t]*[\r\n]*', line):
                chunks.append(''.join(code).rstrip('\r\n'))
                code, fence = [], None
        elif opening := _OPEN_FENCE.match(line):
            flush()
            fence, code = opening[1], [line]
        else:
            prose.append(line)
    flush()
    if code:
        chunks.append(''.join(code).rstrip('\r\n'))
    return '\n\n'.join(chunks).strip() + '\n'


def summary_document(meeting, tab_id, language=None):
    tab = next((t if isinstance(t, dict) else t.model_dump() for t in meeting.tabs or []
                if (t.get('tab_id') if isinstance(t, dict) else t.tab_id) == tab_id), None)
    if not tab:
        raise FileNotFoundError('Summary section not found')
    language = language.upper() if language else None
    markdown = (store.get_translation_md(meeting.id, tab_id, language) if language
                else store.get_section_md(meeting.id, tab_id))
    if not markdown or not markdown.strip():
        raise FileNotFoundError('The selected summary is not available yet')
    if len(markdown) > MAX_MARKDOWN:
        raise ValueError('Summary is too large to export; export a shorter section')
    markdown = clean_summary(markdown, meeting.speaker_names or {})
    if len(markdown) > MAX_MARKDOWN:
        raise ValueError('Summary is too large to export; export a shorter section')
    title = ' — '.join(value for value in [meeting.title, tab.get('name') or 'Summary', language] if value)[:240]
    return {'title': title, 'markdown': markdown,
            'citations': [], 'document_kind': 'meeting'}


def export_filename(title, fmt):
    safe = re.sub(r'[\x00-\x1f\x7f\\/:*?"<>|]+', '-', title).strip(' .-')[:140]
    return (safe or 'Meeting summary') + '.' + fmt


def meeting_image_resolver(meeting_id):
    def resolve(url):
        parsed = urlsplit(url)
        prefix = f'/api/meetings/{meeting_id}/images/'
        if parsed.scheme or parsed.netloc or not parsed.path.startswith(prefix):
            raise ValueError('Export images must belong to this meeting')
        filename = unquote(parsed.path[len(prefix):])
        path = store.get_note_image_path(meeting_id, filename)
        if not path.is_file():
            raise ValueError('An image in this summary is no longer available')
        if path.stat().st_size > rendering.MAX_ASSET_BYTES:
            raise ValueError('Summary image exceeds the export size limit')
        return path.read_bytes()
    return resolve


def render_summary(document, fmt, meeting_id):
    return {'docx': rendering.render_docx, 'pdf': rendering.render_pdf}[fmt](
        document, image_resolver=meeting_image_resolver(meeting_id))
