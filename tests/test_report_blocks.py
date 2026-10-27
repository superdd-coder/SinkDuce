import copy
import json

import pytest

from src.reports.store import ReportStore


@pytest.fixture
def store(tmp_path):
    return ReportStore(tmp_path, session_exists=lambda sid: sid == 's')


def create(store, source='## First\n\nRevenue is 10.\n\n## Second\n\nRevenue is 10.\n'):
    result = store.save('s', 'Draft', source, [])
    assert result.get('blocks'), 'Saved reports need a server-owned block index'
    return result


def block_text(report, block):
    return report['markdown'][block['start']:block['end']]


def edit_for(report, block, before, after, id='e1'):
    return dict(id=id, block_id=block['id'], block_hash=block['hash'], before=before, after=after)


def test_index_covers_real_markdown_blocks_and_keeps_exact_unicode_source(store):
    source = '# Title\n\n第一段 🧭。\n续行。\n\n| Item | Count |\n|---|---|\n| A | 2 |\n\n```svg\n<svg>\n# A diagram label\n</svg>\n```\n\n- First\n- Second\n'
    report = create(store, source)
    assert [b['kind'] for b in report['blocks']] == ['heading', 'paragraph', 'table', 'code', 'list']
    assert len({b['id'] for b in report['blocks']}) == 5
    assert block_text(report, report['blocks'][1]) == '第一段 🧭。\n续行。\n'
    assert report['blocks'][1]['start_line'] == 3 and report['blocks'][1]['end_line'] == 4
    import hashlib
    assert all(b['hash'] == hashlib.sha256(block_text(report, b).encode()).hexdigest() for b in report['blocks'])
    assert report['markdown'] == source


def test_repeated_paragraph_is_edited_by_block_id_and_preserves_both_identities(store):
    before = create(store)
    target = before['blocks'][3]
    updated = store.save_edits('s', before['id'], [edit_for(before, target, 'Revenue is 10.', 'Revenue is 12.')], [], expected_revision=1)
    assert updated['markdown'] == before['markdown'].replace('## Second\n\nRevenue is 10.', '## Second\n\nRevenue is 12.')
    assert [b['id'] for b in updated['blocks']] == [b['id'] for b in before['blocks']]
    assert updated['blocks'][3]['hash'] != target['hash']
    assert updated['edit_log'][0]['block_ids'] == [target['id']]
    assert updated['edit_log'][0]['base_revision'] == 1
    assert store.read('s', before['id'], 1) == before


def test_a_completely_rewritten_paragraph_keeps_its_explicit_identity(store):
    before = create(store, 'Heading.\n\nOriginal argument.\n')
    target = before['blocks'][1]
    after = store.save_edits('s', before['id'], [edit_for(before, target, 'Original argument.', 'An entirely different conclusion.')], [], expected_revision=1)
    assert after['blocks'][1]['id'] == target['id']


def test_explicit_target_identity_wins_when_two_paragraphs_swap_their_wording(store):
    before = create(store, 'First fact.\n\nSecond fact.\n')
    a, b = before['blocks']
    updated = store.save_edits('s', before['id'], [
        edit_for(before, a, 'First fact.', 'Second fact.', id='a'),
        edit_for(before, b, 'Second fact.', 'First fact.', id='b'),
    ], [], expected_revision=1)
    assert [block['id'] for block in updated['blocks']] == [a['id'], b['id']]
    restarted = ReportStore(store.root, session_exists=lambda sid: True)
    assert restarted.read('s', before['id']) == updated


def test_retried_targeted_edit_returns_saved_result_even_after_another_revision(store):
    before = create(store)
    edits = [edit_for(before, before['blocks'][3], 'Revenue is 10.', 'Revenue is 12.')]
    operation = 'round-' + 'x' * 220
    saved = store.save_edits('s', before['id'], edits, [], expected_revision=1, operation_id=operation)
    store.save('s', 'Draft', saved['markdown'] + '\nMore.', [], report_id=before['id'], expected_revision=2)
    retried = store.save_edits('s', before['id'], edits, [], expected_revision=1, operation_id=operation)
    assert retried == saved and store.read('s', before['id'])['revision'] == 3


