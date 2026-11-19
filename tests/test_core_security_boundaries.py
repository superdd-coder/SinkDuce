"""Access contracts using synthetic records and isolated storage."""
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.identity import Actor, reset_actor, set_actor


@contextmanager
def as_device(role="member", key="reader"):
    token = set_actor(Actor(id=key, kind="key", role_id=role))
    try:
        yield
    finally:
        reset_actor(token)


def client_for(router):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize(("host", "expected"), [
    ("localhost", True), ("127.10.20.30", True), ("::ffff:7f00:1", True),
    ("127.workstation.example.test", False), ("127.400.0.1", False),
])
def test_loopback_requires_an_address_or_localhost(host, expected):
    from src.pairing.service import is_loopback_host
    assert is_loopback_host(host) is expected


def test_gate_coverage_uses_path_segments():
    from src.pairing.middleware import assert_gate_route_coverage
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.get("/apiary")(lambda: {})
    with pytest.raises(RuntimeError):
        assert_gate_route_coverage(app)


def test_remote_member_has_no_box_management(tmp_path, monkeypatch):
    from src.main import app
    from src.pairing import service, store

    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    issued = service.issue_token(name="Reader", is_local=True)
    client = TestClient(app, base_url="http://unit.example.test")
    original = store.ensure_box()
    response = client.put("/api/pairing/box", json={"box_name": "Workshop"},
                          headers={"X-Pairing-Token": issued["token"]})
    assert response.status_code == 403
    assert store.ensure_box() == original


@pytest.mark.parametrize("path", ["pairing/sample.txt", "hub/sample.json", "identity/sample.json",
                                  "collections/one/meta.db-wal"])
def test_mcp_file_import_excludes_service_state(tmp_path, path):
    from src.paths import assert_readable_data_file
    with pytest.raises(ValueError):
        assert_readable_data_file(tmp_path / path, data_root=tmp_path)


@pytest.mark.parametrize("endpoint", ["preview_file", "get_extracted_text"])
def test_document_read_checks_explicit_collection_before_storage(monkeypatch, endpoint):
    from src.api.routes import documents
    from src.identity import acl
    monkeypatch.setattr(acl, "collection_allows", lambda *_: False)
    monkeypatch.setattr(documents, "_find_file_path", lambda *a, **kw: pytest.fail("unexpected disk lookup"))
    with as_device(), pytest.raises(HTTPException) as denial:
        getattr(documents, endpoint)("sample.txt", collection="private_library")
    assert denial.value.status_code == 404


def test_ingest_retry_checks_write_before_history(monkeypatch):
    from src.api.routes import documents
    monkeypatch.setattr(documents.history_store, "get_record", lambda *a: pytest.fail("unexpected lookup"))
    with as_device("visitor"), pytest.raises(HTTPException) as denial:
        documents.retry_ingest_history("row", documents.IngestRetryRequest(collection="library"))
    assert denial.value.status_code == 403


def test_note_operations_bound_to_actual_library(tmp_path, monkeypatch):
    from src.notes import routes, store
    monkeypatch.setattr(store, "NOTES_DIR", tmp_path)
    note = store.create_note("library_a", "Sample")
    client = client_for(routes.router)
    with as_device():
        assert client.get(f"/notes/library_b/{note.id}").status_code == 404
        assert client.get(f"/notes/library_b/{note.id}/propagation-preview").status_code == 404


def test_note_reader_cannot_add_images(tmp_path, monkeypatch):
    from src.notes import routes, store
    monkeypatch.setattr(store, "NOTES_DIR", tmp_path)
    note = store.create_note("library_a", "Sample")
    with as_device("visitor"):
        response = client_for(routes.router).post(f"/notes/library_a/{note.id}/images",
                                                  files={"file": ("sample.png", b"image")})
    assert response.status_code == 403
    assert not (tmp_path / note.id / "images").exists()


def test_session_creation_checks_existing_owner(tmp_path, monkeypatch):
    from src.api.routes import sessions
    from src.db.sessions import SessionStore
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = store.create_session(title="Sample", created_by="device_a")
    monkeypatch.setattr(sessions, "_get_store", lambda: store)
    with as_device(), pytest.raises(HTTPException) as denial:
        sessions.create_session(sessions.SessionCreateRequest(id=session.id))
    assert denial.value.status_code == 404


@pytest.mark.parametrize("sid", ["meeting_example", "group_example"])
def test_scoped_chat_respects_meeting_domain(tmp_path, monkeypatch, sid):
    from src.api.routes import sessions
    from src.db.sessions import SessionStore
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(sessions, "_get_store", lambda: store)
    with as_device("visitor"), pytest.raises(HTTPException) as denial:
        sessions.create_session(sessions.SessionCreateRequest(id=sid))
    assert denial.value.status_code == 404
    assert store.get_session(sid) is None


