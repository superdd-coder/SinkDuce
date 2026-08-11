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
    ModelInfo("transcription","SenseVoiceSmall",              "hf", "FunAudioLLM/SenseVoiceSmall",                    "transcription", 900),
    ModelInfo("vad",          "FSMN-VAD",                    "hf", "funasr/fsmn-vad",                                 "transcription", 10),
    ModelInfo("speaker",      "CAM++ Speaker ID",            "hf", "funasr/campplus",                                 "transcription", 30),
    ModelInfo("punc",         "CT-Transformer Punctuation",   "hf", "funasr/ct-punc",                                  "transcription", 30),
    ModelInfo("realtime",     "Paraformer Streaming",         "hf", "funasr/paraformer-zh-streaming",                   "transcription", 220),
]

# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------

_download_lock = threading.Lock()
_download_progress: dict[str, dict[str, Any]] = {}  # model_id -> {status, progress, message}


def _get_model_dir(model: ModelInfo) -> Path:
    """Return the expected directory for a downloaded model."""
    hf_home = Path(os.environ.get("HF_HOME", "data/models"))
    safe_name = model.repo_id.replace("/", "--")
    return hf_home / "hub" / f"models--{safe_name}"


def _has_config(d: Path) -> bool:
    """Check if a directory has a model config file (any of the common formats)."""
    for name in ("config.json", "config.yaml", "configuration.json", "model_config.json"):
        if (d / name).exists():
            return True
    return False


def _is_downloaded(model: ModelInfo) -> bool:
    """Check if a model is fully downloaded with valid weight files."""
    d = _get_model_dir(model)
    if not d.exists():
        return False

    # Determine minimum file size threshold based on model size
    min_file_size = max(500_000, (model.size_mb * 1_000_000) // 10)  # At least 500KB, max ~10% of model size
    # Standard HF cache: snapshots/<hash>/ must have config + weight files
    snaps = d / "snapshots"
    if snaps.exists():
        for s in snaps.iterdir():
            if s.is_dir() and _has_config(s):
                # Check for any weight files above minimum size
                weight_files = [
                    f for f in s.iterdir()
                    if f.is_file() and f.suffix in (".safetensors", ".bin", ".pt", ".onnx")
                    and f.stat().st_size > min_file_size
                ]
                if weight_files:
                    for wf in weight_files:
                        if wf.suffix == ".safetensors" and _is_valid_safetensors(wf):
                            return True
                        elif wf.suffix in (".bin", ".pt", ".onnx"):
                            return True  # .bin/.pt/.onnx files are valid if they exist above threshold
    # Fallback: model downloaded with local_dir
    for f in d.glob("*.safetensors"):
        if f.is_file() and f.stat().st_size > min_file_size and _is_valid_safetensors(f):
            return True
    for f in d.glob("*.bin"):
        if f.is_file() and f.stat().st_size > min_file_size:
            return True
    # Also check for models that have config at the root (local_dir layout)
    if _has_config(d):
        for ext in ("*.safetensors", "*.bin", "*.pt", "*.onnx"):
            for f in d.glob(ext):
                if f.is_file() and f.stat().st_size > min_file_size:
                    return True
    return False


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


def delete_model(model_id: str) -> dict[str, Any]:
    """Delete downloaded model files from the HuggingFace hub cache.

    Removes ``HF_HOME/hub/models--{repo}`` for the registered model id.
    Safe if the directory is already missing.
    """
    model = next((m for m in LOCAL_MODELS if m.id == model_id), None)
    if not model:
        return {"success": False, "error": f"Unknown model: {model_id}"}

    model_dir = _get_model_dir(model)
    removed = False
    freed_mb = 0.0
    if model_dir.exists():
        # Approximate size before delete
        try:
            total = 0
            for root, _dirs, files in os.walk(model_dir):
                for name in files:
                    try:
                        total += (Path(root) / name).stat().st_size
                    except OSError:
                        pass
            freed_mb = round(total / 1_000_000, 1)
        except OSError:
            freed_mb = 0.0
        shutil.rmtree(model_dir, ignore_errors=False)
        removed = True
        logger.info("Deleted local model files: %s (%s)", model.display_name, model_dir)
    else:
        logger.info("Model dir already absent: %s", model_dir)

    with _download_lock:
        _download_progress.pop(model_id, None)

    return {
        "success": True,
        "model_id": model_id,
        "display_name": model.display_name,
        "removed": removed,
        "path": str(model_dir),
        "freed_mb": freed_mb,
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


