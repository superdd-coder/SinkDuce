from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import struct
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .models import Meeting, MeetingMode, MeetingStatus, ProcessingState, GenerationState, TranscriptionResult
from src.config import DATA_DIR

from .webm_fixer import fix_webm_duration

logger = logging.getLogger("meeting.store")
MEETINGS_DIR = DATA_DIR / "meetings"


def section_content_hash(content: str) -> str:
    """Stable SHA-256 of section markdown (raw editor content on disk)."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def enrich_tabs_needs_reingest(meeting: Meeting) -> Meeting:
    """Recompute ``needs_reingest`` from disk MD vs ``ingested_content_hash``.

    Survives UI remounts: boolean-only flags can be lost; hash comparison is durable.
    Mutates a shallow copy of tab dicts on the meeting object.
    """
    if not meeting.tabs:
        return meeting
    enriched: list[dict] = []
    for t in meeting.tabs:
        td = dict(t) if isinstance(t, dict) else (
            t.model_dump() if hasattr(t, "model_dump") else dict(t)
        )
        fid = (td.get("allocated_file_id") or "").strip()
        stored = (td.get("ingested_content_hash") or "").strip()
        if fid and stored:
            tid = td.get("tab_id") or ""
            md = get_section_md(meeting.id, tid) if tid else None
            current = section_content_hash(md or "")
            td["needs_reingest"] = current != stored
        elif fid and not stored:
            # Legacy allocate without hash: keep explicit flag if set
            td.setdefault("needs_reingest", False)
        else:
            td["needs_reingest"] = False
        enriched.append(td)
    meeting.tabs = enriched
    return meeting

# Per-meeting file lock to prevent concurrent read-modify-write races
# when multiple section streams write to the same meta.json.
_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_lock(meeting_id: str) -> threading.Lock:
    with _locks_lock:
        if meeting_id not in _locks:
            _locks[meeting_id] = threading.Lock()
        return _locks[meeting_id]


def _meeting_dir(meeting_id: str) -> Path:
    from src.paths import assert_resource_id, confine

    assert_resource_id(meeting_id, name="meeting_id")
    return confine(MEETINGS_DIR / meeting_id, MEETINGS_DIR)


def _write_json(path: Path, data: dict) -> None:
    from src.atomic_io import write_text_atomic

    write_text_atomic(
        path, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _read_cache_evict(path)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── Read cache (mtime-validated) ─────────────────────────────────────
# meta.json / sentences.json / section md are re-read several times per
# chat turn (catalog, digest, lookup stitching). Cache the parsed value
# keyed by path and revalidate with st_mtime_ns. Every write goes through
# _write_json / write_text below, so a changed mtime invalidates the entry;
# a missing file is cached as (None, None).
_read_cache: dict[str, tuple[int | None, Any]] = {}
_read_cache_lock = threading.Lock()


def _read_cached(path: Path, load):
    """mtime-validated cache around a pure read of *path*.

    ``load`` is called only on miss and must not mutate its result after
    returning (callers get defensive copies from the public getters).
    """
    key = str(path)
    try:
        mtime: int | None = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    with _read_cache_lock:
        entry = _read_cache.get(key)
        if entry is not None and entry[0] == mtime:
            return entry[1]
    value = None if mtime is None else load(path)
    with _read_cache_lock:
        _read_cache[key] = (mtime, value)
    return value


def _read_cache_evict(path: Path) -> None:
    with _read_cache_lock:
        _read_cache.pop(str(path), None)


def _meeting_to_dict(meeting: Meeting) -> dict:
    data = meeting.model_dump()
    data["created_at"] = meeting.created_at.isoformat()
    data["updated_at"] = meeting.updated_at.isoformat()
    return data


def _dict_to_meeting(data: dict) -> Meeting:
    if "created_at" in data and isinstance(data["created_at"], str):
        dt = datetime.fromisoformat(data["created_at"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        data["created_at"] = dt
    if "updated_at" in data and isinstance(data["updated_at"], str):
        dt = datetime.fromisoformat(data["updated_at"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        data["updated_at"] = dt
    return Meeting(**data)


def create_meeting(
    title: str,
    mode: MeetingMode | None = None,
    ui_lang: str | None = None,
) -> Meeting:
    from src.identity import authorize, get_actor

    actor = get_actor()
    authorize(actor, "meeting.create", {})
    meeting_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    # 记下创建者：`policy._check_domain` 的**创建者例外**（"member 能操作自己建的会议"）
    # 靠它才有数据可用。没有这个字段时那条例外永远取不到 creator，等于死代码——
    # 后果是 member 被移出 operate 档后**连自己建的会议都录不了、删不掉**
    # （2026-09-17 实测：远端 member 开始录制，`recording-pcm` 一片 403）。
    # 存 actor.id（本机 = "local"，钥匙 = 钥匙 id），与 policy 的比较口径一致。
    meeting = Meeting(
        id=meeting_id,
        title=title,
        mode=mode,
        created_at=now,
        updated_at=now,
        created_by=actor.id if actor else None,
        ui_lang=ui_lang,
    )
    meeting_dir = _meeting_dir(meeting_id)
    meeting_dir.mkdir(parents=True, exist_ok=True)
    _write_json(meeting_dir / "meta.json", _meeting_to_dict(meeting))
    logger.info("Created meeting id=%s title='%s' dir=%s", meeting_id, title, meeting_dir)
    return meeting


def get_meeting(meeting_id: str) -> Meeting | None:
    cached = _read_cached(_meeting_dir(meeting_id) / "meta.json", _load_meeting)
    if cached is None:
        return None
    # Fresh instance per call: callers mutate their Meeting freely.
    return cached.model_copy(deep=True)


def _load_meeting(path: Path) -> Meeting | None:
    data = _read_json(path)
    if data is None:
        return None
    return _dict_to_meeting(data)


def list_meetings() -> list[Meeting]:
    if not MEETINGS_DIR.exists():
        return []
    meetings: list[Meeting] = []
    for entry in MEETINGS_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            data = _read_json(entry / "meta.json")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping corrupted meeting %s: %s", entry.name, exc)
            continue
        if data is not None:
            meetings.append(_dict_to_meeting(data))
    # Newest first by creation time (not last activity)
    meetings.sort(key=lambda m: m.created_at or m.updated_at, reverse=True)
    return meetings


#: 会议列表卡片上**看得到**的状态字段。这几个一变，对端就必须刷新。
#: 刻意只列这些：`update_meeting` 每次进度写入都会被调用，若对任何字段都广播，
#: 一条转写进度就能刷屏（十几个事件/秒）。
_VISIBLE_STATE_FIELDS: tuple[str, ...] = (
    "status",
    "processing_state",
    "summary_gen_state",
    "blueprint_gen_state",
    "transcript_index_status",
)


def _emit_state_change(meeting_id: str, before: dict, after: Mapping[str, Any]) -> None:
    """列表可见状态变化 → 广播 ``meeting.updated``（多端同步）。

    **为什么放在 `update_meeting` 里而不是各路由**：状态会从很多地方变——
    请求路由、后台任务线程（转写/总结/提取跑完）、录制 PCM 落盘（录制开始就是
    这里翻的 status）。逐个路由加广播**必然漏**，而漏掉的那条就是用户看到的
    "别的设备上状态不刷新，要手动刷新才发现"。放在唯一的写入漏斗里，一次覆盖全部。

    事件是**告知**不是授权，`bus.publish` 线程安全（后台线程直接发即可）；
    `emit()` 在没有请求上下文时会留空 origin，于是广播给所有订阅端。
    """
    changed = [f for f in _VISIBLE_STATE_FIELDS if before.get(f) != after.get(f)]
    if not changed:
        return
    try:
        from src.events.emit import emit

        emit(
            "meeting.updated",
            scope={"meeting_id": meeting_id},
            payload={"changed": changed, "status": after.get("status")},
        )
    except Exception:
        # 广播失败绝不能影响写入本身（状态已经落盘了）。
        logger.warning("meeting state broadcast failed for %s", meeting_id, exc_info=True)


def update_meeting(meeting_id: str, **fields) -> Meeting:
    from src.identity import authorize, get_actor

    # resource 里带上该会议的 created_by：**创建者例外**要靠它
    # （member 能改/录自己建的会议）。只传 meeting_id 时 policy 取不到 creator，
    # 例外永远不生效 —— 那是个"看着有、其实死"的授权分支。
    _existing = get_meeting(meeting_id)
    authorize(
        get_actor(),
        "meeting.update",
        {
            "meeting_id": meeting_id,
            "created_by": getattr(_existing, "created_by", None),
        },
    )
    with _get_lock(meeting_id):
        meeting = get_meeting(meeting_id)
        if meeting is None:
            raise FileNotFoundError(f"Meeting {meeting_id} not found")
        if "hot_words_library_ids" in fields:
            ids = [
                str(x).strip()
                for x in (fields.get("hot_words_library_ids") or [])
                if str(x).strip()
            ]
            fields["hot_words_library_ids"] = ids
            fields["hot_words_library_id"] = ids[0] if ids else None
        elif "hot_words_library_id" in fields:
            one = fields.get("hot_words_library_id") or None
            fields["hot_words_library_id"] = one
            fields["hot_words_library_ids"] = [one] if one else []
        # 变更前快照（只取要比较的那几个字段，避免整对象深拷贝）
        before = {f: getattr(meeting, f, None) for f in _VISIBLE_STATE_FIELDS}
        for key, value in fields.items():
            setattr(meeting, key, value)
        meeting.updated_at = datetime.now(timezone.utc)
        _write_json(_meeting_dir(meeting_id) / "meta.json", _meeting_to_dict(meeting))
        after = {f: getattr(meeting, f, None) for f in _VISIBLE_STATE_FIELDS}
    # 锁外广播：publish 只做入队，不回调业务代码，但放在锁外能避免将来有人
    # 在订阅端做同步工作时把会议写锁拖长。
    _emit_state_change(meeting_id, before, after)
    return meeting


def delete_meeting(meeting_id: str) -> bool:
    from src.identity import authorize, get_actor

    _m = get_meeting(meeting_id)
    authorize(
        get_actor(),
        "meeting.delete",
        {"meeting_id": meeting_id, "created_by": getattr(_m, "created_by", None)},
    )
    directory = _meeting_dir(meeting_id)
    if not directory.exists():
        return False
    meeting = get_meeting(meeting_id)
    if meeting is not None and meeting.speaker_people:
        try:
            from src.speakers.service import unenroll_meeting

            unenroll_meeting(meeting_id)
        except Exception:
            logger.warning("voiceprint unenroll skipped for %s", meeting_id, exc_info=True)
    try:
        from src.meeting.transcript_index import purge_meeting_transcripts

        purge_meeting_transcripts(meeting_id)
    except Exception:
        logger.warning("transcript index purge skipped for %s", meeting_id, exc_info=True)
    try:
        from src.meeting.group_store import drop_meeting_from_all_groups

        drop_meeting_from_all_groups(meeting_id)
    except Exception:
        logger.warning("group membership drop skipped for %s", meeting_id, exc_info=True)
    shutil.rmtree(directory)
    return True


def save_audio(meeting_id: str, file_bytes: "bytes | BinaryIO", ext: str, original_filename: str | None = None) -> str:
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    # Delete old audio file if replacing
    if meeting.audio_path:
        old_path = Path(meeting.audio_path)
        if old_path.exists():
            old_path.unlink()
            logger.info("Deleted old audio: %s for meeting %s", old_path, meeting_id)
    # Use original filename if provided, otherwise fall back to audio.{ext}
    if original_filename and "." in original_filename:
        safe_name = original_filename.replace("/", "_").replace("\\", "_")
        audio_path = _meeting_dir(meeting_id) / safe_name
    else:
        audio_path = _meeting_dir(meeting_id) / f"audio.{ext}"
    size = 0
    if isinstance(file_bytes, (bytes, bytearray)):
        size = len(file_bytes)
        audio_path.write_bytes(file_bytes)
    else:
        # 流式：音频文件可到几百 MB，分块落盘避免整块进 RAM。
        import shutil

        with open(audio_path, "wb") as out:
            while True:
                chunk = file_bytes.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                out.write(chunk)
    fix_webm_duration(audio_path)
    update_meeting(meeting_id, audio_path=str(audio_path))
    logger.info("Saved audio: %s (%d bytes) for meeting %s", audio_path, size, meeting_id)
    return str(audio_path)


RECORDING_PCM_NAME = "recording.s16le"
RECORDING_WAV_NAME = "recording.wav"
_PCM_RATE = 16_000


def recording_pcm_path(meeting_id: str) -> Path:
    return _meeting_dir(meeting_id) / RECORDING_PCM_NAME


#: 判定"这台机器上是否**正在**录制"的活跃窗口（秒）。
#: 采集端每 ~500ms POST 一次 `recording-pcm`，所以只要文件近几十秒还在长，
#: 就是有人在录；反之说明录制已中断（页面刷新 / 进程崩了），那才是可回收的孤儿。
RECORDING_LIVE_WINDOW_S = 90


def recording_is_live(meeting_id: str, *, window_s: int = RECORDING_LIVE_WINDOW_S) -> bool:
    """这台机器上是否**正在**录制（PCM 文件近期还在增长）。

    **为什么需要它**：``status == "recording"`` 只说明"曾经开始录过"，不说明现在还在录。
    区分这两种情况很关键：

    - **正在录**（别的设备 / 窗口在录）→ 旁观者应当看得到「录制中」，且**不能进去**
      （进去会抢走或干扰正在录的那个客户端），更不能触发任何 finalize。
    - **已中断**（自己的页面刷新过、进程崩了）→ 才是可自动回收的孤儿。

    判据用 PCM 文件 mtime：采集端每 ~500ms append + fsync，"还在录" = 近
    ``window_s`` 秒内被写过。文件不存在 → 没在录。
    """
    try:
        st = recording_pcm_path(meeting_id).stat()
    except OSError:
        return False
    return (datetime.now(timezone.utc).timestamp() - st.st_mtime) <= window_s


def append_recording_pcm(meeting_id: str, chunk: bytes) -> None:
    """Append 16 kHz s16le PCM during live capture and fsync so a crash keeps audio."""
    if not chunk:
        return
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    path = recording_pcm_path(meeting_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _get_lock(meeting_id):
        with path.open("ab") as fh:
            fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
    # Mark live capture server-side: nothing else ever sets status='recording'
    # (the recording UI is browser-local state), and the post-refresh recovery
    # banner keys off this status. Idempotent — writes only on the transition.
    if meeting.status != MeetingStatus.recording:
        update_meeting(meeting_id, status=MeetingStatus.recording)


def _pcm_s16le_to_wav(pcm: bytes, sample_rate: int = _PCM_RATE) -> bytes:
    n = len(pcm)
    return b"".join(
        (
            b"RIFF",
            struct.pack("<I", 36 + n),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16),
            b"data",
            struct.pack("<I", n),
            pcm,
        )
    )


def finalize_recording_pcm(meeting_id: str) -> str | None:
    """Turn crash-safe PCM into a WAV and set meeting.audio_path. None if empty.

    **收尾后必须删掉 PCM 文件**（2026-09-17 修的"锁死"bug）：

    `recording_is_live()` 是按 **PCM 文件的 mtime 新鲜度**判"此刻是否真在录"，而这个
    文件原先 finalize 之后一直留着 —— 于是**录制已经停了，那个会议在接下来 90 秒里
    仍然被判为"正在录"**，对所有人（包括刚停下来的创建者自己）都锁着、点不进去。
    用户报的就是这个：「Meeting 被锁住之后…想回去也被锁在外面，所有人都锁在外面了」。

    PCM 转成 WAV 之后就没用途了（它只是崩溃时的保险），所以 WAV 写成功即删 ——
    这样 `recording_live` 立刻翻 false，锁当场释放。

    **删除放在 WAV 写成功之后**：万一写 WAV 失败，PCM 还在，音频不至于丢。
    """
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    pcm_path = recording_pcm_path(meeting_id)
    if not pcm_path.is_file() or pcm_path.stat().st_size < 2:
        return None
    pcm = pcm_path.read_bytes()
    wav_path = _meeting_dir(meeting_id) / RECORDING_WAV_NAME
    wav_path.write_bytes(_pcm_s16le_to_wav(pcm))
    update_meeting(meeting_id, audio_path=str(wav_path), mode=MeetingMode.record)
    logger.info(
        "Finalized recording WAV: %s (%d pcm bytes) for meeting %s",
        wav_path,
        len(pcm),
        meeting_id,
    )
    # WAV 已落盘 → 可以安全丢掉崩溃保险。留着会让这个会议继续被判为"录制中"。
    try:
        pcm_path.unlink()
        logger.info("Removed PCM after finalize for meeting %s", meeting_id)
    except OSError:
        logger.warning(
            "could not remove PCM after finalize for %s", meeting_id, exc_info=True,
        )
    return str(wav_path)


_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"})


def save_note_image(meeting_id: str, filename: str, content: bytes) -> str:
    """Save a notes image under ``meetings/{id}/images/`` and return the stored name."""
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else "png"
    if ext not in _IMAGE_EXTS:
        ext = "png"
    safe_name = f"{uuid.uuid4().hex[:10]}.{ext}"
    images_dir = _meeting_dir(meeting_id) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    from src.paths import confine

    path = confine(images_dir / safe_name, images_dir)
    path.write_bytes(content)
    logger.info("Saved note image: %s (%d bytes) for meeting %s", path, len(content), meeting_id)
    return safe_name


def get_note_image_path(meeting_id: str, filename: str) -> Path:
    from src.paths import confine

    images_dir = _meeting_dir(meeting_id) / "images"
    return confine(images_dir / filename, images_dir)


def save_notes(meeting_id: str, content: str) -> str:
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    notes_path = _meeting_dir(meeting_id) / "notes.md"
    from src.atomic_io import write_text_atomic

    write_text_atomic(notes_path, content, encoding="utf-8")
    _read_cache_evict(notes_path)
    update_meeting(meeting_id, notes_path=str(notes_path))
    logger.info("Saved notes: %s (%d chars) for meeting %s", notes_path, len(content), meeting_id)
    return str(notes_path)


def save_transcript(meeting_id: str, result: TranscriptionResult) -> str:
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    transcript_path = _meeting_dir(meeting_id) / "transcript.json"
    data = result.model_dump()
    _write_json(transcript_path, data)
    update_meeting(meeting_id, transcript_path=str(transcript_path))
    logger.info("Saved transcript: %s (%d segments, %d chars) for meeting %s", transcript_path, len(result.segments), len(result.text), meeting_id)
    return str(transcript_path)


def get_notes(meeting_id: str) -> str | None:
    meeting = get_meeting(meeting_id)
    if meeting is None or meeting.notes_path is None:
        return None
    notes_path = Path(meeting.notes_path)
    if not notes_path.exists():
        return None
    return notes_path.read_text(encoding="utf-8")


def get_transcript(meeting_id: str) -> TranscriptionResult | None:
    meeting = get_meeting(meeting_id)
    if meeting is None or meeting.transcript_path is None:
        return None
    data = _read_cached(Path(meeting.transcript_path), _load_transcript_dict)
    if data is None:
        return None
    return TranscriptionResult(**data)


def _load_transcript_dict(path: Path) -> dict | None:
    return _read_json(path)


# ── Live summary (in-meeting incremental state) ───────────────────────


def live_summary_path(meeting_id: str) -> Path:
    return _meeting_dir(meeting_id) / "live_summary.json"


def save_live_summary(meeting_id: str, state: dict) -> None:
    """Persist the LiveSummaryState snapshot written after each engine round."""
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")
    _write_json(live_summary_path(meeting_id), state)


def get_live_summary(meeting_id: str) -> dict | None:
    """Return the persisted LiveSummaryState dict, or None if never written."""
    data = _read_cached(live_summary_path(meeting_id), _read_json)
    if data is None:
        return None
    import copy

    return copy.deepcopy(data)


# ── Pipeline data (sentences, chunks, section markdown) ───────────────


def sentences_path(meeting_id: str) -> Path:
    return _meeting_dir(meeting_id) / "sentences.json"


def save_sentences(meeting_id: str, sentences: list[dict]) -> str:
    """Persist Sentence array (metadata only, no embedding vectors)."""
    path = sentences_path(meeting_id)
    with _get_lock(meeting_id):
        _write_json(path, {"sentences": sentences})
    return str(path)


def get_sentences(meeting_id: str) -> list[dict] | None:
    """Return raw Sentence dicts or None if not yet computed."""
    cached = _read_cached(sentences_path(meeting_id), _load_sentences)
    if cached is None:
        return None
    # Fresh dicts per call so callers can mutate rows without touching the cache.
    return [dict(s) for s in cached]


def _load_sentences(path: Path) -> list[dict] | None:
    data = _read_json(path)
    if data is None:
        return None
    return data.get("sentences", [])


def apply_section_tags(
    meeting_id: str, tab_id: str, sentence_ids: list[str]
) -> None:
    """Atomically set *tab_id* on the given sentences; drop it from others.

    Parallel section streams must use this instead of load/save of the
    whole sentences.json, or the last writer wipes the others' T-tags.
    """
    wanted = set(sentence_ids)
    with _get_lock(meeting_id):
        data = _read_json(sentences_path(meeting_id))
        if data is None:
            return
        sentences = data.get("sentences") or []
        for s in sentences:
            tags = [t for t in (s.get("section_tags") or []) if t != tab_id]
            if s.get("sentence_id", "") in wanted:
                tags.append(tab_id)
            s["section_tags"] = tags
        _write_json(sentences_path(meeting_id), {"sentences": sentences})


def section_md_path(meeting_id: str, tab_id: str) -> Path:
    return _meeting_dir(meeting_id) / f"{tab_id}.md"


def save_section_md(meeting_id: str, tab_id: str, content: str) -> str:
    """Write a section's generated markdown to its tab file.

    **正文一变，译文就作废**：译文是这份 summary 的派生物，内容换了之后旧译文
    描述的是旧内容。不清掉的话，用户在语言下拉里切过去看到的还是**上一版**，
    而且绿点还亮着（表示"已有译文"）——2026-09-17 用户两次报这个问题。

    所以在这里做**唯一的收口**：`save_section_md` 是"写小节正文"的唯一漏斗，
    重新生成总览（`generate-summary`）、重新生成小节、重新转录、手改正文全走它。
    之前只在重新转录/删除小节的路径上清，**漏了"重新生成 Summary"这条**（它不经过
    `delete_pipeline_data`），所以用户重新生成后仍然看到旧译文。

    只在**内容真的变了**时才清：重跑出完全相同的正文时留着译文，避免无谓损失。
    """
    path = section_md_path(meeting_id, tab_id)
    # 先读旧内容（写下去就没法比了）
    old: str | None = None
    try:
        if path.exists():
            old = path.read_text(encoding="utf-8")
    except OSError:
        old = None

    from src.atomic_io import write_text_atomic

    write_text_atomic(path, content, encoding="utf-8")
    _read_cache_evict(path)

    if old is not None and old != content:
        try:
            removed = delete_translation_mds(meeting_id, tab_id)
            if removed:
                logger.info(
                    "[SECTION-MD] %s/%s content changed → cleared %d stale translation(s)",
                    meeting_id, tab_id, removed,
                )
        except Exception:
            # 清理失败不该让正文写入失败（正文已经落盘了）。
            logger.warning(
                "translation cleanup after section md write failed for %s/%s",
                meeting_id, tab_id, exc_info=True,
            )
    return str(path)


def get_section_md(meeting_id: str, tab_id: str) -> str | None:
    """Read a section's generated markdown or None."""
    path = section_md_path(meeting_id, tab_id)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    with _read_cache_lock:
        entry = _read_cache.get(str(path))
        if entry is not None and entry[0] == mtime:
            return entry[1]
    content = path.read_text(encoding="utf-8")
    with _read_cache_lock:
        _read_cache[str(path)] = (mtime, content)
    return content


