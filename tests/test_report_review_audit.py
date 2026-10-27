import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.chatbox.citations import EvidenceRegistry
from src.reports.store import for_sessions
from src.reports.tools import execute_report_tool


def test_incomplete_proposal_is_rejected_before_any_report_or_outcome_is_committed(tmp_path):
    sessions=SimpleNamespace(_db_path=tmp_path/'sessions.db',get_session=lambda _:dict(id='s'))
    store=for_sessions(sessions);r=store.save('s','Portfolio review','Portfolio summary.\n\nProject detail.',[])
    c=store.create_comment('s',r['id'],1,text='Focus the report on the selected project.',author='local',anchor=dict(start_line=3,end_line=3,quote='Project detail.'))
    batch=store.freeze_reviews('s',[dict(report_id=r['id'],revision=1,comment_ids=[c['id']])],'')[0]
    b=r['blocks'][-1]
    args=dict(report_id=r['id'],edits=[dict(id='detail',block_id=b['id'],block_hash=b['hash'],before='Project detail.',after='Selected project detail.')],
              results=[dict(comment_id=c['id'],status='partial',summary='Updated the detail; title and summary remain to be aligned.',edit_ids=['detail'])])
    def validate(parent,task,proposed,results):
        assert parent['revision']==1 and task['id']==batch['id']
        assert proposed['markdown'].endswith('Selected project detail.')
        return [dict(comment_id=c['id'],required_action='Align the title and introduction with the requested report scope.')]
    response=json.loads(execute_report_tool(sessions,'s','revise_report',args,EvidenceRegistry('s'),review_batches=[batch],review_validator=validate))
    assert response['saved'] is False and response['review_issues'][0]['comment_id']==c['id']
    assert store.read('s',r['id'])['revision']==1
    assert store.review_batch('s',r['id'],batch['id'])['status']=='running'


def test_auditor_receives_full_scope_and_requires_actionable_feedback_for_rejection():
    from src.reports.validation import audit_review
    llm=MagicMock();llm.generate.return_value=json.dumps(dict(ok=False,issues=[dict(comment_id='c',required_action='Update the introductory summary as requested.',proposed_excerpt='Original full introduction.')]))
    report=dict(title='Portfolio',markdown='Original full introduction.\n\nProject detail.')
    batch=dict(instruction='',comments=[dict(id='c',text='Focus on the selected project.',anchor=dict(quote='Project detail.'))])
    proposed=dict(title='Portfolio',markdown='Original full introduction.\n\nSelected project detail.')
    issues=audit_review(llm,report,batch,proposed,[dict(comment_id='c',status='partial',summary='Only changed the detail.')])
    assert issues[0]['comment_id']=='c'
    prompt=llm.generate.call_args.args[0]
    assert 'Original full introduction.' in prompt and 'Selected project detail.' in prompt
    llm.generate.return_value='unstructured response'
    with pytest.raises(ValueError,match='validation'):
        audit_review(llm,report,batch,proposed,[])


def test_audit_accepts_answer_only_and_genuinely_blocked_outcomes_without_forcing_edits():
    from src.reports.validation import audit_review
    llm=MagicMock();llm.generate.return_value='```json\n{"ok":true,"issues":[]}\n```'
    report=dict(title='Draft',markdown='A supported claim.')
    batch=dict(instruction='',comments=[dict(id='c',text='Explain the basis.',anchor=dict(quote='A supported claim.'))])
    assert audit_review(llm,report,batch,report,[dict(comment_id='c',status='unchanged',summary='Explained the evidence.')])==[]


def test_failed_coverage_can_be_repaired_and_revalidated_before_one_atomic_save(tmp_path):
    sessions=SimpleNamespace(_db_path=tmp_path/'sessions.db',get_session=lambda _:dict(id='s'))
    store=for_sessions(sessions);r=store.save('s','Draft','Original.',[])
    c=store.create_comment('s',r['id'],1,text='Clarify this.',author='local',anchor=dict(start_line=1,end_line=1,quote='Original.'))
    batch=store.freeze_reviews('s',[dict(report_id=r['id'],revision=1,comment_ids=[c['id']])],'')[0]
    b=r['blocks'][0]
    args=dict(report_id=r['id'],edits=[dict(id='e',mode='block_range',block_id=b['id'],block_hash=b['hash'],after='Incomplete.')],results=[dict(comment_id=c['id'],status='applied',summary='Revised for clarity.',edit_ids=['e'])])
    seen=[]
    def validate(parent,task,proposed,results):
        seen.append(proposed['markdown'])
        return [] if proposed['markdown']=='Complete.' else [dict(comment_id=c['id'],required_action='Complete the argument.')]
    def repair(parent,task,edits,results,issues):
        assert parent['markdown']=='Original.' and issues[0]['required_action']=='Complete the argument.'
        assert store.read('s',r['id'])['revision']==1
        return dict(edits=[{**edits[0],'after':'Complete.'}],results=results)
    response=json.loads(execute_report_tool(sessions,'s','revise_report',args,EvidenceRegistry('s'),review_batches=[batch],review_validator=validate,review_repairer=repair))
    assert response['report']['revision']==2, response
    assert seen==['Incomplete.','Complete.']
    assert store.read('s',r['id'])['markdown']=='Complete.'
    assert store.review_batch('s',r['id'],batch['id'])['status']=='completed'
    assert len(store.history('s',r['id']))==2