def test_recording_write_guard_runs_before_accepting_audio(tmp_path, monkeypatch):
    from src.meeting import routes, store
    monkeypatch.setattr(store, "MEETINGS_DIR", tmp_path)
    meeting = store.create_meeting(title="Practice")
    with as_device():
        response = client_for(routes.router).post(f"/meetings/{meeting.id}/recording-pcm", content=b"pcm")
    assert response.status_code == 403
    assert not store.recording_pcm_path(meeting.id).exists()


@pytest.mark.parametrize(("method", "path"), [("post", "sysaudio-warmup"), ("post", "sysaudio-stop"),
                                               ("get", "pcm")])
def test_desktop_audio_access_is_local_before_helper_call(monkeypatch, method, path):
    from src.api.routes import desktop
    from src import config
    monkeypatch.setattr(config, "is_desktop_runtime", lambda: True)
    monkeypatch.setattr(desktop, "_sysaudio_base", lambda: pytest.fail("unexpected helper access"))
    response = getattr(client_for(desktop.router), method)(f"/api/desktop/{path}")
    assert response.status_code == 404


def test_uploaded_page_served_with_restrictive_response_headers(tmp_path, monkeypatch):
    from src.api.routes import documents
    sample = tmp_path / "sample.html"
    sample.write_text("<p>Sample</p>")
    monkeypatch.setattr(documents, "_find_file_path", lambda *a, **kw: sample)
    response = documents.preview_file(sample.name)
    assert "sandbox" in response.headers.get("content-security-policy", "").split(";")
    assert response.headers.get("x-content-type-options") == "nosniff"


def test_secret_mode_applies_when_temp_file_is_opened(tmp_path, monkeypatch):
    from src.atomic_io import write_text_atomic
    import os, stat
    original = os.open
    observed = []
    def observe(path, flags, mode=0o777, **kwargs):
        fd = original(path, flags, mode, **kwargs)
        if flags & os.O_CREAT:
            observed.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return fd
    monkeypatch.setattr(os, "open", observe)
    previous = os.umask(0o022)
    try:
        target = tmp_path / "example"
        write_text_atomic(target, "sample", mode=0o600)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert observed == [0o600]
    finally:
        os.umask(previous)


