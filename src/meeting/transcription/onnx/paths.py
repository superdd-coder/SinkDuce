from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_WEIGHT_EXTS = {".safetensors", ".bin", ".pt", ".onnx"}


def _hf_home() -> Path:
    return Path(os.environ.get("HF_HOME", "data/models"))


def resolve_hf_snapshot(repo_id: str) -> Path | None:
    """Return a HuggingFace hub snapshot dir that looks complete, or None."""
    safe = repo_id.replace("/", "--")
    hub = _hf_home() / "hub" / f"models--{safe}"
    snaps = hub / "snapshots"
    if not snaps.is_dir():
        # local_dir layout
        if hub.is_dir() and _looks_like_model_dir(hub):
            return hub
        return None
    best: Path | None = None
    best_size = 0
    for snap in snaps.iterdir():
        if not snap.is_dir():
            continue
        size = _weight_bytes(snap)
        if size > best_size and _looks_like_model_dir(snap):
            best = snap
            best_size = size
    return best


def _looks_like_model_dir(d: Path) -> bool:
    has_cfg = any(
        (d / n).exists()
        for n in ("config.yaml", "config.json", "configuration.json")
    )
    has_w = any(
        f.is_file() and f.suffix in _WEIGHT_EXTS and f.stat().st_size > 100_000
        for f in d.iterdir()
        if f.is_file()
    )
    return has_cfg and has_w


def _weight_bytes(d: Path) -> int:
    total = 0
    for f in d.iterdir():
        if f.is_file() and f.suffix in _WEIGHT_EXTS:
            total += f.stat().st_size
    return total


def onnx_cache_dir(repo_id: str) -> Path:
    """Dedicated ONNX export cache (keeps HF pytorch snapshots clean)."""
    safe = repo_id.replace("/", "--")
    return _hf_home() / "onnx" / safe


def _has_any_large_onnx(d: Path, min_bytes: int = 100_000) -> bool:
    if not d.is_dir():
        return False
    return any(
        f.is_file() and f.suffix == ".onnx" and f.stat().st_size > min_bytes
        for f in d.iterdir()
    )


def has_onnx_artifacts(
    d: Path,
    *,
    streaming: bool = False,
    quantize: bool = False,
    loose: bool = False,
) -> bool:
    """Check for loadable ONNX files.

    When ``quantize=True``, require ``*_quant.onnx`` (int8) — do not fall back
    to fp32 ``model.onnx`` (SenseVoice must run int8).

    ``loose=True`` accepts any large ``*.onnx`` (e.g. CAM++ packs that ship
    ``campplus_zh_cn_common_200k.onnx`` without ``model_quant.onnx`` / config).
    """
    if not d.is_dir():
        return False
    if loose:
        return _has_any_large_onnx(d)
    if not (d / "config.yaml").exists():
        return False
    if streaming:
        if quantize:
            enc = (d / "model_quant.onnx").exists()
            dec = (d / "decoder_quant.onnx").exists()
        else:
            enc = (d / "model.onnx").exists() or (d / "model_quant.onnx").exists()
            dec = (d / "decoder.onnx").exists() or (d / "decoder_quant.onnx").exists()
        return enc and dec
    if quantize:
        return (d / "model_quant.onnx").exists()
    return (d / "model.onnx").exists() or (d / "model_quant.onnx").exists()


def ensure_onnx_model_dir(
    repo_id: str,
    *,
    streaming: bool = False,
    quantize: bool = True,
    label: str = "model",
    loose: bool = False,
) -> Path:
    """Return a directory funasr-onnx can load (with .onnx files present).

    Resolution order:
    1. ``data/models/onnx/<repo>`` if already exported
    2. HF snapshot if it already contains required .onnx
    3. Export from HF pytorch snapshot via FunASR AutoModel.export (needs torch once)

    SenseVoice / default path uses ``quantize=True`` → int8 ``model_quant.onnx``.
    Pass ``loose=True`` for speaker/embedding packs (non-standard filenames).
    """
    # Embedding / CAM++ packs never ship model_quant.onnx + config.yaml.
    if not loose and label.lower() in {"speaker", "spk", "campplus", "embedding"}:
        loose = True

    cache = onnx_cache_dir(repo_id)
    if has_onnx_artifacts(cache, streaming=streaming, quantize=quantize, loose=loose):
        logger.info(
            "Using cached ONNX %s (quantize=%s, loose=%s): %s",
            label,
            quantize,
            loose,
            cache,
        )
        return cache

    snap = resolve_hf_snapshot(repo_id)
    if snap is None:
        raise RuntimeError(
            f"Model not downloaded: {repo_id} ({label}). "
            "Download it first via Settings → Local Models."
        )

    if has_onnx_artifacts(snap, streaming=streaming, quantize=quantize, loose=loose):
        logger.info(
            "HF snapshot already has ONNX for %s (quantize=%s): %s",
            label,
            quantize,
            snap,
        )
        return snap

    # Export once (int8 when quantize=True). Embedding packs are usually
    # pre-downloaded ONNX — export path needs funasr/torch.
    logger.info(
        "Exporting ONNX for %s from %s → %s (quantize=%s, one-time)",
        label,
        snap,
        cache,
        quantize,
    )
    cache.mkdir(parents=True, exist_ok=True)
    try:
        from funasr import AutoModel
    except ImportError as e:
        raise RuntimeError(
            f"Cannot export ONNX for {repo_id}: funasr/torch not installed. "
            "Install optional extra [asr-export] once to export, or place "
            f"pre-exported ONNX under {cache}."
        ) from e

    model = AutoModel(model=str(snap), disable_update=True)
    exported = model.export(type="onnx", quantize=quantize)
    export_path = Path(exported) if not isinstance(exported, Path) else exported
    # FunASR may return the model dir or a nested export folder
    if not has_onnx_artifacts(
        export_path, streaming=streaming, quantize=quantize, loose=loose
    ):
        found = None
        if export_path.is_dir():
            for child in [export_path, *export_path.iterdir()]:
                if child.is_dir() and has_onnx_artifacts(
                    child, streaming=streaming, quantize=quantize, loose=loose
                ):
                    found = child
                    break
        if found is None:
            raise RuntimeError(
                f"ONNX export for {repo_id} finished but "
                f"{'model_quant.onnx' if quantize else 'model.onnx'} "
                f"not found under {export_path}"
            )
        export_path = found

    # Copy into stable cache dir
    if export_path.resolve() != cache.resolve():
        for item in export_path.iterdir():
            dest = cache / item.name
            if item.is_file():
                shutil.copy2(item, dest)
            elif item.is_dir() and not dest.exists():
                shutil.copytree(item, dest)

    if not has_onnx_artifacts(
        cache, streaming=streaming, quantize=quantize, loose=loose
    ):
        raise RuntimeError(
            f"Failed to materialize ONNX artifacts for {repo_id} at {cache} "
            f"(quantize={quantize})"
        )

    logger.info("ONNX export ready (quantize=%s): %s", quantize, cache)
    return cache
