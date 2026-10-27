"""Explicit downgrade keeps a recoverable copy of permission data."""

import json

import pytest

from src.edition import assert_community_compatible, IncompatibleEditionData
from scripts.migrate_enterprise_to_community import migrate


def test_downgrade_requires_confirmation_and_backs_up_all_permission_files(tmp_path):
    data = tmp_path / "data"
    files = {
        "identity/edition.json": {"version": 1, "edition": "enterprise"},
        "identity/roles.json": {"version": 1, "roles": [{"id": "role_custom"}]},
        "identity/domain_acl.json": {"version": 1, "domains": {"meeting": {"read": []}}},
        "collections/first/meta.json": {"id": "first", "acl": {"read": []}, "name": "First"},
        "pairing/tokens.json": {"version": 1, "tokens": [
            {"id": "old", "name": "old phone", "role": "visitor", "revoked_at": None},
            {"id": "new", "name": "new phone", "role": "member", "revoked_at": None},
        ]},
    }
    for relative, payload in files.items():
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    backup = tmp_path / "permission-backup.tar.gz"
    with pytest.raises(ValueError, match="confirmation"):
        migrate(data, backup, confirm=False)
    assert not backup.exists()
    with pytest.raises(IncompatibleEditionData):
        assert_community_compatible(data)

    migrate(data, backup, confirm=True)
    assert backup.exists()
    assert backup.stat().st_mode & 0o777 == 0o600
    assert_community_compatible(data)
    tokens = json.loads((data / "pairing/tokens.json").read_text())["tokens"]
    assert tokens[0]["revoked_at"]
    assert tokens[1]["revoked_at"] is None
    meta = json.loads((data / "collections/first/meta.json").read_text())
    assert meta["name"] == "First"
    assert "acl" not in meta


def test_downgrade_refuses_corrupt_input_before_writing(tmp_path):
    data = tmp_path / "data"
    path = data / "identity" / "roles.json"
    path.parent.mkdir(parents=True)
    path.write_text("{bad json", encoding="utf-8")
    backup = tmp_path / "backup.tar.gz"
    with pytest.raises(ValueError, match="cannot read"):
        migrate(data, backup, confirm=True)
    assert not backup.exists()
    assert path.read_text(encoding="utf-8") == "{bad json"
