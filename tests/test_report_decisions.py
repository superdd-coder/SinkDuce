from pathlib import Path
import pytest
from src.reports.store import ReportStore


def setup_report(tmp_path,source='A.\n\nOther.'):
    store=ReportStore(tmp_path,session_exists=lambda _:True)
    return store,store.save('s','Draft',source,[])


def propose(store,r,before,after,text='Revise this.',parent=None):
    if parent:
        c=store.follow_up('s',r['id'],parent,text=text,author='local',expected_revision=r['revision'],operation_id=f'follow-{r["revision"]}')
    else:
        block=next(b for b in r['blocks'] if before in r['markdown'][b['start']:b['end']])
        c=store.create_comment('s',r['id'],r['revision'],text=text,author='local',anchor=dict(start_line=block['start_line'],end_line=block['end_line'],quote=before))
    batch=store.freeze_reviews('s',[dict(report_id=r['id'],revision=r['revision'],comment_ids=[c['id']])],'')[0]
    b=next(b for b in r['blocks'] if before in r['markdown'][b['start']:b['end']])
    edited=store.apply_review('s',r['id'],batch['id'],[dict(id='e',block_id=b['id'],block_hash=b['hash'],before=before,after=after)],[dict(comment_id=c['id'],status='applied',summary='Reworded the passage for clarity.',edit_ids=['e'])],[],require_block_targets=True)
    return edited,edited['suggestions'][-1]


def decide(store,r,g,decision,key):
    return store.decide_suggestion('s',r['id'],g['id'],decision,expected_revision=r['revision'],operation_id=key)


def test_accept_reject_and_followup_use_the_last_accepted_text(tmp_path):
    store,r=setup_report(tmp_path)
    r,b=propose(store,r,'A.','B.')
    assert b['decision']=='pending' and b['comments'][0]['anchor']['quote']=='A.'
    r=decide(store,r,b,'accepted','accept-b')
    assert r['markdown']=='B.\n\nOther.'
    c=store.follow_up('s',r['id'],b['id'],text='Make it clearer.',author='local',expected_revision=r['revision'],operation_id='draft-c')
    assert 'B.' in c['anchor']['quote']
    store.delete_comment('s',r['id'],c['id'])
    r,c=propose(store,r,'B.','C.',parent=b['id'])
    r=decide(store,r,c,'rejected','reject-c')
    assert r['markdown']=='B.\n\nOther.'
    next_round=store.follow_up('s',r['id'],c['id'],text='Try again.',author='local',expected_revision=r['revision'],operation_id='draft-again')
    assert 'B.' in next_round['anchor']['quote'] and 'C.' not in next_round['anchor']['quote']
    history=next_round['discussion_history']
    assert [round['decision'] for round in history]==['accepted','rejected']
    assert history[0]['comments'][0]['anchor']['quote']=='A.'
    assert history[1]['patches'][0]['before']=='B.' and history[1]['patches'][0]['after']=='C.'


def test_rejecting_a_continuation_of_an_unaccepted_candidate_restores_a(tmp_path):
    store,r=setup_report(tmp_path)
    r,b=propose(store,r,'A.','B.')
    r,c=propose(store,r,'B.','C.',parent=b['id'])
    assert r['suggestions'][0]['decision']=='superseded'
    r=decide(store,r,c,'rejected','reject')
    assert r['markdown']=='A.\n\nOther.'


def test_rejection_is_local_idempotent_and_stale_pages_cannot_decide(tmp_path):
    store,r=setup_report(tmp_path)
    r,b=propose(store,r,'A.','B.')
    r,other=propose(store,r,'Other.','Unrelated change.')
    request_revision=r['revision']
    r=decide(store,r,b,'rejected','local-reject')
    assert r['markdown']=='A.\n\nUnrelated change.'
    assert next(g for g in r['suggestions'] if g['id']==other['id'])['decision']=='pending'
    assert store.decide_suggestion('s',r['id'],b['id'],'rejected',expected_revision=request_revision,operation_id='local-reject')['revision']==r['revision']
    with pytest.raises(ValueError,match='conflict|changed'):
        store.decide_suggestion('s',r['id'],other['id'],'accepted',expected_revision=request_revision,operation_id='stale')