def test_unrelated_insertion_moves_lines_without_confusing_duplicate_paragraphs(store):
    before = create(store)
    target = before['blocks'][0]
    after = store.save_edits('s', before['id'], [edit_for(before, target, '## First', 'Preface.\n\n## First')], [], expected_revision=1)
    old_repeated = [b for b in before['blocks'] if b['kind'] == 'paragraph']
    new_by_id = {b['id']: b for b in after['blocks']}
    assert all(new_by_id[b['id']]['start_line'] == b['start_line'] + 2 for b in old_repeated)


@pytest.mark.parametrize('bad', ['unknown_id', 'wrong_hash', 'wrong_text', 'missing_id', 'missing_hash'])
def test_edits_reject_unverified_targets_without_saving_a_version(store, bad):
    before = create(store)
    edit = edit_for(before, before['blocks'][1], 'Revenue is 10.', 'Revenue is 12.')
    if bad == 'unknown_id': edit['block_id'] = 'b_invented'
    if bad == 'wrong_hash': edit['block_hash'] = '0' * 64
    if bad == 'wrong_text': edit['before'] = '## Second'
    if bad == 'missing_id': edit.pop('block_id')
    if bad == 'missing_hash': edit.pop('block_hash')
    with pytest.raises(ValueError):
        store.save_edits('s', before['id'], [edit], [], expected_revision=1)
    assert store.read('s', before['id'])['revision'] == 1


def test_matching_old_text_does_not_override_a_stale_base_revision(store):
    before = create(store)
    edit = edit_for(before, before['blocks'][1], 'Revenue is 10.', 'Revenue is 12.')
    store.save('s', 'Draft', before['markdown'] + '\nA later edit.', [], report_id=before['id'], expected_revision=1)
    with pytest.raises(ValueError, match='[Vv]ersion'):
        store.save_edits('s', before['id'], [edit], [], expected_revision=1)


def test_same_phrase_twice_inside_a_block_requires_disambiguating_context(store):
    before = create(store, 'Value 10, followed by value 10.\n')
    target = before['blocks'][0]
    with pytest.raises(ValueError, match='unique'):
        store.save_edits('s', before['id'], [edit_for(before, target, '10', '12')], [], expected_revision=1)
    updated = store.save_edits('s', before['id'], [edit_for(before, target, 'by value 10.', 'by value 12.')], [], expected_revision=1)
    assert updated['markdown'] == 'Value 10, followed by value 12.\n'


def test_overlapping_occurrences_are_not_mistaken_for_a_unique_match(store):
    before = create(store, '哈哈哈\n')
    with pytest.raises(ValueError, match='unique'):
        store.save_edits('s', before['id'], [edit_for(before, before['blocks'][0], '哈哈', '好')], [], expected_revision=1)


def test_a_cross_block_edit_requires_both_verified_boundaries(store):
    before = create(store, 'First paragraph.\n\nSecond paragraph.\n\nKeep.\n')
    first, last = before['blocks'][:2]
    old = 'First paragraph.\n\nSecond paragraph.'
    edit = edit_for(before, first, old, 'Merged conclusion.')
    with pytest.raises(ValueError):
        store.save_edits('s', before['id'], [edit], [], expected_revision=1)
    edit.update(end_block_id=last['id'], end_block_hash=last['hash'])
    after = store.save_edits('s', before['id'], [edit], [], expected_revision=1)
    assert after['markdown'] == 'Merged conclusion.\n\nKeep.\n'
    assert set(after['blocks'][0]['parent_ids']) == {first['id'], last['id']}
    assert after['blocks'][0]['id'] not in {first['id'], last['id']}
    assert after['blocks'][1]['id'] == before['blocks'][2]['id']