def translation_md_path(meeting_id: str, tab_id: str, lang: str) -> Path:
    """Path of a translated summary: `{tab_id}_{LANG}.md` (never ingested)."""
    return _meeting_dir(meeting_id) / f"{tab_id}_{lang.upper()}.md"


def save_translation_md(meeting_id: str, tab_id: str, lang: str, content: str) -> str:
    """Write a translated summary markdown to its language file."""
    path = translation_md_path(meeting_id, tab_id, lang)
    from src.atomic_io import write_text_atomic

    write_text_atomic(path, content, encoding="utf-8")
    _read_cache_evict(path)
    return str(path)


def get_translation_md(meeting_id: str, tab_id: str, lang: str) -> str | None:
    """Read a translated summary markdown or None."""
    path = translation_md_path(meeting_id, tab_id, lang)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def list_translation_langs(meeting_id: str, tab_id: str) -> list[str]:
    """Return the language codes that already have a translation file."""
    prefix = f"{tab_id}_"
    langs = [
        p.stem[len(prefix):]
        for p in _meeting_dir(meeting_id).glob(f"{prefix}*.md")
        if p.stem[len(prefix):]
    ]
    return sorted(langs)


def delete_translation_mds(meeting_id: str, tab_id: str) -> int:
    """Delete every translated summary of one section. Returns how many were removed.

    **为什么只按"已知语言码"白名单删，而不是 glob `{tab_id}_*.md`**：
    同一个目录里还住着 `notes.md`（用户手写的会议笔记）和
    `note_distillation_tab_general.md` 之类的东西。`tab_general_CN.md` 与
    它们只差一个后缀，任何"按前缀扫掉一片"的写法都有把它们一起删掉的风险，
    而会议笔记是不可再生的用户数据。所以这里对 `TRANSLATION_LANG_NAMES`
    （与生成侧同一份白名单）逐个精确删——**命中面只有翻译产物本身**。
    """
    from src.prompts import TRANSLATION_LANG_NAMES

    removed = 0
    for lang in TRANSLATION_LANG_NAMES:
        path = translation_md_path(meeting_id, tab_id, lang)
        try:
            if path.exists():
                path.unlink()
                _read_cache_evict(path)
                removed += 1
        except OSError:
            logger.warning(
                "cannot delete translation %s for meeting %s", path.name, meeting_id,
                exc_info=True,
            )
    return removed


