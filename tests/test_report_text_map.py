import copy

import pytest

from src.reports.store import ReportStore


@pytest.fixture
def store(tmp_path):
    return ReportStore(tmp_path, session_exists=lambda sid: sid == 's')


def create(store, source):
    return store.save('s', 'Report', source, [])


def anchored_comment(store, report, quote, start, end, line=1, text='Change this specific occurrence.'):
    return store.create_comment('s', report['id'], report['revision'], text=text, author='local',
        anchor=dict(start_line=line, end_line=line, quote=quote, selection_start=start, selection_end=end))


def batch_for(store, report, comment):
    return store.freeze_reviews('s', [dict(report_id=report['id'], revision=report['revision'], comment_ids=[comment['id']])], '')[0]


def test_exact_selection_distinguishes_identical_context_in_the_same_paragraph(store):
    r = create(store, 'A ' * 200)
    c = anchored_comment(store, r, 'A', 100, 101)
    assert c['anchor']['selection_start'] == 100
    assert c['anchor']['source_ranges'] == [dict(start=200, end=201, before='A')]
    assert store.comments('s', r['id'], 1)[0]['anchor'] == c['anchor']


@pytest.mark.parametrize('selection', [(0, 1), (1, 1), (0, 500), (-1, 2)])
def test_exact_selection_is_validated_instead_of_trusting_browser_offsets(store, selection):
    r = create(store, '🧭 A A')
    with pytest.raises(ValueError):
        anchored_comment(store, r, 'A', *selection)


def test_unicode_and_formatted_selection_maps_to_raw_markdown_without_losing_format(store):
    r = create(store, '🧭 **A** and [A](https://example.test/A) &amp; A')
    c = anchored_comment(store, r, 'A', 5, 6)
    at = r['markdown'].index('[A]') + 1
    assert c['anchor']['source_ranges'] == [dict(start=at, end=at + 1, before='A')]
    batch = batch_for(store, r, c)
    updated = store.apply_review('s', r['id'], batch['id'], [dict(id='selected', mode='selection', comment_id=c['id'], after='B')],
        [dict(comment_id=c['id'], status='applied', summary='Corrected the selected label.', edit_ids=['selected'])], [])
    assert updated['markdown'] == '🧭 **A** and [B](https://example.test/A) &amp; A'
    assert updated['review']['comments'][0]['anchor'] == c['anchor']
    assert updated['edit_log'][0]['comment_ids'] == [c['id']]


@pytest.mark.parametrize('space', ['\ufeff', '\u0085', '\u00a0'])
def test_copy_pasted_unicode_spacing_has_the_same_selection_coordinates_as_the_browser(store, space):
    r = create(store, 'X' + space + 'A')
    c = anchored_comment(store, r, 'A', 1, 2)
    assert c['anchor']['source_ranges'] == [dict(start=2, end=3, before='A')]


def test_escaped_entities_are_selected_as_visible_characters(store):
    r = create(store, r'🧭 **A** &amp; \* A')
    # Normalized visible text: 🧭A&*A.
    c = anchored_comment(store, r, '&', 2, 3)
    at = r['markdown'].index('&amp;')
    assert c['anchor']['source_ranges'] == [dict(start=at, end=at + 5, before='&amp;')]


@pytest.mark.parametrize('source', ['- [ ] **A**', '- [x] A', '~~A~~'])
def test_task_controls_and_strikethrough_markers_do_not_shift_visible_selection_offsets(store, source):
    r = create(store, source)
    c = anchored_comment(store, r, 'A', 0, 1)
    at = source.index('A')
    assert c['anchor']['source_ranges'] == [dict(start=at, end=at + 1, before='A')]
    from src.reports.text_map import visible_text
    assert visible_text(source) == 'A'


def test_selection_operation_changes_only_the_exact_repeated_occurrence(store):
    r = create(store, 'A ' * 200)
    c = anchored_comment(store, r, 'A', 100, 101)
    batch = batch_for(store, r, c)
    updated = store.apply_review('s', r['id'], batch['id'], [dict(id='one', mode='selection', comment_id=c['id'], after='B')],
        [dict(comment_id=c['id'], status='applied', summary='Changed only the selected occurrence.', edit_ids=['one'])], [])
    assert updated['markdown'] == r['markdown'][:200] + 'B' + r['markdown'][201:]
    assert updated['edit_log'][0]['start'] == 200


