"""ONNX-based FunASR pipeline (no torch at inference time).

Uses ``funasr-onnx`` (ORT) for VAD / ASR / punctuation, and optional
3D-Speaker CAM++ ONNX for speaker embeddings.  Export of missing ONNX
graphs may temporarily need FunASR+torch once; runtime only needs
onnxruntime + funasr-onnx.
"""

from __future__ import annotations

from src.meeting.transcription.onnx.paths import (
    ensure_onnx_model_dir,
    resolve_hf_snapshot,
)

__all__ = [
    "ensure_onnx_model_dir",
    "resolve_hf_snapshot",
]
