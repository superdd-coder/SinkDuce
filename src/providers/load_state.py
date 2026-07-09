from __future__ import annotations

import threading
from typing import Literal

LoadState = Literal["unloaded", "loading", "loaded", "error"]

_states: dict[str, LoadState] = {}
_events: dict[str, threading.Event] = {}
_lock = threading.Lock()

# Global semaphore: only one model loads at a time to avoid CPU/memory thrashing
_model_load_semaphore = threading.Semaphore(1)


def detect_device() -> str:
    """Auto-detect the best available compute device (CUDA > MPS > CPU)."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            try:
                t = torch.zeros(1, device="mps")
                del t
                return "mps"
            except Exception:
                pass
    except ImportError:
        pass
    return "cpu"


def set_state(provider_id: str, state: LoadState) -> None:
    with _lock:
        _states[provider_id] = state
        if state == "loaded":
            _events.pop(provider_id, None)
        elif state == "loading" and provider_id not in _events:
            _events[provider_id] = threading.Event()


def get_state(provider_id: str) -> LoadState:
    with _lock:
        return _states.get(provider_id, "unloaded")


def get_all_states() -> dict[str, LoadState]:
    with _lock:
        return dict(_states)


def acquire_load_slot() -> None:
    """Acquire the global model-loading slot — only one model loads at a time."""
    _model_load_semaphore.acquire()


def release_load_slot() -> None:
    """Release the global model-loading slot."""
    _model_load_semaphore.release()