def test_selection_operation_cannot_reference_a_comment_outside_the_submitted_batch(store):
    r = create(store, 'A A')
    first = anchored_comment(store, r, 'A', 0, 1)
    other = anchored_comment(store, r, 'A', 1, 2)
    batch = batch_for(store, r, first)
    with pytest.raises(ValueError):
        store.apply_review('s', r['id'], batch['id'], [dict(id='one', mode='selection', comment_id=other['id'], after='B')],
            [dict(comment_id=first['id'], status='applied', summary='Changed it.', edit_ids=['one'])], [])
    assert store.read('s', r['id'])['revision'] == 1


def test_selection_changes_must_remain_associated_with_their_original_comment(store):
    r = create(store, 'A A')
    first = anchored_comment(store, r, 'A', 0, 1)
    second = anchored_comment(store, r, 'A', 1, 2)
    batch = store.freeze_reviews('s', [dict(report_id=r['id'], revision=1, comment_ids=[first['id'], second['id']])], '')[0]
    with pytest.raises(ValueError, match='selection.*comment|comment.*selection'):
        store.apply_review('s', r['id'], batch['id'], [dict(id='one', mode='selection', comment_id=first['id'], after='B')],
            [dict(comment_id=first['id'], status='unchanged', summary='Kept it.', edit_ids=[]),
             dict(comment_id=second['id'], status='applied', summary='Changed it.', edit_ids=['one'])], [])
    assert store.read('s', r['id'])['revision'] == 1


def test_rough_selection_does_not_constrain_semantic_edits_to_the_selected_words(store):
    r = create(store, 'Project X has stock of 420 units.\n\nThe inventory table also shows 420 units.')
    c = anchored_comment(store, r, 'Project X', 0, 8, text='Set its stock to 480 units and keep the related section consistent.')
    snapshot = copy.deepcopy(c['anchor'])
    batch = batch_for(store, r, c)
    edits = [dict(id=f'e{i}', block_id=b['id'], block_hash=b['hash'], before='420 units', after='480 units') for i, b in enumerate(r['blocks'])]
    updated = store.apply_review('s', r['id'], batch['id'], edits,
        [dict(comment_id=c['id'], status='applied', summary='Updated inventory in the paragraph and related table.', edit_ids=['e0', 'e1'])], [], require_block_targets=True)
    assert updated['markdown'].count('480 units') == 2
    assert updated['review']['comments'][0]['anchor'] == snapshot
    assert all(edit['comment_ids'] == [c['id']] for edit in updated['edit_log'])


def test_visible_replace_all_preserves_links_citation_ids_and_svg_attributes(store):
    source = '# A\n\n**A** and [A](https://example.test/A) ![A](https://example.test/A.png)\n\n| A | Count |\n|---|---|\n| A | 2 |\n\n```svg\n<svg xmlns="http://www.w3.org/2000/svg"><text id="A">A<tspan>A</tspan></text><path id="A" d="M0 0"/></svg>\n```\n'
    r = create(store, source)
    updated = store.save_edits('s', r['id'], [dict(id='rename', mode='replace_all', before='A', after='B')], [], expected_revision=1)
    assert updated['markdown'].startswith('# B\n\n**B** and [B](https://example.test/A) ![B](https://example.test/A.png)')
    assert '| B | 2 |' in updated['markdown']
    assert '<text id="A">B<tspan>B</tspan></text><path id="A"' in updated['markdown']
    assert len(updated['edit_log']) == 8
    assert {edit['operation_id'] for edit in updated['edit_log']} == {'rename'}