def test_repair_receives_exact_block_source_and_returns_only_a_structured_proposal():
    from src.reports.validation import repair_review
    llm=MagicMock();llm.generate.return_value='```json\n{"edits":[],"results":[]}\n```'
    report=dict(title='Draft',markdown='## Section\n\nText.',blocks=[dict(id='b',hash='h',kind='heading',start=0,end=11)])
    batch=dict(instruction='Keep the purpose clear.',comments=[dict(id='c',text='Revise the section.',anchor=dict(quote='Section'))])
    assert repair_review(llm,report,batch,[],[],[dict(comment_id='c',required_action='Include the explanation.')])==dict(edits=[],results=[])
    prompt=llm.generate.call_args.args[0]
    assert 'Include the explanation.' in prompt and 'Keep the purpose clear.' in prompt and '"text": "## Section' in prompt
    llm.generate.return_value='{"edits":"unstructured","results":[]}'
    with pytest.raises(ValueError,match='repair'):
        repair_review(llm,report,batch,[],[],[])


def test_audit_rechecks_a_rejection_against_the_actual_proposal_without_old_draft_noise():
    from src.reports.validation import audit_review
    report=dict(title='Draft',markdown='## Overview\n\nFirst fact. Second fact.')
    batch=dict(id='batch',instruction='',comments=[dict(id='c',text='Combine this into one sentence.',
        anchor=dict(start_line=3,end_line=3,quote='First fact. Second fact.'),
        discussion_history=[dict(comments=[dict(text='Shorten this.')],summary='Kept two sentences.',decision='accepted',
            citations=[dict(quote='Unrelated historical evidence '*1000)],patches=[dict(before='Old text '*1000,after='Other old text '*1000)])])])
    proposed=dict(title='Draft',markdown='## Overview\n\nFirst fact and second fact.')
    llm=MagicMock();llm.generate.side_effect=[
        json.dumps(dict(ok=False,issues=[dict(comment_id='c',required_action='Combine the two sentences.',
            proposed_excerpt='First fact. Second fact.')])),
        json.dumps(dict(ok=True,issues=[])),
    ]
    assert audit_review(llm,report,batch,proposed,[dict(comment_id='c',status='applied',summary='Combined the facts.')])==[]
    assert llm.generate.call_count==2
    first=llm.generate.call_args_list[0].args[0]
    confirmation=llm.generate.call_args_list[1].args[0]
    assert 'First fact and second fact.' in first and 'First fact and second fact.' in confirmation
    assert 'Unrelated historical evidence' not in first and 'Other old text' not in first
    assert 'Kept two sentences.' not in confirmation
    assert '"before"' not in confirmation, 'The confirmation judges the current candidate, without another old document'


def test_audit_requires_confirmed_feedback_to_quote_the_current_proposal():
    from src.reports.validation import audit_review
    report=dict(title='Draft',markdown='Old first. Old second.')
    batch=dict(instruction='',comments=[dict(id='c',text='One sentence.',anchor=dict(quote=report['markdown']))])
    llm=MagicMock();llm.generate.return_value=json.dumps(dict(ok=False,issues=[dict(comment_id='c',
        required_action='Combine the two sentences.',proposed_excerpt='Old first. Old second.')]))
    with pytest.raises(ValueError,match='validation'):
        audit_review(llm,report,batch,dict(title='Draft',markdown='One combined sentence.'),[])


def test_false_rejection_is_resolved_inside_one_tool_call_before_one_atomic_save(tmp_path):
    from src.reports.validation import audit_review
    sessions=SimpleNamespace(_db_path=tmp_path/'sessions.db',get_session=lambda _:dict(id='s'))
    store=for_sessions(sessions);report=store.save('s','Draft','First fact. Second fact.',[])
    c=store.create_comment('s',report['id'],1,text='One sentence.',author='local',
                           anchor=dict(start_line=1,end_line=1,quote=report['markdown']))
    batch=store.freeze_reviews('s',[dict(report_id=report['id'],revision=1,comment_ids=[c['id']])],'')[0]
    block=report['blocks'][0]
    args=dict(report_id=report['id'],edits=[dict(id='edit',block_id=block['id'],block_hash=block['hash'],
        before=report['markdown'],after='First fact and second fact.')],results=[dict(comment_id=c['id'],status='applied',summary='Combined the two facts.',edit_ids=['edit'])])
    llm=MagicMock();llm.generate.side_effect=[json.dumps(dict(ok=False,issues=[dict(comment_id=c['id'],
        required_action='The original still has two sentences.',proposed_excerpt=report['markdown'])])),json.dumps(dict(ok=True,issues=[]))]
    response=json.loads(execute_report_tool(sessions,'s','revise_report',args,EvidenceRegistry('s'),review_batches=[batch],
        review_validator=lambda *a:audit_review(llm,*a)))
    assert response['report']['revision']==2,response
    assert store.read('s',report['id'])['markdown']=='First fact and second fact.'
    assert len(store.history('s',report['id']))==2
    assert llm.generate.call_count==2


def test_audit_cache_is_about_the_document_change_not_applied_reply_wording_or_edit_ids():
    from src.reports.validation import review_audit_key
    report=dict(revision=4);batch=dict(id='batch')
    proposal=dict(title='Draft',markdown='A revised sentence.')
    first=[dict(comment_id='c',status='applied',summary='Shortened it.',edit_ids=['e1'])]
    renamed=[dict(comment_id='c',status='applied',summary='Combined the wording.',edit_ids=['another'])]
    assert review_audit_key(report,batch,proposal,first)==review_audit_key(report,batch,proposal,renamed)
    assert review_audit_key(report,batch,proposal,first)!=review_audit_key(report,batch,{**proposal,'markdown':'Different.'},first)
    answered=[dict(comment_id='c',status='unchanged',summary='The source confirms it.',edit_ids=[])]
    assert review_audit_key(report,batch,proposal,answered)!=review_audit_key(report,batch,proposal,[{**answered[0],'summary':'The source is unavailable.'}])