def test_splitting_a_block_records_lineage_instead_of_reusing_one_id_twice(store):
    before = create(store, 'A long paragraph.\n')
    old = before['blocks'][0]
    after = store.save_edits('s', before['id'], [edit_for(before, old, 'A long paragraph.', 'First.\n\nSecond.')], [], expected_revision=1)
    assert len(after['blocks']) == 2 and len({b['id'] for b in after['blocks']}) == 2
    assert all(b['parent_ids'] == [old['id']] for b in after['blocks'])


def test_removing_a_paragraph_boundary_retains_both_parents_in_merged_lineage(store):
    before = create(store, 'First.\n\nSecond.\n')
    first, second = before['blocks']
    after = store.save_edits('s', before['id'], [edit_for(before, first, 'First.\n', 'First.')], [], expected_revision=1)
    assert len(after['blocks']) == 1
    assert set(after['blocks'][0]['parent_ids']) == {first['id'], second['id']}


def test_tracking_a_deleted_duplicate_uses_the_explicit_target_instead_of_diff_guessing(store):
    before = create(store, '## Repeated\n## Repeated\n## Keep\n')
    target = before['blocks'][1]
    after = store.save_edits('s', before['id'], [edit_for(before, target, '## Repeated\n', '')], [], expected_revision=1)
    removed = [change for change in after['changes'] if 'Repeated' in change['before']]
    assert len(removed) == 1 and removed[0]['old_start'] == 2
    assert removed[0]['after'] == ''


def test_verified_tracking_preserves_full_markdown_when_an_edit_joins_paragraphs(store):
    before = create(store, 'First.\n\nSecond.\n')
    after = store.save_edits('s', before['id'], [edit_for(before, before['blocks'][0], 'First.\n', 'First.')], [], expected_revision=1)
    assert after['changes'][0]['before'] == before['markdown']
    assert after['changes'][0]['after'] == after['markdown']


def test_old_snapshot_is_projected_deterministically_without_rewriting_its_file(store):
    from src.reports.store import ReportStore
    record = store.save('s', 'Draft', '## Title\n\nOriginal.\n', [])
    path = store.root / 's' / record['id'] / 'revisions' / '1.json'
    legacy = {k: v for k, v in record.items() if k != 'blocks'}
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(legacy))
    store._write(path.parent.parent / 'meta.json', {k:v for k,v in record.items() if k not in {'markdown','citations','blocks'}})
    (path.parent.parent / 'history.json').unlink()
    original_bytes = path.read_bytes()
    a = ReportStore(store.root, session_exists=lambda sid: True).read('s', record['id'])
    b = ReportStore(store.root, session_exists=lambda sid: True).read('s', record['id'])
    assert a['blocks'] == b['blocks'] and a['blocks']
    assert path.read_bytes() == original_bytes


def test_comment_anchor_carries_validated_block_identity_and_stays_frozen(store):
    before = create(store)
    target = before['blocks'][3]
    anchor = dict(start_line=7, end_line=7, quote='Revenue is 10.', block_id=target['id'], block_hash=target['hash'])
    comment = store.create_comment('s', before['id'], 1, text='Check this.', author='local', anchor=anchor)
    assert comment['anchor']['block_id'] == target['id']
    frozen = store.freeze_reviews('s', [{'report_id': before['id'], 'revision': 1, 'comment_ids': [comment['id']]}], '')[0]
    old_anchor = copy.deepcopy(frozen['comments'][0]['anchor'])
    store.release_reviews('s', [frozen])
    first = before['blocks'][0]
    after = store.save_edits('s', before['id'], [edit_for(before, first, '## First', 'Preface.\n\n## First')], [], expected_revision=1)
    pending = store.pending_comments('s')[0]
    assert pending['revision'] == 2 and pending['anchor']['start_line'] == 9
    assert pending['anchor']['block_id'] == target['id'] and not pending.get('anchor_stale')
    assert store.review_batch('s', before['id'], frozen['id'])['comments'][0]['anchor'] == old_anchor
    assert after['blocks'][-1]['id'] == target['id']


