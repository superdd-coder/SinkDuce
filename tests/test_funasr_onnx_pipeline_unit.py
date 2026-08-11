"""Unit tests for ONNX pipeline helpers (no model weights required)."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from src.meeting.transcription.onnx.campplus import cluster_speakers
from src.meeting.transcription.onnx.paths import has_onnx_artifacts, onnx_cache_dir
from src.meeting.transcription.onnx.pipeline import (
    _load_via_ffmpeg,
    _load_wav_mono16k,
    _resample_mono_linear,
)


def test_cluster_speakers_merges_similar():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.99, 0.01, 0.0], dtype=np.float32)
    c = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    labels = cluster_speakers([a, b, c], threshold=0.9)
    assert labels[0] == labels[1]
    assert labels[0] != labels[2]


def test_cluster_speakers_empty():
    assert cluster_speakers([]) == []
    assert cluster_speakers([np.ones(4, dtype=np.float32)]) == [0]


def test_cluster_speakers_all_zero_collapses_to_one():
    """Broken CAM++ path used to emit zero vectors → N unique speakers."""
    z = np.zeros(192, dtype=np.float32)
    labels = cluster_speakers([z, z, z], threshold=0.55)
    assert labels == [0, 0, 0]


def test_cluster_speakers_zeros_join_dominant():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.99, 0.0, 0.0], dtype=np.float32)
    z = np.zeros(3, dtype=np.float32)
    labels = cluster_speakers([a, b, z], threshold=0.9)
    assert labels[0] == labels[1]
    assert labels[2] == labels[0]


def test_onnx_cache_dir_layout():
    p = onnx_cache_dir("funasr/paraformer-zh-streaming")
    assert "onnx" in p.parts
    assert "funasr--paraformer-zh-streaming" in str(p)


def test_has_onnx_artifacts_false_on_empty(tmp_path):
    assert has_onnx_artifacts(tmp_path, streaming=False) is False
    assert has_onnx_artifacts(tmp_path, streaming=True) is False


def test_has_onnx_artifacts_requires_quant_when_requested(tmp_path):
    (tmp_path / "config.yaml").write_text("x: 1\n")
    (tmp_path / "model.onnx").write_bytes(b"0" * 200)
    assert has_onnx_artifacts(tmp_path, quantize=False) is True
    # fp32 only must NOT satisfy int8 requirement (SenseVoice)
    assert has_onnx_artifacts(tmp_path, quantize=True) is False
    (tmp_path / "model_quant.onnx").write_bytes(b"0" * 200)
    assert has_onnx_artifacts(tmp_path, quantize=True) is True


def test_has_onnx_artifacts_loose_accepts_campplus_filename(tmp_path):
    (tmp_path / "campplus_zh_cn_common_200k.onnx").write_bytes(b"0" * 200_000)
    assert has_onnx_artifacts(tmp_path, quantize=True) is False
    assert has_onnx_artifacts(tmp_path, loose=True) is True


def test_is_downloaded_prefers_onnx_pack(tmp_path, monkeypatch):
    """Status API should report ready when only ONNX pack exists (no hub .pt)."""
    from src.models import download as dl

    monkeypatch.setenv("HF_HOME", str(tmp_path))
    # rebuild path helpers use env
    model = next(m for m in dl.LOCAL_MODELS if m.id == "transcription")
    onnx_dir = tmp_path / "onnx" / "FunAudioLLM--SenseVoiceSmall"
    onnx_dir.mkdir(parents=True)
    (onnx_dir / "model_quant.onnx").write_bytes(b"0" * 200_000)
    (onnx_dir / "config.yaml").write_text("x: 1\n")
    assert dl._is_onnx_ready(model) is True
    assert dl._is_downloaded(model) is True


def test_resample_mono_linear_identity():
    x = np.linspace(-0.5, 0.5, 1600, dtype=np.float32)
    y = _resample_mono_linear(x, 16000, 16000)
    assert y.shape == x.shape
    assert np.allclose(y, x)


def test_resample_mono_linear_downsamples():
    x = np.sin(np.linspace(0, 20, 32000, dtype=np.float32))
    y = _resample_mono_linear(x, 32000, 16000)
    assert abs(len(y) - 16000) <= 1


def _write_sine_wav(path: Path, sr: int = 16000, seconds: float = 0.1) -> None:
    n = int(sr * seconds)
    samples = (0.2 * np.sin(2 * np.pi * 440 * np.arange(n) / sr)).astype(np.float32)
    pcm = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def test_load_wav_mono16k_wav(tmp_path):
    wav = tmp_path / "tone.wav"
    _write_sine_wav(wav, sr=16000, seconds=0.05)
    audio, sr = _load_wav_mono16k(str(wav))
    assert sr == 16000
    assert audio.ndim == 1
    assert audio.dtype == np.float32
    assert audio.size > 100


def test_load_wav_mono16k_webm_uses_ffmpeg(tmp_path, monkeypatch):
    """WebM must not go through soundfile-only path (libsndfile cannot open it)."""
    webm = tmp_path / "rec.webm"
    webm.write_bytes(b"fake-webm")

    called: dict[str, str] = {}

    def fake_ffmpeg(path: str, target_sr: int = 16000) -> np.ndarray:
        called["path"] = path
        return np.zeros(1600, dtype=np.float32)

    monkeypatch.setattr(
        "src.meeting.transcription.onnx.pipeline._load_via_ffmpeg",
        fake_ffmpeg,
    )
    audio, sr = _load_wav_mono16k(str(webm))
    assert called["path"] == str(webm)
    assert sr == 16000
    assert audio.shape == (1600,)


def test_load_via_ffmpeg_raises_without_binary(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="ffmpeg is required"):
        _load_via_ffmpeg(str(tmp_path / "x.webm"))
