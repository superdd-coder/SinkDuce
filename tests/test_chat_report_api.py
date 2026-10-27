from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.routes import sessions
    from src.reports.routes import router
    from src.reports.store import for_sessions
    from src.db.sessions import SessionStore
    from src.services import services
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.create_session(session_id="owned", created_by="local")
    store.create_session(session_id="foreign", created_by="another-user")
    monkeypatch.setattr(services, "session_store", store)
    monkeypatch.setattr("src.chatbox.image_memory.default_base_dir", lambda: tmp_path / "images")
    reports = for_sessions(store)
    report = reports.save("owned", "中文报告", "## Findings\n\nA report.", [])
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(sessions.router, prefix="/api")
    return TestClient(app), reports, report


def test_report_api_lists_revisions_exports_and_deletes_with_session(api):
    client, store, report = api
    prefix = f'/api/sessions/owned/reports/{report["id"]}'
    assert client.get("/api/sessions/owned/reports").json()[0]["title"] == "中文报告"
    assert "operations" not in client.get("/api/sessions/owned/reports").json()[0]
    assert client.get(prefix + "/revisions").json()[0]["revision"] == 1
    assert client.get(prefix + "/revisions").json()[0]["session_id"] == "owned"
    assert client.get(prefix + "/revisions/1").json()["markdown"].startswith("## Findings")
    assert client.get(prefix + "/revisions/1/exports/html/download").status_code == 409
    started = client.post(prefix + "/revisions/1/exports", json={"format": "html"})
    assert started.status_code == 202, started.text
    state = client.get(prefix + "/revisions/1/exports/html").json()
    assert state["status"] == "ready"
    download = client.get(prefix + "/revisions/1/exports/html/download")
    assert download.status_code == 200 and "中文报告" in download.text
    assert download.headers["content-disposition"].startswith("attachment;")
    assert client.delete("/api/sessions/owned").status_code == 204
    assert not (store.root / "owned").exists()
    assert client.get(prefix + "/revisions/1").status_code == 404


def test_history_menu_api_replays_restores_and_rejects_stale_requests(api):
    client, store, report = api
    root = f'/api/sessions/owned/reports/{report["id"]}'
    newer = store.save('owned', 'Updated', 'Changed.', [], report_id=report['id'], expected_revision=1)
    response = client.get(root + '/history')
    assert response.status_code == 200, response.text
    assert [r['kind'] for r in response.json()] == ['edit', 'create']
    request = dict(expected_revision=2, operation_id='restore-test')
    restored = client.post(root + '/history/1/restore', json=request)
    assert restored.status_code == 200, restored.text
    assert restored.json()['markdown'] == report['markdown']
    assert restored.json()['revision'] == 3
    assert client.post(root + '/history/1/restore', json=request).json()['revision'] == 3
    assert restored.json()['history_revision'] == 1
    assert [r['revision'] for r in client.get(root + '/history').json()] == [1]
    assert client.get(root + '/revisions/2').json()['markdown'] == newer['markdown']
    assert client.post(root + '/history/2/restore', json={**request, 'operation_id':'stale'}).status_code == 409
    assert client.post(root + '/history/999/restore', json=dict(expected_revision=3,operation_id='missing')).status_code == 404
    assert client.get(root.replace('/owned/', '/foreign/') + '/history').status_code == 404
    assert client.post(root.replace('/owned/', '/foreign/') + '/history/1/restore',json=request).status_code == 404


def test_current_report_read_uses_actual_head_when_content_history_filters_a_save(api):
    from src.identity.actor import Actor
    client, store, original = api
    root = f'/api/sessions/owned/reports/{original["id"]}'
    current = store.save('owned', original['title'], original['markdown'], [],
                         report_id=original['id'], expected_revision=1,
                         review={'comments': [], 'results': []})
    assert client.get(root + '/history').json()[0]['revision'] == 1
    response = client.get(root)
    assert response.status_code == 200
    assert response.json()['revision'] == current['revision'] == 2
    assert response.json()['markdown'] == original['markdown']
    restored = client.post(root + '/history/1/restore', json={
        'expected_revision': response.json()['revision'], 'operation_id': 'restore-current-head'})
    assert restored.status_code == 200, restored.text
    assert client.get(root).json()['revision'] == restored.json()['revision'] == 3
    assert client.get(root.replace('/owned/', '/foreign/')).status_code == 404
    with patch('src.identity.get_actor', return_value=Actor(
            id='unrelated', kind='key', role_id='member')), patch('src.identity.may_see_everything', return_value=False):
        assert client.get(root).status_code == 404


