"""Guards when DashScope / ONNX VAD return None instead of structured output."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_file_id_from_upload_none_output_raises_clear_error():
    from src.meeting.transcription.dashscope_file import _file_id_from_upload

    result = SimpleNamespace(status_code=200, output=None, code="InvalidApiKey", message="nope")
    with pytest.raises(RuntimeError, match="file upload failed") as ei:
        _file_id_from_upload(result)
    assert "NoneType" not in str(ei.value)
    assert "InvalidApiKey" in str(ei.value)


def test_file_id_from_upload_missing_files_raises():
    from src.meeting.transcription.dashscope_file import _file_id_from_upload

    result = SimpleNamespace(status_code=200, output={}, code=None, message=None)
    with pytest.raises(RuntimeError, match="no file_id"):
        _file_id_from_upload(result)


def test_file_id_from_upload_ok():
    from src.meeting.transcription.dashscope_file import _file_id_from_upload

    result = SimpleNamespace(
        status_code=200,
        output={"uploaded_files": [{"file_id": "file-1"}]},
        code=None,
        message=None,
    )
    assert _file_id_from_upload(result) == "file-1"


def test_oss_url_from_file_info_none_output():
    from src.meeting.transcription.dashscope_file import _oss_url_from_file_info

    info = SimpleNamespace(status_code=200, output=None, code="X", message="bad")
    with pytest.raises(RuntimeError, match="no url"):
        _oss_url_from_file_info(info)


def test_wait_oss_url_polls_until_ready():
    from src.meeting.transcription.dashscope_file import wait_oss_url

    calls = {"n": 0}
    sleeps: list[float] = []

    def fetch():
        calls["n"] += 1
        if calls["n"] < 3:
            return SimpleNamespace(status_code=200, output=None, code=None, message=None)
        return SimpleNamespace(status_code=200, output={"url": "https://oss.example/a.webm"})

    url = wait_oss_url(
        fetch, attempts=5, delay_sec=0.1, timeout_sec=30, sleep=sleeps.append
    )
    assert url == "https://oss.example/a.webm"
    assert calls["n"] == 3
    assert len(sleeps) == 2
    assert sleeps[0] == pytest.approx(0.1)
    assert sleeps[1] == pytest.approx(0.15)


def test_wait_oss_url_times_out_with_clear_error():
    from src.meeting.transcription.dashscope_file import wait_oss_url

    def fetch():
        return SimpleNamespace(status_code=200, output=None, code="Pending", message="not ready")

    with pytest.raises(RuntimeError, match="timed out after") as ei:
        wait_oss_url(fetch, attempts=2, delay_sec=0.01, timeout_sec=30, sleep=lambda _d: None)
    assert "NoneType" not in str(ei.value)
    assert "Pending" in str(ei.value)


def test_wait_oss_url_stops_at_timeout_not_attempt_cap():
    from src.meeting.transcription.dashscope_file import wait_oss_url

    now = {"t": 0.0}
    calls = {"n": 0}

    def clock() -> float:
        return now["t"]

    def fetch():
        calls["n"] += 1
        return SimpleNamespace(status_code=200, output=None, code="Pending", message="not ready")

    def sleep(d: float) -> None:
        now["t"] += d

    with pytest.raises(RuntimeError, match="timed out after") as ei:
        wait_oss_url(
            fetch,
            attempts=100,
            delay_sec=1.0,
            timeout_sec=2.5,
            sleep=sleep,
            clock=clock,
        )
    assert calls["n"] < 10
    assert "2.5" in str(ei.value) or "2." in str(ei.value)


def test_normalize_vad_raw_none_and_holes():
    from src.meeting.transcription.onnx.pipeline import normalize_vad_raw

    assert normalize_vad_raw(None) == []
    assert normalize_vad_raw([]) == []
    assert normalize_vad_raw([None]) == []
    assert normalize_vad_raw([[None]]) == []
    assert normalize_vad_raw([[[0, 100], None, [200, 300]]]) == [(0, 100), (200, 300)]
    assert normalize_vad_raw([[0, 50], [60, 90]]) == [(0, 50), (60, 90)]
