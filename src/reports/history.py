"""One initial document plus verified JSON actions, in one atomic journal.

Internal revision counters identify immutable replay positions. Restoring a node
moves the visible timeline back to it; replay actions remain available to old links.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from difflib import SequenceMatcher


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _string_actions(before, after, path):
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    offsets = [0]
    for line in old:
        offsets.append(offsets[-1] + len(line))
    actions = []
    for kind, a, b, c, d in SequenceMatcher(None, old, new, autojunk=True).get_opcodes():
        if kind == 'equal':
            continue
        start, left, right = offsets[a], ''.join(old[a:b]), ''.join(new[c:d])
        prefix = 0
        while prefix < min(len(left), len(right)) and left[prefix] == right[prefix]:
            prefix += 1
        suffix = 0
        while suffix < min(len(left), len(right)) - prefix and left[-1 - suffix] == right[-1 - suffix]:
            suffix += 1
        left_end, right_end = len(left) - suffix, len(right) - suffix
        actions.append({'op': 'splice', 'path': path, 'start': start + prefix,
                            'delete': left_end - prefix, 'value': right[prefix:right_end]})
    return list(reversed(actions))


def actions_between(before, after, path=()):
    path = list(path)
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        actions = [{'op': 'remove', 'path': path + [key]} for key in before if key not in after]
        for key, value in after.items():
            actions += actions_between(before[key], value, path + [key]) if key in before else [{'op': 'set', 'path': path + [key], 'value': value}]
        return actions
    if isinstance(before, str) and isinstance(after, str):
        return _string_actions(before, after, path)
    if isinstance(before, list) and isinstance(after, list):
        if len(before) == len(after):
            return [action for i, (a, b) in enumerate(zip(before, after)) for action in actions_between(a, b, path + [i])]
        lo, hi = 0, 0
        while lo < min(len(before), len(after)) and before[lo] == after[lo]:
            lo += 1
        while hi < min(len(before), len(after)) - lo and before[-1 - hi] == after[-1 - hi]:
            hi += 1
        return [{'op': 'splice', 'path': path, 'start': lo, 'delete': len(before) - hi - lo, 'value': after[lo:len(after) - hi]}]
    return [{'op': 'set', 'path': path, 'value': after}]


def apply_actions(record, actions):
    result = copy.deepcopy(record)
    for action in actions:
        path = action['path']
        parent = result
        for key in path[:-1]:
            parent = parent[key]
        key = path[-1]
        if action['op'] == 'set':
            parent[key] = copy.deepcopy(action['value'])
        elif action['op'] == 'remove':
            del parent[key]
        elif action['op'] == 'splice':
            value, start = parent[key], action['start']
            parent[key] = value[:start] + action['value'] + value[start + action['delete']:]
        else:
            raise ValueError('Report history integrity check failed: unknown action')
    return result


def history_entry(record):
    review = record.get('review') or {}
    decision = record.get('decision_event') or {}
    group = next((g for g in record.get('suggestions') or [] if g['id'] == decision.get('group_id')), {})
    label = next((c['text'] for c in group.get('comments') or [] if c.get('text')), '') or group.get('summary', '')
    label = re.sub(r'\s+', ' ', label).strip()
    if len(label) > 48:
        label = label[:48] + '…'
    kind = 'decision' if decision else 'create' if record['revision'] == 1 else 'restore' if 'restored_from' in record else 'review' if review else 'edit'
    return {**{k:record[k] for k in ('id', 'session_id', 'title', 'revision', 'updated_at')},
            'kind':kind, 'change_count':len(record.get('changes') or []),
            'comment_count':len(review.get('comments') or []),
            'summaries':[r['summary'] for r in review.get('results') or [] if r.get('summary')],
            **({'decision':decision['decision'], 'group_id':decision['group_id'], 'label':label} if decision else {}),
            **({'restored_from':record['restored_from']} if 'restored_from' in record else {})}


def _timeline(records):
    parents, entries, previous = {}, {}, None
    for record in records:
        revision = record['revision']
        parents[revision] = record.get('restored_from', previous['revision'] if previous else None)
        changed = previous is None or any(record.get(key) != previous.get(key) for key in ('title', 'markdown', 'citations'))
        if 'restored_from' not in record and (changed or record.get('decision_event')):
            entries[revision] = history_entry(record)
        previous = record
    return parents, entries, previous['revision'] if previous else None


def _ancestors(parents, revision):
    path = []
    while revision is not None:
        if revision not in parents or (parents[revision] is not None and parents[revision] >= revision):
            raise ValueError('Report history integrity check failed: invalid timeline')
        path.append(revision)
        revision = parents[revision]
    return path


def active_revisions(records, revision=None):
    parents, _, head = _timeline(records)
    return set(_ancestors(parents, head if revision is None else revision))


def content_history(records, revision=None):
    """Show only the chosen timeline, omitting restore actions and abandoned steps."""
    parents, entries, head = _timeline(records)
    return [entries[n] for n in _ancestors(parents, head if revision is None else revision) if n in entries]


def journal_records(journal):
    record = journal['base']
    yield record
    for event in journal['events']:
        record = apply_actions(record, event['actions'])
        yield record


def new_journal(record, meta):
    return {'schema': 1, 'base': copy.deepcopy(record), 'base_hash': digest(record),
                'initial_entry': history_entry(record), 'events': [], 'meta': meta}


def append_action(journal, before, after, meta):
    if after['revision'] != before['revision'] + 1:
        raise ValueError('Report history integrity check failed: nonconsecutive action')
    event = {'revision': after['revision'], 'from_hash': digest(before), 'to_hash': digest(after),
                 'actions': actions_between(before, after), 'entry': history_entry(after)}
    journal['events'].append(event)
    journal['meta'] = meta


def read_node(journal, revision):
    try:
        record = copy.deepcopy(journal['base'])
        expected = journal['base_hash']
        if digest(record) != expected:
            raise ValueError('Report history integrity check failed: initial document')
        if revision < record['revision']:
            raise FileNotFoundError('History entry not found')
        for event in journal['events']:
            if record['revision'] == revision:
                return record
            if event['revision'] != record['revision'] + 1 or event['from_hash'] != expected:
                raise ValueError('Report history integrity check failed: action chain')
            record = apply_actions(record, event['actions'])
            expected = event['to_hash']
            if record['revision'] != event['revision'] or digest(record) != expected:
                raise ValueError('Report history integrity check failed: replay')
        if record['revision'] == revision:
            return record
        raise FileNotFoundError('History entry not found')
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError('Report history integrity check failed: invalid journal') from exc
