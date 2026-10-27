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
    monkeypatch.delenv("SINKDUCE_ONNX_MODELS_R2_BASE", raising=False)
    monkeypatch.setenv("SINKDUCE_ONNX_MODELS_VERSION", "1.0.0")
    monkeypatch.setenv("SINKDUCE_ONNX_MODELS_REPO", "superdd-coder/SinkDuce")
    cands = dl.onnx_release_zip_candidates()
    # R2 first (private repo → GitHub assets 404 for outside users), GitHub fallback
    assert cands[0][0] == f"{dl.DEFAULT_ONNX_R2_BASE}/sinkduce-onnx-models-v1.0.0.zip"
    assert cands[0][1] == f"{dl.DEFAULT_ONNX_R2_BASE}/sinkduce-onnx-models-v1.0.0.zip.sha256"
    assert cands[1][0] == (
        "https://github.com/superdd-coder/SinkDuce/releases/download/"
        "onnx-models-v1.0.0/sinkduce-onnx-models-v1.0.0.zip"
    )
    assert dl.onnx_release_zip_url() == cands[0][0]
    monkeypatch.setenv("SINKDUCE_ONNX_MODELS_URL", "http://example.test/pack.zip")
    assert dl.onnx_release_zip_candidates() == [
        ("http://example.test/pack.zip", "http://example.test/pack.zip.sha256")
    ]


def test_download_falls_back_to_second_mirror(tmp_path, monkeypatch):
    """R2 404s → downloads from the GitHub fallback candidate."""
    from src.models import download as dl

    monkeypatch.setenv("HF_HOME", str(tmp_path))
    zip_bytes = _make_minimal_release_zip()
    zip_file = tmp_path / "pack.zip"
    zip_file.write_bytes(zip_bytes)
    # Candidate 1 (R2 slot): file:// URL that does not exist → URLError.
    # Candidate 2 (GitHub slot): the real local zip.
    monkeypatch.setenv(
        "SINKDUCE_ONNX_MODELS_URL",
        (tmp_path / "missing-from-r2.zip").as_uri(),
    )
    cands = dl.onnx_release_zip_candidates()
    cands.append((zip_file.as_uri(), zip_file.as_uri() + ".sha256"))

    def _fake_candidates(version=None):
        return cands

    monkeypatch.setattr(dl, "onnx_release_zip_candidates", _fake_candidates)
    dl.download_all()
    assert all(dl._is_downloaded(m) for m in dl.LOCAL_MODELS)


def test_sha256_mismatch_tries_next_mirror(tmp_path, monkeypatch):
    """Corrupted first download → sha256 mismatch → falls through to next source."""
    from src.models import download as dl

    monkeypatch.setenv("HF_HOME", str(tmp_path))
    zip_bytes = _make_minimal_release_zip()
    good = tmp_path / "good.zip"
    good.write_bytes(zip_bytes)

    import hashlib

    bad = tmp_path / "bad.zip"
    bad.write_bytes(zip_bytes[:-64] + b"x" * 64)
    bad_sha = tmp_path / "bad.zip.sha256"
    bad_sha.write_text(f"{hashlib.sha256(b'nope').hexdigest()}  bad.zip\n")

    cands = [(bad.as_uri(), bad_sha.as_uri()), (good.as_uri(), good.as_uri() + ".sha256")]
    monkeypatch.setattr(dl, "onnx_release_zip_candidates", lambda version=None: cands)

    dl.download_all()
    assert all(dl._is_downloaded(m) for m in dl.LOCAL_MODELS)


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
