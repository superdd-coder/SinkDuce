"""Release recovery keeps the requested application tag and version guards."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/docker-publish.yml"


@pytest.fixture
def workflow():
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def test_existing_tag_can_be_dispatched_from_main(workflow):
    recovery = workflow["on"].get("workflow_dispatch", {})
    assert recovery.get("inputs", {}).get("release_tag", {}).get("required") == "true"
    for job in workflow["jobs"].values():
        checkout = next(
            step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout.get("with", {}).get("ref") == "refs/tags/${{ env.RELEASE_TAG }}"
    release = workflow["jobs"]["release"]["steps"][-1]
    assert release["with"].get("tag_name") == "${{ env.RELEASE_TAG }}"


@pytest.mark.parametrize("job_name", ["docker", "release"])
@pytest.mark.parametrize(
    ("tag", "backend_version", "frontend_version", "allowed"),
    [
        ("v2.3.4", "2.3.4", "2.3.4", True),
        ("v2.3.4", "2.3.5", "2.3.4", False),
        ("v2.3.4", "2.3.4", "2.3.5", False),
        ("onnx-models-v2.3.4", "2.3.4", "2.3.4", False),
        ("v2.3.4-rc.1", "2.3.4-rc.1", "2.3.4-rc.1", False),
    ],
)
def test_tag_verification_on_recovery(
    workflow, tmp_path, job_name, tag, backend_version, frontend_version, allowed
):
    (tmp_path / "pyproject.toml").write_text(f'version = "{backend_version}"\n')
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/package.json").write_text(json.dumps({"version": frontend_version}))
    step = next(
        step
        for step in workflow["jobs"][job_name]["steps"]
        if step.get("name") == "Verify tag matches pyproject.toml"
    )
    # A manual dispatch runs the workflow on main, while releasing the supplied tag.
    script = step["run"].replace("${{ github.ref_name }}", "main")
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "RELEASE_TAG": tag, "GITHUB_ENV": str(tmp_path / "github-env")},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert (result.returncode == 0) is allowed, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("permission_management", "enterprise_identity", "allowed"),
    [(True, False, True), (None, False, False), (True, True, False)],
)
def test_public_release_requires_full_community_source(
    workflow, tmp_path, permission_management, enterprise_identity, allowed
):
    step = next(
        (
            step
            for step in workflow["jobs"]["docker"]["steps"]
            if step.get("name") == "Verify full community assembly"
        ),
        None,
    )
    assert step is not None, "Public releases must verify full-community source capabilities"
    package = tmp_path / "src"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = f"def is_enterprise():\n    return {enterprise_identity!r}\n"
    if permission_management is not None:
        source += f"def has_permission_management():\n    return {permission_management!r}\n"
    (package / "edition.py").write_text(source)
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert (result.returncode == 0) is allowed, result.stdout + result.stderr