def test_device_pairing_updates_do_not_lose_concurrent_keys(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from src.pairing import service, store
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    store.ensure_box()
    with ThreadPoolExecutor(max_workers=8) as pool:
        issued = list(pool.map(lambda i: service.issue_token(name=f"device-{i}", is_local=True), range(16)))
    assert {r["id"] for r in issued} == {r["id"] for r in store.load()}


def test_local_credential_is_bound_to_the_local_channel(tmp_path, monkeypatch):
    from src.main import app
    from src.pairing import service, store
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    key = service.ensure_local_key()
    ticket = service.mint_access_token(key["token"])
    client = TestClient(app, base_url="http://unit.example.test")
    for credential in (key["token"], ticket["access_token"]):
        assert client.get("/api/identity/me", headers={"X-Pairing-Token": credential}).status_code == 401


@pytest.mark.parametrize("path", ["/api/mcp/stage-content", "/api/mcp/upload", "/api/mcp/meeting-upload"])
def test_mcp_local_side_channels_follow_mcp_boundary(tmp_path, monkeypatch, path):
    from src.main import app
    from src.pairing import service, store
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    key = service.issue_token(name="Device", is_local=True)
    response = TestClient(app, base_url="http://unit.example.test").post(
        path, headers={"X-Pairing-Token": key["token"]}
    )
    assert response.status_code == 404


def test_file_lookup_validates_identifiers_before_touching_storage(tmp_path, monkeypatch):
    from src.api.routes import documents
    monkeypatch.setattr(documents, "COLLECTIONS_DIR", tmp_path)
    with pytest.raises((ValueError, HTTPException)):
        documents._find_file_path("__file__:invalid id", "library")


def test_nested_router_is_included_in_gate_inventory():
    from fastapi import APIRouter
    from src.pairing.middleware import assert_gate_route_coverage
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    router = APIRouter()
    router.websocket("/stream")(lambda: None)
    app.include_router(router, prefix="/public")
    with pytest.raises(RuntimeError):
        assert_gate_route_coverage(app)


def test_short_ticket_rejects_malformed_text(tmp_path, monkeypatch):
    from src.pairing import service, store
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    assert service.verify_access_token(service.ACCESS_TOKEN_PREFIX + "sample.é") is None


def test_note_graph_limits_metadata_to_readable_libraries(tmp_path, monkeypatch):
    from src.notes import routes, store
    from src.identity import acl
    monkeypatch.setattr(store, "NOTES_DIR", tmp_path)
    source = store.create_note("library_a", "Sample A")
    target = store.create_note("library_b", "Sample B")
    store._add_referenced_by(source.id, target.id)
    monkeypatch.setattr(acl, "collection_allows", lambda cid, tier, role: cid == "library_a")
    with as_device():
        client = client_for(routes.router)
        assert client.get(f"/notes/library_a/{source.id}").json()["extracted_into"] == []
        assert client.get(f"/notes/library_a/{source.id}/propagation-preview").json()["links"] == []


def test_upload_audio_has_its_own_storage_directory(tmp_path, monkeypatch):
    from src.meeting import store
    monkeypatch.setattr(store, "MEETINGS_DIR", tmp_path)
    meeting = store.create_meeting(title="Practice")
    audio = Path(store.save_audio(meeting.id, b"sample", "wav", original_filename="practice.wav"))
    assert audio.parent != store._meeting_dir(meeting.id)
    assert audio.is_relative_to(store._meeting_dir(meeting.id))
    assert audio.read_bytes() == b"sample"


def test_read_only_summary_cannot_queue_consolidation(monkeypatch):
    import asyncio
    from src.api.routes import info
    from src.tasks import task_manager
    monkeypatch.setattr(info, "_resolve_collection_id", lambda c: c)
    monkeypatch.setattr(task_manager, "create_task", lambda **kw: pytest.fail("unexpected work"))
    with as_device("visitor"), pytest.raises(HTTPException) as denial:
        asyncio.run(info.trigger_consolidation("library"))
    assert denial.value.status_code == 403


def test_note_content_and_cached_distillation_inherit_visibility(tmp_path, monkeypatch):
    from src.notes import store
    from src.identity import acl
    monkeypatch.setattr(store, "NOTES_DIR", tmp_path)
    note = store.create_note("library_b", "Practice")
    store.save_content(note.id, "A sample note")
    store.save_distillation(note.id, "A sample summary")
    monkeypatch.setattr(acl, "collection_allows", lambda *args: False)
    with as_device():
        assert store.get_content(note.id) is None
        assert store.get_distillation(note.id) is None


def test_image_fallback_honors_collection_visibility(tmp_path, monkeypatch):
    from src.file_mgmt import storage_paths
    from src.identity import acl
    monkeypatch.setattr(storage_paths, "COLLECTIONS_DIR", tmp_path)
    directory = tmp_path / "library_b" / "files" / "document" / "images"
    directory.mkdir(parents=True)
    (directory / "diagram.png").write_bytes(b"fixture")
    assert storage_paths.find_image_file(None, "document", "diagram") is not None
    monkeypatch.setattr(acl, "collection_allows", lambda *args: False)
    with as_device():
        assert storage_paths.find_image_file(None, "document", "diagram") is None


def test_file_resolution_stays_within_its_managed_file(tmp_path, monkeypatch):
    from src.file_mgmt import storage_paths
    monkeypatch.setattr(storage_paths, "COLLECTIONS_DIR", tmp_path / "libraries")
    directory = storage_paths.managed_file_dir("library", "document")
    directory.mkdir(parents=True)
    other = tmp_path / "sample.txt"
    other.write_text("Sample")
    (directory / "example.txt").symlink_to(other)
    assert storage_paths.resolve_version_blob("library", "document", None, "example.txt") is None


def test_read_only_device_cannot_change_eval_cases(monkeypatch):
    from src.api.routes import recall
    monkeypatch.setattr(recall, "_load_cases", lambda *args: pytest.fail("unexpected read"))
    with as_device("visitor"), pytest.raises(HTTPException) as denial:
        recall.delete_eval_case("library", "sample")
    assert denial.value.status_code == 403


def test_local_entry_rejects_ambiguous_headers_and_cross_site_without_origin():
    from src.pairing.service import is_local_scope, local_entry_port
    scope = {"type": "http", "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", local_entry_port()),
             "headers": [(b"host", b"localhost")]}
    assert is_local_scope(scope)
    for extra in [(b"host", b"unit.example.test"), (b"sec-fetch-site", b"cross-site")]:
        assert not is_local_scope({**scope, "headers": [*scope["headers"], extra]})


def test_corrupt_enterprise_acl_preserves_restrictions(tmp_path, monkeypatch):
    from ee.identity import acl
    path = tmp_path / "acl.json"
    path.write_text("{")
    monkeypatch.setattr(acl.core, "DOMAIN_ACL_PATH", path)
    assert "member" not in acl.load_domain_acl()["meeting"]["read"]
    path.write_text('{"domains":{"meeting":{"read":"invalid"}}}')
    assert "member" not in acl.load_domain_acl()["meeting"]["read"]


def test_task_read_write_and_meeting_scopes(monkeypatch):
    from types import SimpleNamespace
    from src.tasks import access, task_manager
    task = SimpleNamespace(id="sample_task", collection="library")
    monkeypatch.setattr(task_manager, "_task_args", {task.id: ("upload", {"collection": "library"})})
    with as_device("visitor"):
        from src.identity import get_actor
        assert access.can_see_task(get_actor(), task.id, task)
        assert not access.can_manage_task(get_actor(), task.id, task)
        task_manager._task_args[task.id] = ("transcribe", {"meeting_id": "sample_meeting"})
        assert not access.can_see_task(get_actor(), task.id, task)


