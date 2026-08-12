"""Action gate. Local installs always allow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.identity.actor import Actor


def authorize(actor: Actor, action: str, resource: Mapping[str, Any]) -> None:
    return