def test_pending_deletion_retains_original_and_can_be_rejected(tmp_path):
    store,r=setup_report(tmp_path)
    r,g=propose(store,r,'A.','')
    assert 'A.' not in r['markdown']
    assert g['patches'][0]['before']=='A.' and g['patches'][0]['after']==''
    r=decide(store,r,g,'rejected','restore')
    assert r['markdown']=='A.\n\nOther.'


def test_shared_edits_share_one_decision_and_full_history_does_not_nest(tmp_path):
    store,r=setup_report(tmp_path)
    comments=[store.create_comment('s',r['id'],1,text=f'Review {i}',author='local',anchor=dict(start_line=1,end_line=1,quote='A.')) for i in range(2)]
    batch=store.freeze_reviews('s',[dict(report_id=r['id'],revision=1,comment_ids=[c['id'] for c in comments])],'')[0]
    r=store.apply_review('s',r['id'],batch['id'],[dict(id='shared',before='A.',after='B.')],[dict(comment_id=c['id'],status='applied',summary='Clarified the shared statement.',edit_ids=['shared']) for c in comments],[])
    assert len(r['suggestions'])==1 and len(r['suggestions'][0]['comments'])==2
    g=r['suggestions'][0]
    r=decide(store,r,g,'accepted','shared-accept')
    assert r['suggestions'][0]['decision']=='accepted'
    r,g=propose(store,r,'B.','C.',parent=g['id'])
    assert all('discussion_history' not in c for c in g['comments'])


def test_legacy_candidate_projection_is_read_only_and_survives_reopening(tmp_path):
    import json
    from src.reports.history import new_journal, append_action
    store,r=setup_report(tmp_path)
    r,g=propose(store,r,'A.','B.')
    raw=store._raw_read('s',r['id'],r['revision']);raw.pop('suggestions')
    path=store._dir('s',r['id'])/'history.json'
    initial=store._raw_read('s',r['id'],1)
    journal=new_journal(initial,store._meta('s',r['id']));append_action(journal,initial,raw,store._meta('s',r['id']))
    path.write_text(json.dumps(journal))
    original=path.read_bytes()
    reopened=ReportStore(tmp_path,session_exists=lambda _:True)
    projected=reopened.read('s',r['id'])
    assert projected['suggestions'][0]['decision']=='pending' and path.read_bytes()==original
    r=decide(reopened,projected,projected['suggestions'][0],'rejected','legacy')
    assert r['markdown']=='A.\n\nOther.'


def test_instruction_rejection_can_restore_all_its_locations_without_losing_unrelated_edits(tmp_path):
    store,r=setup_report(tmp_path,'A one.\n\nMiddle.\n\nA two.')
    c=store.create_comment('s',r['id'],1,text='Use B consistently.',author='local',anchor=dict(start_line=1,end_line=1,quote='A one.'))
    batch=store.freeze_reviews('s',[dict(report_id=r['id'],revision=1,comment_ids=[c['id']])],'')[0]
    r=store.apply_review('s',r['id'],batch['id'],[dict(id='e1',before='A one.',after='B one.'),dict(id='e2',before='A two.',after='B two.')],[dict(comment_id=c['id'],status='applied',summary='Aligned both mentions.',edit_ids=['e1','e2'])],[])
    g=r['suggestions'][0]
    assert len(r['suggestions'])==2 and len(g['patches'])==1
    r,_=propose(store,r,'Middle.','A longer unrelated paragraph.')
    r=store.decide_suggestion('s',r['id'],g['id'],'rejected',scope='instruction',expected_revision=r['revision'],operation_id='both')
    assert r['markdown']=='A one.\n\nA longer unrelated paragraph.\n\nA two.'


def test_decision_and_followup_failures_leave_the_entire_state_unchanged(tmp_path,monkeypatch):
    store,r=setup_report(tmp_path);r,g=propose(store,r,'A.','B.')
    journal=(store._dir('s',r['id'])/'history.json').read_bytes()
    reviews=(store._dir('s',r['id'])/'reviews.json').read_bytes()
    original=store._write
    def fail(path,data):raise OSError('simulated disk failure')
    monkeypatch.setattr(store,'_write',fail)
    with pytest.raises(OSError):decide(store,r,g,'rejected','disk')
    with pytest.raises(OSError):store.follow_up('s',r['id'],g['id'],text='Continue.',author='local',expected_revision=r['revision'],operation_id='follow-fail')
    assert (store._dir('s',r['id'])/'history.json').read_bytes()==journal
    assert (store._dir('s',r['id'])/'reviews.json').read_bytes()==reviews
    monkeypatch.setattr(store,'_write',original)
    assert store.read('s',r['id'])['suggestions'][0]['decision']=='pending'


