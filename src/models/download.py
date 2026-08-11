"""Unified model download manager for all local models."""

from __future__ import annotations

import logging
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    id: str
    display_name: str
    source: str  # "hf"
    repo_id: str  # HuggingFace repo id or ModelScope model id
    category: str  # "llm", "embedding", "reranker", "transcription"
    size_mb: int  # approximate size in MB


LOCAL_MODELS: list[ModelInfo] = [
    # size_mb ≈ ONNX int8 pack size (not original .pt)
    ModelInfo("transcription", "SenseVoiceSmall (ONNX int8)", "hf", "FunAudioLLM/SenseVoiceSmall", "transcription", 240),
    ModelInfo("vad", "FSMN-VAD (ONNX int8)", "hf", "funasr/fsmn-vad", "transcription", 1),
    ModelInfo("speaker", "CAM++ Speaker (ONNX)", "hf", "funasr/campplus", "transcription", 30),
    ModelInfo("punc", "CT-Punc (ONNX int8)", "hf", "funasr/ct-punc", "transcription", 280),
    ModelInfo("realtime", "Paraformer Streaming (ONNX int8)", "hf", "funasr/paraformer-zh-streaming", "transcription", 230),
]

# Transcription models are considered ready when the ONNX pack exists under
# ``HF_HOME/onnx/<repo--name>/`` (preferred). Legacy HF hub .pt is optional.
_TRANSCRIPTION_ONNX_IDS = frozenset(
    {"transcription", "vad", "speaker", "punc", "realtime"}
)

# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------

_download_lock = threading.Lock()
_download_progress: dict[str, dict[str, Any]] = {}  # model_id -> {status, progress, message}


def _hf_home() -> Path:
    return Path(os.environ.get("HF_HOME", "data/models"))


def _get_model_dir(model: ModelInfo) -> Path:
    """Return the expected HF hub directory for a downloaded model (legacy .pt)."""
    safe_name = model.repo_id.replace("/", "--")
    return _hf_home() / "hub" / f"models--{safe_name}"


def _get_onnx_dir(model: ModelInfo) -> Path:
    """ONNX pack directory used by funasr_onnx adapters."""
    safe_name = model.repo_id.replace("/", "--")
    return _hf_home() / "onnx" / safe_name


def _has_config(d: Path) -> bool:
    """Check if a directory has a model config file (any of the common formats)."""
    for name in ("config.json", "config.yaml", "configuration.json", "model_config.json", "vad.yaml"):
        if (d / name).exists():
            return True
    return False


def _is_onnx_ready(model: ModelInfo) -> bool:
    """True if ``data/models/onnx/<repo>/`` has the artifacts we need to load."""
    d = _get_onnx_dir(model)
    if not d.is_dir():
        return False

    mid = model.id
    if mid == "realtime":
        # Streaming Paraformer: encoder + decoder (prefer int8)
        has_enc = (d / "model_quant.onnx").is_file() or (d / "model.onnx").is_file()
        has_dec = (d / "decoder_quant.onnx").is_file() or (d / "decoder.onnx").is_file()
        return has_enc and has_dec

    if mid == "speaker":
        # Any reasonably large embedding onnx
        for f in d.glob("*.onnx"):
            try:
                if f.is_file() and f.stat().st_size > 1_000_000:
                    return True
            except OSError:
                continue
        return False

    if mid == "vad":
        # VAD int8 can be < 1MB; allow smaller threshold
        return (d / "model_quant.onnx").is_file() or (d / "model.onnx").is_file()

    # SenseVoice / punc: prefer int8 quant graph
    if (d / "model_quant.onnx").is_file():
        try:
            return d.joinpath("model_quant.onnx").stat().st_size > 100_000
        except OSError:
            return False
    if (d / "model.onnx").is_file():
        try:
            return d.joinpath("model.onnx").stat().st_size > 100_000
        except OSError:
            return False
    return False


