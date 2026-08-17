"""Bundled RapidOCR engine (PP-OCRv6 small).

Models ship inside the pinned ``rapidocr`` wheel and are loaded from
disk. There is no ModelScope / HuggingFace download at runtime — if a
weight is missing, ingest fails that image instead of fetching it.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

OCR_MAX_SIDE = 1600

_DET = "PP-OCRv6_det_small.onnx"
_REC = "PP-OCRv6_rec_small.onnx"
_CLS = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"

_engine = None
_engine_lock = threading.Lock()
_infer_lock = threading.Lock()


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
    logger.info("[OCR] RapidOCR models from %s (no download)", model_dir)
    return RapidOCR(
        params={
            "Global.log_level": "error",
            "Global.model_root_dir": str(model_dir),
            "Det.model_path": str(model_dir / _DET),
            "Rec.model_path": str(model_dir / _REC),
            "Cls.model_path": str(model_dir / _CLS),
            "Global.max_side_len": OCR_MAX_SIDE,
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
    )


def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            _engine = _make_engine()
        return _engine


def warmup() -> bool:
    """Load ONNX weights now so the first ingest does not pay cold start."""
    t0 = time.monotonic()
    try:
        get_engine()
        logger.info("[OCR] RapidOCR ready in %.1fs", time.monotonic() - t0)
        return True
    except Exception:
        logger.warning(
            "[OCR] RapidOCR warmup failed — will retry on first ingest",
            exc_info=True,
        )
        return False


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
    Engine load is done before the per-image timer so cold start does not
    look like a hang. Inference is serial (ONNX session is not shared).
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

    # Load weights once, outside the timed section.
    engine = get_engine()
    h, w = arr.shape[:2]
    t0 = time.monotonic()
    try:
        with _infer_lock:
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