def test_comment_cannot_supply_another_blocks_identity_with_correct_text(store):
    before = create(store)
    wrong = before['blocks'][1]
    with pytest.raises(ValueError):
        store.create_comment('s', before['id'], 1, text='Check.', author='local', anchor=dict(start_line=7, end_line=7, quote='Revenue is 10.', block_id=wrong['id'], block_hash=wrong['hash']))


def test_strict_review_targets_a_repeated_block_and_keeps_the_frozen_selection(store):
    before = create(store)
    target = before['blocks'][3]
    comment = store.create_comment('s', before['id'], 1, text='Correct this value.', author='local', anchor=dict(start_line=7, end_line=7, quote='Revenue is 10.'))
    batch = store.freeze_reviews('s', [{'report_id': before['id'], 'revision': 1, 'comment_ids': [comment['id']]}], '')[0]
    edit = edit_for(before, target, 'Revenue is 10.', 'Revenue is 12.')
    outcomes = [dict(comment_id=comment['id'], status='applied', summary='Corrected the selected metric.', edit_ids=['e1'])]
    missing = {k: v for k, v in edit.items() if k not in {'block_id', 'block_hash'}}
    with pytest.raises(ValueError, match='[Bb]lock'):
        store.apply_review('s', before['id'], batch['id'], [missing], outcomes, [], require_block_targets=True)
    after = store.apply_review('s', before['id'], batch['id'], [edit], outcomes, [], require_block_targets=True)
    assert after['blocks'][3]['id'] == target['id']
    assert after['review']['comments'][0]['anchor']['quote'] == 'Revenue is 10.'
    assert after['review']['edits'][0]['block_ids'] == [target['id']]
    assert after['edit_log'][0]['base_revision'] == 1


def test_agent_local_update_uses_verified_blocks_instead_of_rewriting_the_whole_document(tmp_path):
    from src.chatbox.citations import EvidenceRegistry
    from src.db.sessions import SessionStore
    from src.reports.store import for_sessions
    from src.reports.tools import execute_report_tool
    sessions = SessionStore(str(tmp_path / 'sessions.db'))
    sessions.create_session(session_id='s')
    reports = for_sessions(sessions)
    before = create(reports)
    registry = EvidenceRegistry('s')
    read = json.loads(execute_report_tool(sessions, 's', 'get_report', dict(report_id=before['id']), registry))
    assert [{k: v for k, v in b.items() if k != 'text'} for b in read['blocks']] == before['blocks']
    assert all(b['text'] == before['markdown'][b['start']:b['end']] for b in read['blocks'])
    args = dict(report_id=before['id'], expected_revision=1, edits=[edit_for(read, read['blocks'][3], 'Revenue is 10.', 'Revenue is 12.')])
    result = json.loads(execute_report_tool(sessions, 's', 'update_report', args, registry))
    assert result.get('report', {}).get('revision') == 2, result
    updated = reports.read('s', before['id'])
    assert updated['markdown'].count('Revenue is 10.') == 1
    assert updated['markdown'].count('Revenue is 12.') == 1
    assert updated['edit_log'][0]['block_ids'] == [read['blocks'][3]['id']]