def _is_hub_ready(model: ModelInfo) -> bool:
    """Legacy: HuggingFace hub snapshot with .pt / .bin weights."""
    d = _get_model_dir(model)
    if not d.exists():
        return False

    min_file_size = max(100_000, min(500_000, (model.size_mb * 1_000_000) // 20))
    snaps = d / "snapshots"
    if snaps.exists():
        for s in snaps.iterdir():
            if not s.is_dir():
                continue
            if not _has_config(s) and not any(s.glob("*.pt")) and not any(s.glob("*.bin")):
                continue
            for f in s.iterdir():
                if not f.is_file():
                    continue
                try:
                    sz = f.stat().st_size
                except OSError:
                    continue
                if sz < min_file_size:
                    continue
                if f.suffix == ".safetensors" and _is_valid_safetensors(f):
                    return True
                if f.suffix in (".bin", ".pt", ".onnx"):
                    return True
    if _has_config(d):
        for ext in ("*.safetensors", "*.bin", "*.pt", "*.onnx"):
            for f in d.glob(ext):
                try:
                    if f.is_file() and f.stat().st_size > min_file_size:
                        return True
                except OSError:
                    continue
    return False


def _is_downloaded(model: ModelInfo) -> bool:
    """Check if a model is ready on disk.

    Transcription packs: prefer ONNX under ``HF_HOME/onnx/``.
    Other categories: HF hub cache as before.
    """
    if model.id in _TRANSCRIPTION_ONNX_IDS or model.category == "transcription":
        if _is_onnx_ready(model):
            return True
        # Fall back to hub only for non-default workflows (legacy pytorch adapters)
        return _is_hub_ready(model)

    return _is_hub_ready(model)


def _is_valid_safetensors(path: Path) -> bool:
    """Quick check that a safetensors file is not truncated."""
    try:
        with open(path, "rb") as f:
            # safetensors files start with a JSON header length (8 bytes LE)
            header_len_bytes = f.read(8)
            if len(header_len_bytes) < 8:
                return False
            import struct
            header_len = struct.unpack("<Q", header_len_bytes)[0]
            # Sanity: header should be between 64 bytes and 10MB
            if header_len < 64 or header_len > 10_000_000:
                return False
            header_bytes = f.read(header_len)
            if len(header_bytes) < header_len:
                return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_models_status() -> list[dict[str, Any]]:
    """Return status of all local models based on actual file presence."""
    result = []
    for m in LOCAL_MODELS:
        downloaded = _is_downloaded(m)
        progress_info = _download_progress.get(m.id, {})
        # Always trust actual file state over cached progress
        if downloaded:
            status = "downloaded"
            progress = 100
            message = progress_info.get("message", "") if progress_info.get("status") == "done" else ""
        else:
            progress_status = progress_info.get("status", "")
            if progress_status == "downloading":
                status = "downloading"
                progress = progress_info.get("progress", 0)
                message = progress_info.get("message", "")
            elif progress_status == "error":
                status = "error"
                progress = 0
                message = progress_info.get("message", "")
            else:
                status = "not_downloaded"
                progress = 0
                message = ""
        result.append({
            "id": m.id,
            "display_name": m.display_name,
            "source": m.source,
            "category": m.category,
            "size_mb": m.size_mb,
            "downloaded": downloaded,
            "status": status,
            "progress": progress,
            "message": message,
        })
    return result


def download_model(model_id: str, hf_token: str | None = None) -> None:
    """Download a single model. Called in a background thread."""
    model = next((m for m in LOCAL_MODELS if m.id == model_id), None)
    if not model:
        logger.error("Unknown model: %s", model_id)
        return

    if _is_downloaded(model):
        with _download_lock:
            _download_progress[model_id] = {"status": "done", "progress": 100, "message": f"{model.display_name} already downloaded"}
        logger.info("Model already downloaded: %s", model.display_name)
        return

    with _download_lock:
        _download_progress[model_id] = {"status": "downloading", "progress": 0, "message": f"Downloading {model.display_name}..."}

    try:
        _download_hf(model, hf_token)

        # Verify download completed successfully
        if _is_downloaded(model):
            _download_progress[model_id] = {"status": "done", "progress": 100, "message": f"{model.display_name} downloaded"}
            logger.info("Model downloaded: %s", model.display_name)
        else:
            _download_progress[model_id] = {"status": "error", "progress": 0, "message": "Download completed but model files not found"}
            logger.error("Model download appeared to succeed but files missing: %s", model.display_name)
    except Exception as e:
        _download_progress[model_id] = {"status": "error", "progress": 0, "message": str(e)}
        logger.error("Failed to download %s: %s", model.display_name, e)


def download_all(hf_token: str | None = None) -> None:
    """Download all missing models sequentially."""
    for m in LOCAL_MODELS:
        if not _is_downloaded(m):
            download_model(m.id, hf_token)


def start_download_all(hf_token: str | None = None) -> None:
    """Start downloading all missing models in a background thread."""
    t = threading.Thread(target=download_all, args=(hf_token,), daemon=True)
    t.start()


def _dir_size_mb(path: Path) -> float:
    total = 0
    if not path.exists():
        return 0.0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        return 0.0
    return round(total / 1_000_000, 1)


def delete_model(model_id: str) -> dict[str, Any]:
    """Delete local model packs (ONNX first, then legacy HF hub).

    Removes ``HF_HOME/onnx/<repo>`` and ``HF_HOME/hub/models--{repo}``.
    """
    model = next((m for m in LOCAL_MODELS if m.id == model_id), None)
    if not model:
        return {"success": False, "error": f"Unknown model: {model_id}"}

    paths = [_get_onnx_dir(model), _get_model_dir(model)]
    removed = False
    freed_mb = 0.0
    last_path = paths[0]
    for model_dir in paths:
        last_path = model_dir
        if not model_dir.exists():
            continue
        freed_mb += _dir_size_mb(model_dir)
        shutil.rmtree(model_dir, ignore_errors=False)
        removed = True
        logger.info("Deleted local model files: %s (%s)", model.display_name, model_dir)

    if not removed:
        logger.info("Model dirs already absent for %s", model.display_name)

    with _download_lock:
        _download_progress.pop(model_id, None)

    return {
        "success": True,
        "model_id": model_id,
        "display_name": model.display_name,
        "removed": removed,
        "path": str(last_path),
        "freed_mb": round(freed_mb, 1),
        "downloaded": _is_downloaded(model),
    }


def delete_models(model_ids: list[str]) -> dict[str, Any]:
    """Delete multiple models; returns per-id results."""
    results = []
    for mid in model_ids:
        results.append(delete_model(mid))
    ok = all(r.get("success") for r in results)
    freed = sum(float(r.get("freed_mb") or 0) for r in results)
    return {
        "success": ok,
        "results": results,
        "freed_mb": round(freed, 1),
    }


# ---------------------------------------------------------------------------
# Internal download functions
# ---------------------------------------------------------------------------

def _download_hf(model: ModelInfo, hf_token: str | None = None) -> None:
    """Download a model from HuggingFace Hub to the standard cache layout."""
    from huggingface_hub import snapshot_download

    logger.info("Downloading HF model: %s", model.repo_id)

    def _update_progress(pct: int, msg: str):
        with _download_lock:
            _download_progress[model.id] = {
                "status": "downloading",
                "progress": pct,
                "message": msg,
            }

    _update_progress(1, f"Downloading {model.display_name}...")

    try:
        snapshot_download(
            repo_id=model.repo_id,
            token=hf_token or None,
            resume_download=True,
        )
    except Exception as e:
        with _download_lock:
            _download_progress[model.id] = {
                "status": "error",
                "progress": 0,
                "message": str(e),
            }
        raise


