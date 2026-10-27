import copy
import pytest
from src.reports.store import ReportStore


@pytest.fixture
def report(tmp_path):
    store = ReportStore(tmp_path, session_exists=lambda sid: sid == 's')
    record = store.save('s', 'Review', '## Summary\n\nRevenue is 10.\n\n## Detail\n\nRevenue is 10.\n\nUnrelated.', [])
    return store, record


def comment(store, record, text='Correct this metric throughout the report', line=3, quote='Revenue is 10.'):
    return store.create_comment('s', record['id'], 1, text=text,
        anchor={'start_line': line, 'end_line': line, 'quote': quote, 'prefix': '', 'suffix': ''}, author='local')


def test_anchors_are_revision_scoped_and_pending_comments_survive_restart(report):
    store, r = report
    c = comment(store, r)
    reloaded = ReportStore(store.root, session_exists=lambda sid: sid == 's')
    assert reloaded.pending_comments('s')[0]['id'] == c['id']
    assert reloaded.comments('s', r['id'], 1)[0]['anchor']['start_line'] == 3
    with pytest.raises(ValueError, match='anchor'):
        comment(store, r, line=7, quote='not in report')
    with pytest.raises(FileNotFoundError):
        store.comments('foreign', r['id'], 1)


def test_freeze_is_immutable_and_cancel_restores_remaining_pending(report):
    store, r = report
    c = comment(store, r)
    batch = store.freeze_reviews('s', [{'report_id': r['id'], 'revision': 1, 'comment_ids': [c['id']]}], 'Overall request')[0]
    snapshot = copy.deepcopy(batch)
    assert not store.pending_comments('s')
    store.delete_comment('s', r['id'], c['id'])
    later = comment(store, r, 'Later requirement')
    assert store.review_batch('s', r['id'], batch['id']) == snapshot
    assert [x['id'] for x in store.pending_comments('s')] == [later['id']]
    store.release_reviews('s', [batch])
    assert store.comments('s', r['id'], 1)[0]['id'] == later['id']


def test_review_edits_can_span_sections_and_save_outcomes_with_actual_diff_once(report):
    store, r = report
    c = comment(store, r)
    batch = store.freeze_reviews('s', [{'report_id': r['id'], 'revision': 1, 'comment_ids': [c['id']]}], '')[0]
    edits = [{'id': 'summary', 'before': 'Revenue is 10.\n\n## Detail', 'after': 'Revenue is 12.\n\n## Detail'},
             {'id': 'detail', 'before': 'Revenue is 10.\n\nUnrelated.', 'after': 'Revenue is 12.\n\nUnrelated.'}]
    results = [{'comment_id': c['id'], 'status': 'applied', 'summary': 'Updated the summary and detail to use the verified metric.', 'edit_ids': ['summary', 'detail']}]
    revised = store.apply_review('s', r['id'], batch['id'], edits, results, [])
    assert revised['revision'] == 2 and revised['parent_revision'] == 1
    assert revised['markdown'].count('Revenue is 12.') == 2
    assert revised['markdown'].endswith('Unrelated.')
    assert revised['review']['comments'][0]['id'] == c['id']
    assert len(revised['changes']) == 2
    assert all(change['after'] == 'Revenue is 12.\n' for change in revised['changes'])
    assert store.read('s', r['id'], 1) == r
    assert store.apply_review('s', r['id'], batch['id'], edits, results, [])['revision'] == 2
    store.release_reviews('s', [batch])
    assert not store.pending_comments('s')


def test_review_rejects_invented_missing_and_wordy_outcomes_and_ambiguous_edits(report):
    store, r = report
    c = comment(store, r)
    batch = store.freeze_reviews('s', [{'report_id': r['id'], 'revision': 1, 'comment_ids': [c['id']]}], '')[0]
    edits = [{'id': 'one', 'before': 'Revenue is 10.', 'after': 'Revenue is 12.'}]
    results = [{'comment_id': c['id'], 'status': 'applied', 'summary': 'Corrected the metric to match the source.', 'edit_ids': ['one']}]
    with pytest.raises(ValueError, match='unique'):
        store.apply_review('s', r['id'], batch['id'], edits, results, [])
    edits[0]['before'] = 'Revenue is 10.\n\n## Detail'
    for bad in ([], [{**results[0], 'comment_id': 'invented'}], [{**results[0], 'summary': '冗' * 81}], [{**results[0], 'edit_ids': ['fake']}]):
        with pytest.raises(ValueError):
            store.apply_review('s', r['id'], batch['id'], edits, bad, [])
    assert store.read('s', r['id'])['revision'] == 1


