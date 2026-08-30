from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException

from src.speakers import profile as profile_domain
from src.speakers import service, store

logger = logging.getLogger("speakers")
router = APIRouter(tags=["speakers"])


@router.get("/speakers")
async def list_speakers(q: str | None = None):
    people = store.list_people(q=q)
    counts: dict[str, int] = {}
    for p in people:
        key = (p.display_name or "").strip().lower()
        counts[key] = counts.get(key, 0) + 1
    return [store.person_public_dict(p, name_counts=counts) for p in people]


@router.post("/speakers")
async def create_speaker(body: dict = Body()):
    name = (body.get("display_name") or "").strip()
    if not name:
        raise HTTPException(400, "display_name is required")
    person = store.create_person(name, body.get("disambiguator") or "")
    return store.person_public_dict(person)


@router.get("/speakers/me")
async def get_me_speaker():
    pid = store.get_me_person_id()
    if not pid:
        return {"person_id": None, "person": None}
    person = store.get_person(pid)
    if person is None:
        return {"person_id": None, "person": None}
    return {"person_id": pid, "person": store.person_public_dict(person)}


@router.put("/speakers/me")
async def set_me_speaker(body: dict = Body()):
    raw = body.get("person_id", body.get("id"))
    if raw is None or raw is False or raw == "":
        store.set_me_person_id(None)
        return {"person_id": None, "person": None}
    try:
        pid = store.set_me_person_id(str(raw).strip())
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    person = store.get_person(pid) if pid else None
    return {
        "person_id": pid,
        "person": store.person_public_dict(person) if person else None,
    }