def test_restoring_history_restores_its_review_decisions_as_well(tmp_path):
    store,r=setup_report(tmp_path);r,g=propose(store,r,'A.','B.')
    candidate=r['revision'];r=decide(store,r,g,'accepted','accept')
    r=store.restore('s',r['id'],candidate,expected_revision=r['revision'],operation_id='back')
    assert r['suggestions'][0]['id']==g['id'] and r['suggestions'][0]['decision']=='pending'


def test_adjacent_independent_revisions_do_not_merge_or_reject_each_other(tmp_path):
    store,r=setup_report(tmp_path,'AABB')
    r,a=propose(store,r,'AA','LongAA')
    r,b=propose(store,r,'BB','CC')
    assert len([g for g in r['suggestions'] if g['decision']=='pending'])==2
    r=decide(store,r,a,'rejected','adjacent')
    assert r['markdown']=='AACC'
    r=decide(store,r,b,'rejected','adjacent-b')
    assert r['markdown']=='AABB'


@pytest.mark.parametrize('rewritten,remaining', [
    ('XBBCC', 'XAACC'),
    ('BBXCC', 'AAXCC'),
    ('XBBYCC', 'XAAYCC'),
])
@pytest.mark.parametrize('reject_original_first', [True, False])
def test_rewrite_boundary_insertions_stay_outside_independent_pending_patches(
    tmp_path, rewritten, remaining, reject_original_first,
):
    store, r = setup_report(tmp_path, 'AACC')
    r, original = propose(store, r, 'AA', 'BB')
    r = store.save('s', r['title'], rewritten, [], report_id=r['id'], expected_revision=r['revision'])
    # Read through a new instance to verify that the saved coordinates are valid.
    store = ReportStore(tmp_path, session_exists=lambda _: True)
    r = store.read('s', r['id'])
    assert len([g for g in r['suggestions'] if g['decision'] == 'pending']) == 2
    rebased = next(g for g in r['suggestions'] if g['id'] == original['id'])
    for patch in rebased['patches']:
        assert r['markdown'][patch['start']:patch['end']] == patch['after'] == 'BB'
    context = store.follow_up_context('s', r['id'], original['id'], expected_revision=r['revision'])
    assert context['current_text'] == 'BB'
    inserted = r['suggestions'][-1]
    if reject_original_first:
        r = decide(store, r, original, 'rejected', 'reject-original')
        assert r['markdown'] == remaining
        assert next(g for g in r['suggestions'] if g['id'] == inserted['id'])['decision'] == 'pending'
        r = decide(store, r, inserted, 'rejected', 'reject-insertion')
    else:
        r = decide(store, r, inserted, 'rejected', 'reject-insertion')
        assert r['markdown'] == 'BBCC'
        r = decide(store, r, original, 'rejected', 'reject-original')
    assert r['markdown'] == 'AACC'


def test_title_revision_rejects_without_removing_an_independent_boundary_insertion(tmp_path):
    store = ReportStore(tmp_path, session_exists=lambda _: True)
    r = store.save('s', 'AACC', 'Stable body.', [])
    r = store.save('s', 'BBCC', r['markdown'], [], report_id=r['id'], expected_revision=r['revision'])
    original = r['suggestions'][-1]
    r = store.save('s', 'BBXCC', r['markdown'], [], report_id=r['id'], expected_revision=r['revision'])
    inserted = r['suggestions'][-1]
    r = decide(store, r, original, 'rejected', 'reject-title')
    assert r['title'] == 'AAXCC' and r['markdown'] == 'Stable body.'
    r = decide(store, r, inserted, 'rejected', 'reject-title-insertion')
    assert r['title'] == 'AACC'