def delete_all_translation_mds(meeting_id: str) -> int:
    """Delete translated summaries for **every** section of a meeting.

    Covers the case where the section list itself is about to change (re-transcribe
    rebuilds tabs from scratch), so we cannot rely on iterating current tabs — a
    section that is about to disappear would otherwise leave its translation behind
    forever.
    """
    from src.prompts import TRANSLATION_LANG_NAMES

    directory = _meeting_dir(meeting_id)
    removed = 0
    # 找 `*_{LANG}.md` 里语言码命中白名单的那些——仍然逐个精确匹配，不做前缀通配。
    for lang in TRANSLATION_LANG_NAMES:
        for path in directory.glob(f"*_{lang}.md"):
            try:
                path.unlink()
                _read_cache_evict(path)
                removed += 1
            except OSError:
                logger.warning(
                    "cannot delete translation %s for meeting %s",
                    path.name, meeting_id, exc_info=True,
                )
    return removed


def delete_pipeline_data(meeting_id: str) -> None:
    """Remove all derived pipeline data (sentences, section mds, translations).

    Called before re-running pipeline after re-transcription.
    Keeps transcript.json and meta.json intact.
    """
    import os as _os

    _meeting_dir(meeting_id)
    for name in ("sentences.json",):
        p = _meeting_dir(meeting_id) / name
        if p.exists():
            p.unlink()
    # Remove section .md files
    meeting = get_meeting(meeting_id)
    if meeting and meeting.tabs:
        for tab in meeting.tabs:
            if isinstance(tab, dict):
                tid = tab.get("tab_id", "")
            else:
                tid = getattr(tab, "tab_id", "")
            if tid:
                md = section_md_path(meeting_id, tid)
                if md.exists():
                    md.unlink()
    # 译文的正文来自旧 summary —— 旧 summary 一删，它们就是过期内容：
    # 重新转录/重新生成后如果留着，用户切到那个语言看到的还是**上一版**的翻译。
    # 这里按会议整体清（而不是遍历 tabs），因为接下来 tabs 本身会被重建，
    # 依赖 tabs 会漏掉"即将消失的那个 section"的译文。
    try:
        removed = delete_all_translation_mds(meeting_id)
        if removed:
            logger.info(
                "[PIPELINE] Deleted %d stale translation file(s) for meeting %s",
                removed, meeting_id,
            )
    except Exception:
        logger.warning(
            "translation cleanup skipped for %s", meeting_id, exc_info=True
        )
    try:
        from src.meeting.transcript_index import purge_meeting_transcripts

        purge_meeting_transcripts(meeting_id)
    except Exception:
        logger.warning("transcript index purge skipped for %s", meeting_id, exc_info=True)
    logger.info("Deleted pipeline data for meeting %s", meeting_id)


