"""Process incarnation identities backed by lifetime locks on Linux and macOS."""
from __future__ import annotations

import fcntl
import os
import threading
import uuid
from pathlib import Path

_incarnation = uuid.uuid4().hex
_handles: dict[Path, int] = {}
_guard = threading.Lock()


def _after_fork() -> None:
    global _incarnation
    # A child must not keep an exited parent's incarnation alive. flock locks
    # otherwise survive fork through the inherited open file descriptions.
    for descriptor in _handles.values():
        os.close(descriptor)
    _handles.clear()
    _incarnation = uuid.uuid4().hex
    _guard.release()


# Serialize fork with descriptor registration, so the child can close every
# inherited owner handle, including one another thread was just creating.
os.register_at_fork(before=_guard.acquire, after_in_parent=_guard.release, after_in_child=_after_fork)


def identity(root: Path) -> str:
    """Share one identity/lock across every ledger in this server process."""
    directory = root.resolve() / ".lease-owners"
    with _guard:
        if directory not in _handles:
            directory.mkdir(exist_ok=True)
            descriptor = os.open(directory / _incarnation, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise
            # Keep the descriptor for the process lifetime, independently of
            # AttachmentStore/ResourceLedger instance lifetimes.
            _handles[directory] = descriptor
    return _incarnation


def alive(root: Path, incarnation: str) -> bool:
    """The OS releases the lock on exit, even when the next PID is identical."""
    if len(incarnation) != 32 or any(c not in "0123456789abcdef" for c in incarnation):
        # Pre-incarnation leases cannot identify a live worker after upgrade.
        return False
    directory = root.resolve() / ".lease-owners"
    with _guard:
        if incarnation == _incarnation and directory in _handles:
            return True
    try:
        descriptor = os.open(directory / incarnation, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        # No process owns this incarnation anymore; UUIDs are never reused.
        (directory / incarnation).unlink(missing_ok=True)
        return False
    finally:
        os.close(descriptor)