def test_insertion_at_a_shared_boundary_belongs_to_neither_pending_revision(tmp_path):
    store, r = setup_report(tmp_path, 'AACC')
    r, left = propose(store, r, 'AA', 'BB')
    r, right = propose(store, r, 'CC', 'DD')
    r = store.save('s', r['title'], 'BBXDD', [], report_id=r['id'], expected_revision=r['revision'])
    inserted = r['suggestions'][-1]
    assert len([g for g in r['suggestions'] if g['decision'] == 'pending']) == 3
    r = decide(store, r, left, 'rejected', 'reject-left')
    assert r['markdown'] == 'AAXDD'
    r = decide(store, r, right, 'rejected', 'reject-right')
    assert r['markdown'] == 'AAXCC'
    r = decide(store, r, inserted, 'rejected', 'reject-shared-boundary-insertion')
    assert r['markdown'] == 'AACC'


def test_deleted_followup_anchor_stays_empty_after_a_later_insertion_at_its_position(tmp_path):
    store, r = setup_report(tmp_path, 'AACC')
    r, deletion = propose(store, r, 'AA', '')
    r = decide(store, r, deletion, 'accepted', 'accept-deletion')
    r = store.save('s', r['title'], 'XCC', [], report_id=r['id'], expected_revision=r['revision'])
    context = store.follow_up_context('s', r['id'], deletion['id'], expected_revision=r['revision'])
    assert context['deleted'] and context['current_text'] == ''


def test_reject_can_restore_trusted_legacy_text_without_relaxing_new_report_validation(tmp_path):
    from src.chatbox.citations import EvidenceRegistry
    store=ReportStore(tmp_path,session_exists=lambda _:True)
    evidence=EvidenceRegistry('s');e=evidence.register('Original evidence.',dict(file_id='file',page_number=4,filename='File.pdf'))
    bad=f'Source: page 7 [cite:{e["id"]}]'
    good=f'Source: page 4 [cite:{e["id"]}]'
    with pytest.raises(ValueError,match='page'):
        store.save('s','Draft',bad,evidence.records())
    # Historical source from an older writer predates the stricter caption check.
    r=store.save('s','Draft',bad,evidence.records(),historical_replay=True)
    b=r['blocks'][0]
    r=store.save_edits('s',r['id'],[dict(id='e',block_id=b['id'],block_hash=b['hash'],before=bad,after=good)],evidence.records(),expected_revision=1)
    r=decide(store,r,r['suggestions'][0],'rejected','historical-reject')
    assert r['markdown']==bad


def test_followup_anchors_the_commented_region_when_an_edit_also_updates_the_summary(tmp_path):
    store,r=setup_report(tmp_path,'Summary A.\n\nSection A.')
    c=store.create_comment('s',r['id'],1,text='Revise this section and keep the summary aligned.',author='local',anchor=dict(start_line=3,end_line=3,quote='Section A.'))
    batch=store.freeze_reviews('s',[dict(report_id=r['id'],revision=1,comment_ids=[c['id']])],'')[0]
    r=store.apply_review('s',r['id'],batch['id'],[dict(id='summary',before='Summary A.',after='Summary B.'),dict(id='section',before='Section A.',after='Section B.')],[dict(comment_id=c['id'],status='applied',summary='Revised the section and aligned its summary.',edit_ids=['summary','section'])],[])
    section=next(g for g in r['suggestions'] if any(p['before']=='Section A.' for p in g['patches']))
    context=store.follow_up_context('s',r['id'],section['id'],expected_revision=2)
    assert context['current_text'].strip()=='Section B.' and context['anchor']['start_line']==3


def test_deletion_redlines_keep_unchanged_paragraphs_out_of_the_deleted_layer(tmp_path):
    store,r=setup_report(tmp_path,'## Summary\n\nSummary B.\n\nDelete.\n\nKeep.')
    c=store.create_comment('s',r['id'],1,text='Remove this.',author='local',anchor=dict(start_line=5,end_line=5,quote='Delete.'))
    batch=store.freeze_reviews('s',[dict(report_id=r['id'],revision=1,comment_ids=[c['id']])],'')[0]
    b=r['blocks'][0]
    r=store.apply_review('s',r['id'],batch['id'],[dict(id='e',mode='section',block_id=b['id'],block_hash=b['hash'],after='## Summary\n\nSummary B.\n\nKeep.')],[dict(comment_id=c['id'],status='applied',summary='Removed the redundant paragraph.',edit_ids=['e'])],[],require_block_targets=True)
    patch=r['suggestions'][0]['patches'][0]
    assert patch['display_patches'] and all(p['after']=='' for p in patch['display_patches'])
    assert 'Summary B.' not in ''.join(p['before'] for p in patch['display_patches'])
    assert 'Delete.' in ''.join(p['before'] for p in patch['display_patches'])


