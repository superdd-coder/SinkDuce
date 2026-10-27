import copy
import json

import pytest

from src.reports.store import ReportStore


def make_store(root):
    return ReportStore(root, session_exists=lambda sid: sid == 's')


def test_history_stores_actions_and_replays_every_node_after_restart(tmp_path):
    store = make_store(tmp_path)
    source = '## Summary\n\n' + 'An unchanged paragraph.\n\n' * 120 + 'Amount: 10.\n'
    first = store.save('s', 'Draft', source, [], operation_id='first')
    second = store.save('s', 'Draft', source.replace('Amount: 10.', 'Amount: 12.'), [],
                        report_id=first['id'], expected_revision=1, operation_id='second')
    third = store.save('s', 'Renamed', second['markdown'] + '\nConclusion.\n', [],
                       report_id=first['id'], expected_revision=2, operation_id='third')
    directory = store._dir('s', first['id'])
    assert not (directory / 'revisions').exists(), 'New changes must not create a full file per step'
    ledger = json.loads((directory / 'history.json').read_text())
    assert len(ledger['events']) == 2
    assert 'markdown' not in ledger['events'][0], 'Events contain actions, not full reports'
    assert len(json.dumps(ledger['events'][0], ensure_ascii=False)) < len(json.dumps(first, ensure_ascii=False))
    assert 'An unchanged paragraph.' not in json.dumps(ledger['events'][0])
    reopened = make_store(tmp_path)
    for expected in (first, second, third):
        assert reopened.read('s', first['id'], expected['revision']) == expected
    assert reopened.saved_operation('s', 'second', first['id']) == second
    assert [r['revision'] for r in reopened.history('s', first['id'])] == [3, 2, 1]
    assert len(reopened.list('s')) == 1


def test_restore_rewinds_the_visible_timeline_without_adding_a_history_entry(tmp_path):
    store = make_store(tmp_path)
    first = store.save('s', 'Original', 'First paragraph.\n\nKeep this.', [])
    second = store.save('s', 'Changed', 'Changed paragraph.\n\nKeep this.', [],
                        report_id=first['id'], expected_revision=1)
    restored = store.restore('s', first['id'], 1, expected_revision=2, operation_id='restore-first')
    assert restored['revision'] == 3
    assert restored['markdown'] == first['markdown'] and restored['title'] == first['title']
    assert restored['restored_from'] == 1
    assert restored['history_revision'] == 1
    assert not restored.get('changes') and not restored.get('parent_revision')
    assert store.read('s', first['id'], 2) == second
    assert store.restore('s', first['id'], 1, expected_revision=2, operation_id='restore-first') == restored
    assert [e['revision'] for e in store.history('s', first['id'])] == [1]
    with pytest.raises(ValueError, match='conflict'):
        store.restore('s', first['id'], 2, expected_revision=2, operation_id='stale')
    with pytest.raises(FileNotFoundError):
        store.restore('s', first['id'], 2, expected_revision=3, operation_id='abandoned')
    edited = store.save('s', 'Continued', 'Continue from the restored state.', [],
                        report_id=first['id'], expected_revision=3)
    assert [e['revision'] for e in store.history('s', first['id'])] == [edited['revision'], 1]
    assert not make_store(tmp_path).read('s', first['id'], 3).get('changes')