def test_restore_conflict_is_identifiable_without_relying_on_english_error_text(api):
    client, store, report = api
    store.save('owned', report['title'], 'Changed.', [], report_id=report['id'], expected_revision=1)
    response = client.post(f'/api/sessions/owned/reports/{report["id"]}/history/1/restore',
                           json={'expected_revision': 1, 'operation_id': 'stale-restore'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'report_version_conflict'


def test_reports_cannot_be_read_from_another_session_or_by_another_owner(api):
    from src.identity.actor import Actor
    client, _, report = api
    assert client.get(f'/api/sessions/foreign/reports/{report["id"]}/revisions/1').status_code == 404
    actor = Actor(id="unrelated", kind="key", role_id="member")
    with patch("src.identity.get_actor", return_value=actor), patch("src.identity.may_see_everything", return_value=False):
        assert client.get("/api/sessions/owned/reports").status_code == 404


def test_pdf_failure_preserves_other_exports_and_can_retry(api, monkeypatch):
    from src.reports import service
    client, store, report = api
    prefix = f'/api/sessions/owned/reports/{report["id"]}/revisions/1/exports'
    def fail(*args, **kwargs):
        raise ValueError("PDF export requires LibreOffice Writer")
    monkeypatch.setattr(service, "render_pdf", fail)
    client.post(prefix, json={"format": "pdf"})
    assert client.get(prefix + "/pdf").json()["status"] == "failed"
    client.post(prefix, json={"format": "docx"})
    assert client.get(prefix + "/docx").json()["status"] == "ready"
    monkeypatch.setattr(service, "render_pdf", lambda report, **kwargs: b"%PDF-1.4\nready")
    client.post(prefix, json={"format": "pdf"})
    assert client.get(prefix + "/pdf").json()["status"] == "ready"


def test_comment_api_is_authorized_persistent_and_revision_scoped(api):
    client, store, report = api
    root = f'/api/sessions/owned/reports/{report["id"]}'
    body = {'text': 'Explain the finding with evidence.', 'anchor': {'start_line': 3, 'end_line': 3, 'quote': 'A report.'}}
    created = client.post(root + '/revisions/1/comments', json=body)
    assert created.status_code == 201, created.text
    c = created.json()
    assert client.get(root + '/revisions/1/comments').json()[0]['id'] == c['id']
    assert client.get('/api/sessions/owned/report-comments').json()[0]['id'] == c['id']
    changed = client.patch(root + '/comments/' + c['id'], json={'text': 'Shorter request.'})
    assert changed.status_code == 200 and changed.json()['text'] == 'Shorter request.'
    assert client.post(root + '/revisions/1/comments', json={**body, 'anchor': {**body['anchor'], 'quote': 'fake'}}).status_code == 400
    assert client.get(root.replace('/owned/', '/foreign/') + '/revisions/1/comments').status_code == 404
    assert client.delete(root + '/comments/' + c['id']).status_code == 204
    assert client.get('/api/sessions/owned/report-comments').json() == []


def test_comment_api_preserves_server_block_targets_and_rejects_a_forged_target(api):
    client, store, _ = api
    report = store.save('owned', 'Repeated passages', 'Repeated.\n\nRepeated.\n', [])
    root = f'/api/sessions/owned/reports/{report["id"]}'
    read = client.get(root + '/revisions/1').json()
    first, second = read['blocks']
    anchor = dict(start_line=3, end_line=3, quote='Repeated.', block_id=second['id'], block_hash=second['hash'], end_block_id=second['id'], end_block_hash=second['hash'])
    response = client.post(root + '/revisions/1/comments', json=dict(text='Check this specific passage.', anchor=anchor))
    assert response.status_code == 201, response.text
    assert response.json()['anchor']['block_id'] == second['id']
    assert client.get(root + '/revisions/1/comments').json()[0]['anchor']['end_block_hash'] == second['hash']
    forged = {**anchor, 'block_id': first['id']}
    assert client.post(root + '/revisions/1/comments', json=dict(text='Wrong location.', anchor=forged)).status_code == 400
    invalid_hash = {**anchor, 'block_hash': 'invalid'}
    assert client.post(root + '/revisions/1/comments', json=dict(text='Bad hash.', anchor=invalid_hash)).status_code == 422
    assert store.read('owned', report['id'])['revision'] == 1


def test_exact_selection_round_trips_through_api_and_client_cannot_forge_source_ranges(api):
    client, store, _ = api
    r = store.save('owned', 'Repeated text', '🧭 ' + 'A ' * 200, [])
    root = f'/api/sessions/owned/reports/{r["id"]}/revisions/1/comments'
    anchor = dict(start_line=1, end_line=1, quote='A', selection_start=101, selection_end=102,
                  source_ranges=[dict(start=0, end=1, before='forged')])
    response = client.post(root, json=dict(text='Only this occurrence.', anchor=anchor))
    assert response.status_code == 201, response.text
    saved = response.json()['anchor']
    assert saved['selection_start'] == 101
    assert saved['source_ranges'] == [dict(start=202, end=203, before='A')]
    assert client.get(root).json()[0]['anchor'] == saved
    assert client.post(root, json=dict(text='Invalid offset.', anchor={**anchor, 'selection_start': 0, 'selection_end': 1})).status_code == 400
    assert client.post(root, json=dict(text='Invalid coordinate type.', anchor={**anchor, 'selection_start': True})).status_code == 422


def test_review_history_retains_no_change_outcomes_without_creating_empty_versions(api):
    client,store,r=api
    c=store.create_comment('owned',r['id'],1,text='Verify this claim.',anchor={'start_line':3,'end_line':3,'quote':'A report.'},author='local')
    batch=store.freeze_reviews('owned',[{'report_id':r['id'],'revision':1,'comment_ids':[c['id']]}],'')[0]
    store.apply_review('owned',r['id'],batch['id'],[],[{'comment_id':c['id'],'status':'unchanged','summary':'Kept the original wording because the source already supports it.','edit_ids':[]}],[])
    root=f'/api/sessions/owned/reports/{r["id"]}'
    response=client.get(root+'/reviews')
    assert response.status_code==200,response.text
    assert response.json()[0]['results'][0]['status']=='unchanged'
    assert 'runtime_id' not in response.json()[0]
    assert len(client.get(root+'/revisions').json())==1


def test_comment_only_send_uses_the_same_agent_flow_and_rejects_stale_batches(api, monkeypatch):
    from src.services import services
    client,store,r=api
    c=store.create_comment('owned',r['id'],1,text='Rewrite this finding.',author='local',anchor={'start_line':3,'end_line':3,'quote':'A report.'})
    received={}
    async def stream(sid,content,**kwargs):
        received.update(sid=sid,content=content,**kwargs)
        yield {'type':'done','sources':[]}
    monkeypatch.setattr(services,'chatbox_agent',SimpleNamespace(chat_stream=stream))
    request={'report_id':r['id'],'revision':1,'comment_ids':[c['id']]}
    response=client.post('/api/sessions/owned/messages',json={'content':'','report_reviews':[request]})
    assert response.status_code==200,response.text
    assert received['report_reviews']==[request] and received['content']
    store.save('owned','Revised','New.',[],report_id=r['id'],expected_revision=1)
    assert client.post('/api/sessions/owned/messages',json={'content':'','report_reviews':[request]}).status_code==409


def test_review_decision_api_preserves_history_and_followup_is_a_current_comment(api):
    client,store,report=api
    root=f'/api/sessions/owned/reports/{report["id"]}'
    c=store.create_comment('owned',report['id'],1,text='Clarify.',author='local',anchor=dict(start_line=3,end_line=3,quote='A report.'))
    batch=store.freeze_reviews('owned',[dict(report_id=report['id'],revision=1,comment_ids=[c['id']])],'')[0]
    saved=store.apply_review('owned',report['id'],batch['id'],[dict(id='e',before='A report.',after='A clearer report.')],[dict(comment_id=c['id'],status='applied',summary='Clarified the statement.',edit_ids=['e'])],[])
    g=saved['suggestions'][0]
    request=dict(expected_revision=2,operation_id='api-accept',decision='accepted')
    response=client.post(root+f'/suggestions/{g["id"]}/decision',json=request)
    assert response.status_code==200,response.text
    assert response.json()['suggestions'][0]['decision']=='accepted'
    assert client.post(root+f'/suggestions/{g["id"]}/decision',json=request).json()['revision']==3
    follow=client.post(root+f'/suggestions/{g["id"]}/follow-up',json=dict(expected_revision=3,operation_id='api-follow',text='Explain the evidence.'))
    assert follow.status_code==201,follow.text
    assert 'A clearer report.' in follow.json()['anchor']['quote']
    assert client.get('/api/sessions/owned/report-comments').json()[0]['id']==follow.json()['id']
    assert client.post(root.replace('/owned/','/foreign/')+f'/suggestions/{g["id"]}/decision',json=request).status_code==404
    assert client.post(root+f'/suggestions/{g["id"]}/decision',json={**request,'decision':'invalid'}).status_code==422