def test_stale_versions_noop_and_session_deletion_cannot_fake_success(report):
    store, r = report
    c = comment(store, r)
    batch = store.freeze_reviews('s', [{'report_id': r['id'], 'revision': 1, 'comment_ids': [c['id']]}], '')[0]
    outcome = [{'comment_id': c['id'], 'status': 'needs_clarification', 'summary': 'The requested figure needs a source before it can be changed.', 'edit_ids': []}]
    unchanged = store.apply_review('s', r['id'], batch['id'], [], outcome, [])
    assert unchanged['revision'] == 1 and unchanged['review_result']['results'] == outcome
    assert store.pending_comments('s') == []
    assert store.review_history('s',r['id'])[0]['results'] == outcome
    store.save('s', 'Review', 'Changed elsewhere.', [], report_id=r['id'], expected_revision=1)
    with pytest.raises(ValueError, match='version'):
        store.freeze_reviews('s', [{'report_id': r['id'], 'revision': 1, 'comment_ids': [c['id']]}], '')
    store.purge('s')
    with pytest.raises(FileNotFoundError):
        store.create_comment('s', r['id'], 1, text='x', anchor=c['anchor'], author='local')


def test_formatted_unicode_selection_and_whole_figure_anchors_are_verified(report):
    store, r = report
    body = '**第一项 🧭 收益** 与第二项 🧭 收益。\n\n```svg\n<svg><text>收益分布</text></svg>\n```'
    r = store.save('s', 'Unicode', body, [], report_id=r['id'], expected_revision=1)
    c = store.create_comment('s', r['id'], 2, text='Explain the second metric.', author='local',
        anchor={'start_line':1,'end_line':1,'quote':'🧭 收益','prefix':'与第二项 ','suffix':'。'})
    assert c['anchor']['quote'] == '🧭 收益'
    block = '\n'.join(body.splitlines()[2:])
    c = store.create_comment('s', r['id'], 2, text='Improve this chart.', author='local',
        anchor={'start_line':3,'end_line':5,'quote':block,'kind':'block'})
    assert c['anchor']['kind'] == 'block'
    with pytest.raises(ValueError):
        store.create_comment('s', r['id'], 2, text='fake', author='local',anchor={'start_line':3,'end_line':5,'kind':'block','quote':'fake'})


def test_restart_recovers_unfinished_batches_without_rewriting_the_snapshot(report):
    store, r = report
    c = comment(store, r)
    frozen=store.freeze_reviews('s',[{'report_id':r['id'],'revision':1,'comment_ids':[c['id']]}],'')[0]
    restarted=ReportStore(store.root, session_exists=lambda sid:sid=='s')
    assert restarted.pending_comments('s')[0]['id']==c['id']
    assert restarted.review_batch('s',r['id'],frozen['id'])['comments']==frozen['comments']


def test_track_changes_keeps_whole_table_and_fence_context(report):
    from src.reports.reviews import compute_changes
    before='## Data\n\n| Name | Value |\n| --- | --- |\n| A | 10 |\n\n```svg\n<svg><text>10</text></svg>\n```'
    changes=compute_changes(before,before.replace('10','12'))
    assert len(changes)==2
    assert changes[0]['before'].startswith('| Name |')
    assert changes[0]['after'].startswith('| Name |')
    assert changes[1]['after'].startswith('```svg') and changes[1]['after'].endswith('```')


def test_completed_review_links_outcomes_to_computed_changes_and_exports_stay_clean(report):
    from src.reports.rendering import render_html
    store,r=report
    c=comment(store,r)
    batch=store.freeze_reviews('s',[{'report_id':r['id'],'revision':1,'comment_ids':[c['id']]}],'')[0]
    edit={'id':'e','before':'Revenue is 10.\n\n## Detail','after':'Revenue is 12.\n\n## Detail'}
    outcome={'comment_id':c['id'],'status':'applied','summary':'Corrected the summary to match the verified metric.','edit_ids':['e']}
    updated=store.apply_review('s',r['id'],batch['id'],[edit],[outcome],[])
    assert updated['review']['results'][0]['change_ids']==[updated['changes'][0]['id']]
    exported=render_html(updated).decode()
    assert 'Revenue is 12.' in exported
    assert c['text'] not in exported and outcome['summary'] not in exported


