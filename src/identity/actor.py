"""Current operator. Local installs always resolve to id='local'."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    id: str = "local"
    kind: str = "local"


_current: ContextVar[Actor] = ContextVar("sinkduce_actor", default=Actor())


def get_actor() -> Actor:
    return _current.get()


def _set_actor(actor: Actor) -> Token[Actor]:
    return _current.set(actor)
