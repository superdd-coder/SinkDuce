"""Quiet identity seam: Actor / authorize / emit_event default user."""

from __future__ import annotations

import contextvars
import shutil
from dataclasses import FrozenInstanceError

import pytest

from src.file_mgmt.store import COLLECTIONS_DIR


@pytest.fixture(autouse=True)
def _cleanup_identity_collections():
    existing: set[str] = set()
    if COLLECTIONS_DIR.exists():
        existing = {d.name for d in COLLECTIONS_DIR.iterdir() if d.is_dir()}
    yield
    if COLLECTIONS_DIR.exists():
        for d in COLLECTIONS_DIR.iterdir():
            if d.is_dir() and d.name not in existing:
                shutil.rmtree(d, ignore_errors=True)


def test_get_actor_defaults_to_local():
    from src.identity import get_actor

    actor = get_actor()
    assert actor.id == "local"
    assert actor.kind == "local"


def test_actor_is_frozen():
    from src.identity import Actor

    actor = Actor()
    with pytest.raises(FrozenInstanceError):
        actor.id = "x"  # type: ignore[misc]


def test_set_actor_is_isolated_across_copied_contexts():
    from src.identity import Actor, get_actor
    from src.identity.actor import _set_actor

    token = _set_actor(Actor(id="outer"))
    try:
        assert get_actor().id == "outer"

        def _in_copy():
            _set_actor(Actor(id="inner"))
            return get_actor().id

        ctx = contextvars.copy_context()
        inner_id = ctx.run(_in_copy)
        assert inner_id == "inner"
        assert get_actor().id == "outer"
    finally:
        from src.identity.actor import _current

        _current.reset(token)


def test_authorize_never_raises():
    from src.identity import Actor, authorize

    authorize(Actor(), "folder.create", {"collection_id": "x"})
    authorize(Actor(id="t1"), "file.delete", {"collection_id": "c", "file_id": "f"})


def test_emit_event_defaults_user_to_current_actor(caplog):
    from src.file_mgmt.events import emit_event
    from src.identity import Actor
    from src.identity.actor import _current, _set_actor

    token = _set_actor(Actor(id="tester"))
    try:
        with caplog.at_level("DEBUG", logger="file_mgmt.events"):
            emit_event("folder.created", "col_x", {"folder_id": "f1"})
        assert any("user=tester" in r.getMessage() for r in caplog.records)
    finally:
        _current.reset(token)


def test_emit_event_explicit_user_id_wins(caplog):
    from src.file_mgmt.events import emit_event

    with caplog.at_level("DEBUG", logger="file_mgmt.events"):
        emit_event("folder.created", "col_x", {}, user_id="explicit")
    assert any("user=explicit" in r.getMessage() for r in caplog.records)


def test_create_folder_uses_current_actor_id():
    from src.collections.store import create_collection_meta
    from src.file_mgmt.models import FolderCreate
    from src.file_mgmt.service import create_folder
    from src.file_mgmt.store import init_collection_db
    from src.identity import Actor
    from src.identity.actor import _current, _set_actor

    coll = "id-actor-folder"
    create_collection_meta(coll, "Actor folder")
    init_collection_db(coll)
    token = _set_actor(Actor(id="t1"))
    try:
        folder = create_folder(coll, FolderCreate(name="FromActor"))
        assert folder.created_by == "t1"
    finally:
        _current.reset(token)