def test_followup_of_a_deleted_passage_stays_empty_even_inside_a_larger_pending_revision(tmp_path):
    store,r=setup_report(tmp_path,'A1.\n\nA2.\n\nOther.')
    c=store.create_comment('s',r['id'],1,text='Revise the opening.',author='local',anchor=dict(start_line=1,end_line=1,quote='A1.'))
    b=store.freeze_reviews('s',[dict(report_id=r['id'],revision=1,comment_ids=[c['id']])],'')[0]
    first,second=r['blocks'][:2]
    r=store.apply_review('s',r['id'],b['id'],[dict(id='e',mode='block_range',block_id=first['id'],block_hash=first['hash'],end_block_id=second['id'],end_block_hash=second['hash'],after='B1.\n\nB2.')],[dict(comment_id=c['id'],status='applied',summary='Reframed the opening.',edit_ids=['e'])],[],require_block_targets=True)
    r,g=propose(store,r,'B2.','')
    assert g['patches'][0]['after']==''
    assert 'B1.' in r['markdown']
    context=store.follow_up_context('s',r['id'],g['id'],expected_revision=r['revision'])
    assert context['deleted'] and context['current_text']==''
    r=decide(store,r,g,'accepted','accept-delete')
    context=store.follow_up_context('s',r['id'],g['id'],expected_revision=r['revision'])
    assert context['deleted'] and context['current_text']==''


@pytest.mark.parametrize('decision,expected', [('accepted', 'B.\n\nAdditional detail.'), ('rejected', 'A.')])
def test_pending_followup_reanchors_when_its_parent_is_decided(tmp_path, decision, expected):
    store, report = setup_report(tmp_path, 'Opening.\n\nA.\n\nClosing.')
    report, group = propose(store, report, 'A.', 'B.\n\nAdditional detail.')
    followup = store.follow_up('s', report['id'], group['id'], text='Explain the reasoning more clearly.',
                              author='local', expected_revision=report['revision'], operation_id='pending-followup')
    original_anchor = dict(followup['anchor'])
    report = decide(store, report, group, decision, 'decide-parent')
    current = next(c for c in store.comments('s', report['id'], report['revision']) if c['id'] == followup['id'])
    assert not current.get('anchor_stale'), 'A decision must not detach a pending discussion'
    assert current['anchor']['start_line'] == 3
    assert current['anchor']['quote'] == ('B.' if decision == 'accepted' else 'A.')
    assert current['current_text'] == expected
    assert current['previous_anchor'] == original_anchor
    assert current['discussion_history'][-1]['decision'] == decision
    frozen = store.freeze_reviews('s', [dict(report_id=report['id'], revision=report['revision'], comment_ids=[current['id']])], '')[0]
    assert not frozen['comments'][0].get('anchor_stale')
    assert frozen['comments'][0]['current_text'] == expected


def test_one_instruction_has_coarse_independent_locations_and_scoped_acceptance(tmp_path):
    source = "# Delivery\n\nThe amber parcel stays amber.\n\nThe amber crate arrives.\n\nOther plan."
    store, r = setup_report(tmp_path, source)
    c = store.create_comment('s', r['id'], 1, text='Replace amber throughout.', author='local',
        anchor=dict(start_line=3, end_line=3, quote='amber', selection_start=3, selection_end=8))
    batch = store.freeze_reviews('s', [dict(report_id=r['id'], revision=1, comment_ids=[c['id']])], '')[0]
    r = store.apply_review('s', r['id'], batch['id'], [dict(id='terms', mode='replace_all', before='amber', after='violet', match_count=3)],
        [dict(comment_id=c['id'], status='applied', summary='Aligned the terminology.', edit_ids=['terms'])], [])
    locations = [g for g in r['suggestions'] if g['decision'] == 'pending']
    assert len(locations) == 2, 'Two changes in one paragraph share a card; another paragraph has its own card'
    first, second = locations
    assert len(first['patches']) == 2 and len(second['patches']) == 1
    assert first['instruction_id'] == second['instruction_id']
    assert first['location_id'] != second['location_id']
    r = decide(store, r, first, 'rejected', 'reject-first-location')
    assert 'The amber parcel stays amber.' in r['markdown']
    assert 'The violet crate arrives.' in r['markdown']
    r, unrelated = propose(store, r, 'Other plan.', 'Separate plan.')
    r = store.decide_suggestion('s', r['id'], second['id'], 'accepted', scope='instruction',
        expected_revision=r['revision'], operation_id='accept-remaining-instruction')
    states = {g['id']: g['decision'] for g in r['suggestions']}
    assert states[first['id']] == 'rejected'
    assert states[second['id']] == 'accepted'
    assert states[unrelated['id']] == 'pending'
    again = store.decide_suggestion('s', r['id'], second['id'], 'accepted', scope='instruction',
        expected_revision=2, operation_id='accept-remaining-instruction')
    assert again['revision'] == r['revision']
    with pytest.raises(ValueError, match='changed'):
        store.decide_suggestion('s', r['id'], second['id'], 'accepted', scope='location',
            expected_revision=2, operation_id='accept-remaining-instruction')


