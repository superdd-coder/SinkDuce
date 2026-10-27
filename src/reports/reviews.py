"""Anchored comments, frozen review tasks and verified revision edits."""
from __future__ import annotations

import copy
from bisect import bisect_left
import difflib
import re
import uuid
from datetime import datetime, timezone

import markdown
from bs4 import BeautifulSoup
from src.reports.blocks import anchor_blocks, resolve_edits, apply_resolved, change_regions, operation_summaries, markdown_lines


def now():
    return datetime.now(timezone.utc).isoformat()


def normalized(value):
    return re.sub(r'\s+', '', value)


HIGHLIGHT_VERSION = 2
_UNSPACED_SCRIPTS = '\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af'
_UNSPACED = re.compile(f'[{_UNSPACED_SCRIPTS}]')
_WORD = rf'[^\W\d_{_UNSPACED_SCRIPTS}]+'
_TRACKING_TOKEN = re.compile(
    rf'[+−-]?\d+(?:[.,:/−-]\d+)*(?:[eE][+-]?\d+)?[%％]?|{_WORD}(?:[’\x27-]{_WORD})*|[^\s]'
)
_SENTENCE_END = re.compile(r'[。！？!?；;\n\u2028]|\.(?=\s|$)')


def _tracking_text(source):
    source = re.sub(r'\[cite:[^\]]+\]', '', source)
    soup = BeautifulSoup(markdown.markdown(source, extensions=['tables', 'fenced_code']), 'html.parser')
    # Preserve inline words, rendered block boundaries and table-cell units.
    for tag in soup.find_all(['p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'table', 'blockquote']):
        tag.insert_before('\u2029')
        tag.insert_after('\u2029')
    for tag in soup.find_all(['td', 'th', 'br']):
        tag.insert_before('\u2028')
        tag.insert_after('\u2028')
    blocks = []
    for block in soup.get_text().split('\u2029'):
        cells = [re.sub(r'\s+', ' ', cell).strip() for cell in block.split('\u2028')]
        text = '\u2028'.join(cell for cell in cells if cell)
        if text:
            blocks.append(text)
    return '\n'.join(blocks)


def _reading_intervals(text, tokens, edits):
    """Use whole words/numbers; expand unspaced rewrites and substantial edits to sentences."""
    positions = [token.start() for token in tokens]
    units, start = [], 0
    for boundary in _SENTENCE_END.finditer(text):
        units.append((start, boundary.end()))
        start = boundary.end()
    units.append((start, len(text)))
    intervals, cursor = [], 0
    for lo, hi in units:
        while lo < hi and text[lo].isspace():
            lo += 1
        while hi > lo and text[hi - 1].isspace():
            hi -= 1
        while cursor < len(edits) and edits[cursor][1] <= lo:
            cursor += 1
        touches = []
        for index in range(cursor, len(edits)):
            a, b, unspaced = edits[index]
            if a >= hi:
                break
            if b > lo:
                touches.append((max(lo, a), min(hi, b), unspaced))
        if not touches:
            continue
        words = tokens[bisect_left(positions, lo):bisect_left(positions, hi)]
        changed = sum(any(token.start() < b and token.end() > a for a, b, _ in touches) for token in words)
        if any(unspaced for _, _, unspaced in touches) or (changed >= 3 and changed >= len(words) * .4):
            intervals.append((lo, hi))
        else:
            intervals.extend((a, b) for a, b, _ in touches)
    merged = []
    for start, end in sorted(intervals):
        gap = text[merged[-1][1]:start] if merged else ''
        if merged and (start <= merged[-1][1] or (gap.isspace() and '\n' not in gap and '\u2028' not in gap)):
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def text_changes(before, after):
    old, new = _tracking_text(before), _tracking_text(after)
    if max(len(old), len(new)) > 16000:
        return [], []
    old_tokens, new_tokens = list(_TRACKING_TOKEN.finditer(old)), list(_TRACKING_TOKEN.finditer(new))
    old_edits, new_edits = [], []
    matcher = difflib.SequenceMatcher(None, [token.group() for token in old_tokens],
                                     [token.group() for token in new_tokens], autojunk=max(len(old_tokens), len(new_tokens)) > 4000)
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag == 'equal':
            continue
        old_part = old[old_tokens[a].start():old_tokens[b - 1].end()] if b > a else ''
        new_part = new[new_tokens[c].start():new_tokens[d - 1].end()] if d > c else ''
        unspaced = bool(_UNSPACED.search(old_part + new_part))
        if b > a:
            old_edits.append((old_tokens[a].start(), old_tokens[b - 1].end(), unspaced))
        if d > c:
            new_edits.append((new_tokens[c].start(), new_tokens[d - 1].end(), unspaced))

    def passages(text, tokens, edits):
        result = []
        for start, end in _reading_intervals(text, tokens, edits):
            # Context stays inside the DOM block used by the browser's range locator.
            lo = text.rfind('\n', 0, start) + 1
            hi = text.find('\n', end)
            if hi < 0:
                hi = len(text)
            result.append({'quote': text[start:end], 'prefix': text[max(lo, start - 60):start], 'suffix': text[end:min(hi, end + 60)]})
        return result
    return passages(new, new_tokens, new_edits), passages(old, old_tokens, old_edits)


def validate_anchor(source, anchor):
    start, end = anchor.get('start_line'), anchor.get('end_line')
    lines = markdown_lines(source)
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
        raise ValueError('Invalid comment anchor lines')
    quote = anchor.get('quote', '')
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 8000:
        raise ValueError('Invalid comment anchor quote')
    fragment = '\n'.join(lines[start - 1:end])
    if anchor.get('kind') == 'block':
        if fragment[:8000] != quote:
            raise ValueError('Block anchor must match the saved Markdown block')
        return {'start_line': start, 'end_line': end, 'quote': quote, 'prefix': '', 'suffix': '', 'kind': 'block'}
    prefix, suffix = str(anchor.get('prefix', ''))[-100:], str(anchor.get('suffix', ''))[:100]
    from src.reports.text_map import selection_anchor
    selected, _ = selection_anchor(source, {**anchor, 'prefix': prefix, 'suffix': suffix})
    return {'start_line': start, 'end_line': end, 'quote': quote, 'prefix': prefix, 'suffix': suffix, **selected}


def compute_changes(before, after):
    old, new = markdown_lines(before, keepends=True), markdown_lines(after, keepends=True)
    def expand(lines, start, end):
        # Keep tables and fenced figures renderable instead of diffing broken fragments.
        fence = None
        for index, line in enumerate(lines):
            marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
            if marker:
                if fence is None:
                    fence = (index, marker[1][0])
                elif marker[1][0] == fence[1]:
                    if fence[0] <= start <= index:
                        return fence[0], index + 1
                    fence = None
        if start < len(lines) and '|' in lines[start]:
            lo, hi = start, max(start + 1, end)
            while lo > 0 and '|' in lines[lo-1]:
                lo -= 1
            while hi < len(lines) and '|' in lines[hi]:
                hi += 1
            if any(re.match(r'^\s*\|?\s*:?-{3,}', line) for line in lines[lo:hi]):
                return lo, hi
        return start, end
    hunks = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=len(old) > 2000).get_opcodes():
        if tag == 'equal':
            continue
        a, b = expand(old, a, b) if b > a else (a, b)
        c, d = expand(new, c, d) if d > c else (c, d)
        if hunks and a < hunks[-1][2] and c < hunks[-1][4]:
            prev = hunks.pop()
            hunks.append(('replace', prev[1], max(b, prev[2]), prev[3], max(d, prev[4])))
        else:
            hunks.append((tag, a, b, c, d))
    result = []
    for n, (tag, a, b, c, d) in enumerate(hunks):
        before_text, after_text = ''.join(old[a:b]), ''.join(new[c:d])
        added, removed = text_changes(before_text, after_text)
        result.append({'id': f'change-{n}', 'kind': tag, 'old_start': a + 1, 'old_end': b,
                       'new_start': c + 1, 'new_end': d, 'before': before_text, 'after': after_text,
                       'added': added, 'removed': removed, 'highlight_version': HIGHLIGHT_VERSION})
    return result


def compute_verified_changes(report, after, edits, *, title=None):
    """Use explicit operation locations before diffing text within each region."""
    body_edits = [e for e in edits if e.get('field', 'markdown') == 'markdown']
    if any(e['start'] == e['end'] for e in body_edits):
        # Restoring a deleted passage inserts into a gap with no old block.
        # Its persisted decision retains exact coordinates; compare full lines
        # for the history preview instead of inventing an old containing block.
        body_edits = []
    source = report['markdown']
    result = [] if body_edits else compute_changes(source, after)
    for region in change_regions(report, after, body_edits) if body_edits else []:
        before_text = source[region['start']:region['end']]
        after_text = after[region['new_start']:region['new_end']]
        old_offset = source[:region['start']].count('\n')
        new_offset = after[:region['new_start']].count('\n')
        if region['structural']:
            added, removed = text_changes(before_text, after_text)
            changes = [dict(kind='replace' if after_text else 'delete', old_start=1, old_end=len(markdown_lines(before_text)),
                            new_start=1, new_end=len(markdown_lines(after_text)), before=before_text, after=after_text,
                            added=added, removed=removed, highlight_version=HIGHLIGHT_VERSION)]
        else:
            changes = compute_changes(before_text, after_text)
        for change in changes:
            result.append({**change, 'id': f'change-{len(result)}', 'old_start': change['old_start'] + old_offset,
                           'old_end': change['old_end'] + old_offset, 'new_start': change['new_start'] + new_offset,
                           'new_end': change['new_end'] + new_offset})
    if title is not None and title != report['title']:
        added, removed = text_changes(report['title'], title)
        result.append(dict(id=f'change-{len(result)}', field='title', kind='replace', before=report['title'], after=title,
                           old_start=0, old_end=0, new_start=0, new_end=0, added=added, removed=removed, highlight_version=HIGHLIGHT_VERSION))
    return result


class ReviewsMixin:
    @staticmethod
    def _settle_batch(state, batch, record, review, changed):
        batch.update(status='completed' if changed else 'unchanged', result_revision=record['revision'],
                     results=review['results'], result=review)
        outcomes = {r['comment_id']: r for r in review['results']}
        for c in state['comments']:
            if c.get('batch_id') != batch['id']:
                continue
            # This submitted round has a recorded result. Further discussion is
            # a new round, rather than silently re-queuing the same comment.
            if c['id'] in outcomes:
                c['state'] = 'resolved'

    def _review_state(self, sid, rid):
        latest = self.read(sid, rid)
        try:
            state = self._json(self._dir(sid, rid) / 'reviews.json')
        except FileNotFoundError:
            state = {'comments': [], 'batches': {}}
        active = self.active_revisions(sid, rid)
        # Recover tasks abandoned by a previous process; committed versions are authoritative.
        dirty = False
        for batch in state['batches'].values():
            archived = batch.get('result_revision', batch['revision']) not in active
            if bool(batch.get('archived')) != archived:
                batch['archived'] = archived
                dirty = True
            if archived:
                continue
            if batch['status'] == 'running':
                meta = self._meta(sid, rid)
                committed = meta.get('operations', {}).get('review:' + batch['id'])
                if committed:
                    record = self.read(sid, rid, committed)
                    self._settle_batch(state, batch, record, record['review'], True)
                    dirty = True
                elif batch.get('runtime_id') != self.runtime_id:
                    batch['status'] = 'interrupted'
                    for c in state['comments']:
                        if c.get('batch_id') == batch['id']:
                            c['state'] = 'pending'
                    dirty = True
        for c in state['comments']:
            batch = state['batches'].get(c.get('batch_id'))
            if 'created_revision' not in c:
                c['created_revision'] = next((r['revision'] for r in reversed(list(self._history_records(sid, rid)))
                    if r.get('updated_at', '') <= c.get('created_at', '')), c.get('previous_revision', c['revision']))
                dirty = True
            archived = c['created_revision'] not in active or bool((batch or {}).get('archived'))
            if bool(c.get('archived')) != archived:
                c['archived'] = archived
                dirty = True
            if archived:
                continue
            original = next((old for old in (batch or {}).get('comments',[]) if old['id']==c['id']), None)
            if c['state'] != 'resolved' and batch and batch['status'] in {'completed','unchanged'} and original and c['text']==original['text'] and any(r['comment_id']==c['id'] for r in batch.get('results') or []):
                c['state'] = 'resolved'
                dirty = True
            if c['state'] != 'pending' or c['revision'] >= latest['revision']:
                continue
            old = self.read(sid, rid, c['revision'])['markdown']
            anchor = c['anchor']
            fragment = '\n'.join(markdown_lines(old)[anchor['start_line']-1:anchor['end_line']])
            c.update(previous_anchor=copy.deepcopy(anchor), previous_revision=c['revision'], revision=latest['revision'], title=latest['title'])
            if c.get('parent_group_id'):
                # A pending discussion follows its thread's current passage,
                # including rollback after the user rejects its parent proposal.
                context = self.follow_up_context(sid, rid, c['parent_group_id'], expected_revision=latest['revision'])
                c.update({k: copy.deepcopy(v) for k, v in context.items() if k not in {'anchor', 'revision'}})
                c['anchor'] = {**validate_anchor(latest['markdown'], context['anchor']), **anchor_blocks(latest, context['anchor'])}
                c.pop('anchor_stale', None)
                dirty = True
                continue
            indexed = anchor.get('block_id')
            first = next((b for b in latest['blocks'] if b['id'] == indexed), None)
            last = next((b for b in latest['blocks'] if b['id'] == anchor.get('end_block_id', indexed)), None)
            if indexed:
                if first and last and first['hash'] == anchor.get('block_hash') and last['hash'] == anchor.get('end_block_hash', anchor.get('block_hash')):
                    parent = self.read(sid, rid, c['previous_revision'])
                    previous = next((b for b in parent['blocks'] if b['id'] == indexed), None)
                    shift = first['start_line'] - previous['start_line'] if previous else 0
                    candidate = {**anchor, 'start_line': anchor['start_line'] + shift, 'end_line': anchor['end_line'] + shift}
                    new_fragment = '\n'.join(markdown_lines(latest['markdown'])[candidate['start_line']-1:candidate['end_line']])
                    if new_fragment == fragment:
                        c['anchor'] = {**candidate, **validate_anchor(latest['markdown'], candidate)}
                        c.pop('anchor_stale', None)
                    else:
                        c['anchor_stale'] = True
                else:
                    c['anchor_stale'] = True
            elif fragment and old.count(fragment) == 1 and latest['markdown'].count(fragment) == 1:
                first = latest['markdown'][:latest['markdown'].index(fragment)].count('\n') + 1
                c['anchor'] = {**anchor, 'start_line': first, 'end_line': first + anchor['end_line'] - anchor['start_line']}
                c.pop('anchor_stale', None)
            else:
                c['anchor_stale'] = True
            dirty = True
        if dirty:
            self._write_reviews(sid, rid, state)
        return state

    def _write_reviews(self, sid, rid, state):
        self._alive(sid)
        self._write(self._dir(sid, rid) / 'reviews.json', state)

    def comments(self, sid, rid, revision):
        with self._lock:
            self.read(sid, rid, revision)
            return [c for c in self._review_state(sid, rid)['comments'] if c['revision'] == revision and c['state'] != 'resolved' and not c.get('archived')]

    def pending_comments(self, sid):
        with self._lock:
            self._alive(sid)
            result = []
            for report in self.list(sid):
                result.extend(c for c in self._review_state(sid, report['id'])['comments']
                              if c['revision'] == report['revision'] and c['state'] == 'pending' and not c.get('archived'))
            return sorted(result, key=lambda c: c['created_at'])

    def create_comment(self, sid, rid, revision, *, text, anchor, author):
        with self._lock:
            report = self.read(sid, rid, revision)
            if self.read(sid, rid)['revision'] != revision:
                raise ValueError('Add comments to the latest report version')
            text = str(text).strip()
            if not text or len(text) > 4000:
                raise ValueError('Comment must contain 1–4000 characters')
            identities = anchor_blocks(report, anchor)
            anchor = {**validate_anchor(report['markdown'], anchor), **identities}
            state = self._review_state(sid, rid)
            if len(state['comments']) >= 1000:
                raise ValueError('This report already has 1000 comments')
            record = {'id': uuid.uuid4().hex, 'session_id': sid, 'report_id': rid, 'title': report['title'],
                      'revision': revision, 'created_revision': revision, 'author': author, 'text': text, 'anchor': anchor,
                      'state': 'pending', 'created_at': now(), 'updated_at': now()}
            state['comments'].append(record)
            self._write_reviews(sid, rid, state)
            return record

    def edit_comment(self, sid, rid, cid, text):
        with self._lock:
            state = self._review_state(sid, rid)
            c = next((c for c in state['comments'] if c['id'] == cid), None)
            if c is None or c.get('archived'):
                raise FileNotFoundError('Comment not found')
            if c['state'] != 'pending':
                raise ValueError('This comment has already been submitted')
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                raise ValueError('Comment must contain 1–4000 characters')
            c.update(text=text.strip(), updated_at=now())
            self._write_reviews(sid, rid, state)
            return c

    def delete_comment(self, sid, rid, cid):
        with self._lock:
            state = self._review_state(sid, rid)
            state['comments'] = [c for c in state['comments'] if c['id'] != cid]
            self._write_reviews(sid, rid, state)

    def validate_reviews(self, sid, requests):
        with self._lock:
            return self._validate_reviews(sid, requests)

    def _validate_reviews(self, sid, requests):
        if not requests or len(requests) > 10 or len({r['report_id'] for r in requests}) != len(requests):
            raise ValueError('Choose distinct reports for this review')
        checked = []
        for request in requests:
            rid = request['report_id']
            report = self.read(sid, rid)
            if request['revision'] != report['revision']:
                raise ValueError('Report version changed; reload before submitting comments')
            state = self._review_state(sid, rid)
            if any(b['status'] == 'running' and not b.get('archived') for b in state['batches'].values()):
                raise ValueError('A review for this report is already running')
            ids = request.get('comment_ids') or []
            available = {c['id']: c for c in state['comments'] if c['revision'] == report['revision'] and c['state'] == 'pending' and not c.get('archived')}
            if not ids or len(ids) > 100 or len(set(ids)) != len(ids) or any(cid not in available for cid in ids):
                raise ValueError('Comments changed; reload before sending')
            checked.append((report, state, [available[cid] for cid in ids]))
        return checked

    def freeze_reviews(self, sid, requests, instruction):
        with self._lock:
            checked = self.validate_reviews(sid, requests)
            batches = []
            for report, state, comments in checked:
                for comment in comments:
                    if comment.get('parent_group_id'):
                        context=self.follow_up_context(sid,report['id'],comment['parent_group_id'],expected_revision=report['revision'])
                        comment.update(discussion_history=context['discussion_history'],current_text=context['current_text'],deleted=context['deleted'])
                bid = uuid.uuid4().hex
                batch = {'id': bid, 'report_id': report['id'], 'revision': report['revision'], 'title': report['title'],
                         'comments': copy.deepcopy(comments), 'instruction': instruction, 'status': 'running',
                         'created_at': now(), 'runtime_id': self.runtime_id}
                state['batches'][bid] = batch
                for c in comments:
                    c.update(state='submitted', batch_id=bid)
                self._write_reviews(sid, report['id'], state)
                batches.append(copy.deepcopy(batch))
            return batches

    def review_batch(self, sid, rid, bid):
        with self._lock:
            batch = self._review_state(sid, rid)['batches'].get(bid)
            if batch is None:
                raise FileNotFoundError('Review batch not found')
            return copy.deepcopy(batch)

    def review_history(self, sid, rid):
        with self._lock:
            batches = self._review_state(sid, rid)['batches'].values()
            rows = [{k: copy.deepcopy(v) for k, v in b.items() if k not in {'runtime_id', 'result'}}
                    for b in batches if b['status'] in {'completed', 'unchanged'} and not b.get('archived')]
            return sorted(rows, key=lambda b: b['created_at'], reverse=True)[:100]

    def release_reviews(self, sid, batches):
        with self._lock:
            for ref in batches:
                try:
                    state = self._review_state(sid, ref['report_id'])
                except FileNotFoundError:
                    continue
                batch = state['batches'].get(ref['id'])
                if not batch or batch['status'] != 'running':
                    continue
                meta = self._meta(sid, ref['report_id'])
                committed = meta.get('operations', {}).get('review:' + ref['id'])
                batch['status'] = 'completed' if committed else 'interrupted'
                for c in state['comments']:
                    if c.get('batch_id') == ref['id']:
                        c['state'] = 'resolved' if committed else 'pending'
                self._write_reviews(sid, ref['report_id'], state)

    def apply_review(self, sid, rid, bid, edits, results, citations, *, require_block_targets=False):
        with self._lock:
            state = self._review_state(sid, rid)
            batch = state['batches'].get(bid)
            if batch is None or batch.get('archived'):
                raise ValueError('Review batch is unavailable')
            if batch['status'] == 'completed':
                return self.read(sid, rid, batch['result_revision'])
            if batch['status'] == 'unchanged':
                return {**self.read(sid, rid, batch['revision']), 'review_result': batch['result']}
            if batch['status'] != 'running':
                raise ValueError('Review batch is no longer active')
            parent = self.read(sid, rid)
            if parent['revision'] != batch['revision']:
                raise ValueError('Report version conflict; reload the latest version')
            source = parent['markdown']
            if not isinstance(edits, list) or len(edits) > 200 or not isinstance(results, list):
                raise ValueError('Invalid revision edits or results')
            if any(not isinstance(e, dict) for e in edits) or any(not isinstance(r, dict) or not isinstance(r.get('comment_id'), str) for r in results):
                raise ValueError('Each edit and outcome must be a structured object with valid IDs')
            resolved = resolve_edits(parent, edits, require_targets=require_block_targets, comments=batch['comments'])
            edit_ids = {e['operation_id'] for e in resolved}
            ids = {c['id'] for c in batch['comments']}
            if len(results) != len(ids) or {r.get('comment_id') for r in results} != ids:
                raise ValueError('Return exactly one result for every submitted comment')
            linked = set()
            for result in results:
                summary, links, status = result.get('summary'), result.get('edit_ids'), result.get('status')
                if status not in {'applied', 'partial', 'unchanged', 'needs_clarification'}:
                    raise ValueError('Invalid comment outcome')
                if not isinstance(summary, str) or not summary.strip() or len(summary) > (80 if re.search(r'[\u3400-\u9fff]', summary) else 240):
                    raise ValueError('Keep each reply concise: one sentence, at most two short sentences')
                if not isinstance(links, list) or any(not isinstance(link, str) or link not in edit_ids for link in links) or (status in {'applied', 'partial'} and not links):
                    raise ValueError('Applied outcomes must link to actual edits')
                linked.update(links)
            if linked != edit_ids:
                raise ValueError('Associate every edit with at least one comment')
            for operation in edits:
                if operation.get('mode') == 'selection' and not any(r['comment_id'] == operation.get('comment_id') and operation['id'] in r['edit_ids'] for r in results):
                    raise ValueError('Keep each selection replacement linked to its original comment')
            for edit in resolved:
                edit['comment_ids'] = [r['comment_id'] for r in results if edit['operation_id'] in r['edit_ids']]
            body = apply_resolved(source, resolved)
            title = apply_resolved(parent['title'], resolved, field='title').strip()
            if edits and body == source and title == parent['title']:
                raise ValueError('The revision must contain an actual change')
            from src.reports.rendering import render_html
            from src.chatbox.citations import EvidenceRegistry
            from src.reports.models import validate_content
            validate_content(title, body)
            citations = EvidenceRegistry(sid, citations).used(body, strict=True)
            render_html({'title': title, 'markdown': body, 'citations': citations})
            review = {k: copy.deepcopy(v) for k, v in batch.items() if k not in {'runtime_id', 'status'}}
            actual_changes = compute_verified_changes(parent, body, resolved, title=title)
            edit_changes = {}
            for edit in resolved:
                if edit.get('field') == 'title':
                    edit_changes.setdefault(edit['operation_id'], []).extend(ch['id'] for ch in actual_changes if ch.get('field') == 'title')
                    continue
                start = edit['start']
                first_line = source[:start].count('\n') + 1
                last_line = first_line + edit['before'].count('\n')
                edit_changes.setdefault(edit['operation_id'], []).extend(ch['id'] for ch in actual_changes
                    if ch['old_start'] <= last_line and max(ch['old_start'], ch['old_end']) >= first_line)
            enriched_results = [{**r, 'change_ids': list(dict.fromkeys(cid for eid in r['edit_ids'] for cid in edit_changes[eid]))} for r in results]
            review.update(results=enriched_results if edits else results, edits=resolved, operations=operation_summaries(resolved))
            if edits:
                record = self.save(sid, title, body, citations, report_id=rid,
                                   expected_revision=parent['revision'], operation_id='review:' + bid,
                                   review=review, block_edits=resolved)
            else:
                record = {**parent, 'review_result': review}
            self._settle_batch(state, batch, record, review, bool(edits))
            self._write_reviews(sid, rid, state)
            return record