def test_recall_search_checks_each_library_before_retrieval(monkeypatch):
    from src.api.routes import recall
    from src.identity import acl
    monkeypatch.setattr(acl, "collection_allows", lambda cid, tier, role: cid == "library_a")
    with as_device(), pytest.raises(HTTPException) as denial:
        recall.recall_search(recall.RecallSearchRequest(query="Practice", collections=["library_a", "library_b"]))
    assert denial.value.status_code == 404


def test_body_limit_counts_received_chunks():
    from fastapi import Request
    from src.main import _BodySizeLimitMiddleware
    app = FastAPI()
    app.add_middleware(_BodySizeLimitMiddleware, max_bytes=8)
    @app.post("/sample")
    async def sample(request: Request):
        return {"size": len(await request.body())}
    client = TestClient(app)
    assert client.post("/sample", content=iter([b"1234", b"5678"])).status_code == 200
    assert client.post("/sample", content=iter([b"1234", b"56789"])).status_code == 413


def test_unexpected_errors_do_not_echo_service_diagnostics():
    from src.main import _format_provider_error
    assert "fixture-value" not in _format_provider_error(ValueError("fixture-value"))[1]


def test_model_pack_requires_a_valid_checksum(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    from src.models import download
    archive = tmp_path / "sample.zip"
    archive.write_bytes(b"sample")
    monkeypatch.setattr(download, "_http_opener", lambda: SimpleNamespace(open=lambda *a, **kw: io.BytesIO(b"")))
    with pytest.raises(RuntimeError):
        download._verify_sha256(archive, "https://example.test/checksum")


def test_model_pack_checks_entire_layout_before_install(tmp_path):
    import zipfile
    from src.models import download
    archive = tmp_path / "sample.zip"
    target = tmp_path / "models"
    with zipfile.ZipFile(archive, "w") as contents:
        contents.writestr("onnx/example/model.txt", "Sample")
        contents.writestr("unrelated/example.txt", "Sample")
    with pytest.raises(RuntimeError):
        download._extract_onnx_zip(archive, target)
    assert not (target / "onnx").exists()
    with zipfile.ZipFile(archive, "w") as contents:
        contents.writestr("onnx/example/model.txt", "Sample")
        contents.writestr("ATTRIBUTION.md", "Sample")
        contents.writestr("LICENSES/example.txt", "Sample")
    download._extract_onnx_zip(archive, target)
    assert (target / "onnx/example/model.txt").read_text() == "Sample"


def test_windows_link_open_uses_os_url_handler(monkeypatch):
    from src import desktop_open
    import os
    calls = []
    monkeypatch.setattr(desktop_open.sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", calls.append, raising=False)
    monkeypatch.setattr(desktop_open.subprocess, "Popen", lambda *a: pytest.fail("unexpected command"))
    desktop_open.open_external_url("https://example.test/manual")
    assert calls == ["https://example.test/manual"]


def test_remote_open_url_does_not_launch_on_host(monkeypatch):
    from src.api.routes import desktop
    monkeypatch.setenv("SINKDUCE_DESKTOP", "1")
    monkeypatch.setattr("src.desktop_open.open_external_url", lambda *a: pytest.fail("unexpected launch"))
    response = client_for(desktop.router).post("/api/desktop/open-url", json={"url": "https://example.test"})
    assert response.status_code == 404


def test_note_catalog_filters_hidden_libraries(tmp_path, monkeypatch):
    from src.notes import store
    from src.identity import acl
    monkeypatch.setattr(store, "NOTES_DIR", tmp_path)
    store.create_note("library_a", "Sample A")
    store.create_note("library_b", "Sample B")
    monkeypatch.setattr(acl, "collection_allows", lambda cid, tier, role: cid == "library_a")
    with as_device():
        assert [note.collection for note in store.list_notes()] == ["library_a"]


def test_folder_import_excludes_linked_files(tmp_path):
    from src.parsers import parse_directory
    source = tmp_path / "sample.txt"
    source.write_text("Practice")
    folder = tmp_path / "import"
    folder.mkdir()
    (folder / "example.txt").symlink_to(source)
    assert parse_directory(folder) == []


def test_audio_url_credentials_are_scrubbed():
    from tests.test_log_token_scrubbing import _scrub
    assert "sample-value" not in _scrub("/api/meetings/example/audio?audio_token=sample-value")


def test_proxied_requests_require_device_identity(monkeypatch):
    from src.pairing import service
    monkeypatch.setattr(service, "_in_container", lambda: True)
    port = service.local_entry_port()
    scope = {"type": "http", "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", port),
             "headers": [(b"host", b"localhost"), (b"forwarded", b"for=192.0.2.1")]}
    assert not service.is_local_scope(scope)
    assert service.local_key_access(scope) != service.LOCAL_KEY_GRANT


def test_task_cleanup_preserves_tasks_in_other_libraries(monkeypatch):
    from src.identity import acl, get_actor
    from src.tasks import Task, TaskStatus, task_manager
    from src.tasks.access import clear_manageable_tasks
    tasks = {cid: Task(id=cid, filename="practice.txt", collection=cid, status=TaskStatus.COMPLETED)
             for cid in ("library_a", "library_b")}
    monkeypatch.setattr(task_manager, "tasks", tasks)
    monkeypatch.setattr(task_manager, "_task_args", {})
    monkeypatch.setattr(acl, "collection_allows", lambda cid, tier, role: cid == "library_a")
    with as_device():
        assert clear_manageable_tasks(get_actor()) == (1, 0)
    assert list(tasks) == ["library_b"]


def test_open_device_stream_stops_after_revocation(tmp_path, monkeypatch):
    from src.pairing import service, store
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    key = service.issue_token(name="paired-device", is_local=True)
    clock = [10.0]
    active = service.device_stream_guard(Actor(id=key["id"], kind="key", role_id="member"),
                                         now=lambda: clock[0])
    assert active()
    assert service.revoke_token(key["id"])
    clock[0] += 6
    assert not active()


def test_recording_replacement_keeps_new_audio_if_old_file_is_unreadable(tmp_path, monkeypatch):
    from src.meeting import store
    monkeypatch.setattr(store, "MEETINGS_DIR", tmp_path)
    meeting = store.create_meeting(title="Practice")
    old = Path(store.save_audio(meeting.id, b"first", "wav"))
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == old:
            raise OSError("Unavailable previous recording")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    new = Path(store.save_audio(meeting.id, b"second", "wav"))
    assert store.get_meeting(meeting.id).audio_path == str(new)
    assert new.read_bytes() == b"second"


@pytest.mark.parametrize("kind", ["note", "web"])
def test_chat_confirmation_belongs_to_its_requester_and_is_single_use(kind):
    import asyncio
    from src.chatbox.confirm_store import ChatConfirmStore
    from src.chatbox.web_search import WebSearchConfirmStore

    store = WebSearchConfirmStore() if kind == "web" else ChatConfirmStore("card_")
    with as_device(key="issuer"):
        ticket = store.create("sample" if kind == "web" else {"title": "sample"})
    with as_device(key="other"):
        assert not store.resolve(ticket, False)
    with as_device(key="issuer"):
        assert store.resolve(ticket, True)
        assert not store.resolve(ticket, False)
        assert asyncio.run(store.wait(ticket, timeout=0.1))


def test_chat_history_checks_regular_document_sources(tmp_path, monkeypatch):
    from src.api.routes import sessions
    from src.chatbox.agent import ChatboxAgent
    from src.db.sessions import SessionStore
    from src.identity import visibility
    from unittest.mock import Mock

    store = SessionStore(str(tmp_path / "chat.db"))
    monkeypatch.setattr(sessions.services, "session_store", store)
    monkeypatch.setattr(visibility, "visible_collection_ids", lambda actor: {"library_a"})
    with as_device():
        session = store.create_session(title="Practice")
        store.add_message(session.id, "user", "Show the recent material.")
        store.add_message(session.id, "assistant", "Visible passage", sources=[{"collection": "library_a"}])
        store.add_message(session.id, "assistant", "Restricted passage", sources=[{"collection": "library_b"}])
        detail = sessions.get_session(session.id)
        assert "Restricted passage" not in detail.model_dump_json()
        assert "Visible passage" in detail.model_dump_json()
        assert "Restricted passage" not in sessions._session_response(session, store).model_dump_json()
        agent = ChatboxAgent(store, Mock(), Mock())
        outbound = agent._build_messages(session.id, "Continue", catalog_text=" ")
        assert "Restricted passage" not in str(outbound)
        assert "Visible passage" in str(outbound)


@pytest.mark.parametrize("scenario", ["permitted", "queued_revocation", "running_revocation", "recovery"])
def test_workers_recheck_the_requester(tmp_path, monkeypatch, scenario):
    import asyncio
    from src.identity import get_actor
    from src.pairing import service, store
    from src.tasks import task_persistence
    from src.tasks.task_manager import TaskManager, TaskStatus, check_cancelled

    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path / "pairing")
    monkeypatch.setattr(task_persistence, "DB_PATH", tmp_path / "jobs.db")
    key = service.issue_token(name="practice", is_local=True)
    visited = []

    def handler(task, **kwargs):
        visited.append(get_actor().id)
        if scenario == "running_revocation":
            service.revoke_token(key["id"])
        check_cancelled()
        visited.append("completed")
        return {}

    async def run():
        manager = TaskManager()
        with as_device(key=key["id"]):
            task = await manager.create_task_async("sample.txt", "upload", "library_a")
        if scenario in {"queued_revocation", "recovery"}:
            service.revoke_token(key["id"])
        if scenario == "recovery":
            manager = TaskManager()
            assert await manager.recover_upload_tasks(settle_delay=0) == 1
            await asyncio.sleep(0)
            task = manager.get_task(task.id)
        manager.register_handler("upload", handler)
        work = await manager._upload_queue.get()
        await manager._execute_task(*work)
        return task.status

    status = asyncio.run(run())
    if scenario == "permitted":
        assert visited == [key["id"], "completed"] and status == TaskStatus.COMPLETED
    elif scenario == "running_revocation":
        assert visited == [key["id"]] and status == TaskStatus.FAILED
    else:
        assert visited == [] and status == TaskStatus.FAILED


