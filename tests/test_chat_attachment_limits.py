"""Resource-abuse regressions using independently fictional attachment data."""
import asyncio
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from tests.test_chat_attachments import api as attachment_api

api = attachment_api


def tighten(monkeypatch, **changes):
    from src.chat_attachments import limits
    monkeypatch.setattr(limits, "LIMITS", replace(limits.LIMITS, **changes))


def multipart(data=b"fictional", *, complete=True):
    body = b'--sample\r\nContent-Disposition: form-data; name="file"; filename="sample.txt"\r\n\r\n' + data
    return body + (b"\r\n--sample--\r\n" if complete else b"")


def test_retained_session_files_are_counted_before_accepting_another_upload(api):
    from src.chat_attachments.store import for_sessions

    client, sessions = api
    store = for_sessions(sessions)
    for index in range(100):
        store.create("owned", f"sample-{index}.txt", b"Fictional sample")

    response = client.post("/api/sessions/owned/attachments", files={"file": ("extra.txt", b"extra")})

    assert response.status_code == 413
    assert len(store.list("owned")) == 100


def test_full_session_is_rejected_without_reading_the_request_body(api):
    from src.chat_attachments.store import for_sessions

    client, sessions = api
    store = for_sessions(sessions)
    for index in range(100):
        store.create("owned", f"sample-{index}.txt", b"Fictional sample")
    consumed = []

    def body():
        consumed.append(True)
        yield b'--sample\r\nContent-Disposition: form-data; name="file"; filename="sample.txt"\r\n\r\nx\r\n--sample--\r\n'

    response = client.post("/api/sessions/owned/attachments", content=body(),
                           headers={"Content-Type": "multipart/form-data; boundary=sample"})

    assert response.status_code == 413
    assert consumed == []


@pytest.mark.parametrize("scope", ["session", "actor", "installation"])
@pytest.mark.parametrize("kind", ["files", "bytes"])
def test_storage_quotas_include_atomic_reservations(tmp_path, monkeypatch, scope, kind):
    from src.chat_attachments.limits import ResourceLedger, ResourceLimit
    tighten(monkeypatch, **{f"{scope}_{kind}": 1 if kind == "files" else 8})
    ledger = ResourceLedger(tmp_path / "attachments")
    first = ledger.reserve("session-a", "actor-a", 5, parse=False)
    # Other scopes vary, so only the selected quota can explain the denial.
    sid = "session-a" if scope == "session" else "session-b"
    actor = "actor-a" if scope == "actor" else "actor-b"
    with pytest.raises(ResourceLimit) as error:
        ResourceLedger(ledger.root).reserve(sid, actor, 5, parse=False)
    assert error.value.status_code == 413
    assert error.value.scope == scope
    ledger.abort(first)
    ledger.abort(ledger.reserve(sid, actor, 5, parse=False))


def test_parallel_admission_cannot_oversubscribe_the_last_installation_slot(tmp_path, monkeypatch):
    from src.chat_attachments.limits import ResourceLedger, ResourceLimit
    tighten(monkeypatch, installation_files=1)

    def reserve(index):
        ledger = ResourceLedger(tmp_path / "attachments")
        try:
            return ledger.reserve(f"session-{index}", f"actor-{index}", 1, parse=False)
        except ResourceLimit:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = [lease for lease in pool.map(reserve, range(8)) if lease]
    assert len(admitted) == 1


def _process_admission(root, policy, ready, start, results, release):
    from src.chat_attachments import limits
    limits.LIMITS = policy
    ledger = limits.ResourceLedger(root)
    ready.put(True)
    start.wait(10)
    lease = None
    try:
        lease = ledger.reserve(f"session-{os.getpid()}", f"actor-{os.getpid()}", 1, parse=False)
        results.put(True)
        release.wait(10)
    except limits.ResourceLimit:
        results.put(False)
    finally:
        if lease:
            ledger.abort(lease)


