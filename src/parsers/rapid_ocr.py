"""Bundled RapidOCR engine (PP-OCRv6 small).

Models ship inside the pinned ``rapidocr`` wheel and are loaded from
disk. There is no ModelScope / HuggingFace download at runtime — if a
weight is missing, ingest fails that image instead of fetching it.

A pool of up to three engines. Startup warms one. Extra engines follow
the remaining OCR backlog: ≤10 images → 1 engine, 11–20 → 2, >20 → 3.
Extras unload after a short idle when the backlog falls back.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


def _desktop_ocr() -> bool:
    from src.config import is_desktop_runtime

    return is_desktop_runtime()

OCR_MAX_SIDE = 1600
OCR_ENGINE_COUNT = 3
OCR_KEEP_ENGINES = 1
# Backlog tiers: 1–10 → 1 engine, 11–20 → 2, 21+ → 3 (cap).
OCR_TIER2_AFTER = 10
OCR_TIER3_AFTER = 20
# Drop idle extras after this pause so a brief gap between files
# does not destroy and rebuild an engine.
OCR_SHRINK_IDLE_SEC = 10.0
_PRECHECK_SIDE = 320
# Nearly-flat images (solid fill / faint wash) are not worth ONNX.
_MIN_CONTRAST = 18.0
# Fraction of strong gradient pixels. Below this → blank / soft photo.
_MIN_EDGE_RATIO = 0.012

_DET = "PP-OCRv6_det_small.onnx"
_REC = "PP-OCRv6_rec_small.onnx"
_CLS = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"

_pool: queue.Queue | None = None
_pool_lock = threading.Lock()
_created = 0
_pending = 0
_shrink_timer: threading.Timer | None = None


def target_engine_count(pending: int | None = None) -> int:
    """How many engines the current (or given) backlog should use."""
    n = _pending if pending is None else pending
    if n > OCR_TIER3_AFTER:
        return OCR_ENGINE_COUNT
    if n > OCR_TIER2_AFTER:
        return 2
    return OCR_KEEP_ENGINES


def bundled_model_dir() -> Path:
    """Directory that already contains the three ONNX weights."""
    here = Path(__file__).resolve().parent / "ocr_models"
    if (here / _DET).is_file() and (here / _REC).is_file() and (here / _CLS).is_file():
        return here
    import rapidocr

    pkg = Path(rapidocr.__file__).resolve().parent / "models"
    if (pkg / _DET).is_file() and (pkg / _REC).is_file() and (pkg / _CLS).is_file():
        return pkg
    raise FileNotFoundError(
        "RapidOCR models are not installed. Expected "
        f"{_DET}, {_REC}, {_CLS} next to the parser or in the rapidocr wheel."
    )


def _make_engine():
    from rapidocr import RapidOCR

    model_dir = bundled_model_dir()
    return RapidOCR(
        params={
            "Global.log_level": "error",
            "Global.model_root_dir": str(model_dir),
            "Det.model_path": str(model_dir / _DET),
            "Rec.model_path": str(model_dir / _REC),
            "Cls.model_path": str(model_dir / _CLS),
            "Global.max_side_len": OCR_MAX_SIDE,
            # 3 engines × 1 thread keeps Docker's ~6 CPUs from thrashing.
            # Docker: 1 thread × 3 engines. Desktop: a bit more per engine.
            "EngineConfig.onnxruntime.intra_op_num_threads": (
                2 if _desktop_ocr() else 1
            ),
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
    )


def _ensure_pool() -> queue.Queue:
    """Create the pool with a single engine (startup / first use)."""
    global _pool, _created
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        model_dir = bundled_model_dir()
        q: queue.Queue = queue.Queue()
        q.put(_make_engine())
        _created = 1
        _pool = q
        logger.info(
            "[OCR] RapidOCR started with 1/%d engines from %s (no download)",
            OCR_ENGINE_COUNT,
            model_dir,
        )
        return _pool


def backlog_add(n: int) -> int:
    """Register *n* upcoming OCR images (negative to complete some)."""
    global _pending
    if not n:
        return _pending
    with _pool_lock:
        _pending = max(0, _pending + int(n))
        cur = _pending
    if n > 0:
        _grow_to_target()
    else:
        _schedule_shrink()
    return cur


def backlog_done(n: int = 1) -> int:
    return backlog_add(-abs(int(n)))


def _grow_to_target() -> None:
    """Create engines until ``target_engine_count()`` is met."""
    global _created
    pool = _ensure_pool()
    while True:
        with _pool_lock:
            want = min(OCR_ENGINE_COUNT, target_engine_count())
            if _created >= want:
                return
            _created += 1
            n = _created
            pending = _pending
        engine = _make_engine()
        pool.put(engine)
        logger.info(
            "[OCR] RapidOCR grew to %d/%d engines (backlog=%d)",
            n,
            OCR_ENGINE_COUNT,
            pending,
        )


def _take_engine():
    """Wait for an idle engine. Growth is driven by backlog, not waiters."""
    _grow_to_target()
    return _ensure_pool().get()


def _release_engine(engine) -> None:
    """Drop an extra RapidOCR instance so ONNX sessions can be freed."""
    try:
        for name in ("text_det", "text_cls", "text_rec"):
            part = getattr(engine, name, None)
            if part is None:
                continue
            sess = getattr(part, "session", None)
            inner = getattr(sess, "session", None)
            if inner is not None:
                del inner
    except Exception:
        pass


def _cancel_shrink_timer_locked() -> None:
    global _shrink_timer
    if _shrink_timer is not None:
        _shrink_timer.cancel()
        _shrink_timer = None


def _schedule_shrink() -> None:
    global _shrink_timer
    with _pool_lock:
        keep = max(OCR_KEEP_ENGINES, target_engine_count())
        if _created <= keep:
            _cancel_shrink_timer_locked()
            return
        _cancel_shrink_timer_locked()
        timer = threading.Timer(OCR_SHRINK_IDLE_SEC, _shrink_idle)
        timer.daemon = True
        _shrink_timer = timer
        timer.start()


def _shrink_idle() -> None:
    global _created, _shrink_timer
    discarded: list = []
    with _pool_lock:
        _shrink_timer = None
        keep = max(OCR_KEEP_ENGINES, target_engine_count())
        pool = _pool
        if pool is None or _created <= keep:
            return
        while _created > keep:
            try:
                discarded.append(pool.get_nowait())
                _created -= 1
            except queue.Empty:
                break
        if discarded:
            logger.info(
                "[OCR] RapidOCR shrank to %d/%d engines (backlog=%d)",
                _created,
                OCR_ENGINE_COUNT,
                _pending,
            )
    for engine in discarded:
        _release_engine(engine)


@contextmanager
def _borrow_engine():
    with _pool_lock:
        _cancel_shrink_timer_locked()
    try:
        engine = _take_engine()
        try:
            yield engine
        finally:
            _ensure_pool().put(engine)
    finally:
        _schedule_shrink()


def get_engine():
    """Return a live engine for tests / one-off use. Prefer ``_borrow_engine``."""
    pool = _ensure_pool()
    engine = pool.get()
    pool.put(engine)
    return engine


def warmup() -> bool:
    """Load one ONNX engine at startup. Extra engines follow the backlog."""
    t0 = time.monotonic()
    try:
        _ensure_pool()
        logger.info(
            "[OCR] RapidOCR ready in %.1fs (1/%d engines)",
            time.monotonic() - t0,
            OCR_ENGINE_COUNT,
        )
        return True
    except Exception:
        logger.warning(
            "[OCR] RapidOCR warmup failed — will retry on first ingest",
            exc_info=True,
        )
        return False


def looks_like_has_text(image) -> bool:
    """Cheap ink/edge check. False means skip RapidOCR.

    Conservative: only drop nearly flat or very soft images. Busy photos
    may still go through (false positive is cheap compared to missing text).
    """
    try:
        import numpy as np

        arr = image
        if hasattr(image, "convert"):
            arr = np.asarray(image.convert("L"))
        elif getattr(image, "ndim", 0) == 3:
            arr = (
                0.299 * image[:, :, 0]
                + 0.587 * image[:, :, 1]
                + 0.114 * image[:, :, 2]
            )
        gray = np.asarray(arr, dtype=np.float32)
        if gray.ndim != 2 or gray.size < 16:
            return False
        h, w = gray.shape
        longest = max(h, w)
        if longest > _PRECHECK_SIDE:
            scale = _PRECHECK_SIDE / float(longest)
            nh, nw = max(8, int(h * scale)), max(8, int(w * scale))
            from PIL import Image

            gray = np.asarray(
                Image.fromarray(np.clip(gray, 0, 255).astype("uint8")).resize(
                    (nw, nh), Image.Resampling.BILINEAR
                ),
                dtype=np.float32,
            )
        p5, p95 = np.percentile(gray, [5, 95])
        if float(p95 - p5) < _MIN_CONTRAST:
            return False
        gx = np.abs(gray[:, 1:] - gray[:, :-1])
        gy = np.abs(gray[1:, :] - gray[:-1, :])
        edge = (gx[:-1, :] + gy[:, :-1]) * 0.5
        thr = max(18.0, 0.25 * float(p95 - p5))
        return float(np.mean(edge > thr)) >= _MIN_EDGE_RATIO
    except Exception:
        # Unsure → run OCR rather than drop text.
        return True


def _to_rgb_array(image):
    import numpy as np

    if hasattr(image, "convert"):
        arr = np.asarray(image.convert("RGB"))
    else:
        arr = np.asarray(image)
    if arr.ndim != 3 or arr.shape[-1] != 3:
        return None
    h, w = arr.shape[:2]
    longest = max(h, w)
    if longest > OCR_MAX_SIDE:
        scale = OCR_MAX_SIDE / float(longest)
        nh, nw = max(1, int(h * scale)), max(1, int(w * scale))
        from PIL import Image

        arr = np.asarray(
            Image.fromarray(arr).resize((nw, nh), Image.Resampling.BILINEAR)
        )
    return arr


def ocr_array(image) -> tuple[str, float]:
    """Run RapidOCR on a PIL Image or numpy RGB array.

    Returns ``(text, mean_confidence_0_100)``. Empty on failure / no text.
    Images without ink-like contrast skip the engine. Up to
    ``OCR_ENGINE_COUNT`` inferences run at once.
    """
    if image is None:
        return "", 0.0
    try:
        arr = _to_rgb_array(image)
    except Exception:
        logger.debug("[OCR] RapidOCR could not convert image", exc_info=True)
        return "", 0.0
    if arr is None:
        return "", 0.0

    h, w = arr.shape[:2]
    if not looks_like_has_text(arr):
        logger.debug("[OCR] skip %dx%d — no text-like edges", w, h)
        return "", 0.0

    t0 = time.monotonic()
    try:
        with _borrow_engine() as engine:
            result = engine(arr)
    except Exception:
        logger.debug("[OCR] RapidOCR failed on %dx%d", w, h, exc_info=True)
        return "", 0.0
    elapsed = time.monotonic() - t0
    if elapsed >= 10.0:
        logger.info("[OCR] RapidOCR %dx%d took %.1fs", w, h, elapsed)

    txts = list(getattr(result, "txts", None) or [])
    scores = list(getattr(result, "scores", None) or [])
    text = " ".join(t.strip() for t in txts if t and str(t).strip())
    if scores:
        mean = sum(float(s) for s in scores) / len(scores) * 100.0
    else:
        mean = 0.0
    return text.strip(), mean