def test_committed_version_recovers_after_review_index_write_failure(report, monkeypatch):
    store,r=report
    c=comment(store,r)
    batch=store.freeze_reviews('s',[{'report_id':r['id'],'revision':1,'comment_ids':[c['id']]}],'')[0]
    edit={'id':'e','before':'Revenue is 10.\n\n## Detail','after':'Revenue is 12.\n\n## Detail'}
    outcome={'comment_id':c['id'],'status':'partial','summary':'Corrected the summary; the detailed figure still needs a source.','edit_ids':['e']}
    original=store._write_reviews
    def fail(*args):
        raise OSError('disk interrupted')
    monkeypatch.setattr(store,'_write_reviews',fail)
    with pytest.raises(OSError):
        store.apply_review('s',r['id'],batch['id'],[edit],[outcome],[])
    monkeypatch.setattr(store,'_write_reviews',original)
    assert store.apply_review('s',r['id'],batch['id'],[edit],[outcome],[])['revision']==2
    restarted=ReportStore(store.root,session_exists=lambda sid:sid=='s')
    pending=restarted.pending_comments('s')
    assert pending==[]
    assert restarted.review_history('s',r['id'])[0]['results'][0]['status']=='partial'


def test_comments_added_during_revision_remain_pending_on_the_new_version(report):
    store,r=report;c=comment(store,r)
    batch=store.freeze_reviews('s',[{'report_id':r['id'],'revision':1,'comment_ids':[c['id']]}],'')[0]
    later=store.create_comment('s',r['id'],1,text='Also expand the unrelated section.',author='local',anchor={'start_line':9,'end_line':9,'quote':'Unrelated.'})
    edit={'id':'e','before':'Revenue is 10.\n\n## Detail','after':'Revenue is 12.\n\n## Detail'}
    store.apply_review('s',r['id'],batch['id'],[edit],[{'comment_id':c['id'],'status':'applied','summary':'Corrected the summary to match the verified metric.','edit_ids':['e']}],[])
    pending=store.pending_comments('s')
    assert [c['id'] for c in pending]==[later['id']]
    assert pending[0]['revision']==2
    assert pending[0]['anchor']['start_line']==9 and not pending[0].get('anchor_stale')


def test_invalid_model_shapes_are_recoverable_validation_errors(report):
    store,r=report;c=comment(store,r)
    b=store.freeze_reviews('s',[{'report_id':r['id'],'revision':1,'comment_ids':[c['id']]}],'')[0]
    for edits,results in [([None],[]),([], [42]),([{'id':[],'before':'a','after':'b'}],[]),([],[{'comment_id':[],'summary':'x','edit_ids':[]}])]:
        with pytest.raises(ValueError):store.apply_review('s',r['id'],b['id'],edits,results,[])


def test_tracking_text_highlights_ignore_markdown_and_keep_changed_numbers_whole():
    from src.reports.reviews import compute_changes
    change=compute_changes('Value is **10**.','Value is **12**.')[0]
    assert change['added'][0]['quote']=='12'
    assert change['removed'][0]['quote']=='10'
    assert '**' not in change['added'][0]['prefix']
    unchanged=compute_changes('Value is 10.','Value is **10**.')[0]
    assert unchanged['added']==[] and unchanged['removed']==[]


def test_tracking_chinese_rewrites_highlight_the_changed_sentence_without_neighbours():
    from src.reports.reviews import text_changes
    old='当前需要进一步核实数据，才能作出结论。'
    new='当前证据仍然有限，应先补充数据再作出结论。'
    added,removed=text_changes('项目尚未启动。'+old+'下一步保持不变。','项目尚未启动。'+new+'下一步保持不变。')
    assert [p['quote'] for p in added]==[new]
    assert [p['quote'] for p in removed]==[old]


def test_tracking_keeps_thousands_decimals_and_percentages_together():
    from src.reports.reviews import text_changes
    added,removed=text_changes('仓库存量为 2,460 件，损耗率为 2.5%。','仓库存量为 2,430 件，损耗率为 3.5%。')
    assert [p['quote'] for p in added]==['2,430','3.5%']
    assert [p['quote'] for p in removed]==['2,460','2.5%']