def test_separate_server_processes_share_the_same_installation_quota(tmp_path):
    import multiprocessing

    from src.chat_attachments.limits import LIMITS
    context = multiprocessing.get_context("spawn")
    ready, results = context.Queue(), context.Queue()
    start, release = context.Event(), context.Event()
    policy = replace(LIMITS, installation_files=1)
    workers = [context.Process(target=_process_admission,
                              args=(tmp_path / "attachments", policy, ready, start, results, release)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        for _ in workers:
            assert ready.get(timeout=15)
        start.set()
        assert sum(results.get(timeout=15) for _ in workers) == 1
    finally:
        release.set()
        start.set()
        for worker in workers:
            worker.join(15)
            if worker.is_alive():
                worker.terminate()
                worker.join()
    assert all(worker.exitcode == 0 for worker in workers)


@pytest.mark.parametrize("scope", ["session", "actor", "installation"])
def test_concurrent_upload_limits_apply_before_bytes_arrive(tmp_path, monkeypatch, scope):
    from src.chat_attachments.limits import ResourceLedger, ResourceLimit
    tighten(monkeypatch, **{f"{scope}_uploads": 1})
    ledger = ResourceLedger(tmp_path / "attachments")
    first = ledger.reserve("session-a", "actor-a", 1)
    sid = "session-a" if scope == "session" else "session-b"
    actor = "actor-a" if scope == "actor" else "actor-b"
    with pytest.raises(ResourceLimit) as error:
        ledger.reserve(sid, actor, 1)
    assert error.value.status_code == 429
    ledger.abort(first)
    ledger.abort(ledger.reserve(sid, actor, 1))


def test_streamed_sources_bypass_the_in_memory_create_path(api, monkeypatch):
    from src.chat_attachments.store import AttachmentStore, for_sessions
    client, sessions = api
    monkeypatch.setattr(AttachmentStore, "create", lambda *args: pytest.fail("Upload buffered into bytes"))
    response = client.post("/api/sessions/owned/attachments", files={"file": ("sample.txt", b"x" * 200000)})
    assert response.status_code == 201
    store = for_sessions(sessions)
    assert store.source_path("owned", response.json()["id"]).read_bytes() == b"x" * 200000
    assert not list((store.root / ".uploads").glob("*"))


@pytest.mark.parametrize("body,status", [
    (multipart(b"123456789"), 413),
    (multipart(b"123", complete=False), 400),
    (multipart(b""), 400),
    (b"--sample--\r\n", 400),
    (multipart().replace(b'name="file"', b'name="other"'), 400),
    (multipart().replace(b'sample.txt', b'sample.exe'), 400),
])
def test_bad_or_oversized_chunked_body_releases_storage_and_job_capacity(api, monkeypatch, body, status):
    from src.api.routes import chat_attachments
    from src.chat_attachments.store import for_sessions
    client, sessions = api
    tighten(monkeypatch, session_files=1, pending_parses=1)
    monkeypatch.setattr(chat_attachments, "MAX_FILE_BYTES", 8)
    response = client.post("/api/sessions/owned/attachments", content=iter([body[:30], body[30:]]),
                           headers={"Content-Type": "multipart/form-data; boundary=sample", "Content-Length": "1"})
    assert response.status_code == status
    store = for_sessions(sessions)
    assert store.list("owned") == []
    assert not list((store.root / ".uploads").glob("*"))
    assert client.post("/api/sessions/owned/attachments", files={"file": ("next.txt", b"next")}).status_code == 201


def test_actor_quota_survives_reopen_and_cannot_be_reset_with_a_new_session(api, monkeypatch):
    import src.identity
    from src.chat_attachments.store import for_sessions
    from src.identity.actor import Actor
    client, sessions = api
    actor = Actor(id="actor-demo", kind="key", role_id="visitor")
    monkeypatch.setenv("SINKDUCE_EDITION", "enterprise")
    monkeypatch.setattr(src.identity, "get_actor", lambda: actor)
    sessions.create_session(session_id="visitor-a", created_by=actor.id)
    sessions.create_session(session_id="visitor-b", created_by=actor.id)
    tighten(monkeypatch, actor_files=1)
    assert client.post("/api/sessions/visitor-a/attachments", files={"file": ("sample.txt", b"sample")}).status_code == 201
    assert len(for_sessions(sessions).list("visitor-a")) == 1
    assert client.post("/api/sessions/visitor-b/attachments", files={"file": ("next.txt", b"next")}).status_code == 413
    assert client.delete("/api/sessions/visitor-a").status_code == 204
    assert client.post("/api/sessions/visitor-b/attachments", files={"file": ("next.txt", b"next")}).status_code == 201


def test_reserved_bytes_shrink_to_actual_size_and_delete_reclaims_capacity(api, monkeypatch):
    from src.api.routes import chat_attachments
    client, _ = api
    monkeypatch.setattr(chat_attachments, "MAX_FILE_BYTES", 8)
    tighten(monkeypatch, session_bytes=10)
    first = client.post("/api/sessions/owned/attachments", files={"file": ("first.txt", b"abc")})
    assert first.status_code == 201
    assert client.post("/api/sessions/owned/attachments", files={"file": ("next.txt", b"x")}).status_code == 413
    assert client.delete(f'/api/sessions/owned/attachments/{first.json()["id"]}').status_code == 204
    assert client.post("/api/sessions/owned/attachments", files={"file": ("next.txt", b"x")}).status_code == 201


def test_parse_admission_and_retry_share_the_same_bounded_queue(api, monkeypatch):
    from fastapi import BackgroundTasks, HTTPException

    from src.api.routes import chat_attachments
    from src.chat_attachments.store import for_sessions
    client, sessions = api
    store = for_sessions(sessions)
    waiting = store.create("owned", "waiting.txt", b"fictional")
    failed = store.create("owned", "failed.txt", b"fictional")
    store.update("owned", failed.id, status="failed")
    tighten(monkeypatch, pending_parses=1)
    tasks = BackgroundTasks()
    chat_attachments._schedule(tasks, store, "owned", waiting.id)
    try:
        assert client.post("/api/sessions/owned/attachments", files={"file": ("next.txt", b"next")}).status_code == 429
        assert client.post(f"/api/sessions/owned/attachments/{failed.id}/retry").status_code == 429
        assert store.get("owned", failed.id).status == "failed"
        with pytest.raises(HTTPException) as error:
            chat_attachments._schedule(BackgroundTasks(), store, "owned", waiting.id)
        assert error.value.status_code == 409
    finally:
        asyncio.run(tasks())
    assert client.post(f"/api/sessions/owned/attachments/{failed.id}/retry").status_code == 200


def test_delete_during_upload_rejects_the_late_commit_without_recreating_files(api):
    from src.chat_attachments.limits import ResourceLimit
    from src.chat_attachments.store import for_sessions
    client, sessions = api
    store = for_sessions(sessions)
    lease = store.resources.reserve("owned", "local", 8)
    store.upload_path(lease).write_bytes(b"fictional")
    assert client.delete("/api/sessions/owned").status_code == 204
    with pytest.raises(ResourceLimit):
        store.commit_upload(lease, "sample.txt", 8)
    assert not (store.root / "owned").exists()
    assert not store.upload_path(lease).exists()


def test_session_reset_between_access_check_and_admission_rejects_the_old_request(api, monkeypatch):
    from src.chat_attachments.limits import ResourceLedger
    from src.chat_attachments.store import for_sessions
    client, sessions = api
    original = ResourceLedger.reserve

    def reset_then_reserve(self, *args, **kwargs):
        monkeypatch.setattr(ResourceLedger, "reserve", original)
        assert client.delete("/api/sessions/owned").status_code == 204
        assert client.post("/api/sessions", json={"id": "owned"}).status_code == 201
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ResourceLedger, "reserve", reset_then_reserve)
    response = client.post("/api/sessions/owned/attachments", files={"file": ("late.txt", b"fictional")})
    assert response.status_code == 409
    assert for_sessions(sessions).list("owned") == []


def test_deleted_running_work_still_counts_until_its_worker_finishes(tmp_path, monkeypatch):
    from src.chat_attachments.limits import ResourceLimit
    from src.chat_attachments.store import AttachmentStore
    store = AttachmentStore(tmp_path / "attachments")
    a = store.create("session-a", "sample.txt", b"sample")
    tighten(monkeypatch, pending_parses=1)
    lease = store.resources.queue("session-a", a.id, "local")
    assert store.resources.try_start(lease) == "started"
    store.purge("session-a")
    with pytest.raises(ResourceLimit):
        store.resources.reserve("session-b", "local", 1)
    store.resources.finish_job(lease)
    store.resources.abort(store.resources.reserve("session-b", "local", 1))


def test_deleting_a_session_cannot_free_a_still_uploading_requests_slot(tmp_path, monkeypatch):
    from src.chat_attachments.limits import ResourceLimit
    from src.chat_attachments.store import AttachmentStore
    store = AttachmentStore(tmp_path / "attachments")
    tighten(monkeypatch, actor_uploads=1)
    uploading = store.resources.reserve("session-a", "local", 8)
    store.upload_path(uploading).write_bytes(b"partial")
    store.purge("session-a")
    try:
        with pytest.raises(ResourceLimit) as error:
            store.resources.reserve("session-b", "local", 8)
        assert error.value.status_code == 429
    finally:
        store.resources.abort(uploading)
    store.resources.abort(store.resources.reserve("session-b", "local", 8))


def test_legacy_sources_are_imported_using_session_owner_and_counted_once(api, monkeypatch):
    from src.chat_attachments.models import Attachment
    from src.chat_attachments.store import for_sessions
    client, sessions = api
    store = for_sessions(sessions)
    directory = store.root / "owned" / "legacy-file"
    directory.mkdir(parents=True)
    (directory / "source.txt").write_bytes(b"legacy")
    metadata = Attachment(id="legacy-file", session_id="owned", filename="sample.txt", size=6, created_at="2035-01-01T00:00:00Z")
    (directory / "metadata.json").write_text(metadata.model_dump_json())
    tighten(monkeypatch, actor_files=1)
    sessions.create_session(session_id="next", created_by="local")
    assert client.post("/api/sessions/next/attachments", files={"file": ("next.txt", b"next")}).status_code == 413
    assert len(for_sessions(sessions).list("owned")) == 1
    store.purge("owned")
    assert client.post("/api/sessions/next/attachments", files={"file": ("next.txt", b"next")}).status_code == 201


def test_dead_process_leases_are_reclaimed_but_committed_bytes_stay_charged(tmp_path, monkeypatch):
    from src.chat_attachments.limits import ResourceLedger
    from src.chat_attachments.store import AttachmentStore
    store = AttachmentStore(tmp_path / "attachments")
    a = store.create("session-a", "saved.txt", b"saved")
    queued = store.resources.queue("session-a", a.id, "local")
    interrupted = store.resources.reserve("session-b", "local", 8)
    store.upload_path(interrupted).write_bytes(b"partial")
    dead_pid = 2147483647
    with sqlite3.connect(store.resources.path) as db:
        db.execute("UPDATE resources SET pid=?,incarnation=? WHERE phase!='idle'", (dead_pid, "0" * 32))
    tighten(monkeypatch, installation_files=2, pending_parses=1)
    reopened = ResourceLedger(store.root)
    new = reopened.reserve("session-c", "local", 8)
    assert not store.upload_path(interrupted).exists()
    assert not reopened.active("session-a", a.id)
    assert store.source_path("session-a", a.id).read_bytes() == b"saved"
    reopened.abort(new)
    resumed = reopened.queue("session-a", a.id, "local")
    reopened.finish_job(queued)
    assert reopened.active("session-a", a.id), "An old completion must not free the new retry's lease"
    reopened.finish_job(resumed)


def _abandon_leases(root, results, parsing):
    from src.chat_attachments.store import AttachmentStore

    store = AttachmentStore(root)
    if parsing:
        ids = []
        for index in range(5):
            attachment = store.create(f"old-{index}", "sample.txt", b"fictional")
            lease = store.resources.queue(attachment.session_id, attachment.id, "local")
            assert store.resources.try_start(lease) == "started"
            ids.append((attachment.session_id, attachment.id, lease))
        store.purge("old-0")
        results.put(ids)
    else:
        lease = store.resources.reserve("interrupted", "local", 8)
        store.upload_path(lease).write_bytes(b"partial")
        results.put(lease)


def abandoned_leases(root, *, parsing=False):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    worker = context.Process(target=_abandon_leases, args=(root, results, parsing))
    worker.start()
    try:
        leases = results.get(timeout=15)
    finally:
        worker.join(15)
        if worker.is_alive():
            worker.terminate()
            worker.join()
    assert worker.exitcode == 0
    return leases


def test_reused_pid_reclaims_abandoned_upload_without_touching_new_work(tmp_path, monkeypatch):
    from src.chat_attachments.limits import ResourceLedger, ResourceLimit

    root = tmp_path / "attachments"
    ledger = ResourceLedger(root)
    current = ledger.reserve("current", "local", 8)
    ledger.upload_path(current).write_bytes(b"current")
    interrupted = abandoned_leases(root)
    # Model a container restart: the old and new server both appear as PID 1.
    with sqlite3.connect(root / ".resources.sqlite3") as db:
        db.execute("UPDATE resources SET pid=? WHERE id=?", (os.getpid(), interrupted.id))
    tighten(monkeypatch, installation_uploads=1)
    with pytest.raises(ResourceLimit, match="Too many attachment uploads"):
        ResourceLedger(root).reserve("extra", "local", 8)
    assert not ledger.upload_path(interrupted).exists()
    ledger.abort(interrupted)
    ledger.commit(current, 7, lambda: None)
    assert ledger.try_start(current) == "started"
    ledger.finish_job(current)


def test_reused_pid_releases_all_parse_slots_including_deleted_sessions(tmp_path):
    from src.chat_attachments.store import AttachmentStore

    root = tmp_path / "attachments"
    interrupted = abandoned_leases(root, parsing=True)
    with sqlite3.connect(root / ".resources.sqlite3") as db:
        db.execute("UPDATE resources SET pid=? WHERE phase='running'", (os.getpid(),))
    store = AttachmentStore(root)
    assert not store.resources.active(*interrupted[1][:2])
    with sqlite3.connect(store.resources.path) as db:
        assert db.execute("SELECT COUNT(*) FROM resources WHERE retained=0").fetchone()[0] == 0
    resumed = []
    for sid, aid, old_lease in interrupted[1:]:
        assert store.source_path(sid, aid).read_bytes() == b"fictional"
        lease = store.resources.queue(sid, aid, "local")
        store.resources.finish_job(old_lease)
        assert store.resources.try_start(lease) == "started"
        resumed.append(lease)
    attachment = store.create("new-session", "new.txt", b"new")
    fifth = store.resources.queue(attachment.session_id, attachment.id, "local")
    assert store.resources.try_start(fifth) == "started"
    for lease in [*resumed, fifth]:
        store.resources.finish_job(lease)


def test_migration_reclaims_unidentified_work_and_preserves_sources(tmp_path):
    from src.chat_attachments.limits import ResourceLedger
    from src.chat_attachments.store import AttachmentStore

    root = tmp_path / "attachments"
    store = AttachmentStore(root)
    attachment = store.create("retained", "saved.txt", b"fictional")
    store.resources.queue(attachment.session_id, attachment.id, "local")
    interrupted = store.resources.reserve("interrupted", "local", 8)
    store.upload_path(interrupted).write_bytes(b"partial")
    with sqlite3.connect(store.resources.path) as db:
        # Recreate the pre-incarnation schema, including its live-looking PID.
        columns = "id,session_id,actor,bytes,retained,phase,job_actor,pid,token,wants_parse"
        db.execute(f"CREATE TABLE old_resources AS SELECT {columns} FROM resources")
        db.execute("DROP TABLE resources")
        db.execute("ALTER TABLE old_resources RENAME TO resources")
    reopened = ResourceLedger(root)
    assert not reopened.active(attachment.session_id, attachment.id)
    assert not reopened.upload_path(interrupted).exists()
    assert store.source_path(attachment.session_id, attachment.id).read_bytes() == b"fictional"
    retry = reopened.queue(attachment.session_id, attachment.id, "local")
    assert reopened.try_start(retry) == "started"
    reopened.finish_job(retry)


def _hold_upload(root, results):
    import time

    from src.chat_attachments.limits import ResourceLedger

    ledger = ResourceLedger(root)
    lease = ledger.reserve("worker", "local", 8)
    ledger.upload_path(lease).write_bytes(b"partial")
    results.put(lease)
    time.sleep(30)


@pytest.mark.parametrize("method", ["spawn", "fork"])
def test_live_worker_keeps_its_lease_until_abrupt_exit(tmp_path, monkeypatch, method):
    import multiprocessing

    from src.chat_attachments.limits import ResourceLedger, ResourceLimit

    context = multiprocessing.get_context(method)
    root = tmp_path / "attachments"
    ledger = ResourceLedger(root)
    parent = ledger.reserve("parent", "local", 8)
    results = context.Queue()
    worker = context.Process(target=_hold_upload, args=(root, results))
    worker.start()
    try:
        interrupted = results.get(timeout=15)
        tighten(monkeypatch, installation_uploads=2)
        with pytest.raises(ResourceLimit, match="Too many attachment uploads"):
            ResourceLedger(root).reserve("extra", "local", 8)
        worker.kill()
        worker.join(15)
        assert not worker.is_alive()
        # Reusing the dead worker's PID must not mask its released lifetime lock.
        with sqlite3.connect(ledger.path) as db:
            db.execute("UPDATE resources SET pid=? WHERE id=?", (os.getpid(), interrupted.id))
        fresh = ResourceLedger(root).reserve("extra", "local", 8)
        assert not ledger.upload_path(interrupted).exists()
        # forked workers release inherited descriptors without unlocking the parent.
        ledger.commit(parent, 1, lambda: None)
        ledger.abort(fresh)
    finally:
        if worker.is_alive():
            worker.terminate()
        worker.join(15)
        ledger.abort(parent)


def test_independent_ledgers_share_running_parse_limits(tmp_path, monkeypatch):
    from src.chat_attachments.limits import ResourceLedger
    from src.chat_attachments.store import AttachmentStore
    store = AttachmentStore(tmp_path / "attachments")
    first = store.create("session-a", "first.txt", b"fictional")
    second = store.create("session-b", "second.txt", b"fictional")
    first_lease = store.resources.queue("session-a", first.id, "local")
    other = ResourceLedger(store.root)
    second_lease = other.queue("session-b", second.id, "other")
    tighten(monkeypatch, running_parses=1)
    assert store.resources.try_start(first_lease) == "started"
    assert other.try_start(second_lease) == "wait"
    store.resources.finish_job(first_lease)
    assert other.try_start(second_lease) == "started"
    other.finish_job(second_lease)


def test_filesystem_headroom_is_reserved_for_uploads_that_have_not_arrived_yet(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from src.chat_attachments.limits import ResourceLedger, ResourceLimit
    ledger = ResourceLedger(tmp_path / "attachments")
    monkeypatch.setattr("src.chat_attachments.limits.shutil.disk_usage", lambda path: SimpleNamespace(free=16 * 1024**2 + 8))
    first = ledger.reserve("session-a", "local", 5)
    try:
        with pytest.raises(ResourceLimit) as error:
            ledger.reserve("session-b", "local", 5)
        assert error.value.status_code == 507
    finally:
        ledger.abort(first)
    ledger.abort(ledger.reserve("session-b", "local", 5))


def test_disconnected_and_cancelled_uploads_free_their_reservations(api, monkeypatch):
    from fastapi import BackgroundTasks, HTTPException
    from starlette.requests import Request

    from src.api.routes.chat_attachments import upload_attachment
    from src.chat_attachments.store import for_sessions
    _, sessions = api
    tighten(monkeypatch, session_files=1, pending_parses=1)

    async def run(cancel):
        events = iter([
            {"type": "http.request", "body": multipart(b"x", complete=False), "more_body": True},
            {"type": "http.disconnect"},
        ])

        async def receive():
            if cancel:
                raise asyncio.CancelledError
            return next(events)

        request = Request({"type": "http", "headers": [(b"content-type", b"multipart/form-data; boundary=sample")]}, receive)
        with pytest.raises(asyncio.CancelledError if cancel else HTTPException):
            await upload_attachment("owned", BackgroundTasks(), request)
        assert for_sessions(sessions).list("owned") == []
        store = for_sessions(sessions)
        assert not list((store.root / ".uploads").glob("*"))
        store.resources.abort(store.resources.reserve("owned", "local", 1))

    asyncio.run(run(False))
    asyncio.run(run(True))


def test_upload_timeout_releases_its_slot(api, monkeypatch):
    from fastapi import BackgroundTasks, HTTPException
    from starlette.requests import Request

    from src.api.routes.chat_attachments import upload_attachment
    from src.chat_attachments.store import for_sessions
    monkeypatch.setattr("src.chat_attachments.upload.UPLOAD_TIMEOUT", 0.01)
    _, sessions = api

    async def receive():
        await asyncio.Event().wait()

    async def run():
        request = Request({"type": "http", "headers": [(b"content-type", b"multipart/form-data; boundary=sample")]}, receive)
        with pytest.raises(HTTPException) as error:
            await upload_attachment("owned", BackgroundTasks(), request)
        assert error.value.status_code == 408
        store = for_sessions(sessions)
        assert store.list("owned") == []
        assert not list((store.root / ".uploads").glob("*"))
        store.resources.abort(store.resources.reserve("owned", "local", 1))

    asyncio.run(run())


def test_cancelled_parse_waiter_does_not_free_its_running_worker(api, monkeypatch):
    import threading

    from src.api.routes import chat_attachments
    from src.chat_attachments.limits import ResourceLimit
    from src.chat_attachments.store import for_sessions
    _, sessions = api
    store = for_sessions(sessions)
    a = store.create("owned", "sample.txt", b"fictional")
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    tighten(monkeypatch, pending_parses=1)

    def parse(*args):
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(chat_attachments, "parse_attachment", parse)
    original_finish = store.resources.finish_job

    def finish(lease):
        original_finish(lease)
        finished.set()

    monkeypatch.setattr(store.resources, "finish_job", finish)

    async def run():
        task = asyncio.create_task(chat_attachments._parse(store, "owned", a.id))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(ResourceLimit):
                store.resources.reserve("other", "local", 1)
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
        store.resources.abort(store.resources.reserve("other", "local", 1))

    asyncio.run(run())


def test_disk_failure_cleans_partial_file_and_allows_retry(api, monkeypatch):
    from src.chat_attachments.store import AttachmentStore, for_sessions
    client, sessions = api
    tighten(monkeypatch, session_files=1, pending_parses=1)
    original = AttachmentStore.commit_upload

    def fail(*args, **kwargs):
        raise OSError("Synthetic disk error")

    monkeypatch.setattr(AttachmentStore, "commit_upload", fail)
    response = client.post("/api/sessions/owned/attachments", files={"file": ("sample.txt", b"fictional")})
    assert response.status_code == 507
    store = for_sessions(sessions)
    assert store.list("owned") == []
    assert not list((store.root / ".uploads").glob("*"))
    monkeypatch.setattr(AttachmentStore, "commit_upload", original)
    assert client.post("/api/sessions/owned/attachments", files={"file": ("sample.txt", b"fictional")}).status_code == 201
