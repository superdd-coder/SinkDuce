from __future__ import annotations

import threading
import time
from typing import Any, Literal

LoadState = Literal["unloaded", "loading", "loaded", "error"]

_states: dict[str, LoadState] = {}
_details: dict[str, dict[str, Any]] = {}
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


def set_state(
    provider_id: str,
    state: LoadState,
    *,
    message: str | None = None,
    error: str | None = None,
) -> None:
    """Update load state and optional UI-facing message/error."""
    with _lock:
        prev = _states.get(provider_id, "unloaded")
        _states[provider_id] = state
        detail = dict(_details.get(provider_id) or {})
        detail["state"] = state
        if message is not None:
            detail["message"] = message
        elif state == "unloaded":
            detail["message"] = "Not in memory"
        elif state == "loaded" and not detail.get("message"):
            detail["message"] = "Ready in memory"
        if error is not None:
            detail["error"] = error
        elif state != "error":
            detail.pop("error", None)
        if state == "loading" and prev != "loading":
            detail["started_at"] = time.time()
        if state in ("loaded", "unloaded", "error"):
            if "started_at" in detail and state == "loaded":
                detail["load_s"] = round(time.time() - float(detail["started_at"]), 1)
            if state != "loading":
                # keep started_at for load_s display briefly; clear on unload
                if state == "unloaded":
                    detail.pop("started_at", None)
                    detail.pop("load_s", None)
        _details[provider_id] = detail
        if state == "loaded":
            _events.pop(provider_id, None)
        elif state == "loading" and provider_id not in _events:
            _events[provider_id] = threading.Event()


def get_state(provider_id: str) -> LoadState:
    with _lock:
        return _states.get(provider_id, "unloaded")


def get_detail(provider_id: str) -> dict[str, Any]:
    with _lock:
        d = dict(_details.get(provider_id) or {})
        d.setdefault("state", _states.get(provider_id, "unloaded"))
        return d


def get_all_states() -> dict[str, LoadState]:
    with _lock:
        return dict(_states)


def get_all_details() -> dict[str, dict[str, Any]]:
    with _lock:
        out: dict[str, dict[str, Any]] = {}
        ids = set(_states) | set(_details)
        for pid in ids:
            d = dict(_details.get(pid) or {})
            d["state"] = _states.get(pid, "unloaded")
            out[pid] = d
        return out


def acquire_load_slot() -> None:
    """Acquire the global model-loading slot — only one model loads at a time."""
    _model_load_semaphore.acquire()


def release_load_slot() -> None:
    """Release the global model-loading slot."""
    _model_load_semaphore.release()