def test_new_agent_whole_document_rewrite_creates_a_separate_report(tmp_path):
    from src.chatbox.citations import EvidenceRegistry
    from src.db.sessions import SessionStore
    from src.reports.store import for_sessions
    from src.reports.tools import execute_report_tool
    sessions = SessionStore(str(tmp_path / 'sessions.db'))
    sessions.create_session(session_id='s')
    reports = for_sessions(sessions)
    before = create(reports)
    args = dict(report_id=before['id'], expected_revision=1, markdown='A new direction.')
    registry = EvidenceRegistry('s')
    result = json.loads(execute_report_tool(sessions, 's', 'update_report', args, registry))
    assert 'error' in result and reports.read('s', before['id'])['revision'] == 1
    result = json.loads(execute_report_tool(sessions, 's', 'update_report', {**args, 'mode':'rewrite'}, registry))
    assert result['report']['id'] != before['id']
    assert result['report']['revision'] == 1
    assert reports.read('s', before['id']) == before
    assert reports.read('s', result['report']['id'])['markdown'] == 'A new direction.'


def test_whole_report_rewrite_retry_returns_the_same_copy_and_preserves_original_history(tmp_path):
    from src.chatbox.citations import EvidenceRegistry
    from src.db.sessions import SessionStore
    from src.reports.store import for_sessions
    from src.reports.tools import execute_report_tool

    sessions = SessionStore(str(tmp_path / 'sessions.db'))
    sessions.create_session(session_id='s')
    reports = for_sessions(sessions)
    original = reports.save('s', 'Sample review', 'Original assessment.', [])
    journal = (reports._dir('s', original['id']) / 'history.json').read_bytes()
    args = dict(report_id=original['id'], expected_revision=1, mode='rewrite', title='Reworked assessment', markdown='A different structure and conclusion.')
    outputs = [json.loads(execute_report_tool(sessions, 's', 'update_report', args, EvidenceRegistry('s'), operation_id='rewrite-copy')) for _ in range(2)]
    assert outputs[0]['report']['id'] != original['id']
    assert outputs[0]['report']['id'] == outputs[1]['report']['id']
    assert len(reports.list('s')) == 2
    assert reports.read('s', original['id']) == original
    assert (reports._dir('s', original['id']) / 'history.json').read_bytes() == journal
    rewritten = reports.read('s', outputs[0]['report']['id'])
    assert rewritten['suggestions'] == [] and rewritten['revision'] == 1


def test_agent_tool_retry_preserves_the_original_targeted_edit_result(tmp_path):
    from src.chatbox.citations import EvidenceRegistry
    from src.db.sessions import SessionStore
    from src.reports.store import for_sessions
    from src.reports.tools import execute_report_tool
    sessions = SessionStore(str(tmp_path / 'sessions.db'))
    sessions.create_session(session_id='s')
    reports = for_sessions(sessions)
    before = create(reports)
    args = dict(report_id=before['id'], expected_revision=1, edits=[edit_for(before, before['blocks'][3], 'Revenue is 10.', 'Revenue is 12.')])
    registry = EvidenceRegistry('s')
    first = json.loads(execute_report_tool(sessions, 's', 'update_report', args, registry, 'retry-call'))
    second = json.loads(execute_report_tool(sessions, 's', 'update_report', args, registry, 'retry-call'))
    assert first == second and first['report']['revision'] == 2
    assert len(reports.versions('s', before['id'])) == 2


def test_update_tool_cannot_create_a_report_when_its_target_is_missing(tmp_path):
    from src.chatbox.citations import EvidenceRegistry
    from src.db.sessions import SessionStore
    from src.reports.store import for_sessions
    from src.reports.tools import execute_report_tool
    sessions = SessionStore(str(tmp_path / 'sessions.db'))
    sessions.create_session(session_id='s')
    result = json.loads(execute_report_tool(sessions, 's', 'update_report', dict(mode='rewrite', markdown='A new draft.', title='Draft', expected_revision=1), EvidenceRegistry('s')))
    assert 'error' in result and for_sessions(sessions).list('s') == []