def discard_recording(meeting_id: str) -> Meeting:
    """Delete all recording-related data and reset the meeting to 'created'.

    Removes audio file, transcript, pipeline data (sentences, section mds),
    summary, notes, and allocated-collection metadata.  The meeting record
    itself is kept so the user can start a new recording in the same slot.
    """
    meeting = get_meeting(meeting_id)
    if meeting is None:
        raise FileNotFoundError(f"Meeting {meeting_id} not found")

    if meeting.speaker_people:
        try:
            from src.speakers.service import unenroll_meeting

            unenroll_meeting(meeting_id)
        except Exception:
            logger.warning("voiceprint unenroll skipped for %s", meeting_id, exc_info=True)

    # 1. Delete audio file
    if meeting.audio_path:
        p = Path(meeting.audio_path)
        if p.exists():
            p.unlink()
            logger.info("[DISCARD] Deleted audio %s for meeting %s", p, meeting_id)
    for extra in (RECORDING_PCM_NAME, RECORDING_WAV_NAME):
        p = _meeting_dir(meeting_id) / extra
        if p.exists():
            p.unlink()
            logger.info("[DISCARD] Deleted %s for meeting %s", p, meeting_id)

    # 2. Delete transcript file
    if meeting.transcript_path:
        p = Path(meeting.transcript_path)
        if p.exists():
            p.unlink()
            logger.info("[DISCARD] Deleted transcript %s for meeting %s", p, meeting_id)

    # 2b. Delete live summary artifact from the discarded recording
    ls_path = live_summary_path(meeting_id)
    if ls_path.exists():
        ls_path.unlink()
        logger.info("[DISCARD] Deleted live summary for meeting %s", meeting_id)

    # 3. Delete pipeline data (sentences, section mds)
    try:
        delete_pipeline_data(meeting_id)
    except Exception as exc:
        logger.warning("[DISCARD] Failed to delete pipeline data: %s", exc)

    # 4. Delete notes file
    if meeting.notes_path:
        p = Path(meeting.notes_path)
        if p.exists():
            p.unlink()
            logger.info("[DISCARD] Deleted notes %s for meeting %s", p, meeting_id)

    # 5. Reset meeting fields
    updated = update_meeting(
        meeting_id,
        status=MeetingStatus.created,
        audio_path=None,
        transcript_path=None,
        notes_path=None,
        summary=None,
        detail=None,
        transcription_error=None,
        processing_state=ProcessingState.idle.value,
        summary_gen_state=GenerationState.idle.value,
        blueprint_gen_state=GenerationState.idle.value,
        blueprint=None,
        blueprint_taxonomy=None,
        tabs=None,
        allocated_collections=[],
        allocated_file_ids=[],
        speaker_names=None,
        speaker_people=None,
        speaker_matches=None,
        speaker_slots=None,
        speaker_slots_status=None,
        speaker_slots_ms=None,
        transcript_index_status="",
        transcript_index_error="",
    )
    logger.info("[DISCARD] Meeting %s fully discarded, reset to 'created'", meeting_id)
    return updated
