"""Local ONNX ASR model manager — download **only** from GitHub Releases.

No HuggingFace / ModelScope fallback. The app pulls the pre-exported pack:

  https://github.com/<repo>/releases/download/onnx-models-v<ver>/sinkduce-onnx-models-v<ver>.zip

and extracts into ``HF_HOME/onnx/<pack>/`` (default ``data/models/onnx/``).

Env overrides (tests / air-gapped mirrors):
  SINKDUCE_ONNX_MODELS_VERSION  default ``1.0.0``
  SINKDUCE_ONNX_MODELS_REPO     default ``superdd-coder/SinkDuce``
  SINKDUCE_ONNX_MODELS_URL      full zip URL (skips repo/version construction)
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Release package (single source of truth)
# ---------------------------------------------------------------------------

DEFAULT_ONNX_MODELS_VERSION = "1.0.0"
DEFAULT_ONNX_MODELS_REPO = "superdd-coder/SinkDuce"


def onnx_models_version() -> str:
    return (os.environ.get("SINKDUCE_ONNX_MODELS_VERSION") or DEFAULT_ONNX_MODELS_VERSION).strip()


def onnx_models_repo() -> str:
    return (os.environ.get("SINKDUCE_ONNX_MODELS_REPO") or DEFAULT_ONNX_MODELS_REPO).strip()


def onnx_release_zip_name(version: str | None = None) -> str:
    ver = version or onnx_models_version()
    return f"sinkduce-onnx-models-v{ver}.zip"


def onnx_release_zip_url() -> str:
    """URL of the GitHub Release asset. No alternate mirrors."""
    override = (os.environ.get("SINKDUCE_ONNX_MODELS_URL") or "").strip()
    if override:
        return override
    ver = onnx_models_version()
    repo = onnx_models_repo()
    name = onnx_release_zip_name(ver)
    return f"https://github.com/{repo}/releases/download/onnx-models-v{ver}/{name}"


# ---------------------------------------------------------------------------
# Model registry (status UI / readiness per pack)
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    id: str
    display_name: str
    source: str  # always "github" for ONNX ASR packs
    repo_id: str  # logical HF-style id → onnx folder name (slash → --)
    category: str
    size_mb: int


LOCAL_MODELS: list[ModelInfo] = [
    # size_mb ≈ ONNX int8 pack size inside the release zip
    ModelInfo("transcription", "SenseVoiceSmall (ONNX int8)", "github", "FunAudioLLM/SenseVoiceSmall", "transcription", 240),
    ModelInfo("vad", "FSMN-VAD (ONNX int8)", "github", "funasr/fsmn-vad", "transcription", 1),
    ModelInfo("speaker", "CAM++ Speaker (ONNX)", "github", "funasr/campplus", "transcription", 30),
    ModelInfo("punc", "CT-Punc (ONNX int8)", "github", "funasr/ct-punc", "transcription", 280),
    ModelInfo("realtime", "Paraformer Streaming (ONNX int8)", "github", "funasr/paraformer-zh-streaming", "transcription", 230),
]

_TRANSCRIPTION_ONNX_IDS = frozenset(
    {"transcription", "vad", "speaker", "punc", "realtime"}
)

# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------

_download_lock = threading.Lock()
_download_progress: dict[str, dict[str, Any]] = {}  # model_id -> {status, progress, message}
_package_lock = threading.Lock()
_package_active = False


def _hf_home() -> Path:
    return Path(os.environ.get("HF_HOME", "data/models"))


def _get_onnx_dir(model: ModelInfo) -> Path:
    """ONNX pack directory used by funasr_onnx adapters."""
    safe_name = model.repo_id.replace("/", "--")
    return _hf_home() / "onnx" / safe_name


def _get_model_dir(model: ModelInfo) -> Path:
    """Legacy HF hub path (only used for cleanup of old installs)."""
    safe_name = model.repo_id.replace("/", "--")
    return _hf_home() / "hub" / f"models--{safe_name}"


def _is_onnx_ready(model: ModelInfo) -> bool:
    """True if ``HF_HOME/onnx/<repo>/`` has the artifacts we need to load."""
    d = _get_onnx_dir(model)
    if not d.is_dir():
        return False

    mid = model.id
    if mid == "realtime":
        has_enc = (d / "model_quant.onnx").is_file() or (d / "model.onnx").is_file()
        has_dec = (d / "decoder_quant.onnx").is_file() or (d / "decoder.onnx").is_file()
        return has_enc and has_dec

    if mid == "speaker":
        for f in d.glob("*.onnx"):
            try:
                if f.is_file() and f.stat().st_size > 1_000_000:
                    return True
            except OSError:
                continue
        return False

    if mid == "vad":
        return (d / "model_quant.onnx").is_file() or (d / "model.onnx").is_file()

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


def _is_downloaded(model: ModelInfo) -> bool:
    """Ready only when the ONNX pack is on disk — no hub/.pt fallback."""
    return _is_onnx_ready(model)


def _set_progress(model_ids: list[str], status: str, progress: int, message: str) -> None:
    with _download_lock:
        for mid in model_ids:
            _download_progress[mid] = {
                "status": status,
                "progress": progress,
                "message": message,
            }


def _all_model_ids() -> list[str]:
    return [m.id for m in LOCAL_MODELS]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_models_status() -> list[dict[str, Any]]:
    """Return status of all local models based on ONNX pack presence."""
    result = []
    for m in LOCAL_MODELS:
        downloaded = _is_downloaded(m)
        progress_info = _download_progress.get(m.id, {})
        if downloaded:
            status = "downloaded"
            progress = 100
            message = progress_info.get("message", "") if progress_info.get("status") == "done" else ""
        else:
            progress_status = progress_info.get("status", "")
            if progress_status in ("downloading", "extracting"):
                # Keep "extracting" visible to UI (解压中); else downloading + %
                status = progress_status
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
    """Ensure the GitHub Release ONNX pack is installed (covers this model).

    ``hf_token`` is accepted for API compatibility and **ignored**.
    """
    del hf_token  # no HuggingFace path
    model = next((m for m in LOCAL_MODELS if m.id == model_id), None)
    if not model:
        logger.error("Unknown model: %s", model_id)
        return

    if _is_downloaded(model):
        with _download_lock:
            _download_progress[model_id] = {
                "status": "done",
                "progress": 100,
                "message": f"{model.display_name} already downloaded",
            }
        logger.info("Model already downloaded: %s", model.display_name)
        return

    _download_onnx_release_package(requested_ids=[model_id])


def download_all(hf_token: str | None = None) -> None:
    """Download the full ONNX release package if any pack is missing."""
    del hf_token
    missing = [m.id for m in LOCAL_MODELS if not _is_downloaded(m)]
    if not missing:
        logger.info("All ONNX packs already present")
        return
    _download_onnx_release_package(requested_ids=missing)


def start_download_all(hf_token: str | None = None) -> None:
    """Start package download in a background thread."""
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
    """Delete local ONNX pack (and leftover hub cache if any)."""
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
# GitHub Release download (only path)
# ---------------------------------------------------------------------------

def _download_onnx_release_package(requested_ids: list[str] | None = None) -> None:
    """Download + extract the release zip. Fail hard if anything goes wrong."""
    global _package_active

    track_ids = list(requested_ids) if requested_ids else _all_model_ids()
    # Progress is shown on every pack in the registry (zip always installs all)
    progress_ids = _all_model_ids()

    with _package_lock:
        if _package_active:
            logger.info("ONNX release download already in progress; skip concurrent start")
            return
        # Re-check under lock
        still_missing = [mid for mid in track_ids if not _is_downloaded(
            next(m for m in LOCAL_MODELS if m.id == mid)
        )]
        if not still_missing and all(_is_downloaded(m) for m in LOCAL_MODELS):
            _set_progress(progress_ids, "done", 100, "Already downloaded")
            return
        _package_active = True

    url = onnx_release_zip_url()
    dest_home = _hf_home()
    dest_home.mkdir(parents=True, exist_ok=True)
    ver = onnx_models_version()

    try:
        _set_progress(
            progress_ids,
            "downloading",
            1,
            f"Downloading ONNX models v{ver} from GitHub Release...",
        )
        logger.info("Downloading ONNX models from %s", url)

        with tempfile.TemporaryDirectory(prefix="sinkduce-onnx-") as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / onnx_release_zip_name()

            def _hook(block_num: int, block_size: int, total_size: int) -> None:
                if total_size <= 0:
                    return
                downloaded = min(block_num * block_size, total_size)
                # Reserve 0–85% for network, 85–100% for extract/verify
                pct = int(85 * downloaded / total_size)
                pct = max(1, min(85, pct))
                mb = downloaded / 1_000_000
                total_mb = total_size / 1_000_000
                _set_progress(
                    progress_ids,
                    "downloading",
                    pct,
                    f"Downloading ONNX pack… {mb:.0f}/{total_mb:.0f} MB",
                )

            try:
                urllib.request.urlretrieve(url, zip_path, reporthook=_hook)  # noqa: S310 — fixed release URL
            except urllib.error.HTTPError as e:
                raise RuntimeError(
                    f"GitHub Release download failed HTTP {e.code} for {url}. "
                    f"Publish onnx-models-v{ver} with asset {onnx_release_zip_name()}."
                ) from e
            except urllib.error.URLError as e:
                raise RuntimeError(
                    f"GitHub Release download failed for {url}: {e.reason}"
                ) from e

            if not zip_path.is_file() or zip_path.stat().st_size < 32:
                raise RuntimeError(f"Downloaded file missing or empty: {zip_path}")

            _set_progress(progress_ids, "extracting", 88, "Extracting ONNX packs...")
            _extract_onnx_zip(zip_path, dest_home)

        _set_progress(progress_ids, "extracting", 95, "Verifying ONNX packs...")
        missing = [m.display_name for m in LOCAL_MODELS if not _is_downloaded(m)]
        if missing:
            raise RuntimeError(
                "Release zip extracted but packs incomplete: " + ", ".join(missing)
            )

        _set_progress(progress_ids, "done", 100, f"ONNX models v{ver} installed")
        logger.info("ONNX models v%s installed under %s/onnx", ver, dest_home)

    except Exception as e:
        msg = str(e)
        logger.error("ONNX release install failed: %s", msg)
        _set_progress(progress_ids, "error", 0, msg)
        raise
    finally:
        with _package_lock:
            _package_active = False


def _extract_onnx_zip(zip_path: Path, dest_home: Path) -> None:
    """Extract release zip so ``dest_home/onnx/<pack>/`` exists.

    Archive root must be ``onnx/`` (as produced by package_onnx_release.sh).
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if not any(n.replace("\\", "/").startswith("onnx/") for n in names):
            raise RuntimeError(
                "Invalid ONNX release zip: expected top-level 'onnx/' directory"
            )
        # Safety: refuse path traversal
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise RuntimeError(f"Unsafe path in zip: {info.filename}")
        zf.extractall(dest_home)