def test_restore_to_second_step_drops_future_steps_and_continues_from_that_step(tmp_path):
    store = make_store(tmp_path)
    nodes = [store.save('s', 'Draft', 'Step 1.', [], operation_id='step-1')]
    for n in (2, 3, 4):
        nodes.append(store.save('s', 'Draft', f'Step {n}.', [], report_id=nodes[0]['id'],
                                expected_revision=nodes[-1]['revision'], operation_id=f'step-{n}'))
    rid = nodes[0]['id']
    restored = store.restore('s', rid, 2, expected_revision=4, operation_id='back-to-two')
    assert restored['history_revision'] == 2
    assert [e['revision'] for e in store.history('s', rid)] == [2, 1]
    assert store.saved_operation('s', 'step-3', rid) is None
    assert store.saved_operation('s', 'step-4', rid) is None
    assert store.saved_operation('s', 'step-2', rid)['revision'] == 2
    continued = store.save('s', 'Draft', 'A new direction from step 2.', [], report_id=rid,
                           expected_revision=restored['revision'], operation_id='step-3')
    assert continued['markdown'] == 'A new direction from step 2.'
    assert [e['revision'] for e in store.history('s', rid)] == [continued['revision'], 2, 1]
    reopened = make_store(tmp_path)
    assert reopened.read('s', rid)['markdown'] == continued['markdown']
    assert reopened.read('s', rid, 4)['markdown'] == 'Step 4.', 'Existing message references remain readable'
    assert [e['revision'] for e in reopened.history('s', rid)] == [continued['revision'], 2, 1]


def test_legacy_restore_cannot_deduplicate_against_an_abandoned_future_edit(tmp_path):
    store = make_store(tmp_path)
    first = store.save('s', 'Draft', 'Original.', [])
    store.save('s', 'Draft', 'Abandoned.', [], report_id=first['id'], expected_revision=1, operation_id='old-edit')
    store.restore('s', first['id'], 1, expected_revision=2, operation_id='back')
    path = store._dir('s', first['id']) / 'history.json'
    journal = json.loads(path.read_text())
    journal['meta']['operations']['old-edit'] = 2  # Earlier restores retained all operation IDs.
    store._write(path, journal)
    assert make_store(tmp_path).saved_operation('s', 'old-edit', first['id']) is None


def test_legacy_snapshots_remain_readable_and_are_left_untouched_on_first_new_action(tmp_path):
    store = make_store(tmp_path)
    directory = store._dir('s', 'legacy')
    first = dict(id='legacy', session_id='s', title='Draft', revision=1, markdown='Old.', citations=[], created_at='2026-01-01', updated_at='2026-01-01')
    second = {**first, 'revision': 2, 'markdown': 'Current.', 'updated_at': '2026-01-02'}
    for record in (first, second):
        store._write(directory / 'revisions' / f'{record["revision"]}.json', record)
    store._write(directory / 'meta.json', {k: v for k, v in second.items() if k not in ('markdown', 'citations')})
    originals = {p: p.read_bytes() for p in directory.rglob('*.json')}
    assert store.read('s', 'legacy', 1)['markdown'] == 'Old.'
    updated = store.save('s', 'Draft', 'New action.', [], report_id='legacy', expected_revision=2)
    assert updated['revision'] == 3
    assert not (directory / 'revisions' / '3.json').exists()
    assert all(p.read_bytes() == data for p, data in originals.items())
    reopened = make_store(tmp_path)
    assert [reopened.read('s', 'legacy', n)['markdown'] for n in (1, 2, 3)] == ['Old.', 'Current.', 'New action.']
    assert reopened.list('s')[0]['revision'] == 3


def test_corrupt_action_is_detected_before_restoring_or_showing_wrong_content(tmp_path):
    store = make_store(tmp_path)
    first = store.save('s', 'Draft', 'A.', [])
    store.save('s', 'Draft', 'B.', [], report_id=first['id'], expected_revision=1)
    path = store._dir('s', first['id']) / 'history.json'
    assert path.is_file(), 'Action journal is the source of truth'
    ledger = json.loads(path.read_text())
    broken = copy.deepcopy(ledger)
    broken['base']['markdown'] = 'Tampered.'
    store._write(path, broken)
    with pytest.raises(ValueError, match='integrity'):
        make_store(tmp_path).read('s', first['id'], 2)
    with pytest.raises(ValueError, match='integrity'):
        make_store(tmp_path).restore('s', first['id'], 1, expected_revision=2, operation_id='corrupt')


