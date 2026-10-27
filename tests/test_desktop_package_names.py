"""Edition-specific names used by the macOS packager."""

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NAMES = ROOT / "desktop" / "package_names.sh"


@pytest.mark.parametrize(
    ("edition", "expected"),
    [
        ("community", "SinkDuce.app|SinkDuce.dmg|SinkDuce-macos-arm64-v1.5.4.app.tar.gz"),
        ("enterprise", "SinkDuce-EE.app|SinkDuce-EE.dmg|SinkDuce-EE-macos-arm64-v1.5.4.app.tar.gz"),
    ],
)
def test_package_names(edition: str, expected: str) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; package_names "$2" "$3"; printf "%s|%s|%s" "$APP_NAME" "$DMG_NAME" "$TARGZ_NAME"',
            "bash",
            str(NAMES),
            edition,
            "1.5.4",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == expected


def test_package_names_rejects_unknown_edition() -> None:
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; package_names invalid 1.5.4', "bash", str(NAMES)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