def test_tracking_english_edits_use_words_and_complete_rewritten_sentences():
    from src.reports.reviews import text_changes
    added,removed=text_changes('The outcome is unclear.','The outcome is clearer.')
    assert [p['quote'] for p in added]==['clearer']
    assert [p['quote'] for p in removed]==['unclear']
    old='The pipeline is strong and no risks have been identified.'
    new='The portfolio remains preliminary and requires independent validation before investment.'
    added,removed=text_changes('Context stays. '+old+' Closing stays.','Context stays. '+new+' Closing stays.')
    assert [p['quote'] for p in added]==[new]
    assert [p['quote'] for p in removed]==[old]


def test_tracking_quotes_and_context_stay_inside_their_rendered_paragraph():
    from src.reports.reviews import text_changes
    added,_=text_changes('前段保留。\n\n收入仍然不足。\n\n这项判断需要谨慎。','前段保留。\n\n收入已经改善。\n\n这项判断仍需验证。')
    assert [p['quote'] for p in added]==['收入已经改善。','这项判断仍需验证。']
    assert all('前段保留' not in p['prefix'] and '\n' not in p['quote']+p['prefix']+p['suffix'] for p in added)


def test_tracking_a_changed_table_cell_keeps_its_neighbouring_cells_available_as_context():
    from src.reports.reviews import text_changes
    table='| 名称 | 金额 |\n| --- | --- |\n| 第一项 | 10 |\n| 第二项 | 12 |'
    added,removed=text_changes(table,table.replace('第一项 | 10','第一项 | 12'))
    assert [p['quote'] for p in added]==['12']
    assert [p['quote'] for p in removed]==['10']
    assert '第一项' in added[0]['prefix']
    chinese=table.replace('第一项','初步方案')
    added,_=text_changes(chinese,chinese.replace('初步方案','修订后的方案'))
    assert [p['quote'] for p in added]==['修订后的方案']


def test_tracking_inline_formatting_changes_keep_the_rendered_words_equal():
    from src.reports.reviews import text_changes
    assert text_changes('The un**clear** outcome.','The **unclear** outcome.')==([],[])


def test_saved_character_highlights_are_projected_readably_without_rewriting_versions(report):
    import json
    store,r=report
    second=store.save('s','Review',r['markdown'].replace('10','12'),[],report_id=r['id'],expected_revision=1)
    path=store._dir('s',r['id'])/'revisions'/'2.json'
    for change in second['changes']:
        change.pop('highlight_version',None)
        change['added']=[{'quote':'2','prefix':'Revenueis1','suffix':'.'}]
        change['removed']=[{'quote':'0','prefix':'Revenueis1','suffix':'.'}]
    store._write(path,second)
    store._write(path.parent/'1.json', r)
    store._write(path.parent.parent/'meta.json', {k:v for k,v in second.items() if k not in {'markdown','citations','blocks','changes'}})
    (path.parent.parent/'history.json').unlink()
    saved=path.read_bytes()
    restored=store.read('s',r['id'],2)
    assert all(change['added'][0]['quote']=='12' for change in restored['changes'])
    assert [c['id'] for c in restored['changes']]==[c['id'] for c in second['changes']]
    assert restored['parent_revision']==1
    assert path.read_bytes()==saved
    assert json.loads(saved)['changes'][0]['added'][0]['quote']=='2'


def test_existing_report_versions_gain_tracking_without_rewriting_saved_records(report):
    import json
    store,r=report
    second=store.save('s','Review','Revised.',[],report_id=r['id'],expected_revision=1)
    path=store._dir('s',r['id'])/'revisions'/'2.json'
    legacy={k:v for k,v in second.items() if k not in {'parent_revision','changes'}}
    store._write(path,legacy)
    store._write(path.parent/'1.json', r)
    store._write(path.parent.parent/'meta.json', {k:v for k,v in second.items() if k not in {'markdown','citations','blocks','changes'}})
    (path.parent.parent/'history.json').unlink()
    restored=store.read('s',r['id'],2)
    assert restored['parent_revision']==1 and restored['changes'][0]['before']
    assert json.loads(path.read_text())==legacy
