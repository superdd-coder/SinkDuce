"""Crash-safe live recording PCM: append → finalize → playable WAV.

Covers the server-side half of the live-capture durability chain:
  1. append_recording_pcm appends raw s16le chunks with per-meeting locking
  2. finalize_recording_pcm wraps the accumulated PCM into a valid
     16 kHz mono 16-bit WAV and points meeting.audio_path at it
  3. finalize on an empty/absent PCM is a no-op returning None
  4. append on a missing meeting fails loudly

This is the path a page refresh relies on: chunks are already on disk, so the
"finish recording" recovery action can produce a playable file without the
original browser session.

Run: pytest tests/test_meeting_recording_pcm.py -v --tb=short
"""

from __future__ import annotations

import struct

import pytest
from unittest.mock import patch


@pytest.fixture()
def store_dir(tmp_path):
    """Redirect MEETINGS_DIR to a temporary directory."""
    meetings_dir = tmp_path / "meetings"
    with patch("src.meeting.store.MEETINGS_DIR", meetings_dir):
        yield meetings_dir


def _make_meeting() -> str:
    from src.meeting.store import create_meeting

    return create_meeting("PCM test").id


def _append_chunks(meeting_id: str, chunks: list[bytes]) -> None:
    from src.meeting.store import append_recording_pcm

    for chunk in chunks:
        append_recording_pcm(meeting_id, chunk)


def test_append_grows_pcm_file(store_dir):
    from src.meeting.store import recording_pcm_path

    meeting_id = _make_meeting()
    path = recording_pcm_path(meeting_id)
    assert not path.exists()

    _append_chunks(meeting_id, [b"\x00\x01" * 100, b"\x02\x03" * 50])
    assert path.read_bytes() == (b"\x00\x01" * 100) + (b"\x02\x03" * 50)


def test_finalize_wraps_pcm_in_16k_mono_wav(store_dir):
    from src.meeting.store import finalize_recording_pcm, get_meeting

    meeting_id = _make_meeting()
    pcm = b"\x10\x20" * 1600  # 3200 bytes = 0.1 s at 16 kHz s16le mono
    _append_chunks(meeting_id, [pcm])

    wav_path = finalize_recording_pcm(meeting_id)
    assert wav_path is not None
    wav = open(wav_path, "rb").read()

    # RIFF header
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    fmt = struct.unpack("<IHHIIHH", wav[16:36])
    assert fmt[0] == 16  # PCM
    assert fmt[1] == 1  # no compression
    assert fmt[2] == 1  # mono
    assert fmt[3] == 16_000  # sample rate
    assert fmt[4] == 32_000  # byte rate = rate * 2
    assert fmt[5] == 2  # block align
    assert fmt[6] == 16  # bits per sample
    assert wav[36:40] == b"data"
    (data_len,) = struct.unpack("<I", wav[40:44])
    assert data_len == len(pcm)
    assert wav[44:] == pcm

    meeting = get_meeting(meeting_id)
    assert meeting.audio_path == str(wav_path)


def test_finalize_empty_pcm_returns_none(store_dir):
    from src.meeting.store import finalize_recording_pcm

    meeting_id = _make_meeting()
    # No chunks appended at all
    assert finalize_recording_pcm(meeting_id) is None

    # A truncated (odd-size) append still has >=2 bytes guard for tiny input
    _append_chunks(meeting_id, [b""])
    assert finalize_recording_pcm(meeting_id) is None


def test_append_missing_meeting_raises(store_dir):
    from src.meeting.store import append_recording_pcm

    with pytest.raises(FileNotFoundError):
        append_recording_pcm("no-such-meeting", b"\x00\x00")


def test_first_pcm_chunk_marks_meeting_recording(store_dir):
    """Live capture must move the meeting to status='recording' server-side.

    Nothing else in the codebase sets that status — browser recording UI is
    client state only. Without this, a page refresh leaves the meeting in
    'created' and the recovery banner has no signal to show.
    """
    from src.meeting.store import append_recording_pcm, get_meeting

    meeting_id = _make_meeting()
    assert get_meeting(meeting_id).status == "created"

    _append_chunks(meeting_id, [b"\x00\x01" * 10])
    assert get_meeting(meeting_id).status == "recording"

    # Subsequent chunks keep it (idempotent, no churn)
    _append_chunks(meeting_id, [b"\x00\x01" * 10])
    assert get_meeting(meeting_id).status == "recording"


