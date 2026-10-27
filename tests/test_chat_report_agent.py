import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.chatbox.agent import ChatboxAgent
from src.db.sessions import SessionStore
from tests.test_note_create_confirm import _text_round, _tool_round


@pytest.fixture(autouse=True)
def isolated_coverage_model(monkeypatch):
    # Orchestration tests use scripted tool streams; coverage has separate tests.
    monkeypatch.setattr('src.reports.validation.audit_review', lambda *a: [])


@pytest.mark.parametrize("sid,mode", [("main", "agentic"), ("quick_col_a", "direct"), ("meeting_a", "direct"), ("group_a", "direct")])
def test_every_chat_surface_can_save_a_report_and_restore_its_reference(tmp_path, monkeypatch, sid, mode):
    from src.chatbox import agent as module
    from src.reports.store import for_sessions
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.create_session(session_id=sid, collections=["col_a"])
    llm = MagicMock()
    llm._client.chat.completions.create.side_effect = [
        _tool_round("create_report", json.dumps({"title": "Report", "markdown": "## Result\n\nA draft."})),
        _text_round(""),
    ]
    monkeypatch.setattr(module, "_build_surface_context", lambda *a, **k: "")
    agent = ChatboxAgent(session_store=store, chat_llm=llm, agentic_service=MagicMock())
    monkeypatch.setattr(agent, "_build_catalog_text", lambda *a, **k: "")
    monkeypatch.setattr(agent, "_has_meetings", lambda *a, **k: False)
    tools, _, _ = agent._resolve_tools_and_prompt(mode, sid)
    assert {"create_report", "get_report", "update_report"} <= {t["function"]["name"] for t in tools}
    async def run():
        return [event async for event in agent.chat_stream(sid, "Write a report", mode=mode, thinking=False)]
    events = asyncio.run(run())
    done = next(e for e in events if e["type"] == "done")
    assert done["report_changes"][0]["title"] == "Report"
    saved = for_sessions(store).list(sid)
    assert len(saved) == 1
    final = [m for m in store.get_messages(sid) if m.role == "assistant" and not isinstance(m.metadata.get("tool_calls"), list)][-1]
    assert final.metadata["report_changes"][0]["id"] == saved[0]["id"]
    current = for_sessions(store).read(sid, saved[0]['id'])
    target = current['blocks'][-1]
    llm._client.chat.completions.create.side_effect = [
        _tool_round("get_report", json.dumps({"report_id": saved[0]["id"]})),
        _tool_round("update_report", json.dumps({"report_id": saved[0]["id"], "expected_revision": 1, "edits": [
            {"id":"e1", "block_id":target['id'], "block_hash":target['hash'], "before":"A draft.", "after":"Revised."}]})),
        _text_round("Updated."),
    ]
    asyncio.run(run())
    assert for_sessions(store).read(sid, saved[0]["id"])["revision"] == 2


