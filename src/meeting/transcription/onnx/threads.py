"""ONNX Runtime thread policy for local ASR.

Desktop Macs were hard-capped at 4 intra-op threads while Accelerate/OpenMP
also spawned their own pools — the result is oversubscription and slow
SenseVoice / Paraformer / CAM++. Tune ORT up and pin host BLAS to 1 thread.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_MATH_ENV = (
    "OMP_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def configure_host_math_threads() -> None:
    """Keep NumPy / Accelerate from multiplying ORT's thread pool."""
    for key in _MATH_ENV:
        os.environ.setdefault(key, "1")


def _cpu_count() -> int:
    return max(1, int(os.cpu_count() or 4))


def _env_threads() -> int | None:
    raw = (os.environ.get("SINKDUCE_ORT_THREADS") or "").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def file_asr_threads() -> int:
    """CPU threads for file VAD + SenseVoice + punc.

    Apple Silicon reports P+E cores as one pool. Taking ``n-2`` starves
    Finder, the browser, and the Tauri UI. Desktop uses about one third
    of the logical cores, never more than 4, so other apps keep running.
    Docker is unchanged (cap 4). Override with ``SINKDUCE_ORT_THREADS``.
    """
    pinned = _env_threads()
    if pinned is not None:
        return pinned
    n = _cpu_count()
    from src.config import is_desktop_runtime

    if is_desktop_runtime():
        return max(2, min(4, n // 3))
    return max(2, min(4, n))


def realtime_asr_threads() -> int:
    """CPU threads for 600 ms Paraformer chunks.

    Streaming shares the machine with the meeting UI; stay below file ASR.
    Docker cap stays 4.
    """
    pinned = _env_threads()
    if pinned is not None:
        return pinned
    n = _cpu_count()
    from src.config import is_desktop_runtime

    if is_desktop_runtime():
        return max(2, min(3, n // 4 or 2))
    return max(2, min(4, n))


def apply_session_options(opts, *, num_threads: int, arena: bool = True) -> None:
    """Shared ORT SessionOptions knobs (CAM++ and any session we own)."""
    opts.intra_op_num_threads = max(1, int(num_threads))
    opts.inter_op_num_threads = 1
    try:
        from onnxruntime import GraphOptimizationLevel

        opts.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
    except Exception:
        pass
    opts.enable_cpu_mem_arena = bool(arena)
    opts.log_severity_level = 4
    logger.debug(
        "ORT session threads intra=%s inter=1 arena=%s",
        opts.intra_op_num_threads,
        arena,
    )