# ═══════════════ save-transcript partial checkpoint ═══════════════


def _client(tmp_path):
    """Route-level client over the meeting router with isolated storage."""
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.meeting.routes import router

    app = FastAPI()
    app.include_router(router)
    meetings = tmp_path / "meetings"
    ctx = [
        patch("src.meeting.store.MEETINGS_DIR", meetings),
        patch("src.identity.authorize", lambda *a, **k: None),
        patch("src.identity.get_actor", lambda: None),
    ]
    for c in ctx:
        c.start()
    try:
        yield TestClient(app), meetings
    finally:
        for c in ctx:
            c.stop()


def test_partial_transcript_save_keeps_recording_status(tmp_path):
    from src.meeting.store import create_meeting, update_meeting, get_meeting, get_transcript

    gen = _client(tmp_path)
    client, _ = next(gen)
    meeting_id = create_meeting("Live").id
    update_meeting(meeting_id, status="recording")

    resp = client.post(
        f"/meetings/{meeting_id}/save-transcript",
        json={
            "partial": True,
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
        },
    )
    assert resp.status_code == 200
    meeting = get_meeting(meeting_id)
    assert meeting.status == "recording"  # state machine untouched
    assert get_transcript(meeting_id) is not None  # checkpoint is on disk
    gen.close()


def test_final_transcript_save_completes_meeting(tmp_path):
    from unittest.mock import patch
    from src.meeting.store import create_meeting, update_meeting, get_meeting

    gen = _client(tmp_path)
    client, _ = next(gen)
    meeting_id = create_meeting("Live").id
    update_meeting(meeting_id, status="recording")

    with patch("src.meeting.pipeline.normalize_sentences", lambda mid, segs: []):
        resp = client.post(
            f"/meetings/{meeting_id}/save-transcript",
            json={
                "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
            },
        )
    assert resp.status_code == 200
    assert get_meeting(meeting_id).status == "completed"
    gen.close()


# ═══════════════ recovery finalize (interrupted capture) ═══════════════


def test_finalize_with_recovery_resets_recording_status(tmp_path):
    """recovery=true: materialize the PCM and clear the stale recording state.

    The meeting must land in the same shape as an uploaded-audio meeting:
    audio_path set, status back to 'created' so the list stops showing
    Recording and the page shows the audio-ready review UI.
    """
    from src.meeting.store import (
        append_recording_pcm, create_meeting, update_meeting, get_meeting,
    )

    gen = _client(tmp_path)
    client, _ = next(gen)
    meeting_id = create_meeting("Interrupted").id
    update_meeting(meeting_id, status="recording")
    append_recording_pcm(meeting_id, b"\x10\x20" * 100)

    resp = client.post(f"/meetings/{meeting_id}/finalize-recording?recovery=true")
    assert resp.status_code == 200
    meeting = get_meeting(meeting_id)
    assert meeting.status == "created"
    assert meeting.audio_path is not None
    assert meeting.mode == "record"
    gen.close()


def test_finalize_with_recovery_clears_status_even_without_pcm(tmp_path):
    """An orphan shorter than one PCM chunk still must not stay 'recording'."""
    from src.meeting.store import create_meeting, update_meeting, get_meeting

    gen = _client(tmp_path)
    client, _ = next(gen)
    meeting_id = create_meeting("Blink").id
    update_meeting(meeting_id, status="recording")

    resp = client.post(f"/meetings/{meeting_id}/finalize-recording?recovery=true")
    assert resp.status_code == 200
    meeting = get_meeting(meeting_id)
    assert meeting.status == "created"
    assert meeting.audio_path is None
    gen.close()


def test_finalize_without_recovery_keeps_status(tmp_path):
    """Normal stop path: finalize alone does not touch status."""
    from src.meeting.store import (
        append_recording_pcm, create_meeting, update_meeting, get_meeting,
    )

    gen = _client(tmp_path)
    client, _ = next(gen)
    meeting_id = create_meeting("Normal").id
    update_meeting(meeting_id, status="recording")
    append_recording_pcm(meeting_id, b"\x10\x20" * 100)

    resp = client.post(f"/meetings/{meeting_id}/finalize-recording")
    assert resp.status_code == 200
    assert get_meeting(meeting_id).status == "recording"
    gen.close()
