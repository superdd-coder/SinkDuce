"""Restricted core-profile boundaries retained for a future distribution split."""

import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from src import edition
from src.pairing import service, store
from src.edition import assert_community_compatible, IncompatibleEditionData


@pytest.fixture(autouse=True)
def isolated_pairing(tmp_path, monkeypatch):
    monkeypatch.setenv("SINKDUCE_EDITION", "community")
    marker = tmp_path / "package" / "edition.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"edition": "community", "features": []}))
    monkeypatch.setattr(edition, "EDITION_MARKER", marker)
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path)
    from src.collections import store as collections_store

    monkeypatch.setattr(collections_store, "COLLECTIONS_DIR", tmp_path / "collections")


def test_more_than_five_device_keys_and_fixed_member_role():
    for index in range(6):
        service.issue_token(name=f"device-{index}", is_local=True)
    assert service.active_token_count() == 6
    with pytest.raises(service.PairingError) as error:
        service.issue_token(name="admin", role="admin", is_local=True)
    assert error.value.code == "role_not_grantable"


def test_community_does_not_register_permission_management_routes():
    # Router registration happens at import time, so test a fresh process even
    # when the full suite imported src.main earlier in enterprise mode.
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import json, sys; from pathlib import Path; from src import edition; "
            "edition.EDITION_MARKER = Path(sys.argv[1]); "
            "from src.main import app; print(json.dumps(list(app.openapi()['paths'])))",
            str(edition.EDITION_MARKER),
        ],
        env={**os.environ, "SINKDUCE_EDITION": "community"},
        capture_output=True,
        text=True,
        check=True,
    )
    paths = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert "/api/identity/me" in paths
    assert "/api/identity/roles" not in paths
    assert "/api/identity/domain-acl" not in paths
    assert "/api/identity/collection-acl/{collection_id}" not in paths
    assert "/api/pairing/set-role" not in paths


def test_only_local_machine_can_manage_keys():
    from src.main import app

    client = TestClient(app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 50000))
    response = client.get("/api/pairing/tokens")
    assert response.status_code == 200
    assert response.json()["limit"] is None
    profile = client.get("/api/identity/me").json()
    assert profile["capabilities"]["acl.manage"] is False


@pytest.mark.parametrize(
    "relative_path,payload",
    [
        ("identity/roles.json", '{"version": 1, "roles": [{"id": "role_custom"}]}'),
        ("identity/domain_acl.json", '{"version": 1, "domains": {"meeting": {"read": []}}}'),
        ("collections/col_a/meta.json", '{"id": "col_a", "acl": {"read": []}}'),
        ("pairing/tokens.json", '{"version": 1, "tokens": [{"id": "key", "role": "visitor"}]}'),
        ("identity/edition.json", '{"version": 1, "edition": "enterprise"}'),
        ("identity/edition.json", '{"version": 1, "edition": "unknown"}'),
        ("identity/roles.json", '{broken json'),
    ],
)
def test_community_rejects_existing_permission_data(tmp_path, relative_path, payload):
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(IncompatibleEditionData, match=relative_path):
        assert_community_compatible(tmp_path)


def test_community_accepts_empty_or_default_permission_data(tmp_path):
    assert_community_compatible(tmp_path)
    path = tmp_path / "identity" / "roles.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"version": 1, "roles": []}', encoding="utf-8")
    assert_community_compatible(tmp_path)


def test_community_collection_permissions_are_fixed():
    from src.identity import acl

    assert acl.collection_allows("existing", "read", "member")
    assert acl.collection_allows("existing", "write", "member")
    assert not acl.collection_allows("existing", "delete", "member")
    assert not acl.collection_allows("existing", "config", "member")
    with pytest.raises(acl.AclError, match="enterprise_required"):
        acl.save_collection_acl("existing", {"read": []})


def test_member_can_write_each_collection_but_not_manage_it():
    from fastapi import HTTPException
    from src.identity.actor import Actor
    from src.identity.policy import authorize

    member = Actor(id="phone", kind="key", role_id="member")
    for collection_id in ("first", "second"):
        authorize(member, "collection.read", {"collection_id": collection_id})
        authorize(member, "file.create", {"collection_id": collection_id})
        for action in ("collection.update", "collection.delete"):
            with pytest.raises(HTTPException):
                authorize(member, action, {"collection_id": collection_id})
    with pytest.raises(HTTPException):
        authorize(member, "collection.create")


def test_community_cannot_reassign_device_key_role():
    issued = service.issue_token(name="phone", is_local=True)
    with pytest.raises(service.PairingError, match="enterprise_required"):
        service.set_token_role(issued["id"], "admin")


def test_community_rejects_historical_admin_key_even_without_lifespan():
    from src.main import app

    issued = service.issue_token(name="old-device", is_local=True)
    records = store.load()
    records[0]["role"] = "admin"
    store.save(records)
    remote = TestClient(app, base_url="http://192.0.2.10:18901", client=("192.0.2.20", 50000))
    response = remote.get("/api/identity/me", headers={"Authorization": f"Bearer {issued['token']}"})
    assert response.status_code == 401


def test_community_remote_member_cannot_manage_keys_or_box_settings():
    from src.main import app

    issued = service.issue_token(name="phone", is_local=True)
    remote = TestClient(app, base_url="http://192.0.2.10:18901", client=("192.0.2.20", 50000))
    headers = {"Authorization": f"Bearer {issued['token']}"}
    assert remote.put("/api/pairing/box", json={"box_name": "Changed"}, headers=headers).status_code == 403
    assert remote.get("/api/pairing/tokens", headers=headers).status_code == 403
    assert remote.post("/api/pairing/issue", json={"name": "extra"}, headers=headers).status_code == 403
    assert remote.post("/api/pairing/revoke", json={"id": issued["id"]}, headers=headers).status_code == 403


def test_community_never_treats_remote_role_labels_as_owner():
    from src.identity.actor import Actor
    from src.identity.policy import can, may_see_everything

    for role in ("owner", "admin"):
        actor = Actor(id="legacy", kind="key", role_id=role)
        assert not can(actor, "config.write")
        assert not may_see_everything(actor)


def test_enterprise_flag_cannot_enable_missing_implementation(monkeypatch, tmp_path):
    from src import edition

    monkeypatch.setenv("SINKDUCE_EDITION", "enterprise")
    monkeypatch.setattr(edition, "EDITION_MARKER", tmp_path / "absent-package.json")
    monkeypatch.setattr(edition.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RuntimeError, match="ee package is absent"):
        edition.is_enterprise()


def test_packaged_edition_marker_controls_desktop_runtime(tmp_path, monkeypatch):
    from src import edition

    marker = tmp_path / "edition.json"
    monkeypatch.setattr(edition, "EDITION_MARKER", marker)
    marker.write_text('{"edition": "enterprise"}', encoding="utf-8")
    monkeypatch.delenv("SINKDUCE_EDITION", raising=False)
    assert edition.is_enterprise()
    marker.write_text('{"edition": "community"}', encoding="utf-8")
    monkeypatch.setenv("SINKDUCE_EDITION", "enterprise")
    with pytest.raises(RuntimeError, match="fixed"):
        edition.is_enterprise()