def test_global_operation_has_one_outcome_and_all_expanded_changes_remain_linked(store):
    r = create(store, 'A A\n\n## A\n\nA')
    c = anchored_comment(store, r, 'A', 0, 1, text='Replace every A in the report with B.')
    batch = batch_for(store, r, c)
    updated = store.apply_review('s', r['id'], batch['id'], [dict(id='all', mode='replace_all', before='A', after='B')],
        [dict(comment_id=c['id'], status='applied', summary='Unified the name in all four places.', edit_ids=['all'])], [])
    assert updated['markdown'] == 'B B\n\n## B\n\nB'
    assert len(updated['edit_log']) == 4
    assert all(e['comment_ids'] == [c['id']] for e in updated['edit_log'])
    assert set(updated['review']['results'][0]['change_ids']) == {ch['id'] for ch in updated['changes']}
    assert updated['review']['operations'][0]['match_count'] == 4
    assert store.apply_review('s', r['id'], batch['id'], [], [], []) == updated


def test_document_wide_rename_includes_report_title_and_tracks_the_same_comment(store):
    r = store.save('s', 'Project X review', 'Project X has stock of 420 units.', [])
    c = anchored_comment(store, r, 'Project', 0, 7, text='Rename every Project X to Project Y.')
    batch = batch_for(store, r, c)
    updated = store.apply_review('s', r['id'], batch['id'], [dict(id='rename', mode='replace_all', before='Project X', after='Project Y')],
        [dict(comment_id=c['id'], status='applied', summary='Unified the project name in the title and body.', edit_ids=['rename'])], [])
    assert updated['title'] == 'Project Y review'
    assert updated['markdown'] == 'Project Y has stock of 420 units.'
    assert updated['review']['operations'][0]['match_count'] == 2
    assert any(e.get('field') == 'title' and e['comment_ids'] == [c['id']] for e in updated['edit_log'])
    title_change = next(ch for ch in updated['changes'] if ch.get('field') == 'title')
    assert title_change['id'] in updated['review']['results'][0]['change_ids']
    assert store.read('s', r['id'], 1)['title'] == 'Project X review'


def test_a_title_only_literal_rename_is_a_real_revision_and_preserves_duplicate_block_ids(store):
    r = store.save('s', 'Project X', 'Same.\n\nSame.', [])
    updated = store.save_edits('s', r['id'], [dict(id='rename', mode='replace_all', before='Project X', after='Project Y')], [], expected_revision=1)
    assert updated['revision'] == 2 and updated['title'] == 'Project Y'
    assert updated['markdown'] == r['markdown']
    assert [b['id'] for b in updated['blocks']] == [b['id'] for b in r['blocks']]
    assert len(updated['changes']) == 1 and updated['changes'][0]['field'] == 'title'


def test_ordinary_agent_tool_includes_automatic_title_renames(tmp_path):
    import json
    from src.chatbox.citations import EvidenceRegistry
    from src.db.sessions import SessionStore
    from src.reports.store import for_sessions
    from src.reports.tools import execute_report_tool
    sessions = SessionStore(str(tmp_path / 'sessions.db'))
    sessions.create_session(session_id='s')
    reports = for_sessions(sessions)
    r = reports.save('s', 'Project X', 'Project X details.', [])
    args = dict(report_id=r['id'], expected_revision=1, edits=[dict(id='all', mode='replace_all', before='Project X', after='Project Y')])
    result = json.loads(execute_report_tool(sessions, 's', 'update_report', args, EvidenceRegistry('s')))
    assert result['report']['title'] == 'Project Y'
    assert reports.read('s', r['id'])['title'] == 'Project Y'


def test_plain_replacement_text_is_escaped_instead_of_becoming_active_markdown(store):
    r = create(store, '**A** and A')
    updated = store.save_edits('s', r['id'], [dict(id='literal', mode='replace_all', before='A', after='[B](https://other.test)')], [], expected_revision=1)
    from src.reports.text_map import visible_text
    assert visible_text(updated['markdown']) == '[B](https://other.test) and [B](https://other.test)'
    assert r'\[B\]' in updated['markdown']


