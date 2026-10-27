import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.chatbox.agent import ChatboxAgent
from src.chatbox.citations import EvidenceRegistry
from src.db.sessions import SessionStore
from src.reports.store import ReportStore, for_sessions
from src.reports.tools import execute_report_tool
from tests.test_note_create_confirm import _text_round, _tool_round


@pytest.fixture(autouse=True)
def isolated_coverage_model(monkeypatch):
    monkeypatch.setattr('src.reports.validation.audit_review', lambda *a: [])
    monkeypatch.setattr('src.reports.validation.repair_review', lambda llm,report,batch,edits,results,issues: dict(edits=edits,results=results))


@pytest.mark.parametrize('supplied_batch', ['old', 'omitted'])
def test_review_uses_the_server_current_task_instead_of_copied_bookkeeping(tmp_path, supplied_batch):
    sessions = SimpleNamespace(_db_path=tmp_path/'sessions.db', get_session=lambda _: {'id':'s'})
    reports = for_sessions(sessions)
    r = reports.save('s', 'Draft', 'Original.', [])
    c = reports.create_comment('s', r['id'], 1, text='Rewrite this.', anchor=dict(start_line=1,end_line=1,quote='Original.'), author='local')
    request = [dict(report_id=r['id'],revision=1,comment_ids=[c['id']])]
    old = reports.freeze_reviews('s', request, '')[0]
    reports.release_reviews('s', [old])
    current = reports.freeze_reviews('s', request, '')[0]
    target = r['blocks'][0]
    args = dict(report_id=r['id'], edits=[dict(id='e',block_id=target['id'],block_hash=target['hash'],before='Original.',after='Revised.')],
                results=[dict(comment_id=c['id'],status='applied',summary='Reworded the paragraph for clarity.',edit_ids=['e'])])
    if supplied_batch == 'old':
        args['batch_id'] = old['id']
    result = json.loads(execute_report_tool(sessions,'s','revise_report',args,EvidenceRegistry('s'),review_batches=[current]))
    assert result.get('report',{}).get('revision') == 2, result
    assert result['review_result']['id'] == current['id']
    assert reports.review_batch('s',r['id'],old['id'])['status'] == 'interrupted'


@pytest.mark.parametrize('status', ['partial','needs_clarification'])
def test_processed_round_leaves_current_comments_even_when_it_needs_further_discussion(tmp_path, status):
    store = ReportStore(tmp_path,session_exists=lambda _:True)
    r = store.save('s','Draft','Original.\n\nUnrelated.',[])
    c = store.create_comment('s',r['id'],1,text='Review this.',author='local',anchor=dict(start_line=1,end_line=1,quote='Original.'))
    b = store.freeze_reviews('s',[dict(report_id=r['id'],revision=1,comment_ids=[c['id']])],'')[0]
    edits = [dict(id='e',before='Original.',after='Revised.')] if status=='partial' else []
    outcomes = [dict(comment_id=c['id'],status=status,summary='Revised the available argument; the supporting measurement needs evidence.',edit_ids=['e'] if edits else [])]
    saved = store.apply_review('s',r['id'],b['id'],edits,outcomes,[])
    assert store.pending_comments('s') == []
    assert store.comments('s',r['id'],saved['revision']) == []
    history = store.review_history('s',r['id'])
    assert history[0]['results'][0]['status'] == status
    assert history[0]['comments'][0]['id'] == c['id']
    # Repair the old pending-state projection without changing the saved report.
    state = store._review_state('s',r['id'])
    state['comments'][0]['state'] = 'pending'
    store._write_reviews('s',r['id'],state)
    assert store.pending_comments('s') == []


def test_failed_review_never_streams_or_persists_a_fabricated_success(tmp_path, monkeypatch):
    from src.chatbox import agent as module
    sessions = SessionStore(str(tmp_path/'sessions.db')); sessions.create_session(session_id='s')
    reports = for_sessions(sessions); r = reports.save('s','Draft','Original.',[])
    comments = [reports.create_comment('s',r['id'],1,text=f'Edit request {i}',author='local',anchor=dict(start_line=1,end_line=1,quote='Original.')) for i in range(3)]
    llm = MagicMock()
    llm._client.chat.completions.create.side_effect = lambda **kwargs: _text_round('EVERYTHING_SAVED all three requests are finished.')
    agent = ChatboxAgent(session_store=sessions,chat_llm=llm,agentic_service=MagicMock())
    monkeypatch.setattr(agent,'_build_catalog_text',lambda *a,**k:'')
    monkeypatch.setattr(agent,'_has_meetings',lambda *a,**k:False)
    monkeypatch.setattr(module,'_build_surface_context',lambda *a,**k:'')
    monkeypatch.setattr(module,'_MAX_TOOL_ROUNDS',3)
    async def run():
        return [e async for e in agent.chat_stream('s','Revise these comments.',thinking=False,mode='direct',report_reviews=[dict(report_id=r['id'],revision=1,comment_ids=[c['id'] for c in comments])])]
    events = asyncio.run(run())
    text = ''.join(e.get('content','') for e in events if e['type']=='token')
    assert 'EVERYTHING_SAVED' not in text
    assert '3' in text and 'saved' in text.lower()
    done = next(e for e in events if e['type']=='done')
    assert done['report_review_status']['unsaved'] == 3
    final = sessions.get_messages('s')[-1]
    assert final.content == text and 'EVERYTHING_SAVED' not in final.content
    assert final.metadata['report_review_status']['unsaved'] == 3
    assert len(reports.pending_comments('s')) == 3