def test_ordinary_answer_receives_stable_citation_ids_and_persists_full_excerpt(tmp_path, monkeypatch):
    from src.chatbox import agent as module
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.create_session(session_id="quick_col_a", collections=["col_a"])
    llm = MagicMock()
    calls = 0
    def response(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _tool_round("lookup_collection", '{"query":"A fact"}')
        text = "\n".join(str(m.get("content")) for m in kwargs["messages"] if m["role"] == "tool")
        match = re.search(r'e_[a-f0-9]{20}', text)
        assert match, "The answering model must see server-issued citation identities"
        return _text_round(f"A supported fact.[cite:{match.group()}]")
    llm._client.chat.completions.create.side_effect = response
    direct = MagicMock()
    direct.retrieve.return_value = SimpleNamespace(context="A supported fact. " * 30, chunks=[
        SimpleNamespace(text="A supported fact. " * 30, score=.9,
            metadata={"file_id": "file-a", "collection": "col_a", "page_number": 3})])
    monkeypatch.setattr(module, "_build_surface_context", lambda *a, **k: "")
    agent = ChatboxAgent(session_store=store, chat_llm=llm, agentic_service=MagicMock(), direct_module=direct)
    monkeypatch.setattr(agent, "_has_meetings", lambda *a, **k: False)
    async def run():
        return [event async for event in agent.chat_stream("quick_col_a", "Find a fact", mode="direct", thinking=False)]
    events = asyncio.run(run())
    done = next(e for e in events if e["type"] == "done")
    assert len(done["citations"]) == 1
    assert len(done["citations"][0]["quote"]) > 200
    assert done["citations"][0]["location"]["page_number"] == 3
    assert store.get_messages("quick_col_a")[-1].metadata["citations"] == done["citations"]


def test_interrupted_stream_preserves_report_and_citation_metadata(tmp_path):
    from src.chatbox.citations import EvidenceRegistry
    from src.chatbox.stream_handler import ChatStreamHandler
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.create_session(session_id="main")
    store.add_message("main", "user", "Create report")
    agent = ChatboxAgent(session_store=store, chat_llm=MagicMock(), agentic_service=MagicMock())
    r = EvidenceRegistry("main")
    evidence = r.register("Original fact", {"file_id": "a"})
    reference = {"id": "report-a", "session_id": "main", "title": "Saved", "revision": 1}
    async def interrupted(*args, **kwargs):
        yield {"type": "citations", "citations": [evidence]}
        yield {"type": "tool_result", "report_changes": [reference]}
        yield {"type": "token", "content": f"Fact[cite:{evidence['id']}]"}
        raise RuntimeError("Network interrupted")
    agent.chat_stream = interrupted
    async def run():
        return [e async for e in ChatStreamHandler(agent).handle("main", "Create report")]
    asyncio.run(run())
    final = store.get_messages("main")[-1]
    assert final.metadata["citations"] == [evidence]
    assert final.metadata["report_changes"] == [reference]
    assert final.metadata["interrupted"] is True


def test_inline_attachment_citations_are_available_before_the_first_answer_token(tmp_path, monkeypatch):
    from src.chatbox import agent as module
    from src.chat_attachments.store import for_sessions
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.create_session(session_id="main")
    files = for_sessions(store)
    a = files.create("main", "Evidence.txt", b"A visible fact.")
    files.finish("main", a.id, content="A visible fact.", position_map=[], images=[], parser="local")
    llm = MagicMock()
    def response(**kwargs):
        text = json.dumps(kwargs["messages"], ensure_ascii=False)
        identity = re.search(r"e_[a-f0-9]{20}", text)
        assert identity
        return _text_round(f"A visible fact.[cite:{identity.group()}]")
    llm._client.chat.completions.create.side_effect = response
    agent = ChatboxAgent(session_store=store, chat_llm=llm, agentic_service=MagicMock())
    monkeypatch.setattr(module, "_build_surface_context", lambda *a, **k: "")
    monkeypatch.setattr(module, "_chat_model_is_visual", lambda *a, **k: False)
    monkeypatch.setattr(agent, "_build_catalog_text", lambda *a, **k: "")
    monkeypatch.setattr(agent, "_has_meetings", lambda *a, **k: False)
    async def run():
        return [e async for e in agent.chat_stream("main", "Read this", attachment_ids=[a.id], thinking=False)]
    events = asyncio.run(run())
    first_token = next(i for i, e in enumerate(events) if e["type"] == "token")
    assert any(e["type"] == "citations" for e in events[:first_token])
    assert events[-1]["citations"][0]["quote"] == "A visible fact."


def test_report_only_interruption_is_not_mistaken_for_an_empty_tool_placeholder(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.create_session(session_id="main")
    store.add_message("main", "user", "Write a report")
    store.add_message("main", "assistant", "", metadata={"tool_calls": [{"id": "call-a"}]})
    agent = ChatboxAgent(session_store=store, chat_llm=MagicMock(), agentic_service=MagicMock())
    ref = {"id": "a", "session_id": "main", "title": "Saved", "revision": 1}
    agent.persist_interrupted_answer("main", "", report_changes=[ref])
    assert store.get_messages("main")[-1].metadata["report_changes"] == [ref]


def test_failed_report_is_shown_as_error_and_does_not_publish_a_card(tmp_path, monkeypatch):
    from src.chatbox import agent as module
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.create_session(session_id="main")
    llm = MagicMock()
    llm._client.chat.completions.create.side_effect = [
        _tool_round("create_report", json.dumps({"title": "Review", "markdown": "Fake[cite:invented]"})),
        _text_round("Please retry."),
    ]
    agent = ChatboxAgent(session_store=store, chat_llm=llm, agentic_service=MagicMock())
    monkeypatch.setattr(module, "_build_surface_context", lambda *a, **k: "")
    monkeypatch.setattr(agent, "_build_catalog_text", lambda *a, **k: "")
    monkeypatch.setattr(agent, "_has_meetings", lambda *a, **k: False)
    async def run():
        return [e async for e in agent.chat_stream("main", "Write report", thinking=False)]
    events = asyncio.run(run())
    failed = next(e for e in events if e["type"] == "tool_result")
    assert failed["status"] == "error"
    assert not failed.get("report_changes")
    assert not events[-1]["report_changes"]


@pytest.mark.parametrize('sid,mode', [('main','agentic'), ('quick_col_a','direct'), ('meeting_a','direct'), ('group_a','direct')])
def test_review_batch_revises_once_and_preserves_comment_outcomes(tmp_path, monkeypatch, sid, mode):
    from src.chatbox import agent as module
    from src.reports.store import for_sessions
    store = SessionStore(str(tmp_path / 'sessions.db'))
    store.create_session(session_id=sid)
    reports = for_sessions(store)
    report = reports.save(sid, 'Draft', '## Section\n\nOld paragraph.\n\nKeep this.', [])
    c = reports.create_comment(sid, report['id'], 1, text='Rework this section, not just the selected word.',
        anchor={'start_line': 3, 'end_line': 3, 'quote': 'Old'}, author='local')
    llm = MagicMock()
    calls = 0
    def response(**kwargs):
        nonlocal calls
        calls += 1
        messages = str(kwargs['messages'])
        if calls == 1:
            assert c['id'] in messages and 'location cue' in messages
            return _tool_round('get_report', json.dumps({'report_id': report['id']}))
        if calls == 2:
            batch = reports._review_state(sid, report['id'])['batches']
            bid = next(iter(batch))
            return _tool_round('revise_report', json.dumps({'report_id': report['id'], 'batch_id': bid,
                'edits': [{'id': 'e1', 'block_id': report['blocks'][1]['id'], 'block_hash': report['blocks'][1]['hash'], 'before': 'Old paragraph.', 'after': 'New supporting argument.'}],
                'results': [{'comment_id': c['id'], 'status': 'applied', 'summary': 'Rebuilt the argument to explain the recommendation.', 'edit_ids': ['e1']}]}))
        return _text_round('Updated the argument.')
    llm._client.chat.completions.create.side_effect = response
    monkeypatch.setattr(module, '_build_surface_context', lambda *a, **k: '')
    agent = ChatboxAgent(session_store=store, chat_llm=llm, agentic_service=MagicMock())
    monkeypatch.setattr(agent, '_build_catalog_text', lambda *a, **k: '')
    monkeypatch.setattr(agent, '_has_meetings', lambda *a, **k: False)
    async def run():
        from src.prompts import CHAT_REPORT_REVIEW_DEFAULT_REQUEST
        return [e async for e in agent.chat_stream(sid, CHAT_REPORT_REVIEW_DEFAULT_REQUEST, mode=mode, thinking=False,
            report_reviews=[{'report_id': report['id'], 'revision': 1, 'comment_ids': [c['id']]}])]
    events = asyncio.run(run())
    assert events[-1]['report_changes'][0]['revision'] == 2
    saved = reports.read(sid, report['id'])
    assert saved['markdown'].endswith('Keep this.')
    assert saved['review']['results'][0]['comment_id'] == c['id']
    assert not reports.pending_comments(sid)
    user = next(m for m in store.get_messages(sid) if m.role == 'user')
    assert user.metadata['report_reviews'][0]['comments'][0]['text'] == c['text']
    assert user.metadata['display_content'] == ''


def test_interrupting_an_editing_turn_restores_its_pending_comments(tmp_path, monkeypatch):
    from src.chatbox import agent as module
    from src.reports.store import for_sessions
    sessions=SessionStore(str(tmp_path/'sessions.db'));sessions.create_session(session_id='s')
    reports=for_sessions(sessions);r=reports.save('s','Report','Original.',[])
    c=reports.create_comment('s',r['id'],1,text='Revise this.',anchor={'start_line':1,'end_line':1,'quote':'Original.'},author='local')
    async def stopped(*args,**kwargs):
        yield {'type':'token','content':'Editing'}
        raise RuntimeError('provider disconnected')
    agent=ChatboxAgent(session_store=sessions,chat_llm=MagicMock(),agentic_service=MagicMock())
    monkeypatch.setattr(agent,'_chat_stream_body',stopped)
    async def run():
        return [e async for e in agent.chat_stream('s','Revise',report_reviews=[{'report_id':r['id'],'revision':1,'comment_ids':[c['id']]}])]
    with pytest.raises(RuntimeError):asyncio.run(run())
    assert reports.pending_comments('s')[0]['id']==c['id']
    assert reports.read('s',r['id'])['revision']==1


def test_review_and_repair_contracts_use_each_comments_language_independently_of_document_language():
    from src.prompts import CHAT_REPORT_REVIEW_RULES, CHAT_REPORT_REVIEW_REPAIR_SYSTEM, CHAT_REPORT_TOOLS

    for instructions in (CHAT_REPORT_REVIEW_RULES, CHAT_REPORT_REVIEW_REPAIR_SYSTEM):
        assert 'comment.text' in instructions
        assert 'report language' in instructions
    revise = next(tool['function'] for tool in CHAT_REPORT_TOOLS if tool['function']['name'] == 'revise_report')
    summary = revise['parameters']['properties']['results']['items']['properties']['summary']['description']
    assert 'comment.text' in summary


def test_review_can_deliver_a_separate_rewrite_and_resolve_comments_without_editing_original(tmp_path):
    from src.chatbox.citations import EvidenceRegistry
    from src.reports.store import for_sessions
    from src.reports.tools import execute_report_tool

    sessions = SessionStore(str(tmp_path / 'sessions.db'))
    sessions.create_session(session_id='sample-session')
    reports = for_sessions(sessions)
    source = reports.save('sample-session', 'Sample assessment', 'This sample needs inspection.', [])
    comment = reports.create_comment('sample-session', source['id'], 1, text='请把整份报告改成中文。',
                                    author='local', anchor=dict(start_line=1, end_line=1, quote=source['markdown']))
    batch = reports.freeze_reviews('sample-session', [dict(report_id=source['id'], revision=1, comment_ids=[comment['id']])], '')[0]
    translated = reports.save('sample-session', '样品评估', '这批虚构样品需要检查。', [])
    audited = []

    def audit(original, task, proposed, results):
        audited.append(proposed['markdown'])
        return [] if proposed['markdown'] == translated['markdown'] else [dict(comment_id=comment['id'], required_action='Translate the separate deliverable.')]

    args = dict(report_id=source['id'], output_report_id=translated['id'], edits=[],
                results=[dict(comment_id=comment['id'], status='unchanged', summary='已另写中文稿，保留英文原稿供对照。', edit_ids=[])])
    first = json.loads(execute_report_tool(sessions, 'sample-session', 'revise_report', args, EvidenceRegistry('sample-session'), review_batches=[batch], review_validator=audit))
    assert 'report' in first, first
    assert first['report']['id'] == translated['id']
    assert audited == [translated['markdown']]
    assert reports.read('sample-session', source['id'])['markdown'] == source['markdown']
    assert reports.read('sample-session', source['id'])['revision'] == 1
    assert not reports.pending_comments('sample-session')
    assert reports.review_batch('sample-session', source['id'], batch['id'])['status'] == 'unchanged'
    repeated = json.loads(execute_report_tool(sessions, 'sample-session', 'revise_report', args, EvidenceRegistry('sample-session'), review_batches=[batch], review_validator=audit))
    assert repeated['report'] == first['report']
    assert len(reports.list('sample-session')) == 2


@pytest.mark.parametrize('failure', ['source-as-output', 'source-edits', 'incomplete-copy'])
def test_separate_deliverable_failure_keeps_source_and_review_pending(tmp_path, failure):
    from src.chatbox.citations import EvidenceRegistry
    from src.reports.store import for_sessions
    from src.reports.tools import execute_report_tool

    sessions = SessionStore(str(tmp_path / 'sessions.db'))
    sessions.create_session(session_id='sample-session')
    reports = for_sessions(sessions)
    source = reports.save('sample-session', 'Sample review', 'Original sample notes.', [])
    comment = reports.create_comment('sample-session', source['id'], 1, text='Rewrite the complete assessment.',
                                    author='local', anchor=dict(start_line=1, end_line=1, quote=source['markdown']))
    batch = reports.freeze_reviews('sample-session', [dict(report_id=source['id'], revision=1, comment_ids=[comment['id']])], '')[0]
    output = reports.save('sample-session', 'Separate assessment', 'Incomplete assessment.', [])
    args = dict(report_id=source['id'], output_report_id=source['id'] if failure == 'source-as-output' else output['id'],
                edits=[dict(id='bad', before=source['markdown'], after='Unwanted replacement.')] if failure == 'source-edits' else [],
                results=[dict(comment_id=comment['id'], status='unchanged', summary='Created a separate assessment.', edit_ids=[])])
    validator = lambda *args: [dict(comment_id=comment['id'], required_action='Finish the separate assessment.')]
    response = json.loads(execute_report_tool(sessions, 'sample-session', 'revise_report', args, EvidenceRegistry('sample-session'), review_batches=[batch], review_validator=validator))
    assert response.get('saved') is False
    assert reports.read('sample-session', source['id']) == source
    assert reports.review_batch('sample-session', source['id'], batch['id'])['status'] == 'running'
    reports.release_reviews('sample-session', [batch])
    assert reports.pending_comments('sample-session')[0]['id'] == comment['id']


def test_model_cannot_report_review_completion_without_saved_results(tmp_path, monkeypatch):
    from src.reports.store import for_sessions
    sessions=SessionStore(str(tmp_path/'sessions.db'));sessions.create_session(session_id='s')
    reports=for_sessions(sessions);r=reports.save('s','Report','Original.',[])
    c=reports.create_comment('s',r['id'],1,text='Revise this.',anchor={'start_line':1,'end_line':1,'quote':'Original.'},author='local')
    async def no_save(*args,**kwargs):
        yield {'type':'done','sources':[]}
    agent=ChatboxAgent(session_store=sessions,chat_llm=MagicMock(),agentic_service=MagicMock())
    monkeypatch.setattr(agent,'_chat_stream_body',no_save)
    async def run():
        return [e async for e in agent.chat_stream('s','Revise',report_reviews=[{'report_id':r['id'],'revision':1,'comment_ids':[c['id']]}])]
    events=asyncio.run(run())
    assert events[0]['type']=='report_review_incomplete'
    assert reports.pending_comments('s')[0]['id']==c['id']