def test_citations_and_code_are_not_accidentally_included_in_replace_all(store):
    from src.chatbox.citations import EvidenceRegistry
    evidence = EvidenceRegistry('s').register('Original evidence.', {'file_id': 'A'})
    source = f'A[cite:{evidence["id"]}]\n\n```python\nA = 1\n```\n'
    r = store.save('s', 'Report', source, [evidence])
    updated = store.save_edits('s', r['id'], [dict(id='all', mode='replace_all', before='A', after='B')], [evidence], expected_revision=1)
    assert updated['markdown'] == source.replace('A[cite:', 'B[cite:')


def test_precise_source_ranges_are_recomputed_after_an_unchanged_block_moves(store):
    r = create(store, '## Heading\n\nA A A')
    c = anchored_comment(store, r, 'A', 1, 2, line=3)
    heading = r['blocks'][0]
    store.save_edits('s', r['id'], [dict(id='intro', block_id=heading['id'], block_hash=heading['hash'], before='## Heading', after='Preface.\n\n## Heading')], [], expected_revision=1)
    pending = store.pending_comments('s')[0]
    assert pending['anchor']['start_line'] == 5
    assert pending['anchor']['selection_start'] == 1
    assert pending['anchor']['source_ranges'][0]['start'] == c['anchor']['source_ranges'][0]['start'] + len('Preface.\n\n')


def test_model_tool_contract_describes_literal_operations_and_keeps_semantic_scope_flexible():
    from src.prompts import CHAT_REPORT_EDIT_SCHEMA, CHAT_REPORT_REVIEW_RULES
    assert set(CHAT_REPORT_EDIT_SCHEMA['properties']['mode']['enum']) == {'block', 'block_range', 'section', 'selection', 'replace_all'}
    assert 'comment_id' in CHAT_REPORT_EDIT_SCHEMA['properties']
    assert 'location cue' in CHAT_REPORT_REVIEW_RULES
    assert 'mode=selection' in CHAT_REPORT_REVIEW_RULES
    assert 'mode=replace_all' in CHAT_REPORT_REVIEW_RULES


def test_ordinary_chat_replace_all_reports_the_actual_count_and_is_idempotent(tmp_path):
    from src.chatbox.citations import EvidenceRegistry
    from src.db.sessions import SessionStore
    from src.reports.store import for_sessions
    from src.reports.tools import execute_report_tool
    sessions = SessionStore(str(tmp_path / 'sessions.db'))
    sessions.create_session(session_id='s')
    reports = for_sessions(sessions)
    r = create(reports, 'A A\n\n**A**')
    args = dict(report_id=r['id'], expected_revision=1, edits=[dict(id='all', mode='replace_all', before='A', after='B')])
    result = execute_report_tool(sessions, 's', 'update_report', args, EvidenceRegistry('s'), operation_id='all-call')
    import json
    assert json.loads(result)['operations'][0]['match_count'] == 3
    assert execute_report_tool(sessions, 's', 'update_report', args, EvidenceRegistry('s'), operation_id='all-call') == result


def test_encoded_and_bare_urls_are_preserved_while_nearby_visible_text_changes(store):
    source = '<https://example.test/%E4%B8%AD/A> and https://example.test/A then A'
    r = create(store, source)
    updated = store.save_edits('s', r['id'], [dict(id='all', mode='replace_all', before='A', after='B')], [], expected_revision=1)
    assert updated['markdown'] == source[:-1] + 'B'
    assert updated['edit_operations'][0]['match_count'] == 1


def test_literal_match_crossing_markup_either_preserves_the_display_or_rejects_atomically(store):
    r = create(store, 'A**B**C and ABC')
    with pytest.raises(ValueError, match='Markdown structure'):
        store.save_edits('s', r['id'], [dict(id='all', mode='replace_all', before='ABC', after='X')], [], expected_revision=1)
    assert store.read('s', r['id'])['revision'] == 1


def test_replacement_cannot_expand_a_small_report_beyond_the_document_limit(store):
    r = create(store, 'A ' * 100)
    with pytest.raises(ValueError):
        store.save_edits('s', r['id'], [dict(id='all', mode='replace_all', before='A', after='B' * 2000)], [], expected_revision=1)
    assert store.read('s', r['id'])['revision'] == 1