def test_followup_of_one_location_never_uses_a_sibling_or_supersedes_it(tmp_path):
    store, r = setup_report(tmp_path, 'Amber parcel.\n\nAmber crate.')
    r = store.save('s', r['title'], 'Violet parcel.\n\nViolet crate.', [], report_id=r['id'], expected_revision=1)
    first, second = r['suggestions']
    context = store.follow_up_context('s', r['id'], first['id'], expected_revision=r['revision'])
    assert context['current_text'].strip() == 'Violet parcel.'
    assert [g['id'] for g in context['discussion_history']] == [first['id']]
    r, continued = propose(store, r, 'Violet parcel.', 'Blue parcel.', parent=first['id'])
    assert next(g for g in r['suggestions'] if g['id'] == second['id'])['decision'] == 'pending'
    assert continued['instruction_id'] == first['instruction_id']
    assert continued['location_id'] == first['location_id']
    r = decide(store, r, continued, 'rejected', 'reject-continuation')
    assert r['markdown'] == 'Amber parcel.\n\nViolet crate.'


def test_broad_body_edit_yields_locations_by_paragraph_and_table_row(tmp_path):
    source = 'Amber parcel.\n\n| Item | State |\n| --- | --- |\n| A | amber |\n| B | amber |'
    store, r = setup_report(tmp_path, source)
    r = store.save('s', r['title'], source.replace('Amber', 'Violet').replace('amber', 'violet'), [],
        report_id=r['id'], expected_revision=1,
        block_edits=[dict(id='whole', field='markdown', start=0, end=len(source), before=source,
                          after=source.replace('Amber', 'Violet').replace('amber', 'violet'))])
    assert len(r['suggestions']) == 3
    assert len({g['instruction_id'] for g in r['suggestions']}) == 1
    r = decide(store, r, r['suggestions'][1], 'rejected', 'reject-table-row')
    assert '| A | amber |' in r['markdown'] and '| B | violet |' in r['markdown']


def test_instruction_decision_is_one_atomic_history_step_and_reopens_with_local_states(tmp_path):
    store, r = setup_report(tmp_path, 'Old parcel.\n\nOld crate.')
    r = store.save('s', r['title'], 'New parcel.\n\nNew crate.', [], report_id=r['id'], expected_revision=1)
    first, second = r['suggestions']
    request_revision = r['revision']
    r = store.decide_suggestion('s', r['id'], first['id'], 'accepted', scope='instruction',
        expected_revision=request_revision, operation_id='accept-instruction')
    assert r['revision'] == request_revision + 1
    reopened = ReportStore(tmp_path, session_exists=lambda _: True)
    current = reopened.read('s', r['id'])
    assert [g['decision'] for g in current['suggestions']] == ['accepted', 'accepted']
    assert current['decision_event']['group_ids'] == [first['id'], second['id']]
    assert current['decision_event']['scope'] == 'instruction'
    assert reopened.history('s', r['id'])[0]['kind'] == 'decision'
    with pytest.raises(ValueError, match='conflict'):
        reopened.decide_suggestion('s', r['id'], second['id'], 'rejected', expected_revision=request_revision, operation_id='stale-location')
    restored = reopened.restore('s', r['id'], request_revision, expected_revision=current['revision'], operation_id='undo-group')
    assert all(g['decision'] == 'pending' for g in restored['suggestions'])