def test_parser_accepts_only_configured_transfer_destinations():
    from src.parsers.mineru_parser import MinerUParser, MinerUError
    parser = MinerUParser("fixture", base_url="https://parse.example.test/api",
                          transfer_origins=["https://storage.example.test"])
    assert parser.transfer_url("https://storage.example.test/file") == "https://storage.example.test/file"
    assert parser.transfer_url("https://parse.example.test/file") == "https://parse.example.test/file"
    for url in ("http://storage.example.test/file", "https://other.example.test/file"):
        with pytest.raises(MinerUError):
            parser.transfer_url(url)


def test_parser_validates_archive_budget_before_processing(tmp_path, monkeypatch):
    from zipfile import ZipFile
    from src.parsers import archive_limits
    p = tmp_path / "simple.zip"
    with ZipFile(p, "w") as output:
        output.writestr("full.md", "small sample text")
    with ZipFile(p) as archive:
        archive_limits.check_zip(archive)
        monkeypatch.setattr(archive_limits, "MAX_EXPANDED_BYTES", 5)
        with pytest.raises(ValueError, match="limit"):
            archive_limits.check_zip(archive)


@pytest.mark.parametrize("response_limit", [False, True])
def test_mineru_transfers_with_streaming_storage(tmp_path, monkeypatch, response_limit):
    import io
    from zipfile import ZipFile
    import httpx
    from src.parsers import mineru_parser as mineru

    sample = tmp_path / "sample.pdf"
    sample.write_bytes(b"Unit document")
    raw = io.BytesIO()
    with ZipFile(raw, "w") as archive:
        archive.writestr("full.md", "Workshop notes")
    transfers = []
    def handle(req):
        transfers.append((req.method, req.url.host))
        if req.url.host == "parse.example.test":
            assert req.headers["Authorization"] == "Bearer sample-key"
            data = ({"file_urls": ["https://storage.example.test/input"], "batch_id": "workshop"}
                    if req.method == "POST" else {"extract_result": [{"file_name": sample.name, "state": "done",
                                                "full_zip_url": "https://storage.example.test/result"}]})
            from json import dumps
            return httpx.Response(200, stream=httpx.ByteStream(dumps({"code": 0, "data": data}).encode()))
        assert req.url.host == "storage.example.test" and "Authorization" not in req.headers
        if req.method == "PUT":
            assert req.read() == sample.read_bytes()
            return httpx.Response(200)
        return httpx.Response(200, stream=httpx.ByteStream(raw.getvalue()))

    client = httpx.Client
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(mineru.httpx, "Client", lambda **kw: client(transport=transport, **kw))
    parser = mineru.MinerUParser("sample-key", base_url="https://parse.example.test",
                                 transfer_origins=["https://storage.example.test"])
    if response_limit:
        monkeypatch.setattr(mineru, "_MAX_RESULT_BYTES", 8)
        with pytest.raises(mineru.MinerUError, match="size limit"):
            parser.parse(sample)
    else:
        assert "Workshop notes" in parser.parse(sample).content
    assert transfers == [("POST", "parse.example.test"), ("PUT", "storage.example.test"),
                         ("GET", "parse.example.test"), ("GET", "storage.example.test")]


