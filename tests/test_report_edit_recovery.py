import json
from types import SimpleNamespace

from src.chatbox.citations import EvidenceRegistry
from src.reports.blocks import apply_resolved, resolve_edits
from src.reports.store import ReportStore
from src.reports.tools import execute_report_tool


def test_exact_block_deletion_can_include_its_trailing_blank_separator(tmp_path):
    store = ReportStore(tmp_path, session_exists=lambda _: True)
    r = store.save('s', 'Draft', '> Remove this note.\n\n## Keep\n\nKeep this paragraph.\n', [])
    block = r['blocks'][0]
    revised = store.save_edits('s', r['id'], [dict(id='note', block_id=block['id'], block_hash=block['hash'],
        before='> Remove this note.\n\n', after='')], [], expected_revision=1)
    assert revised['markdown'] == '## Keep\n\nKeep this paragraph.\n'
    assert any('Remove this note.' in c['before'] for c in revised['changes'])


def test_verified_block_range_edit_does_not_require_retyping_a_long_section(tmp_path):
    store = ReportStore(tmp_path, session_exists=lambda _: True)
    r = store.save('s', 'Draft', '## Remove\n\n' + 'Repeated paragraph.\n\n' * 100 + '## Keep\n\nKeep this.\n', [])
    first, last = r['blocks'][0], r['blocks'][-3]
    edits = resolve_edits(r, [dict(id='section', mode='block_range', block_id=first['id'], block_hash=first['hash'],
        end_block_id=last['id'], end_block_hash=last['hash'], after='')])
    assert apply_resolved(r['markdown'], edits) == '## Keep\n\nKeep this.\n'
    revised = store.save_edits('s', r['id'], [dict(id='section', mode='block_range', block_id=first['id'], block_hash=first['hash'],
        end_block_id=last['id'], end_block_hash=last['hash'], after='')], [], expected_revision=1)
    assert revised['changes'] and len(revised['blocks']) == 2


def test_complete_block_replacement_preserves_separator_and_stable_identity(tmp_path):
    store = ReportStore(tmp_path, session_exists=lambda _: True)
    r = store.save('s', 'Draft', '## Summary\n\nAmount is 10.\n\n## Detail\n\nKeep.\n', [])
    target = r['blocks'][1]
    revised = store.save_edits('s', r['id'], [dict(id='amount', mode='block_range',
        block_id=target['id'], block_hash=target['hash'], after='Amount is 12.')], [], expected_revision=1)
    assert revised['markdown'] == '## Summary\n\nAmount is 12.\n\n## Detail\n\nKeep.\n'
    assert revised['blocks'][1]['id'] == target['id']
    assert revised['blocks'][2]['id'] == r['blocks'][2]['id']
    assert revised['blocks'][1]['hash'] != target['hash']


def test_tool_returns_each_bad_target_and_exact_recovery_in_one_response(tmp_path):
    sessions = SimpleNamespace(_db_path=tmp_path/'sessions.db', get_session=lambda _: {'id':'s'})
    from src.reports.store import for_sessions
    store = for_sessions(sessions)
    r = store.save('s', 'Draft', '> Note.\n\n## Remove\n\nSection content.\n\n## Keep\n\nKeep this.\n', [])
    note, section, body, keep, _ = r['blocks']
    edits = [dict(id='note', mode='block', block_id=note['id'], block_hash=note['hash'], before='> Note.\n\n', after=''),
        dict(id='section', mode='block', block_id=keep['id'], block_hash=keep['hash'], before='## Remove\n\nSection content.\n\n', after=''),
        dict(id='rename', mode='block', block_id=section['id'], block_hash=section['hash'], before='## Keep', after='## Retained')]
    result = json.loads(execute_report_tool(sessions,'s','update_report',dict(report_id=r['id'],expected_revision=1,edits=edits),EvidenceRegistry('s')))
    assert result['saved'] is False
    assert {e['id'] for e in result['edit_errors']} == {'section', 'rename'}
    suggested = {e['id']: e['suggested_edit'] for e in result['edit_errors']}
    assert suggested['section']['block_id'] == section['id']
    assert suggested['section']['end_block_id'] == body['id']
    fixed = [suggested.get(e['id'], e) for e in edits]
    saved = json.loads(execute_report_tool(sessions,'s','update_report',dict(report_id=r['id'],expected_revision=1,edits=fixed),EvidenceRegistry('s')))
    assert saved['report']['revision'] == 2, saved
    revised = store.read('s', r['id'])
    assert revised['markdown'] == '## Retained\n\nKeep this.\n'
    assert revised['changes']


def test_ambiguous_wrong_target_never_returns_a_guessed_recovery(tmp_path):
    sessions = SimpleNamespace(_db_path=tmp_path/'sessions.db', get_session=lambda _: {'id':'s'})
    from src.reports.store import for_sessions
    store = for_sessions(sessions)
    r = store.save('s','Draft','Repeated.\n\nRepeated.\n\nOther.\n',[])
    block = r['blocks'][-1]
    args = dict(report_id=r['id'],expected_revision=1,edits=[dict(id='ambiguous',before='Repeated.',after='Changed.',block_id=block['id'],block_hash=block['hash'])])
    result = json.loads(execute_report_tool(sessions,'s','update_report',args,EvidenceRegistry('s')))
    error = result['edit_errors'][0]
    assert error['id'] == 'ambiguous' and 'suggested_edit' not in error
    assert len(error['candidates']) == 2
    assert store.read('s',r['id'])['revision'] == 1
