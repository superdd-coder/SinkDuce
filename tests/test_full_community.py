"""The public community distribution includes the retained management modules."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from src import edition
from src.identity import acl, roles
from src.pairing import service, store


@pytest.fixture(autouse=True)
def full_community(tmp_path, monkeypatch):
    monkeypatch.setenv("SINKDUCE_EDITION", "community")
    monkeypatch.setattr(edition, "EDITION_MARKER", tmp_path / "package" / "edition.json")
    monkeypatch.setattr(roles, "IDENTITY_DIR", tmp_path / "identity")
    monkeypatch.setattr(roles, "ROLES_PATH", tmp_path / "identity" / "roles.json")
    monkeypatch.setattr(acl, "IDENTITY_DIR", tmp_path / "identity")
    monkeypatch.setattr(acl, "DOMAIN_ACL_PATH", tmp_path / "identity" / "domain_acl.json")
    monkeypatch.setattr(store, "PAIRING_DIR", tmp_path / "pairing")
    monkeypatch.setattr(service, "local_entry_port", lambda: 18900)
    from src.collections import store as collections_store

    monkeypatch.setattr(collections_store, "COLLECTIONS_DIR", tmp_path / "collections")
    return tmp_path


def test_default_community_has_management_and_community_identity():
    assert not edition.is_enterprise()
    assert edition.has_permission_management()
    role = roles.create_custom_role(name="Catalogue reviewer", modules={"chat": True})
    assert roles.get_role(role.id).name == "Catalogue reviewer"
    key = service.issue_token(name="Review tablet", role="visitor", is_local=True)
    assert next(record for record in store.load() if record["id"] == key["id"])["role"] == "visitor"


def test_packaged_feature_set_is_independent_of_public_identity():
    edition.EDITION_MARKER.parent.mkdir()
    edition.EDITION_MARKER.write_text(
        json.dumps({"edition": "community", "features": ["permission_management"]})
    )
    assert not edition.is_enterprise()
    assert edition.has_permission_management()


def test_restricted_core_profile_remains_available():
    edition.EDITION_MARKER.parent.mkdir()
    edition.EDITION_MARKER.write_text(json.dumps({"edition": "community", "features": []}))
    assert not edition.has_permission_management()
    with pytest.raises(roles.RoleError, match="enterprise_required"):
        roles.create_custom_role(name="Catalogue reviewer")


def test_missing_management_implementation_cannot_silently_reduce_capabilities(monkeypatch):
    monkeypatch.setattr(edition.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RuntimeError, match="ee package is absent"):
        edition.has_permission_management()


@pytest.mark.parametrize("features", [["unknown_feature"], "permission_management", [True]])
def test_invalid_packaged_features_are_rejected(features):
    edition.EDITION_MARKER.parent.mkdir()
    edition.EDITION_MARKER.write_text(json.dumps({"edition": "community", "features": features}))
    with pytest.raises(RuntimeError, match="feature"):
        edition.has_permission_management()


def test_valid_existing_permission_data_is_loaded_without_rewriting(full_community):
    data = full_community
    payloads = {
        "identity/edition.json": {"version": 1, "edition": "enterprise"},
        "identity/roles.json": {
            "version": 1,
            "roles": [{"id": "role_catalogue", "name": "Catalogue reviewer", "modules": {"chat": True}}],
        },
        "identity/domain_acl.json": {
            "version": 1,
            "domains": {"meeting": {"read": ["role_catalogue"], "create": [], "manage": []}},
        },
        "collections/archive/meta.json": {"id": "archive", "acl": {"read": ["role_catalogue"]}},
        "pairing/tokens.json": {"version": 1, "tokens": [{"id": "review-key", "role": "visitor"}]},
    }
    for relative, payload in payloads.items():
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    before = {relative: (data / relative).read_bytes() for relative in payloads}
    edition.assert_data_compatible(data)
    assert roles.get_role("role_catalogue").name == "Catalogue reviewer"
    assert acl.domain_allows("meeting", "read", "role_catalogue")
    assert {relative: (data / relative).read_bytes() for relative in payloads} == before


@pytest.mark.parametrize(
    "relative,payload",
    [
        ("identity/roles.json", "{broken json"),
        ("identity/domain_acl.json", '{"domains": {"meeting": {"read": "everyone"}}}'),
        ("identity/edition.json", '{"edition": "unknown"}'),
        ("collections/archive/meta.json", '{"acl": {"read": "everyone"}}'),
    ],
)
def test_invalid_permission_data_is_rejected_in_full_builds(full_community, relative, payload):
    path = full_community / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload)
    with pytest.raises(edition.IncompatibleEditionData):
        edition.assert_data_compatible(full_community)
    assert path.read_text() == payload


def test_default_community_registers_management_routes(full_community):
    result = subprocess.run(
        [sys.executable, "-c", "import json; from src.main import app; print(json.dumps(list(app.openapi()['paths'])))"],
        env={**os.environ, "SINKDUCE_EDITION": "community", "SINKDUCE_DATA": str(full_community)},
        capture_output=True,
        text=True,
        check=True,
    )
    paths = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert {"/api/identity/roles", "/api/identity/domain-acl", "/api/pairing/set-role"} <= paths


def test_community_management_keeps_role_authorization():
    from src.main import app

    local = TestClient(app, base_url="http://127.0.0.1:18900", client=("127.0.0.1", 51000))
    assert local.get("/api/identity/me").json()["capabilities"]["acl.manage"] is True
    created = local.post("/api/identity/roles", json={"name": "Catalogue reviewer"})
    assert created.status_code == 200
    key = service.issue_token(name="Review tablet", role="member", is_local=True)
    remote = TestClient(app, base_url="http://192.0.2.10:18901", client=("192.0.2.20", 51001))
    response = remote.get("/api/identity/roles", headers={"Authorization": f"Bearer {key['token']}"})
    assert response.status_code == 403
