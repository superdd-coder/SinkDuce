"""Request-scoped client identity for event origin attribution.

The pairing middleware resolves who is calling (key id, or nothing for a local
keyless request). The frontend also sends a stable per-install client id so a
client can skip its own echoes. Both are parked here in a ContextVar while the
request runs, and emit_event reads them to fill `origin`.

Background threads have no request context, so the ContextVar is empty there and
`origin` is left bare — the correct semantic, since a server-side completion
should be delivered to every client.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True)
class ClientContext:
    client_id: str | None = None
    key_id: str | None = None

    def as_origin(self) -> dict[str, str]:
        """Origin dict for an envelope, omitting unknowns."""
        out: dict[str, str] = {}
        if self.client_id:
            out["client_id"] = self.client_id
        if self.key_id:
            out["key_id"] = self.key_id
        return out


_empty = ClientContext()
_current: contextvars.ContextVar[ClientContext] = contextvars.ContextVar(
    "sinkduce_event_client", default=_empty
)

# Header / query names carrying the frontend's stable per-install identifier.
CLIENT_ID_HEADER = "x-client-id"
CLIENT_ID_QUERY = "client"


def current_client() -> ClientContext:
    return _current.get()


def set_client(client_id: str | None, key_id: str | None = None) -> contextvars.Token:
    """Bind the caller identity for the duration of a request."""
    return _current.set(ClientContext(client_id=client_id, key_id=key_id))


def reset_client(token: contextvars.Token) -> None:
    _current.reset(token)
