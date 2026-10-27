"""Coarse review locations within a single instruction, using verified source ranges."""
from difflib import SequenceMatcher

from src.reports.blocks import _ranges, markdown_lines


def review_units(source):
    for block in _ranges(source):
        if block['kind'] == 'table':
            offset = block['start']
            for line in markdown_lines(source[block['start']:block['end']], keepends=True):
                yield offset, offset + len(line)
                offset += len(line)
        else:
            yield block['start'], block['end']


def units_for(edit, units):
    if edit.get('field', 'markdown') == 'title':
        return {('title', 0)}
    hits = {('markdown', a) for a, b in units
            if (a <= edit['start'] < b if edit['start'] == edit['end']
                else a < edit['end'] and edit['start'] < b)}
    return hits or {('gap', edit['start'])}


def local_edits(source, edits):
    """Break a broad source edit at actual changes before grouping by paragraph/row.

    A local edit retains its complete range. Inherited pending patches are still
    composed by decisions.evolve, so a continuation keeps its rollback baseline.
    """
    units = list(review_units(source))
    result = []
    for edit in edits:
        if len(units_for(edit, units)) <= 1:
            result.append(edit)
            continue
        for index, (kind, a, b, c, d) in enumerate(
                SequenceMatcher(None, edit['before'], edit['after'], autojunk=True).get_opcodes()):
            if kind != 'equal':
                result.append({**edit, 'id': f"{edit['id']}-part-{index}",
                               'operation_id': edit.get('operation_id', edit['id']),
                               'start': edit['start'] + a, 'end': edit['start'] + b,
                               'before': edit['before'][a:b], 'after': edit['after'][c:d]})
    return result, units


def discussion_groups(groups, group):
    """Follow a location's lineage, excluding sibling locations of its instruction."""
    if not group.get('location_id'):
        return [g for g in groups if set(group['thread_ids']) & set(g['thread_ids'])]
    ids = {group['id']}
    changed = True
    while changed:
        previous = len(ids)
        for candidate in groups:
            if candidate.get('location_id') == group['location_id'] or candidate['id'] in ids:
                ids.add(candidate['id'])
                ids.update(candidate['parent_ids'])
            elif ids.intersection(candidate['parent_ids']):
                ids.add(candidate['id'])
        changed = previous != len(ids)
    return [g for g in groups if g['id'] in ids]