def test_restore_is_blocked_while_agent_is_processing_submitted_comments(tmp_path):
    store = make_store(tmp_path)
    r = store.save('s', 'Draft', 'Original.', [])
    r = store.save('s', 'Draft', 'Current.', [], report_id=r['id'], expected_revision=1)
    c = store.create_comment('s', r['id'], 2, text='Shorten.', anchor=dict(start_line=1, end_line=1, quote='Current.'), author='local')
    store.freeze_reviews('s', [dict(report_id=r['id'], revision=2, comment_ids=[c['id']])], '')
    with pytest.raises(ValueError, match='running'):
        store.restore('s', r['id'], 1, expected_revision=2, operation_id='busy')
    assert store.read('s', r['id'])['revision'] == 2


def test_failed_atomic_write_leaves_content_history_and_retry_identity_unchanged(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    r = store.save('s', 'Draft', 'Original.', [], operation_id='create')
    path = store._dir('s', r['id']) / 'history.json'
    original = path.read_bytes()
    write = store._write
    def fail(path, record):
        raise OSError('Simulated unavailable disk')
    monkeypatch.setattr(store, '_write', fail)
    with pytest.raises(OSError):
        store.save('s', 'Draft', 'Edited.', [], report_id=r['id'], expected_revision=1, operation_id='edit')
    assert path.read_bytes() == original
    assert make_store(tmp_path).saved_operation('s', 'edit', r['id']) is None
    monkeypatch.setattr(store, '_write', write)
    assert store.save('s', 'Draft', 'Edited.', [], report_id=r['id'], expected_revision=1, operation_id='edit')['revision'] == 2
    assert len(store.history('s', r['id'])) == 2


def test_display_projection_does_not_break_later_action_hash_chain(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    first = store.save('s', 'Draft', 'Old.', [])
    store.save('s', 'Draft', 'New.', [], report_id=first['id'], expected_revision=1)
    # A later renderer can project a new highlight format without mutating history.
    import src.reports.store as module
    monkeypatch.setattr(module, 'HIGHLIGHT_VERSION', 'future')
    current = store.read('s', first['id'])
    assert current['changes'][0]['highlight_version'] == 'future'
    saved = store.save('s', 'Draft', 'Latest.', [], report_id=first['id'], expected_revision=2)
    assert make_store(tmp_path).read('s', first['id'])['markdown'] == saved['markdown']


def test_exported_report_does_not_expose_internal_history_counters():
    import io
    from docx import Document
    from src.reports.rendering import render_html, render_docx
    r = dict(title='Report', markdown='Body.', citations=[], revision=42)
    assert 'v42' not in render_html(r).decode()
    document = Document(io.BytesIO(render_docx(r)))
    assert all('v42' not in p.text for p in document.paragraphs)


def test_comment_and_discussion_actions_do_not_add_content_history(tmp_path):
    store = make_store(tmp_path)
    r = store.save('s', 'Draft', 'Original.', [])
    c = store.create_comment('s', r['id'], 1, text='Explain this.', anchor=dict(start_line=1, end_line=1, quote='Original.'), author='local')
    store.edit_comment('s', r['id'], c['id'], text='Explain this sentence.')
    store.delete_comment('s', r['id'], c['id'])
    assert [e['revision'] for e in store.history('s', r['id'])] == [1]
    # Compatibility: old journals may contain a no-content-change save.
    store.save('s', 'Draft', 'Original.', [], report_id=r['id'], expected_revision=1, review={'comments': [], 'results': []})
    assert [e['revision'] for e in store.history('s', r['id'])] == [1]
    assert store.read('s', r['id'], 2)['markdown'] == 'Original.'


def test_restoring_review_decisions_rewinds_the_history_even_when_markdown_is_unchanged(tmp_path):
    from tests.test_report_decisions import setup_report, propose, decide
    store, original = setup_report(tmp_path)
    proposed, group = propose(store, original, 'A.', 'B.')
    accepted = decide(store, proposed, group, 'accepted', 'accept')
    restored = store.restore('s', original['id'], proposed['revision'],
                             expected_revision=accepted['revision'], operation_id='restore-pending')
    assert restored['markdown'] == accepted['markdown']
    assert restored['suggestions'][-1]['decision'] == 'pending'
    entry = store.history('s', original['id'])[0]
    assert entry['revision'] == restored['history_revision'] == proposed['revision']
    assert entry['kind'] == 'review'
    assert len(store.history('s', original['id'])) == 2
    assert store.read('s', original['id'], accepted['revision'])['suggestions'][-1]['decision'] == 'accepted'


def test_restoring_drops_comments_and_reply_only_rounds_from_the_discarded_future(tmp_path):
    store = make_store(tmp_path)
    first = store.save('s', 'Draft', 'Original.', [])
    second = store.save('s', 'Draft', 'Second.', [], report_id=first['id'], expected_revision=1)
    c = store.create_comment('s', first['id'], 2, text='Explain.', author='local',
                            anchor=dict(start_line=1, end_line=1, quote='Second.'))
    batch = store.freeze_reviews('s', [dict(report_id=first['id'], revision=2, comment_ids=[c['id']])], '')[0]
    store.apply_review('s', first['id'], batch['id'], [], [dict(comment_id=c['id'], status='unchanged',
        summary='The wording already matches the source.', edit_ids=[])], [])
    pending = store.create_comment('s', first['id'], 2, text='Shorten.', author='local',
                                  anchor=dict(start_line=1, end_line=1, quote='Second.'))
    third = store.save('s', 'Draft', 'Third.', [], report_id=first['id'], expected_revision=2)
    assert store.pending_comments('s')[0]['revision'] == third['revision']
    restored = store.restore('s', first['id'], 1, expected_revision=3, operation_id='back')
    assert store.pending_comments('s') == []
    assert store.comments('s', first['id'], restored['revision']) == []
    assert store.review_history('s', first['id']) == []
    assert restored['suggestions'] == []
    with pytest.raises((ValueError, FileNotFoundError)):
        store.apply_review('s', first['id'], batch['id'], [], [], [])
    with pytest.raises((ValueError, FileNotFoundError)):
        store.edit_comment('s', first['id'], pending['id'], 'Revive the old comment.')


def test_failed_restore_preserves_the_visible_timeline_and_deduplication_state(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    first = store.save('s', 'Draft', 'Original.', [], operation_id='create')
    store.save('s', 'Draft', 'Edited.', [], report_id=first['id'], expected_revision=1, operation_id='edit')
    path = store._dir('s', first['id']) / 'history.json'
    original = path.read_bytes()
    monkeypatch.setattr(store, '_write', lambda *_: (_ for _ in ()).throw(OSError('Disk unavailable')))
    with pytest.raises(OSError):
        store.restore('s', first['id'], 1, expected_revision=2, operation_id='failed')
    assert path.read_bytes() == original
    assert [e['revision'] for e in store.history('s', first['id'])] == [2, 1]
    assert store.saved_operation('s', 'edit', first['id'])['revision'] == 2


def test_decision_history_names_the_specific_revision_and_preserves_replay(tmp_path):
    from tests.test_report_decisions import setup_report, propose, decide
    store, r = setup_report(tmp_path)
    r, group = propose(store, r, 'A.', 'B.', text='Shorten the summary')
    r = decide(store, r, group, 'accepted', 'accept')
    accepted = store.history('s', r['id'])[0]
    assert accepted['decision'] == 'accepted'
    assert accepted['label'] == 'Shorten the summary'
    assert accepted['group_id'] == group['id']
    r, group = propose(store, r, 'B.', 'C.', text='Clarify the wording', parent=group['id'])
    r = decide(store, r, group, 'rejected', 'reject')
    rejected = store.history('s', r['id'])[0]
    assert rejected['decision'] == 'rejected'
    assert rejected['label'] == 'Clarify the wording'
    assert store.read('s', r['id'], accepted['revision'])['markdown'].startswith('B.')
