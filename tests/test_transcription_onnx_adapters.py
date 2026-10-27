"""Unit tests for ONNX-only local ASR adapter resolution."""

from __future__ import annotations

from src.config import _migrate_transcription
from src.meeting.transcription import (
    is_local_asr_adapter,
    is_local_file_adapter,
    is_local_realtime_adapter,
    resolve_file_adapter,
    resolve_realtime_adapter,
)


def test_resolve_legacy_pytorch_names_to_onnx():
    assert resolve_file_adapter("funasr_local") == "funasr_onnx"
    assert resolve_file_adapter("funasr_onnx") == "funasr_onnx"
    assert resolve_realtime_adapter("funasr_local_realtime") == "funasr_onnx_realtime"
    assert resolve_realtime_adapter("funasr_onnx_realtime") == "funasr_onnx_realtime"


def test_is_local_adapter_helpers():
    assert is_local_file_adapter("funasr_onnx")
    assert is_local_file_adapter("funasr_local")  # legacy alias
    assert not is_local_file_adapter("dashscope_funasr")
    assert is_local_realtime_adapter("funasr_onnx_realtime")
    assert is_local_realtime_adapter("funasr_local_realtime")
    assert is_local_asr_adapter("funasr_onnx")
    assert is_local_asr_adapter("funasr_onnx_realtime")
    assert not is_local_asr_adapter("openai_compatible")


def test_migrate_transcription_rewrites_adapters():
    raw = {
        "file_providers": [{"id": "builtin-local-file", "adapter": "funasr_local"}],
        "realtime_providers": [{"id": "builtin-local-rt", "adapter": "funasr_local_realtime"}],
    }
    out = _migrate_transcription(raw)
    assert out["file_providers"][0]["adapter"] == "funasr_onnx"
    assert out["realtime_providers"][0]["adapter"] == "funasr_onnx_realtime"
