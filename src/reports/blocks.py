"""Server-owned Markdown block identities and exact, revision-bound edits.

Offsets address the original Unicode source, never rendered text or pixels.
Content hashes verify a target; they are not identities (duplicate blocks have
distinct IDs). Splits and merges retain lineage rather than guessing identity.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import re
import uuid


def content_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def unique_offset(text, needle):
    """Also reject overlapping occurrences, which str.count does not count."""
    at = text.find(needle)
    return at if at >= 0 and text.find(needle, at + 1) < 0 else None


def markdown_lines(source, *, keepends=False):
    """CommonMark line endings; Unicode spacing stays inside its source line."""
    lines = re.findall(r'[^\r\n]*(?:\r\n|\r|\n|$)', source)
    if lines and not lines[-1]:
        lines.pop()
    return lines if keepends else [line.rstrip('\r\n') for line in lines]


_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
_HEADING = re.compile(r'^ {0,3}#{1,6}\s')
_LIST = re.compile(r'^ {0,3}(?:[-+*]|\d+[.)])\s')
_QUOTE = re.compile(r'^ {0,3}>')
_RULE = re.compile(r'^ {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$')


def _table_rule(line):
    cells = line.strip().strip('|').split('|')
    return len(cells) > 1 and all(re.fullmatch(r'\s*:?-{3,}:?\s*', cell) for cell in cells)


def _ranges(source):
    lines = markdown_lines(source, keepends=True)
    offsets, total = [], 0
    for line in lines:
        offsets.append(total)
        total += len(line)
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        start = i
        line = lines[i].rstrip('\r\n')
        if fence := _FENCE.match(line):
            kind = 'code'
            marker = fence[1]
            i += 1
            while i < len(lines):
                closing = re.fullmatch(r' {0,3}' + re.escape(marker[0]) + r'{' + str(len(marker)) + r',}\s*', lines[i].rstrip('\r\n'))
                i += 1
                if closing:
                    break
        elif '|' in line and i + 1 < len(lines) and _table_rule(lines[i + 1]):
            kind = 'table'
            i += 2
            while i < len(lines) and lines[i].strip() and '|' in lines[i]:
                i += 1
        elif _HEADING.match(line) or _RULE.fullmatch(line):
            kind = 'heading' if _HEADING.match(line) else 'rule'
            i += 1
        elif i + 1 < len(lines) and re.fullmatch(r' {0,3}(?:=+|-+)\s*', lines[i + 1].rstrip('\r\n')):
            kind = 'heading'
            i += 2
        else:
            kind = 'list' if _LIST.match(line) else 'quote' if _QUOTE.match(line) else 'paragraph'
            i += 1
            while i < len(lines):
                current = lines[i].rstrip('\r\n')
                if not current.strip():
                    j = i
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and ((kind == 'list' and (_LIST.match(lines[j]) or lines[j].startswith(('  ', '\t')))) or
                                           (kind == 'quote' and _QUOTE.match(lines[j]))):
                        i = j
                        continue
                    break
                table = '|' in current and i + 1 < len(lines) and _table_rule(lines[i + 1])
                if _FENCE.match(current) or _HEADING.match(current) or _RULE.fullmatch(current) or table:
                    break
                if kind == 'paragraph' and (_LIST.match(current) or _QUOTE.match(current)):
                    break
                i += 1
        end = offsets[i] if i < len(lines) else len(source)
        yield dict(kind=kind, start=offsets[start], end=end, start_line=start + 1, end_line=i)


def _project_boundary(position, edits):
    delta = 0
    for edit in edits:
        start, end = edit['start'], edit['end']
        if position <= start:
            break
        if position < end:
            return None
        delta += len(edit['after']) - (end - start)
    return position + delta


def index_blocks(source, seed, *, parent=None, edits=()):
    blocks = []
    for item in _ranges(source):
        digest = content_hash(source[item['start']:item['end']])
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f'{seed}/{item["start"]}/{item["kind"]}/{digest}').hex
        blocks.append(dict(item, id='b_' + identity, hash=digest))
    if not parent:
        return blocks
    old = parent.get('blocks') or []
    if source == parent['markdown']:
        return [dict(block) for block in old]
    edits = [e for e in edits if e.get('field', 'markdown') == 'markdown']
    if not edits:
        # Legacy snapshots / deliberate rewrites have no explicit operation map.
        # Only unique unchanged blocks can be carried conservatively here.
        old_counts = Counter((b['kind'], b['hash']) for b in old)
        new_counts = Counter((b['kind'], b['hash']) for b in blocks)
        unique = {(b['kind'], b['hash']): b for b in old if old_counts[b['kind'], b['hash']] == 1}
        for block in blocks:
            key = block['kind'], block['hash']
            if key in unique and new_counts[key] == 1:
                block['id'] = unique[key]['id']
        return blocks
    assigned = set()
    edits = sorted(edits, key=lambda e: e['start'])
    if apply_resolved(parent['markdown'], edits) != source:
        raise ValueError('Block edits must reproduce the saved report exactly')
    by_range = {(b['start'], b['end'], b['kind']): b for b in blocks}
    for position, previous in enumerate(old):
        touched = [e for e in edits if e['start'] < previous['end'] and e['end'] > previous['start']]
        following = old[position + 1]['start'] if position + 1 < len(old) else len(parent['markdown'])
        complete = touched[0] if len(touched) == 1 and touched[0].get('mode') == 'block_range' else None
        if complete and complete['start'] == previous['start'] and complete['end'] == following:
            start = _project_boundary(previous['start'], edits)
            end = _project_boundary(following, edits)
            contained = [b for b in blocks if start is not None and end is not None and b['start'] >= start and b['end'] <= end]
            if len(contained) == 1 and contained[0]['start'] == start and contained[0]['kind'] == previous['kind']:
                candidate = contained[0]
                if previous['id'] not in assigned and candidate['id'] not in assigned:
                    candidate['id'] = previous['id']
                    assigned.add(previous['id'])
            continue
        # A single block may be rewritten entirely. Multi-block replacements
        # and structural splits use new identities, with their parents below.
        if any(e['start'] < previous['start'] or e['end'] > previous['end'] for e in touched):
            continue
        start = _project_boundary(previous['start'], edits)
        end = _project_boundary(previous['end'], edits)
        if start is None or end is None:
            continue
        candidate = by_range.get((start, end, previous['kind']))
        if candidate and previous['id'] not in assigned and candidate['id'] not in assigned:
            candidate['id'] = previous['id']
            assigned.add(previous['id'])
    # Preserve lineage for new structural blocks, without reusing one ID twice.
    lineage = []
    for previous in old:
        touched = [e for e in edits if e['start'] < previous['end'] and e['end'] > previous['start']]
        start = _project_boundary(previous['start'], edits)
        end = _project_boundary(previous['end'], edits)
        ranges = [(start, end)] if start is not None and end is not None else []
        for edit in touched:
            lo = _project_boundary(edit['start'], edits)
            ranges.append((lo, lo + len(edit['after'])))
        lineage.append((previous['id'], ranges))
    for block in blocks:
        if block['id'] in assigned:
            continue
        parents = []
        for parent_id, ranges in lineage:
            if any(a is not None and b is not None and block['start'] < b and block['end'] > a for a, b in ranges):
                parents.append(parent_id)
        if parents:
            block['parent_ids'] = list(dict.fromkeys(parents))
    return blocks


def resolve_edits(report, edits, *, require_targets=True, comments=()):
    if not isinstance(edits, list) or len(edits) > 200:
        raise ValueError('Provide at most 200 structured edits')
    source = report['markdown']
    blocks = report.get('blocks') or []
    by_id = {b['id']: b for b in blocks}
    result, ids = [], set()
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError('Each edit must be a structured object')
        eid, before, after = edit.get('id'), edit.get('before'), edit.get('after')
        if not isinstance(eid, str) or not eid or eid in ids or not isinstance(after, str):
            raise ValueError('Every edit needs a distinct ID, exact before text and after text')
        ids.add(eid)
        mode = edit.get('mode', 'block')
        if mode == 'section':
            first = by_id.get(edit.get('block_id'))
            if first is None or first['kind'] != 'heading':
                raise ValueError('A section edit requires a verified heading block')
            def level(block):
                text = source[block['start']:block['end']]
                marker = re.match(r'^ {0,3}(#{1,6})\s', text)
                return len(marker[1]) if marker else 1 if re.search(r'(?m)^ {0,3}=+\s*$', text) else 2
            following = next((b for b in blocks if b['start'] > first['start'] and b['kind'] == 'heading' and level(b) <= level(first)), None)
            last = next(b for b in reversed(blocks) if following is None or b['start'] < following['start'])
            edit = {**edit, 'end_block_id': last['id'], 'end_block_hash': last['hash']}
            mode = 'block_range'
        if mode in {'selection', 'replace_all'}:
            from src.reports.text_map import literal_edits
            spans, count = literal_edits(report, edit, comments)
            for index, span in enumerate(spans):
                if span.get('field') == 'title':
                    result.append(dict(id=eid if len(spans) == 1 else f'{eid}#{index + 1}', **span,
                        base_revision=report['revision'], block_ids=[], operation_id=eid, mode=mode, match_count=count))
                    continue
                touched = [b for b in blocks if b['start'] < span['end'] and b['end'] > span['start']]
                if not touched:
                    raise ValueError('Literal replacement must address a report block')
                result.append(dict(id=eid if len(spans) == 1 else f'{eid}#{index + 1}', **span,
                    base_revision=report['revision'], block_id=touched[0]['id'], block_hash=touched[0]['hash'],
                    end_block_id=touched[-1]['id'], end_block_hash=touched[-1]['hash'], block_ids=[b['id'] for b in touched],
                    operation_id=eid, mode=mode, match_count=count))
            continue
        if mode not in {'block', 'block_range'} or (mode == 'block' and (not isinstance(before, str) or not before)):
            raise ValueError('Use block, block_range, selection or replace_all with the corresponding fields')
        target = edit.get('block_id')
        if require_targets or target is not None:
            first = by_id.get(target) if isinstance(target, str) else None
            if first is None:
                raise ValueError('Unknown block ID; read the report block index first')
            last_id = edit.get('end_block_id', target)
            last = by_id.get(last_id) if isinstance(last_id, str) else None
            if last is None or last['start'] < first['start']:
                raise ValueError('Block boundaries must exist in document order')
            expected = edit.get('block_hash')
            if expected != content_hash(source[first['start']:first['end']]):
                raise ValueError('Block content changed; read the latest report before editing')
            last_hash = edit.get('end_block_hash', expected if last_id == target else None)
            if last_hash != content_hash(source[last['start']:last['end']]):
                raise ValueError('End block content changed; read the latest report before editing')
            lo, hi = first['start'], last['end']
            # A block owns its following blank separator, never the next block.
            following = next((b['start'] for b in blocks if b['start'] >= hi), len(source))
            if not source[hi:following].strip():
                hi = following
        else:
            lo, hi = 0, len(source)  # Conservative compatibility for stored legacy batches.
        if mode == 'block_range':
            if not target:
                raise ValueError('A complete block range requires verified block IDs and hashes')
            before = source[lo:hi]
            # Complete-block authoring owns content, while the server retains
            # the Markdown separator so the next heading/paragraph stays apart.
            separator = re.search(r'(?:\r\n|[\r\n])[ \t\r\n]*\Z', before)
            if after and separator:
                supplied = re.search(r'(?:\r\n|[\r\n])[ \t\r\n]*\Z', after)
                if supplied is None:
                    after += separator[0]
                elif len(re.findall(r'\r\n|[\r\n]', supplied[0])) < len(re.findall(r'\r\n|[\r\n]', separator[0])):
                    after = after[:supplied.start()] + separator[0]
        if before == after:
            raise ValueError('Edits must change the report')
        relative = unique_offset(source[lo:hi], before)
        if relative is None:
            raise ValueError('Edit before text must be unique inside the verified block range; include enough context')
        start, end = lo + relative, lo + relative + len(before)
        touched = [b for b in blocks if b['start'] < end and b['end'] > start]
        if not touched:
            raise ValueError('Edit must address a report block')
        result.append(dict(id=eid, before=before, after=after, start=start, end=end,
                           base_revision=report['revision'], block_id=touched[0]['id'], block_hash=touched[0]['hash'],
                           end_block_id=touched[-1]['id'], end_block_hash=touched[-1]['hash'],
                           block_ids=[b['id'] for b in touched], operation_id=eid, mode=mode, match_count=1))
    if len({edit['id'] for edit in result}) != len(result):
        raise ValueError('Edit IDs conflict after expanding replacement operations')
    ordered = sorted(result, key=lambda e: (e.get('field', 'markdown'), e['start']))
    if any(a.get('field', 'markdown') == b.get('field', 'markdown') and a['end'] > b['start'] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('Merge overlapping edits before submitting')
    return result


def operation_summaries(edits):
    grouped = {}
    for edit in edits:
        key = edit.get('operation_id', edit['id'])
        entry = grouped.setdefault(key, dict(id=key, mode=edit.get('mode', 'block'), match_count=edit.get('match_count', 1), edit_ids=[]))
        entry['edit_ids'].append(edit['id'])
    return list(grouped.values())


def apply_resolved(source, edits, *, field='markdown'):
    for edit in sorted((e for e in edits if e.get('field', 'markdown') == field), key=lambda e: e['start'], reverse=True):
        if source[edit['start']:edit['end']] != edit['before']:
            raise ValueError('Edit source does not match the base report')
        source = source[:edit['start']] + edit['after'] + source[edit['end']:]
    return source


def change_regions(report, after, edits):
    """Constrain visual comparison to the verified operations and their blocks.

    When removing a boundary joins neighboring blocks, widen both sides to
    complete blocks before comparing. Repeated text outside an operation can
    never attract its deletion or replacement marker.
    """
    source, old = report['markdown'], report['blocks']
    edits = sorted(edits, key=lambda e: e['start'])
    if apply_resolved(source, edits) != after:
        raise ValueError('Comparison edits must reproduce the saved report exactly')
    new = list(_ranges(after))
    groups = []
    for edit in edits:
        touched = [i for i, block in enumerate(old) if block['start'] < edit['end'] and block['end'] > edit['start']]
        if not touched and edit['start'] == edit['end']:
            touched = [next((i for i, block in enumerate(old) if block['end'] > edit['start']), len(old) - 1)]
        groups.append((touched[0], touched[-1]))
    while True:
        merged = []
        for first, last in sorted(groups):
            if merged and first <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
            else:
                merged.append((first, last))
        widened = []
        for first, last in merged:
            lo = _project_boundary(old[first]['start'], edits)
            end = max(old[last]['end'], *(e['end'] for e in edits if old[first]['start'] <= e['start'] < old[last]['end']))
            hi = _project_boundary(end, edits)
            # Overlapping operation ranges were merged above, so boundaries
            # cannot lie inside a replacement.
            if lo is None or hi is None:
                raise ValueError('Comparison block boundary overlaps an edit')
            touched = [block for block in new if block['start'] < hi and block['end'] > lo]
            a = first - 1 if touched and touched[0]['start'] < lo and first > 0 else first
            b = last + 1 if touched and touched[-1]['end'] > hi and last + 1 < len(old) else last
            widened.append((a, b))
        if widened == merged:
            break
        groups = widened
    regions = []
    for first, last in merged:
        start = old[first]['start']
        end = max(old[last]['end'], *(e['end'] for e in edits if start <= e['start'] < old[last]['end']))
        lo, hi = _project_boundary(start, edits), _project_boundary(end, edits)
        new_kinds = [b['kind'] for b in new if b['start'] < hi and b['end'] > lo]
        regions.append(dict(start=start, end=end, new_start=lo, new_end=hi,
                            structural=[b['kind'] for b in old[first:last + 1]] != new_kinds))
    return regions


def anchor_blocks(report, anchor):
    """Add or verify the exact block boundaries for a saved user selection."""
    blocks = report['blocks']
    first = next((b for b in blocks if b['start_line'] <= anchor['start_line'] <= b['end_line']), None)
    last = next((b for b in blocks if b['start_line'] <= anchor['end_line'] <= b['end_line']), None)
    if first is None or last is None:
        raise ValueError('Comment anchor must address existing blocks')
    values = dict(block_id=first['id'], block_hash=first['hash'], end_block_id=last['id'], end_block_hash=last['hash'])
    for key, value in values.items():
        if anchor.get(key) is not None and anchor[key] != value:
            raise ValueError('Comment block identity does not match its saved selection')
    return values
