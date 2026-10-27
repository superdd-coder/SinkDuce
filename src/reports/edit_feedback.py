"""Actionable, exact-source diagnostics; suggestions never execute an edit."""
from __future__ import annotations

from src.reports.blocks import resolve_edits


def edit_feedback(report, edits, *, comments=()):
    errors = []
    if not isinstance(edits, list):
        return errors
    source, blocks = report['markdown'], report['blocks']
    for edit in edits[:200]:
        if not isinstance(edit, dict):
            continue
        try:
            resolve_edits(report, [edit], comments=comments)
        except ValueError as exc:
            item = {'id': edit.get('id'), 'error': str(exc)}
            needle = edit.get('before')
            if isinstance(needle, str) and needle and edit.get('mode', 'block') == 'block':
                candidates, offset = [], 0
                while len(candidates) < 6 and (at := source.find(needle, offset)) >= 0:
                    touched = [b for b in blocks if b['start'] < at + len(needle) and b['end'] > at]
                    if touched:
                        first, last = touched[0], touched[-1]
                        candidates.append({'start_line': first['start_line'], 'end_line': last['end_line'],
                            'block_id': first['id'], 'block_hash': first['hash'],
                            'end_block_id': last['id'], 'end_block_hash': last['hash']})
                    offset = at + 1
                item['candidates'] = candidates
                # Re-sending requires the model to explicitly select the new target.
                if len(candidates) == 1 and source.find(needle, offset) < 0:
                    target = {k:v for k,v in candidates[0].items() if k not in {'start_line', 'end_line'}}
                    suggestion = {**edit, **target}
                    try:
                        resolve_edits(report, [suggestion], comments=comments)
                    except ValueError:
                        pass
                    else:
                        item['suggested_edit'] = suggestion
            errors.append(item)
    return errors