def test_mixed_outcomes_are_reported_truthfully_and_all_processed_comments_move_to_tracking(tmp_path, monkeypatch):
    from src.chatbox import agent as module
    sessions = SessionStore(str(tmp_path/'sessions.db'));sessions.create_session(session_id='s')
    reports=for_sessions(sessions);r=reports.save('s','Draft','Original.',[])
    comments=[reports.create_comment('s',r['id'],1,text=f'Review request {i}',author='local',anchor=dict(start_line=1,end_line=1,quote='Original.')) for i in range(3)]
    target=r['blocks'][0]
    args=dict(report_id=r['id'],edits=[dict(id='e',block_id=target['id'],block_hash=target['hash'],before='Original.',after='Revised.')],results=[
        dict(comment_id=comments[0]['id'],status='applied',summary='Revised the argument to address the request.',edit_ids=['e']),
        dict(comment_id=comments[1]['id'],status='unchanged',summary='Verified the wording already matches the source.',edit_ids=[]),
        dict(comment_id=comments[2]['id'],status='needs_clarification',summary='The requested figure requires a source measurement.',edit_ids=[])])
    llm=MagicMock();calls=0
    def response(**kwargs):
        nonlocal calls
        calls+=1
        assert 'update_report' not in {t['function']['name'] for t in kwargs['tools']}
        tail=kwargs['messages'][-1]['content']
        assert 'current_report_review_state' in tail
        if calls==1:return _tool_round('revise_report',json.dumps(args))
        return _text_round('ALL_THREE_EDITED Everything is finished.')
    llm._client.chat.completions.create.side_effect=response
    agent=ChatboxAgent(session_store=sessions,chat_llm=llm,agentic_service=MagicMock())
    monkeypatch.setattr(agent,'_build_catalog_text',lambda *a,**k:'')
    monkeypatch.setattr(agent,'_has_meetings',lambda *a,**k:False)
    monkeypatch.setattr(module,'_build_surface_context',lambda *a,**k:'')
    async def run():
        return [e async for e in agent.chat_stream('s','Address all comments.',thinking=False,mode='direct',report_reviews=[dict(report_id=r['id'],revision=1,comment_ids=[c['id'] for c in comments])])]
    events=asyncio.run(run());text=''.join(e.get('content','') for e in events if e['type']=='token')
    assert 'ALL_THREE_EDITED' not in text
    done=next(e for e in events if e['type']=='done')
    assert done['report_review_status']['applied']==1 and done['report_review_status']['needs_clarification']==1
    assert done['report_review_status']['unchanged']==1 and done['report_review_status']['unsaved']==0
    assert not reports.pending_comments('s')
    assert len(reports.review_history('s',r['id'])[0]['results'])==3


def test_old_failed_tool_arguments_and_false_success_are_archived_out_of_model_context():
    from src.reports.completion import project_report_history
    old_call=dict(id='old-call',type='function',function=dict(name='revise_report',arguments='{"batch_id":"stale-batch","edits":[{"before":"huge old body"}]}'))
    current_call=dict(id='new-call',type='function',function=dict(name='revise_report',arguments='{"report_id":"r","edits":[]}'))
    messages=[dict(role='user',content='Earlier review'),dict(role='assistant',content=None,tool_calls=[old_call]),
              dict(role='tool',tool_call_id='old-call',content='{"error":"Rejected edit","saved":false}'),
              dict(role='assistant',content='FALSE_SUCCESS all changes saved. Please confirm again.'),
              dict(role='user',content='Retry the three comments'),dict(role='assistant',content=None,tool_calls=[current_call]),
              dict(role='tool',tool_call_id='new-call',content='{"error":"Current edit needs correction"}')]
    original=json.dumps(messages)
    projected=project_report_history(messages)
    serialized=json.dumps(projected)
    assert 'stale-batch' not in serialized and 'huge old body' not in serialized and 'FALSE_SUCCESS' not in serialized
    assert 'Rejected edit' in serialized and 'saved' in serialized
    assert projected[-2:] == messages[-2:]
    assert json.dumps(messages)==original