def test_new_agent_review_calls_cannot_bypass_block_identity_checks(tmp_path):
    from src.chatbox.citations import EvidenceRegistry
    from src.db.sessions import SessionStore
    from src.reports.store import for_sessions
    from src.reports.tools import execute_report_tool
    sessions = SessionStore(str(tmp_path / 'sessions.db'))
    sessions.create_session(session_id='s')
    reports = for_sessions(sessions)
    before = reports.save('s', 'Draft', 'Unique sentence.', [])
    c = reports.create_comment('s', before['id'], 1, text='Revise.', author='local', anchor=dict(start_line=1, end_line=1, quote='Unique sentence.'))
    batches = reports.freeze_reviews('s', [dict(report_id=before['id'], revision=1, comment_ids=[c['id']])], '')
    args = dict(report_id=before['id'], batch_id=batches[0]['id'], edits=[dict(id='e1', before='Unique sentence.', after='Another sentence.')], results=[dict(comment_id=c['id'], status='applied', summary='Reworded for clarity.', edit_ids=['e1'])])
    result = json.loads(execute_report_tool(sessions, 's', 'revise_report', args, EvidenceRegistry('s'), review_batches=batches))
    assert 'error' in result
    assert reports.read('s', before['id'])['revision'] == 1


def test_report_tools_describe_required_block_targets_to_the_model():
    from src.prompts import CHAT_REPORT_TOOLS
    tools = {tool['function']['name']: tool['function'] for tool in CHAT_REPORT_TOOLS}
    for name in ('update_report', 'revise_report'):
        edit = tools[name]['parameters']['properties']['edits']['items']
        assert {'id', 'after'} <= set(edit['required'])
        assert {'block_id', 'block_hash', 'before'} <= set(edit['anyOf'][0]['required'])
        assert {'end_block_id', 'end_block_hash'} <= set(edit['properties'])
    assert 'rewrite' in tools['update_report']['parameters']['properties']['mode']['enum']


def test_section_edit_owns_descendants_captions_and_lists_until_next_peer():
    from src.reports.blocks import index_blocks, resolve_edits, apply_resolved
    source='## Keep\n\nIntro.\n\n## Remove\n\n### Detail\n\n| Name | Value |\n|---|---|\n| A | 1 |\n\nSource caption.\n\nInterpretation\n\n- Old point\n\n## Next\n\nUnrelated.'
    r=dict(title='Draft',revision=1,markdown=source,blocks=index_blocks(source,'test'))
    b=next(b for b in r['blocks'] if source[b['start']:b['end']].startswith('## Remove'))
    edits=resolve_edits(r,[dict(id='e',mode='section',block_id=b['id'],block_hash=b['hash'],after='')])
    assert apply_resolved(source,edits)=='## Keep\n\nIntro.\n\n## Next\n\nUnrelated.'
    assert edits[0]['mode']=='block_range'
    # Replacement preserves the following peer's Markdown boundary.
    edits=resolve_edits(r,[dict(id='e',mode='section',block_id=b['id'],block_hash=b['hash'],after='## Focus\n\nReframed content.')])
    assert 'Reframed content.\n\n## Next' in apply_resolved(source,edits)


def test_section_edit_requires_a_verified_heading_and_handles_setext_subsections():
    from src.reports.blocks import index_blocks, resolve_edits, apply_resolved
    source='Chapter\n=======\n\nDetail\n------\n\nText.\n\n## Peer\n\nKeep.'
    r=dict(title='Draft',revision=1,markdown=source,blocks=index_blocks(source,'test'))
    b=r['blocks'][1]
    edits=resolve_edits(r,[dict(id='e',mode='section',block_id=b['id'],block_hash=b['hash'],after='')])
    assert apply_resolved(source,edits)=='Chapter\n=======\n\n## Peer\n\nKeep.'
    paragraph=r['blocks'][2]
    with pytest.raises(ValueError,match='heading'):
        resolve_edits(r,[dict(id='e',mode='section',block_id=paragraph['id'],block_hash=paragraph['hash'],after='')])
    with pytest.raises(ValueError,match='changed'):
        resolve_edits(r,[dict(id='e',mode='section',block_id=b['id'],block_hash='stale',after='')])