@router.patch("/speakers/{person_id}")
async def patch_speaker(person_id: str, body: dict = Body()):
    fields = {}
    if "display_name" in body:
        fields["display_name"] = (body.get("display_name") or "").strip()
    if "disambiguator" in body:
        fields["disambiguator"] = (body.get("disambiguator") or "").strip()
    if "is_me" in body:
        want = bool(body.get("is_me"))
        try:
            if want:
                store.set_me_person_id(person_id)
            elif store.get_me_person_id() == person_id:
                store.set_me_person_id(None)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
    if not fields and "is_me" not in body:
        raise HTTPException(400, "No valid fields to update")
    try:
        person = store.update_person(person_id, **fields) if fields else store.get_person(person_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if person is None:
        raise HTTPException(404, "Person not found")
    return store.person_public_dict(person)


@router.get("/speakers/{person_id}")
async def get_speaker(person_id: str):
    person = store.get_person(person_id)
    if person is None:
        raise HTTPException(404, "Person not found")
    return service.person_detail_dict(person)


@router.delete("/speakers/{person_id}")
async def delete_speaker(person_id: str):
    person = store.get_person(person_id)
    if person is None:
        raise HTTPException(404, "Person not found")
    for row in person.recent:
        try:
            from src.meeting.store import get_meeting, update_meeting

            meeting = get_meeting(row.meeting_id)
            if meeting is None or not meeting.speaker_people:
                continue
            people_map = {
                spk: pid for spk, pid in meeting.speaker_people.items() if pid != person_id
            }
            if people_map == meeting.speaker_people:
                continue
            matches = dict(meeting.speaker_matches or {})
            for spk, pid in list((meeting.speaker_people or {}).items()):
                if pid == person_id:
                    matches.pop(spk, None)
            update_meeting(
                meeting.id,
                speaker_people=people_map or None,
                speaker_matches=matches or None,
                speaker_names=service.rebuild_speaker_names(
                    people_map, keep=meeting.speaker_names
                )
                or None,
            )
        except Exception:
            logger.warning("Failed to unbind person %s from meeting %s", person_id, row.meeting_id, exc_info=True)
    store.delete_person(person_id)
    return {"ok": True}


@router.get("/speakers/{person_id}/profile")
async def get_person_profile(person_id: str):
    if store.get_person(person_id) is None:
        raise HTTPException(404, "Person not found")
    return profile_domain.profile_state(person_id)


@router.post("/speakers/{person_id}/profile/regenerate")
async def regenerate_person_profile(person_id: str, body: dict = Body(default=None)):
    if store.get_person(person_id) is None:
        raise HTTPException(404, "Person not found")
    locale = ((body or {}).get("locale") or "zh-CN").strip() or "zh-CN"
    force = bool((body or {}).get("force", True))
    return profile_domain.start_regenerate(person_id, locale=locale, force=force)


@router.get("/speakers/{person_id}/preview")
async def speaker_preview(
    person_id: str,
    exclude_meeting: str | None = None,
    exclude_start: float | None = None,
):
    if store.get_person(person_id) is None:
        raise HTTPException(404, "Person not found")
    preview = service.pick_preview(
        person_id,
        exclude_meeting_id=exclude_meeting,
        exclude_start=exclude_start,
    )
    if preview is None:
        raise HTTPException(404, "No playable clip")
    preview.pop("audio_path", None)
    return preview


@router.get("/speakers/{person_id}/preview-audio")
async def speaker_preview_audio(
    person_id: str,
    exclude_meeting: str | None = None,
    exclude_start: float | None = None,
    meeting_id: str | None = None,
    start: float | None = None,
    end: float | None = None,
):
    """Return a short WAV clip so the browser does not have to seek WebM."""
    if store.get_person(person_id) is None:
        raise HTTPException(404, "Person not found")
    if meeting_id is not None and start is not None and end is not None:
        from src.meeting.store import get_meeting

        meeting = get_meeting(meeting_id)
        audio = service.resolve_meeting_audio_path(
            getattr(meeting, "audio_path", None) if meeting else None
        )
        if audio is None:
            raise HTTPException(404, "Audio file not found on disk")
        wav = _slice_audio_wav(audio, float(start), float(end))
        from fastapi.responses import Response

        return Response(content=wav, media_type="audio/wav")
    preview = service.pick_preview(
        person_id,
        exclude_meeting_id=exclude_meeting,
        exclude_start=exclude_start,
    )
    if preview is None:
        raise HTTPException(404, "No playable clip")
    audio = service.resolve_meeting_audio_path(preview.get("audio_path"))
    if audio is None:
        from src.meeting.store import get_meeting

        meeting = get_meeting(preview["meeting_id"])
        audio = service.resolve_meeting_audio_path(
            getattr(meeting, "audio_path", None) if meeting else None
        )
    if audio is None:
        raise HTTPException(404, "Audio file not found on disk")
    start = float(preview["start"])
    end = float(preview["end"])
    wav = _slice_audio_wav(audio, start, end)
    from fastapi.responses import Response

    return Response(content=wav, media_type="audio/wav")


def _slice_audio_wav(path, start: float, end: float) -> bytes:
    import shutil
    import subprocess

    from src.runtime_bins import ffmpeg_bin

    ff = ffmpeg_bin()
    if ff == "ffmpeg" and not shutil.which("ffmpeg"):
        raise HTTPException(500, "ffmpeg is required to preview speaker audio")
    duration = max(0.3, end - start)
    cmd = [
        ff,
        "-v",
        "error",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(path),
        "-f",
        "wav",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0 or not result.stdout:
        err = (result.stderr or b"").decode("utf-8", errors="replace")[-300:]
        logger.warning("preview ffmpeg failed: %s", err)
        raise HTTPException(500, "Failed to extract preview clip")
    return result.stdout


@router.put("/meetings/{meeting_id}/speakers/{speaker_id}")
async def assign_meeting_speaker(meeting_id: str, speaker_id: str, body: dict = Body()):
    from src.meeting.store import get_meeting
    from src.meeting.routes import _serialize_meeting

    if get_meeting(meeting_id) is None:
        raise HTTPException(404, "Meeting not found")
    new_person = body.get("new_person")
    person_id = body.get("person_id")
    display_name = (body.get("display_name") or "").strip() or None
    if new_person is None and "person_id" not in body and not display_name:
        raise HTTPException(400, "person_id, new_person, or display_name is required")
    try:
        meeting = service.assign_speaker(
            meeting_id,
            speaker_id,
            None if person_id is None and not new_person else person_id,
            new_person=new_person,
            display_name=None if new_person or "person_id" in body else display_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _serialize_meeting(meeting)


@router.post("/meetings/{meeting_id}/speakers/commit")
async def commit_meeting_speakers(meeting_id: str):
    from src.meeting.store import get_meeting
    from src.meeting.routes import _serialize_meeting

    if get_meeting(meeting_id) is None:
        raise HTTPException(404, "Meeting not found")
    meeting = service.commit_pending(meeting_id)
    return _serialize_meeting(meeting)
