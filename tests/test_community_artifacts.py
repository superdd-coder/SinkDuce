"""Full community artifacts keep their identity and ship management code."""

import json
from zipfile import ZipFile

import pytest

from scripts.check_community_artifact import check_tree, check_wheel

MODULES = (
    "ee/__init__.py",
    "ee/api/identity.py",
    "ee/api/pairing.py",
    "ee/identity/roles.py",
    "ee/identity/acl.py",
    "ee/pairing/service.py",
)
CLIENT = b'"/identity/roles";"/identity/domain-acl";"/identity/collection-acl/";"/pairing/set-role";'


def populate(root, *, modules=True):
    (root / "edition.json").write_text(
        json.dumps({"edition": "community", "features": ["permission_management"]})
    )
    dist = root / "frontend/dist"
    dist.mkdir(parents=True)
    (dist / "app.js").write_bytes(CLIENT)
    if modules:
        for relative in MODULES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# packaged test module\n")


def test_full_community_tree_contains_management_modules(tmp_path):
    populate(tmp_path)
    check_tree(tmp_path)


def test_full_community_tree_refuses_a_partial_backend(tmp_path):
    populate(tmp_path, modules=False)
    with pytest.raises(SystemExit, match="management"):
        check_tree(tmp_path)


def test_full_community_tree_refuses_a_missing_management_frontend(tmp_path):
    populate(tmp_path)
    (tmp_path / "frontend/dist/app.js").write_bytes(b"console.log('Catalogue');")
    with pytest.raises(SystemExit, match="management"):
        check_tree(tmp_path)


def test_full_community_wheel_contains_retained_modules(tmp_path):
    path = tmp_path / "sample.whl"
    with ZipFile(path, "w") as wheel:
        for relative in MODULES:
            wheel.writestr(relative, "# packaged test module\n")
    check_wheel(path)


def test_full_community_wheel_refuses_missing_modules(tmp_path):
    path = tmp_path / "sample.whl"
    with ZipFile(path, "w") as wheel:
        wheel.writestr("src/__init__.py", "")
    with pytest.raises(SystemExit, match="management"):
        check_wheel(path)