def test_legacy_followup_text_is_kept_when_retiring_processed_rounds(tmp_path):
    store=ReportStore(tmp_path,session_exists=lambda _:True)
    r=store.save('s','Draft','Original.',[])
    c=store.create_comment('s',r['id'],1,text='Review this claim.',author='local',anchor=dict(start_line=1,end_line=1,quote='Original.'))
    b=store.freeze_reviews('s',[dict(report_id=r['id'],revision=1,comment_ids=[c['id']])],'')[0]
    store.apply_review('s',r['id'],b['id'],[],[dict(comment_id=c['id'],status='needs_clarification',summary='The measurement requires source data.',edit_ids=[])],[])
    state=store._review_state('s',r['id'])
    state['comments'][0].update(state='pending',text='Here is a new follow-up request.')
    store._write_reviews('s',r['id'],state)
    assert store.pending_comments('s')[0]['text']=='Here is a new follow-up request.'
    assert store.review_history('s',r['id'])[0]['comments'][0]['text']=='Review this claim.'


def test_report_tool_pairs_block_identity_with_exact_source_without_persisting_projection(tmp_path):
    sessions=SimpleNamespace(_db_path=tmp_path/'sessions.db',get_session=lambda _:dict(id='s'))
    store=for_sessions(sessions)
    r=store.save('s','Draft','## Section\n\nRepeated phrase.\n\nRepeated phrase.',[])
    payload=json.loads(execute_report_tool(sessions,'s','get_report',dict(report_id=r['id']),EvidenceRegistry('s')))
    assert all(b.get('text')==r['markdown'][b['start']:b['end']] for b in payload['blocks'])
    assert payload['blocks'][1]['id']!=payload['blocks'][2]['id']
    assert all('text' not in b for b in store.read('s',r['id'])['blocks'])


def test_latest_save_feedback_is_refreshed_after_the_tool_trajectory(tmp_path,monkeypatch):
    from src.chatbox import agent as module
    sessions=SessionStore(str(tmp_path/'sessions.db'));sessions.create_session(session_id='s')
    store=for_sessions(sessions);r=store.save('s','Draft','Original.',[])
    c=store.create_comment('s',r['id'],1,text='Revise this passage.',author='local',anchor=dict(start_line=1,end_line=1,quote='Original.'))
    b=r['blocks'][0]
    first=dict(report_id=r['id'],edits=[dict(id='e',mode='block_range',block_id=b['id'],block_hash=b['hash'],after='Incomplete.')],results=[dict(comment_id=c['id'],status='applied',summary='Revised for clarity.',edit_ids=['e'])])
    second=json.loads(json.dumps(first));second['edits'][0]['after']='Complete.'
    monkeypatch.setattr('src.reports.validation.audit_review',lambda llm,parent,task,proposal,results:[] if proposal['markdown']=='Complete.' else [dict(comment_id=c['id'],required_action='Finish the related argument before saving.')])
    llm=MagicMock();calls=0
    def response(**kwargs):
        nonlocal calls
        calls+=1
        if calls==1:return _tool_round('revise_report',json.dumps(first))
        if calls==2:
            tail=kwargs['messages'][-1]['content']
            assert 'latest_save_failures' in tail and 'Finish the related argument before saving.' in tail
            return _tool_round('revise_report',json.dumps(second))
        return _text_round('Finished.')
    llm._client.chat.completions.create.side_effect=response
    agent=ChatboxAgent(session_store=sessions,chat_llm=llm,agentic_service=MagicMock())
    monkeypatch.setattr(agent,'_build_catalog_text',lambda *a,**k:'')
    monkeypatch.setattr(agent,'_has_meetings',lambda *a,**k:False)
    monkeypatch.setattr(module,'_build_surface_context',lambda *a,**k:'')
    async def run():
        return [e async for e in agent.chat_stream('s','Revise.',thinking=False,mode='direct',report_reviews=[dict(report_id=r['id'],revision=1,comment_ids=[c['id']])])]
    events=asyncio.run(run())
    assert any(e['type']=='done' for e in events)
    assert store.read('s',r['id'])['markdown']=='Complete.'


def test_review_retry_focuses_on_current_source_latest_proposal_and_its_feedback():
    from src.reports.completion import focus_review_retry
    def pair(name,tid,content):
        return [dict(role='assistant',content=None,tool_calls=[dict(id=tid,type='function',function=dict(name=name,arguments='{"report_id":"r"}'))]),dict(role='tool',tool_call_id=tid,content=content)]
    messages=[dict(role='system',content='Current frozen task and tool contract.'),dict(role='user',content='Old request.'),dict(role='assistant',content='A prior uncommitted claim.'),dict(role='user',content='Address my current submitted comments.'),
              *pair('get_report','read-old','old source'),*pair('revise_report','failure-old','old feedback'),
              *pair('get_report','read-new','exact current source with blocks'),*pair('read_chat_attachment','evidence','supporting evidence'),
              *pair('revise_report','failure-new','{"error":"Finish the related section","saved":false}')]
    original=json.dumps(messages)
    focused=focus_review_retry(messages)
    encoded=json.dumps(focused)
    assert 'A prior uncommitted claim' not in encoded and 'old source' not in encoded and 'old feedback' not in encoded
    assert 'Current frozen task' in encoded and 'Address my current' in encoded
    assert 'exact current source' in encoded and 'Finish the related section' in encoded and 'supporting evidence' in encoded
    assert json.dumps(messages)==original