def test_office_archive_checked_before_parser(tmp_path, monkeypatch):
    from zipfile import ZipFile
    from src.parsers import parse_file, archive_limits

    sample = tmp_path / "example.docx"
    with ZipFile(sample, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
    monkeypatch.setattr(archive_limits, "MAX_DIRECTORY_BYTES", 1)
    with pytest.raises(ValueError, match="directory"):
        parse_file(sample)


def test_pdf_render_and_page_limits(tmp_path, monkeypatch):
    from PIL import Image
    from src.parsers import pdf
    from tests.test_pdf_evidence_fidelity import Page

    page = Page((0, 0, 300, 400))
    monkeypatch.setattr(pdf, "_MAX_RENDER_PIXELS", 100)
    with pytest.raises(ValueError, match="raster size"):
        pdf._extract_page_images(page, 1)
    doc = tmp_path / "pages.pdf"
    Image.new("RGB", (72, 72)).save(doc, save_all=True, append_images=[Image.new("RGB", (72, 72))])
    monkeypatch.setattr(pdf, "_MAX_PAGES", 1)
    with pytest.raises(ValueError, match="page count"):
        pdf.PDFParser().parse(doc)


def test_recording_duration_checked_before_decoding(tmp_path, monkeypatch):
    import wave
    pytest.importorskip("soundfile")
    from src.meeting.transcription.onnx import pipeline

    recording = tmp_path / "short.wav"
    with wave.open(str(recording), "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(3200))
    monkeypatch.setattr(pipeline, "_MAX_AUDIO_SECONDS", 0)
    with pytest.raises(ValueError, match="duration limit"):
        pipeline._load_wav_mono16k(str(recording))


def test_two_devices_lose_access_to_prior_chat_evidence(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from src.chatbox.agent import ChatboxAgent
    from src.db.sessions import SessionStore
    from src.identity import acl
    from src.pairing import service, store as keys
    from src.main import app
    from src.services import services
    import httpx
    import json
    from openai import OpenAI

    from qdrant_client.qdrant_client import QdrantClient
    from src.db import qdrant
    from src.collections import store as collections

    monkeypatch.setattr(collections, "COLLECTIONS_DIR", tmp_path / "libraries")
    monkeypatch.setattr(qdrant, "QdrantClient", lambda **kwargs: QdrantClient(":memory:"))
    db = qdrant.QdrantManager()
    for cid in ("library_a", "library_b"):
        db.create_collection(cid, vector_size=4)
        collections.create_collection_meta(cid, cid)
    monkeypatch.setattr(keys, "PAIRING_DIR", tmp_path / "pairing")
    store = SessionStore(str(tmp_path / "chat.db"))
    llm = MagicMock()
    llm._model = "practice"
    sent = []
    def model(request):
        sent.append(json.loads(request.read())["messages"])
        chunk = {"id": "reply", "model": "practice", "created": 1, "object": "chat.completion.chunk",
                 "choices": [{"index": 0, "finish_reason": "stop", "delta": {"content": "Workshop reply"}}]}
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")
    llm._client = OpenAI(api_key="fixture", base_url="https://model.example.test/v1",
                         http_client=httpx.Client(transport=httpx.MockTransport(model)))
    monkeypatch.setattr(services, "db", db)
    monkeypatch.setattr(services, "session_store", store)
    monkeypatch.setattr(services, "chatbox_agent", ChatboxAgent(store, llm, None))
    member, peer = [service.issue_token(name=name, is_local=True) for name in ("first", "second")]
    remote = TestClient(app, base_url="http://unit.example.test")
    def headers(key):
        return {"X-Pairing-Token": key["token"]}
    created = remote.post("/api/sessions", json={"collections": ["library_a", "library_b"]}, headers=headers(member))
    assert created.status_code == 201
    endpoint = "/api/sessions/" + created.json()["id"]
    assert remote.get(endpoint, headers=headers(peer)).status_code == 404
    from src.chatbox.citations import EvidenceRegistry
    from src.reports.tools import execute_report_tool
    registry = EvidenceRegistry(created.json()["id"])
    registry.sources.collections.add("library_b")
    with as_device(key=member["id"]):
        result = json.loads(execute_report_tool(store, created.json()["id"], "create_report",
                            {"title": "Restricted workshop report", "markdown": "Derived workshop findings."}, registry))
    report_url = endpoint + "/reports/" + result["report"]["id"]
    assert remote.get(report_url + "/revisions/1", headers=headers(member)).status_code == 200
    export = report_url + "/revisions/1/exports"
    assert remote.post(export, json={"format": "html"}, headers=headers(member)).status_code == 202
    assert remote.get(export + "/html/download", headers=headers(member)).status_code == 200
    for library in ("library_a", "library_b"):
        store.add_message(created.json()["id"], "assistant", "Passage from " + library, sources=[{"collection": library}])
    def send():
        reply = remote.post(endpoint + "/messages", json={"content": "Continue", "collections": ["library_a"],
                            "thinking": False, "mode": "direct"}, headers=headers(member))
        assert "event: done" in reply.text, reply.text[:500]
        assert "_source_access" not in reply.text
        return str(sent[-1])

    assert "Passage from library_b" in send()
    acl.save_collection_acl("library_b", {tier: [] for tier in acl.COLLECTION_TIERS})
    assert remote.post(endpoint + "/messages", json={"content": "Continue", "collections": ["library_b"]},
                       headers=headers(member)).status_code == 404
    detail = remote.get(endpoint, headers=headers(member)).json()
    assert detail["collections"] == ["library_a"]
    assert all(m["content"] != "Workshop reply" for m in detail["messages"])
    assert remote.get(endpoint + "/reports", headers=headers(member)).json() == []
    for suffix in ("", "/revisions/1", "/history", "/revisions/1/exports/html", "/revisions/1/exports/html/download"):
        assert remote.get(report_url + suffix, headers=headers(member)).status_code == 404
    outbound = send()
    assert "Passage from library_a" in outbound
    assert "Passage from library_b" not in outbound and "Workshop reply" not in outbound
    assert "Restricted workshop report" not in outbound


@pytest.mark.parametrize("layout", ["journal", "snapshots"])
def test_reports_recover_older_source_permissions_from_all_revisions(tmp_path, monkeypatch, layout):
    import json
    from src.chatbox.citations import EvidenceRegistry
    from src.identity import visibility
    from src.pairing import service, store as keys
    from src.reports.history import journal_records
    from src.reports.store import ReportStore

    reports = ReportStore(tmp_path / "reports", session_exists=lambda _: True)
    sid = "workshop"
    citation = EvidenceRegistry(sid).register("Workshop evidence", {"collection": "source-library"})
    first = reports.save(sid, "Overview", "First account", [])
    rid = first["id"]
    reports.save(sid, "Revision", f"Later account [cite:{citation['id']}]", [citation],
                 report_id=rid, expected_revision=1)
    reports.save(sid, "Final", "Summary in other words", [], report_id=rid, expected_revision=2)
    folder = reports.root / sid / rid
    path = folder / "history.json"
    journal = json.loads(path.read_text())
    del journal["meta"]["_source_access"]
    if layout == "journal":
        path.write_text(json.dumps(journal))
    else:
        (folder / "revisions").mkdir()
        for record in journal_records(journal):
            (folder / "revisions" / f"{record['revision']}.json").write_text(json.dumps(record))
        (folder / "meta.json").write_text(json.dumps(journal["meta"]))
        path.unlink()

    monkeypatch.setattr(keys, "PAIRING_DIR", tmp_path / "pairing")
    device = service.issue_token(name="Workshop reader", is_local=True)
    permitted = {"source-library"}
    monkeypatch.setattr(visibility, "visible_collection_ids", lambda _: permitted)
    with as_device(key=device["id"]):
        assert reports.read(sid, rid)["title"] == "Final"
        permitted.clear()
        assert reports.list(sid) == []
        with pytest.raises(FileNotFoundError):
            reports.read(sid, rid, 3)
        permitted.add("source-library")
        assert reports.read(sid, rid, 2)["title"] == "Revision"


@pytest.mark.parametrize("domain", ["meeting", "people"])
def test_chat_rechecks_domains_used_by_the_prompt(tmp_path, monkeypatch, domain):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from src.chatbox import meeting_context
    from src.chatbox.agent import ChatboxAgent
    from src.db.sessions import SessionStore
    from src.identity import acl, visibility
    from src.pairing import service, store as keys
    from src.speakers import store as speakers

    monkeypatch.setattr(acl, "IDENTITY_DIR", tmp_path / "identity")
    monkeypatch.setattr(acl, "DOMAIN_ACL_PATH", tmp_path / "identity/domains.json")
    monkeypatch.setattr(visibility, "_all_collection_ids", lambda: [])
    monkeypatch.setattr(keys, "PAIRING_DIR", tmp_path / "pairing")
    marker = "Workshop roster" if domain == "meeting" else "Workshop participant"
    monkeypatch.setattr(meeting_context, "build_meeting_catalog_digest",
                        lambda *_: marker if domain == "meeting" else "")
    monkeypatch.setattr(speakers, "get_me_person_id", lambda: "person" if domain == "people" else None)
    monkeypatch.setattr(speakers, "get_person", lambda _: SimpleNamespace(display_name=marker))
    store = SessionStore(str(tmp_path / "chat.db"))
    llm = MagicMock()
    completion = llm._client.chat.completions.create
    answer = completion.return_value.choices[0].message
    answer.tool_calls = None
    answer.content = "Reply based on the private context."
    agent = ChatboxAgent(store, llm, None)
    device = service.issue_token(name="Workshop tablet", is_local=True)
    with as_device(key=device["id"]):
        sid = store.create_session(title="Workshop").id
        agent.chat(sid, "What is on the agenda?", mode="direct")
        assert marker in str(completion.call_args.kwargs["messages"])
        assert store.get_messages(sid)[-1].metadata["source_domains"] == [domain]

    permissions = acl.default_domain_acl()
    permissions[domain] = {tier: [] for tier in acl.DOMAIN_TIERS[domain]}
    acl.save_domain_acl(permissions)
    with as_device(key=device["id"]):
        assert answer.content not in [row.content for row in store.get_messages(sid)]
        agent.chat(sid, "Continue.", mode="direct")
        messages = str(completion.call_args.kwargs["messages"])
        assert marker not in messages and answer.content not in messages
