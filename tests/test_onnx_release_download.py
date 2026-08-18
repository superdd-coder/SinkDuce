"""ONNX models download only from GitHub Release (no HuggingFace fallback)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest


def _make_minimal_release_zip() -> bytes:
    """Build a tiny zip with onnx/ layout matching LOCAL_MODELS pack names."""
    packs = {
        "FunAudioLLM--SenseVoiceSmall": [("model_quant.onnx", 200_000)],
        "funasr--fsmn-vad": [("model_quant.onnx", 50_000)],
        "funasr--ct-punc": [("model_quant.onnx", 200_000)],
        "funasr--campplus": [("campplus_zh_cn_common_200k.onnx", 1_200_000)],
        "funasr--paraformer-zh-streaming": [
            ("model_quant.onnx", 200_000),
            ("decoder_quant.onnx", 200_000),
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for pack, files in packs.items():
            for name, size in files:
                zf.writestr(f"onnx/{pack}/{name}", b"0" * size)
            zf.writestr(f"onnx/{pack}/config.yaml", "x: 1\n")
    return buf.getvalue()


def test_is_downloaded_onnx_only_no_hub_fallback(tmp_path, monkeypatch):
    from src.models import download as dl

    monkeypatch.setenv("HF_HOME", str(tmp_path))
    model = next(m for m in dl.LOCAL_MODELS if m.id == "transcription")
    assert dl._is_downloaded(model) is False

    # Hub-only files must NOT count as downloaded
    hub = tmp_path / "hub" / "models--FunAudioLLM--SenseVoiceSmall" / "snapshots" / "x"
    hub.mkdir(parents=True)
    (hub / "model.pt").write_bytes(b"0" * 600_000)
    (hub / "config.yaml").write_text("x: 1\n")
    assert dl._is_downloaded(model) is False

    onnx = tmp_path / "onnx" / "FunAudioLLM--SenseVoiceSmall"
    onnx.mkdir(parents=True)
    (onnx / "model_quant.onnx").write_bytes(b"0" * 200_000)
    assert dl._is_downloaded(model) is True


def test_release_url_default_and_override(monkeypatch):
    from src.models import download as dl

    monkeypatch.delenv("SINKDUCE_ONNX_MODELS_URL", raising=False)
    monkeypatch.setenv("SINKDUCE_ONNX_MODELS_VERSION", "1.0.0")
    monkeypatch.setenv("SINKDUCE_ONNX_MODELS_REPO", "superdd-coder/SinkDuce")
    url = dl.onnx_release_zip_url()
    assert url == (
        "https://github.com/superdd-coder/SinkDuce/releases/download/"
        "onnx-models-v1.0.0/sinkduce-onnx-models-v1.0.0.zip"
    )
    monkeypatch.setenv("SINKDUCE_ONNX_MODELS_URL", "http://example.test/pack.zip")
    assert dl.onnx_release_zip_url() == "http://example.test/pack.zip"


def test_download_package_from_local_url(tmp_path, monkeypatch):
    from src.models import download as dl

    monkeypatch.setenv("HF_HOME", str(tmp_path))
    zip_bytes = _make_minimal_release_zip()
    zip_file = tmp_path / "pack.zip"
    zip_file.write_bytes(zip_bytes)
    # file:// URL so no network
    monkeypatch.setenv("SINKDUCE_ONNX_MODELS_URL", zip_file.as_uri())

    # CAM++ ships in the app; other ASR packs still require the release zip.
    assert not any(
        dl._is_downloaded(m) for m in dl.LOCAL_MODELS if m.id != "speaker"
    )

    dl.download_all()

    for m in dl.LOCAL_MODELS:
        assert dl._is_downloaded(m), m.id
    status = dl.check_models_status()
    assert all(s["downloaded"] for s in status)
    assert all(s["source"] == "github" for s in status)


def test_download_rejects_bad_zip_layout(tmp_path, monkeypatch):
    from src.models import download as dl

    monkeypatch.setenv("HF_HOME", str(tmp_path))
    bad = tmp_path / "bad.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("not-onnx/foo.bin", b"x")
    bad.write_bytes(buf.getvalue())
    monkeypatch.setenv("SINKDUCE_ONNX_MODELS_URL", bad.as_uri())

    with pytest.raises(RuntimeError, match="top-level 'onnx/'"):
        dl.download_all()

    st = dl.check_models_status()
    assert any(s["status"] == "error" for s in st)


def test_no_huggingface_import_in_download_module():
    """Guard: download path must not call huggingface_hub."""
    src = Path("src/models/download.py").read_text(encoding="utf-8")
    assert "huggingface_hub" not in src
    assert "snapshot_download" not in src
