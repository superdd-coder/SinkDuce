"""Quick Chat resets reuse IDs without reviving report workers from an old session."""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from tests.test_chat_report_api import api


def create_session(client, sid):
    response = client.post('/api/sessions', json={'id': sid, 'collections': ['col-a']})
    assert response.status_code == 201, response.text


def clear_session(client, sid):
    assert client.delete(f'/api/sessions/{sid}').status_code == 204
    create_session(client, sid)


def create_report(sessions, sid, title='Fresh report'):
    from src.chatbox.citations import EvidenceRegistry
    from src.reports.tools import execute_report_tool
    result = json.loads(execute_report_tool(sessions, sid, 'create_report',
        {'title': title, 'markdown': 'A new report.'}, EvidenceRegistry(sid)))
    assert 'report' in result, result
    return result['report']


@pytest.mark.parametrize('sid', ['quick_col-a', 'meeting_m1', 'group_g1'])
def test_recreated_quick_chat_session_can_save_reports_without_restart(api, sid):
    from src.services import services
    client, reports, _ = api
    create_session(client, sid)
    previous_ids = set()
    for _ in range(3):
        current = create_report(services.session_store, sid)
        assert current['id'] not in previous_ids
        previous_ids.add(current['id'])
        listing = client.get(f'/api/sessions/{sid}/reports')
        assert listing.status_code == 200
        assert [r['id'] for r in listing.json()] == [current['id']]
        clear_session(client, sid)
        assert reports.list(sid) == []


def test_old_request_store_cannot_write_or_read_the_recreated_session(api):
    from src.services import services
    from src.reports.routes import _store
    client, reports, _ = api
    sid = 'quick_col-a'
    create_session(client, sid)
    late_worker = _store(sid)
    old = create_report(services.session_store, sid, 'Old report')
    clear_session(client, sid)
    current = create_report(services.session_store, sid)

    assert late_worker.list(sid) == []
    with pytest.raises(FileNotFoundError):
        late_worker.save(sid, 'Late report', 'Stale content.', [])
    with pytest.raises(FileNotFoundError):
        late_worker.read(sid, current['id'])
    with pytest.raises(FileNotFoundError):
        late_worker.write_export(sid, current['id'], 1, 'html', b'stale', status='ready')
    assert not (reports.root / sid / old['id']).exists()
    assert reports.read(sid, current['id'])['markdown'] == 'A new report.'
    assert not (reports.root / sid / current['id'] / 'exports').exists()


def test_idempotent_session_creation_keeps_current_report_workers_valid(api):
    from src.reports.routes import _store
    client, _, _ = api
    sid = 'quick_col-a'
    create_session(client, sid)
    worker = _store(sid)
    first = worker.save(sid, 'Report', 'Original.', [])
    create_session(client, sid)
    updated = worker.save(sid, 'Report', 'Updated.', [], report_id=first['id'], expected_revision=1)
    assert updated['markdown'] == 'Updated.'


def test_reset_only_revokes_workers_for_the_cleared_session(api):
    from src.reports.routes import _store
    client, _, _ = api
    create_session(client, 'quick_a')
    create_session(client, 'quick_b')
    cleared = _store('quick_a')
    other = _store('quick_b')
    clear_session(client, 'quick_a')
    with pytest.raises(FileNotFoundError):
        cleared.save('quick_a', 'Late', 'Old work.', [])
    assert other.save('quick_b', 'Report', 'Unaffected.', [])['markdown'] == 'Unaffected.'


def test_export_finishing_after_reset_does_not_restore_deleted_report_files(api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from src.reports.routes import _store
    from src.reports import service
    from src.services import services

    client, reports, _ = api
    sid = 'quick_a'
    create_session(client, sid)
    old = create_report(services.session_store, sid)
    worker = _store(sid)
    started, release = Event(), Event()

    def delayed_render(*args, **kwargs):
        started.set()
        assert release.wait(5), 'The reset must complete while rendering is paused'
        return b'old export'

    monkeypatch.setattr(service, 'render_html', delayed_render)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(service._run_export, worker, sid, old['id'], 1, 'html')
        try:
            assert started.wait(5), 'The exporter should reach its renderer'
            clear_session(client, sid)
            current = create_report(services.session_store, sid)
        finally:
            release.set()
        task.result(timeout=5)
    assert not (reports.root / sid / old['id']).exists()
    assert reports.read(sid, current['id'])['markdown'] == 'A new report.'


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('sid', ['quick_col-a', 'meeting_m1'])
def test_agent_report_tool_keeps_the_incarnation_from_turn_start(api, monkeypatch, sid, streaming):
    from src.chatbox.agent import ChatboxAgent
    from src.reports.store import for_sessions
    from src.services import services
    from tests.test_note_create_confirm import _text_round, _tool_round
    from tests.test_chatbox_agent import _fake_llm_response

    client, _, _ = api
    create_session(client, sid)
    sessions = services.session_store
    llm = MagicMock()
    args = {'title': 'Late report', 'markdown': 'From the deleted conversation.'}
    calls = 0

    def response(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            # A reset completes while the old model request is in flight.
            clear_session(client, sid)
            if streaming:
                return _tool_round('create_report', json.dumps(args))
            return _fake_llm_response(tool_calls=[{'id': 'late', 'type': 'function',
                'function': {'name': 'create_report', 'arguments': json.dumps(args)}}])
        return _text_round('Finished.') if streaming else _fake_llm_response(content='Finished.')

    llm._client.chat.completions.create.side_effect = response
    agent = ChatboxAgent(sessions, llm, MagicMock())
    monkeypatch.setattr('src.chatbox.agent._build_surface_context', lambda *a, **k: '')
    monkeypatch.setattr(agent, '_build_catalog_text', lambda *a, **k: '')
    monkeypatch.setattr(agent, '_has_meetings', lambda *a, **k: False)
    if streaming:
        async def collect():
            return [e async for e in agent.chat_stream(sid, 'Create a report', mode='direct', thinking=False)]
        events = asyncio.run(collect())
        assert not next(e for e in events if e['type'] == 'done').get('report_changes')
    else:
        agent.chat(sid, 'Create a report', mode='direct')

    assert for_sessions(sessions).list(sid) == []
    assert not (for_sessions(sessions).root / sid).exists()
    create_report(sessions, sid)
