from __future__ import annotations

import asyncio
import io
import itertools
import json
import logging
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

from typing import Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from src.meeting import store
from src.meeting import live_summary as live_summary_mod
from src.meeting.models import MeetingMode, MeetingStatus, ProcessingState, GenerationState, TranscriptSegment, TranscriptionResult
from src.meeting.schemas import SummaryExportRequest
from src.meeting.service import meeting_service
from src.tasks.task_manager import task_manager

# ── 实时转写韧性（2026-09-16）────────────────────────────────────
# 静默看门狗：DashScope SDK 有 23s 静默计时器（Recognition.SILENCE_TIMEOUT_S），
# 只在有帧被取走时重置。用户暂停录制 / 界面卡住时前端停发 PCM → 会话被静默杀死，
# 恢复后第一帧抛错 → 整条 WS 拆掉重连 → 头几句丢失。这里在客户端空闲时喂静音
# 帧保活（静音产生不了文字，字幕照样「冻住」，但会话不死）。
REALTIME_KEEPALIVE_TICK_S = 5.0
REALTIME_KEEPALIVE_IDLE_S = 15.0
# 每秒 16kHz × 2 字节 = 32KB；喂 500ms 静音与前端分块粒度一致
REALTIME_SILENCE_FRAME = b"\x00" * 16000
# 探针：周期性把计数器推给前端，用于区分「云端没返/返了没显示/显示了又没了」
REALTIME_STATS_INTERVAL_S = 10.0

# ── 录制席仲裁（2026-09-19 并发审查）────────────────────────────────
# 一场会议同时只允许**一个**活跃录制 WS。两台设备同时对同一场会议开录：
# 两路 PCM 交错 append 进同一份文件，finalize 拼出损坏的 WAV，且任何服务端
# 状态都分不清「谁在录」。判别身份用前端 sessionStorage 里的稳定 client_id
# （?client_id=）：同 id = 同一台设备刷新/重连，视为**接管**放行；不同 id =
# 另一台设备，拒绝。不带 client_id 的旧客户端按独立匿名连接处理（严格拒二）。
_active_recorders: dict[str, str] = {}
_recorders_guard = threading.Lock()

# 全进程实时转写 WS 总上限（2026-09-19 P2）：每个连接占一路本地模型推理/
# 云端计费会话，失控客户端无限开连会把盒子拖垮。超出直接拒绝。
MAX_ACTIVE_REALTIME_SESSIONS = 8

logger = logging.getLogger("meeting")

# RBAC：会议域全部端点都需要 meeting ACL 的 read 档（visitor 无权限）。
# 路由器级依赖：一处覆盖本模块所有端点，避免逐个挂而漏掉一个。
from src.api.routes.identity import require_domain as _require_domain

router = APIRouter(dependencies=[Depends(_require_domain("meeting"))])


def require_meeting_operate(meeting_id: str, meeting=None) -> None:
    """会议写操作的统一守卫（含创建者例外）。

    **必须在线程/流式任务诞生之前调用**——后台线程读不到请求身份（``contextvars``
    不跨线程），会以 owner 静默放行，所以请求期是唯一能把关的地方
    （见 ``src/identity/actor.py`` 的传播边界说明）。

    所有"生成/翻译/提取/重生成"这类会写会议内容的入口都走它。
    """
    from src.identity import authorize, get_actor

    m = meeting if meeting is not None else store.get_meeting(meeting_id)
    payload = m.model_dump() if hasattr(m, "model_dump") else m
    authorize(get_actor(), "meeting.update", {"meeting": payload})


def _attach_meeting_permissions(data: list[dict]) -> None:
    """给会议 payload 就地加上 ``permissions: {read, operate}``（UI 化妆用）。

    前端靠它决定删除 / 归档 / 改名按钮画不画——**没有操作权限就不该看到按不动的
    按钮**（用户 2026-09-17 要求："没有会议删除权限的话，删除按钮也要看不到"）。
    判定走 ``visibility.meeting_permissions``（内部与执行路径同一个 ``can`` 调用），
    所以按钮的可见性与后端放行与否是同一个答案。

    取不到身份（测试直挂 router / 内部调用）时不加字段：前端按"无权限"处理。
    """
    from src.identity import get_actor
    from src.identity import visibility as _visibility

    actor = get_actor()
    if actor is None:
        return
    for d in data:
        mid = d.get("id")
        try:
            d["permissions"] = _visibility.meeting_permissions(actor, mid)
        except Exception:
            # fail-closed：拿不准按"不能写"，少画按钮而不是画出会失败的按钮。
            # 键名必须与正常路径一致（`write`）——否则前端读不到该键，
            # 权限字段形状就分成了两种。
            logger.exception("[Meetings] permission probe failed for %s", mid)
            d["permissions"] = {"read": False, "write": False}
        # 「这台机器上**正在**录制」——供旁观者显示"录制中"并禁止进入。
        # status=="recording" 只说明"开始录过"，不说明现在还在录（见 store.recording_is_live）。
        try:
            d["recording_live"] = store.recording_is_live(mid) if mid else False
        except Exception:
            logger.exception("[Meetings] recording liveness probe failed for %s", mid)
            d["recording_live"] = False


def _emit_meeting_group(event_type: str, group_id: str) -> None:
    """Broadcast a meeting-group change so peers refresh their cached list."""
    from src.events.emit import emit

    emit(event_type, scope={"group_id": group_id})


def _emit_meeting(event_type: str, meeting_id: str, **payload) -> None:
    """Broadcast a meeting change so peers refresh their cached view.

    Multi-device sync (see development_doc/2026-09-16-multi-device-state-sync-design.md §5).
    For routes that only *start* async work the event means "work started"; the
    peer then follows up with a scoped poll until the meeting is quiet (§11).
    """
    from src.events.emit import emit

    emit(event_type, scope={"meeting_id": meeting_id}, payload=payload or None)


def _serialize_meeting(meeting, *, include_transcript: bool = False) -> dict:
    """JSON for a meeting, including notes_content (GET-parity).

    PUT/upload/summarize responses used to return ``model_dump()`` only. The
    client then ``setMeeting(m)`` and dropped live notes until a full refresh.
    """
    data = meeting.model_dump(mode="json")
    data.pop("speaker_slots", None)
    # RBAC：把"这个会议能不能操作"一并带回（GET 详情与所有写操作的响应都走这里），
    # 前端据此决定删除/归档/改名按钮画不画。缺失时前端按"无权限"处理。
    _attach_meeting_permissions([data])
    try:
        from src.speakers.service import rebuild_speaker_names

        data["speaker_names"] = rebuild_speaker_names(
            meeting.speaker_people, keep=meeting.speaker_names
        ) or None
    except Exception:
        logger.debug("speaker name rebuild skipped for %s", meeting.id, exc_info=True)
    notes = store.get_notes(meeting.id)
    if notes is not None:
        data["notes_content"] = notes
    if include_transcript:
        transcript = store.get_transcript(meeting.id)
        if transcript is not None:
            data["transcript"] = transcript.model_dump(mode="json")
    return data


_AUDIO_MIME_TYPES = {
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "aac": "audio/aac",
    "flac": "audio/flac",
}


# ── Meeting CRUD ─────────────────────────────────────────────


def _active_transcription_supports_hot_words() -> bool:
    """True if file or realtime active adapter declares supports_hot_words."""
    try:
        from src.config import get_config
        from src.meeting.transcription import resolve_file_adapter, resolve_realtime_adapter
        from src.meeting.transcription.registry import (
            cls_supports_hot_words,
            file_transcription_registry,
            realtime_transcription_registry,
        )

        config = get_config()
        file_cfg = config.transcription.active_file_provider
        if file_cfg is None:
            file_cfg = config.transcription.get_local_file_provider()
        rt_cfg = config.transcription.active_realtime_provider
        if rt_cfg is None:
            rt_cfg = config.transcription.get_local_realtime_provider()

        def _supports(provider_cfg, registry, resolve_adapter) -> bool:
            if not provider_cfg:
                return False
            adapter_name = resolve_adapter(provider_cfg.adapter or "")
            entry = registry.get(adapter_name)
            if not entry:
                return False
            return cls_supports_hot_words(entry.cls)

        return _supports(
            file_cfg, file_transcription_registry, resolve_file_adapter
        ) or _supports(
            rt_cfg, realtime_transcription_registry, resolve_realtime_adapter
        )
    except Exception as exc:
        logger.debug("Hot-words support probe failed: %s", exc)
        return False


def _serialize_group(group) -> dict:
    data = group.model_dump(mode="json")
    # 分组是**跨会议**的容器：组里可能混着别人的会议，所以不给逐会议判定，
    # 只按"角色整体有没有 meeting manage 档"这个保守口径决定删/归档/改名按钮。
    # 创建者例外在这里**不**生效——删整组会牵连别人的会议。
    from src.identity import get_actor
    from src.identity import visibility as _visibility

    actor = get_actor()
    if actor is not None:
        try:
            data["can_operate"] = _visibility.can_manage_meetings(actor)
        except Exception:
            logger.exception("[Meetings] group manage probe failed")
            data["can_operate"] = False
    return data


def _ensure_transcript_index(meeting_id: str) -> None:
    """Start transcript index if the meeting has speech and is not already indexed."""
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        return
    if (meeting.transcript_index_status or "") in ("ready", "building"):
        return
    sentences = store.get_sentences(meeting_id)
    transcript = store.get_transcript(meeting_id)
    if not sentences and not transcript:
        return
    store.update_meeting(
        meeting_id, transcript_index_status="building", transcript_index_error=""
    )
    task_manager.create_task(
        filename=f"tx-index:{meeting_id}",
        task_type="meeting_transcript_index",
        meeting_id=meeting_id,
    )


def _delete_group_session(group_id: str) -> None:
    try:
        from src.services import services

        store = getattr(services, "session_store", None)
        if store is not None:
            store.delete_session(f"group_{group_id}")
    except Exception:
        logger.debug("group session delete skipped %s", group_id, exc_info=True)


@router.get("/meeting-groups")
async def list_meeting_groups():
    from src.meeting.group_store import list_groups

    return [_serialize_group(g) for g in list_groups()]


@router.post("/meeting-groups")
async def create_meeting_group(body: dict = Body()):
    from src.meeting.group_store import create_group

    meeting_id = (body.get("meeting_id") or "").strip()
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    group = create_group(
        title=(body.get("title") or "").strip(),
        meeting_id=meeting_id,
        meeting_title=meeting.title or "",
    )
    _ensure_transcript_index(meeting_id)
    _emit_meeting_group("meeting_group.created", group.id)
    return _serialize_group(group)


@router.get("/meeting-groups/{group_id}")
async def get_meeting_group(group_id: str):
    from src.meeting.group_store import get_group

    group = get_group(group_id)
    if group is None:
        raise HTTPException(404, "Group not found")
    return _serialize_group(group)


@router.delete("/meeting-groups/{group_id}")
async def delete_meeting_group(group_id: str, delete_meetings: bool = True):
    """Delete a group. By default the member meetings are deleted too."""
    from src.meeting.group_store import delete_group, get_group

    group = get_group(group_id)
    if group is None:
        raise HTTPException(404, "Group not found")
    # 门在级联删除**之前**：删一半再发现没权限是最糟的破坏顺序。
    _require_group_manage("meeting_group.delete")
    if delete_meetings:
        # 级联同样不可吞掉正在录制的会议（闸门与单删一致，删前整组预检）。
        live_members = [
            member.meeting_id
            for member in list(group.members)
            if store.get_meeting(member.meeting_id) is not None
            and store.recording_is_live(member.meeting_id)
        ]
        if live_members:
            raise HTTPException(
                409,
                "Group contains a meeting that is currently recording"
                " — stop it before deleting",
            )
        for member in list(group.members):
            if store.get_meeting(member.meeting_id) is not None:
                store.delete_meeting(member.meeting_id)
    _delete_group_session(group_id)
    delete_group(group_id)
    _emit_meeting_group("meeting_group.deleted", group_id)
    return {"message": "Group deleted"}


@router.patch("/meeting-groups/{group_id}")
async def rename_meeting_group(group_id: str, body: dict = Body()):
    from src.meeting.group_store import rename_group

    _require_group_manage("meeting_group.update")
    try:
        group = rename_group(group_id, (body.get("title") or "").strip())
    except FileNotFoundError:
        raise HTTPException(404, "Group not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _emit_meeting_group("meeting_group.updated", group_id)
    return _serialize_group(group)


@router.post("/meeting-groups/{group_id}/archive")
async def archive_meeting_group(group_id: str):
    return await _set_group_archived(group_id, True)


@router.post("/meeting-groups/{group_id}/unarchive")
async def unarchive_meeting_group(group_id: str):
    return await _set_group_archived(group_id, False)


async def _set_group_archived(group_id: str, archived: bool):
    from src.meeting.group_store import get_group, set_group_archived

    if get_group(group_id) is None:
        raise HTTPException(404, "Group not found")
    _require_group_manage("meeting_group.update")
    group = set_group_archived(group_id, archived)
    for member in list(group.members):
        if store.get_meeting(member.meeting_id) is not None:
            store.update_meeting(member.meeting_id, archived=archived)
    _emit_meeting_group("meeting_group.updated", group_id)
    return _serialize_group(group)


@router.post("/meeting-groups/{group_id}/members")
async def add_meeting_group_member(group_id: str, body: dict = Body()):
    from src.meeting.group_store import add_member, get_group

    if get_group(group_id) is None:
        raise HTTPException(404, "Group not found")
    _require_group_manage("meeting_group.update")
    meeting_id = (body.get("meeting_id") or "").strip()
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    try:
        group = add_member(group_id, meeting_id)
    except FileNotFoundError:
        raise HTTPException(404, "Group not found")
    _ensure_transcript_index(meeting_id)
    _emit_meeting_group("meeting_group.updated", group_id)
    return _serialize_group(group)


@router.delete("/meeting-groups/{group_id}/members/{meeting_id}")
async def remove_meeting_group_member(group_id: str, meeting_id: str):
    from src.meeting.group_store import get_group, remove_member

    if get_group(group_id) is None:
        raise HTTPException(404, "Group not found")
    _require_group_manage("meeting_group.update")
    try:
        group = remove_member(group_id, meeting_id)
    except FileNotFoundError:
        raise HTTPException(404, "Group not found")
    _emit_meeting_group("meeting_group.updated", group_id)
    return _serialize_group(group)


@router.get("/meetings/{meeting_id}/groups")
async def list_groups_for_meeting(meeting_id: str):
    from src.meeting.group_store import groups_for_meeting

    if store.get_meeting(meeting_id) is None:
        raise HTTPException(404, "Meeting not found")
    return [_serialize_group(g) for g in groups_for_meeting(meeting_id)]


@router.post("/meetings")
async def create_meeting(body: dict = Body()):
    title = body.get("title") or datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = body.get("mode")  # "upload" or "record"
    meeting_mode = MeetingMode(mode) if mode else None
    # 创建者界面语言（实时纪要语言依据）。**白名单**（与前端 i18n locale 同集）：
    # 白名单外的值一律丢弃视为未声明——这个字符串会被拼进 live-summary prompt，
    # 不能让任意请求把任意文本塞进去。None → 实时纪要回落「跟随转写语言」。
    ui_lang = body.get("ui_lang")
    if ui_lang not in ("en", "zh-CN"):
        ui_lang = None
    meeting = store.create_meeting(title=title, mode=meeting_mode, ui_lang=ui_lang)

    # Copy pinned libraries onto the new meeting (user can turn them off)
    try:
        if _active_transcription_supports_hot_words():
            from src.hot_words.store import apply_pinned_libraries

            meeting = apply_pinned_libraries(meeting.id)
            logger.info(
                "[CREATE] Applied pinned hot-words libraries %s to meeting %s",
                meeting.hot_words_library_ids,
                meeting.id,
            )
    except Exception as exc:
        logger.warning("[CREATE] Failed to apply pinned hot-words: %s", exc)

    logger.info("[CREATE] Meeting '%s' id=%s mode=%s", title, meeting.id, meeting_mode)
    _emit_meeting("meeting.created", meeting.id, title=meeting.title)
    return meeting.model_dump()


@router.get("/meetings")
async def list_meetings(
    lean: bool = Query(False),
    limit: int = Query(100),
    status: Optional[str] = Query(None),
    search: str = Query(""),
    include_archived: bool = Query(False),
):
    """``lean=true``：返回 **Agent 精简形态**（`src/meeting/lean.py`，与 MCP
    ``list_meetings`` 工具同一份投影）——每项只留轻字段，带
    ``{meetings, total, returned}`` 信封，支持与工具同语义的
    status / search / include_archived / limit 过滤。

    为什么放 REST 而不是让调用方自己裁：精简形状是**接口契约**（哪些字段
    该进 Agent 上下文），钉在 REST 上才能被测试锁住；否则 REST 改字段时
    没人会记得同步调用方的裁剪逻辑（跨盒子转发正是这个调用方）。
    不带 lean 时行为与从前完全一致（网页继续拿全量）。
    """
    meetings = store.list_meetings()
    # 过滤在 lean / 非 lean 两条路**共用**（批次 3 审查 #3：参数既然声明了，
    # 非 lean 就不能是静默 no-op——那会教 API 消费者一个假能力）。
    # 语义与 MCP list_meetings 工具同源（见 lean.py 的投影）。
    if not include_archived:
        meetings = [m for m in meetings if getattr(m, "archived", False) is not True]
    if status:
        meetings = [m for m in meetings if m.status.value == status]
    if search:
        q = search.lower()
        meetings = [m for m in meetings if q in (m.title or "").lower()]
    if lean:
        from src.meeting.lean import meeting_light_dict

        total = len(meetings)
        items = [meeting_light_dict(m) for m in meetings[:limit]]
        return {"meetings": items, "total": total, "returned": len(items)}

    logger.debug("[LIST] Returning %d meetings", len(meetings))
    # speaker_slots are per-meeting diarization embeddings consumed only
    # server-side (speakers service reads them from the store); no HTTP
    # client reads the vectors — only _status/_ms/speaker_matches. Shipping
    # them made this list ~260KB and jammed the client main thread on every
    # Meeting-view activation.
    data = [m.model_dump() for m in meetings]
    # RBAC：每会议带上"能不能操作"，前端靠它决定删除/归档/改名按钮画不画。
    # 只是化妆信号——真正的墙是 store.delete_meeting 等处的 authorize。
    _attach_meeting_permissions(data)
    for d in data:
        d.pop("speaker_slots", None)
    return data


@router.get("/meetings/{meeting_id}")
async def get_meeting(meeting_id: str, lean: bool = Query(False)):
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[GET] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    if lean:
        # Agent 精简形态（与 MCP get_meeting 工具同一份投影）——**不内联
        # transcript**，也**不跑 speaker rematch**（那是网页详情页的写副作用：
        # 可能改 speaker_names 并抬 updated_at；本地工具路径同样不跑——
        # 审查 #1：名义只读的转发不能顺手改远端状态）。非 lean 路径维持
        # 既有行为（网页继续触发 rematch）。
        from src.meeting.lean import meeting_meta_dict

        return meeting_meta_dict(meeting)
    if meeting.speaker_slots:
        try:
            from src.speakers.service import apply_matches_from_slots

            if apply_matches_from_slots(meeting_id):
                meeting = store.get_meeting(meeting_id) or meeting
        except Exception:
            logger.debug("speaker rematch skipped for %s", meeting_id, exc_info=True)
    # Trust persisted needs_reingest / ingested_content_hash (set on save & allocate).
    # Do NOT re-hash every section MD here — that made GET/polling very slow.
    data = _serialize_meeting(meeting, include_transcript=True)
    logger.debug(
        "[GET] Meeting %s status=%s has_notes=%s has_transcript=%s audio_path=%s",
        meeting_id, meeting.status.value, "notes_content" in data,
        "transcript" in data, meeting.audio_path,
    )
    return data


@router.delete("/meetings/{meeting_id}")
async def delete_meeting(meeting_id: str):
    logger.info("[DELETE] Meeting %s", meeting_id)
    # 正在录制的会议是「活的」：删掉它，采集端就成了没人能叫停的孤儿流
    # （停止按钮随卡片一起消失）。闸门用 liveness（此刻真在录）；刷新
    # 残留的 status='recording' 不拦——那正是需要删除来清理的场景。
    if store.recording_is_live(meeting_id):
        raise HTTPException(
            409, "Meeting is currently recording — stop the recording before deleting"
        )
    meeting = store.get_meeting(meeting_id)
    cleaned_allocations: list = []
    if meeting:
        cleaned_allocations = meeting_service.cleanup_meeting_allocations(meeting) or []
    live_summary_mod.drop_engine(meeting_id)
    deleted = store.delete_meeting(meeting_id)
    if not deleted:
        logger.warning("[DELETE] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    logger.info("[DELETE] Meeting %s deleted successfully", meeting_id)
    _emit_meeting("meeting.deleted", meeting_id)
    # cleaned_allocations：MCP 工具契约（delete_meeting）要回报清理了多少会议侧
    # 文件分配。加字段，向后兼容（浏览器不读它）。
    return {"message": "Meeting deleted", "cleaned_allocations": cleaned_allocations}



@router.post("/meetings/{meeting_id}/discard")
async def discard_meeting_recording(meeting_id: str):
    """Discard all recording data: stop recording, delete audio + transcript, reset to 'created'."""
    logger.info("[DISCARD] Meeting %s", meeting_id)
    live_summary_mod.drop_engine(meeting_id)
    try:
        meeting = store.discard_recording(meeting_id)
    except FileNotFoundError as exc:
        logger.warning("[DISCARD] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, str(exc))
    logger.info("[DISCARD] Meeting %s discarded successfully", meeting_id)
    _emit_meeting("meeting.updated", meeting_id)
    return _serialize_meeting(meeting)


@router.put("/meetings/{meeting_id}")
async def update_meeting(meeting_id: str, body: dict = Body()):
    # 不存在的会议 → 404（此前 store.update_meeting 抛 FileNotFoundError → 500，
    # 浏览器与跨盒子转发都只能看到 "Internal Server Error"，分不清该怎么办）。
    if not store.get_meeting(meeting_id):
        logger.warning("[UPDATE] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    logger.info("[UPDATE] Meeting %s fields=%s", meeting_id, list(body.keys()))
    allowed_fields = {
        "title",
        "status",
        "mode",
        "speaker_names",
        "hot_words_library_id",
        "hot_words_library_ids",
        "blueprint",
        "tabs",
        "expected_people",
    }
    fields = {k: v for k, v in body.items() if k in allowed_fields}
    # Handle notes separately -- save to file
    if "notes" in body:
        logger.info("[UPDATE] Saving notes for %s (%d chars)", meeting_id, len(body["notes"]))
        store.save_notes(meeting_id, body["notes"])
    if not fields and "notes" not in body:
        logger.warning("[UPDATE] No valid fields in request for %s", meeting_id)
        raise HTTPException(400, "No valid fields to update")
    if fields:
        meeting = store.update_meeting(meeting_id, **fields)
    else:
        meeting = store.get_meeting(meeting_id)
    logger.info("[UPDATE] Meeting %s updated, status=%s", meeting_id, meeting.status.value)
    _emit_meeting("meeting.updated", meeting_id)
    return _serialize_meeting(meeting)


@router.post("/meetings/{meeting_id}/archive")
async def archive_meeting(meeting_id: str):
    return _set_meeting_archived(meeting_id, True)


@router.post("/meetings/{meeting_id}/unarchive")
async def unarchive_meeting(meeting_id: str):
    return _set_meeting_archived(meeting_id, False)


def _require_group_manage(action: str) -> None:
    """会议组写操作的门（``meeting_group.update`` / ``meeting_group.delete`` → manage 档）。

    **为什么组不走创建者例外**：组是**跨会议**的容器，里面可能混着别人的会议；
    删整组会牵连那些会议。所以只认角色整体的 ``manage`` 档，不给逐会议所有权开口子
    （与 ``visibility.can_manage_meetings`` 同一个保守口径，UI 用的是同一个判定）。

    **必须在动手之前调用**：`delete_meeting` 会级联删成员会议，先删一半再发现没权限
    是最糟的"破坏先于授权"。所以门在循环**之前**。
    """
    from src.identity import authorize, get_actor

    authorize(get_actor(), action, {})


def _set_meeting_archived(meeting_id: str, archived: bool):
    if store.get_meeting(meeting_id) is None:
        raise HTTPException(404, "Meeting not found")
    meeting = store.update_meeting(meeting_id, archived=archived)
    if not archived:
        # Pulling one member out of the archive also un-archives any archived
        # group that contains it (the group card returns to the list); the
        # other members keep their own archived state.
        from src.meeting.group_store import groups_for_meeting, set_group_archived

        for group in groups_for_meeting(meeting_id):
            if group.archived:
                set_group_archived(group.id, False)
    _emit_meeting("meeting.archived" if archived else "meeting.unarchived", meeting_id)
    return _serialize_meeting(meeting)


@router.get("/meetings/{meeting_id}/brief")
async def get_meeting_brief(meeting_id: str):
    from src.meeting.prepare import brief_state

    try:
        return brief_state(meeting_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/meetings/{meeting_id}/brief/generate")
async def generate_meeting_brief(meeting_id: str, body: dict = Body(default=None)):
    from src.meeting.prepare import start_brief_generate

    require_meeting_operate(meeting_id)
    locale = ((body or {}).get("locale") or "zh-CN").strip() or "zh-CN"
    try:
        return start_brief_generate(meeting_id, locale=locale)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ── File Uploads ──────────────────────────────────────────────


@router.post("/meetings/{meeting_id}/upload-audio")
def upload_audio(meeting_id: str, file: UploadFile = File(...)):
    # sync（threadpool）+ 流式：音频可到几百 MB，曾经的 async + read() 全量进
    # RAM 且阻塞事件循环，上传期间所有设备的请求一起卡。
    logger.info(
        "[UPLOAD-AUDIO] Meeting %s filename=%s content_type=%s size=%s",
        meeting_id, file.filename, file.content_type,
        file.size if hasattr(file, "size") else "unknown",
    )
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[UPLOAD-AUDIO] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    ext = (
        file.filename.rsplit(".", 1)[-1]
        if file.filename and "." in file.filename
        else "webm"
    )
    path = store.save_audio(meeting_id, file.file, ext, original_filename=file.filename)
    logger.info("[UPLOAD-AUDIO] Saved to %s", path)
    updated = store.update_meeting(
        meeting_id,
        mode=MeetingMode.upload,
        # Only reset to created if there's no transcript yet (i.e. pure upload flow).
        # When coming from record mode, save_realtime_transcript already set status
        # to completed — keep it so the frontend can immediately call /transcribe.
        **({} if meeting.status == MeetingStatus.completed else {"status": MeetingStatus.created}),
    )
    logger.info("[UPLOAD-AUDIO] Meeting %s updated: status=%s audio_path=%s", meeting_id, updated.status.value, updated.audio_path)
    _emit_meeting("meeting.updated", meeting_id)
    return _serialize_meeting(updated)


@router.post("/meetings/{meeting_id}/recording-pcm")
async def ingest_recording_pcm(meeting_id: str, request: Request):
    """Append live 16 kHz s16le PCM so a crash still leaves recoverable audio."""
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    chunk = await request.body()
    store.append_recording_pcm(meeting_id, chunk)
    return {"ok": True, "bytes": len(chunk)}


@router.post("/meetings/{meeting_id}/finalize-recording")
def finalize_recording(meeting_id: str, recovery: bool = False):
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    path = store.finalize_recording_pcm(meeting_id)
    # 只要这个会议**原本处于"录制中"**，收尾后就把状态放回 created（audio-ready 形态）。
    #
    # 原先只在 `recovery=True` 时重置 —— 于是**正常停止录制的那条路会把 status 留在
    # "recording"**：列表一直显示"录制中"，配合"录制中不可进入"的锁，这个会议对所有人
    # （含刚停下来的创建者）就一直点不进去。用户报的"所有人都锁在外面了"就是它。
    # 正常停止与恢复两条路的目标形态本来就一样（audio_path + created），没理由分开。
    if meeting.status == MeetingStatus.recording:
        store.update_meeting(meeting_id, status=MeetingStatus.created)
    updated = store.get_meeting(meeting_id)
    logger.info(
        "[FINALIZE-RECORDING] Meeting %s audio_path=%s recovery=%s status=%s",
        meeting_id, path, recovery, updated.status.value,
    )
    _emit_meeting("meeting.updated", meeting_id)
    return _serialize_meeting(updated)


@router.get("/meetings/{meeting_id}/audio")
async def serve_audio(meeting_id: str, audio_token: str | None = None):
    """Serve the audio file for playback and for external transcription services.

    ``audio_token``（**不是** ``token``）是给外部服务（如 DashScope）用的短时音频
    令牌，存在时先校验，防止该 URL 在令牌过期后仍可访问。

    参数名很重要：查询串里的 ``?token=`` 属于**配对钥匙**通道——门卫用它给
    `<audio>` / `<img>` / EventSource / 下载链接这类带不了请求头的请求鉴权。
    两者曾同名，导致配对钥匙被当作音频令牌误验 → 401 → 连远端盒子时会议音频
    静默播不出来（2026-09-15 实测并修复）。
    """
    if audio_token is not None:
        from src.meeting.security import verify_audio_token
        if not verify_audio_token(meeting_id, audio_token):
            logger.warning("[AUDIO] Invalid or expired audio token for meeting %s", meeting_id)
            raise HTTPException(401, "Invalid or expired token")
    meeting = store.get_meeting(meeting_id)
    if not meeting or not meeting.audio_path:
        raise HTTPException(400, "No audio file")
    from src.speakers.service import resolve_meeting_audio_path

    resolved = resolve_meeting_audio_path(meeting.audio_path)
    audio_path = resolved if resolved is not None else Path(meeting.audio_path)
    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found on disk")
    ext = audio_path.suffix.lstrip(".").lower()
    media_type = _AUDIO_MIME_TYPES.get(ext, "application/octet-stream")
    logger.debug("[AUDIO] Serving %s for meeting %s (mime=%s)", audio_path, meeting_id, media_type)
    return FileResponse(
        path=str(audio_path),
        media_type=media_type,
        filename=audio_path.name,
    )


@router.get("/meetings/{meeting_id}/transcript")
async def get_transcript(meeting_id: str):
    """Return transcript segments for a meeting.

    Prefers sentences.json (with section_tags) when available;
    falls back to transcript.json otherwise.
    """
    sentences = store.get_sentences(meeting_id)
    if sentences:
        segments = [
            {
                "start": s.get("start_time", 0),
                "end": s.get("end_time", 0),
                "text": s.get("original_text", ""),
                "speaker_id": s.get("speaker", ""),
                "sentence_id": s.get("sentence_id", ""),
                "section_tags": s.get("section_tags", []),
            }
            for s in sentences
        ]
        return {"segments": segments}

    # Fallback: transcript.json without sentence metadata
    transcript = store.get_transcript(meeting_id)
    if not transcript:
        return {"segments": []}

    segments = [
        {**seg.model_dump(), "sentence_id": "", "section_tags": []}
        for seg in transcript.segments
    ]
    return {"segments": segments}



@router.get("/transcription/active-provider-info")
async def get_active_provider_info():
    """Return hot-words support and supported language hints for active providers.

    Uses the registry directly to avoid creating actual provider instances,
    which would trigger ML model downloads for local providers.
    """
    from src.config import get_config
    from src.meeting.transcription import resolve_file_adapter, resolve_realtime_adapter
    from src.meeting.transcription.registry import (
        cls_supports_hot_words,
        file_transcription_registry,
        realtime_transcription_registry,
    )

    config = get_config()

    def _info(provider_cfg, registry, *, resolve_adapter):
        from src.meeting.transcription.base import language_hint_limit

        empty = {
            "supports_hot_words": False,
            "supported_language_hints": [],
            "max_language_hints": 1,
            "adapter": None,
            "id": None,
            "name": None,
            "display_name": None,
            "model": None,
        }
        if not provider_cfg:
            return empty
        adapter_name = resolve_adapter(provider_cfg.adapter or "")
        model = getattr(provider_cfg, "model", None)
        meta = {
            "adapter": adapter_name or provider_cfg.adapter or None,
            "id": provider_cfg.id or None,
            "name": provider_cfg.name or None,
            "display_name": None,
            "model": model,
        }
        # If provider has custom language_hints_config, use it (openai_compatible etc.)
        custom = getattr(provider_cfg, "language_hints_config", None)
        if custom:
            entry = registry.get(adapter_name)
            if entry:
                supports = cls_supports_hot_words(entry.cls)
                meta["display_name"] = entry.display_name or entry.name
                max_hints = language_hint_limit(entry.cls, model)
            else:
                supports = False
                max_hints = 1
            # Build hint list from custom config, ensuring "auto" is always first
            hints = [{"code": h.get("code", ""), "label": h.get("label", "")} for h in custom]
            if not any(h["code"] == "auto" for h in hints):
                hints.insert(0, {"code": "auto", "label": "Auto"})
            return {
                "supports_hot_words": supports,
                "supported_language_hints": hints,
                "max_language_hints": max_hints,
                **meta,
            }
        entry = registry.get(adapter_name)
        if not entry:
            return {**empty, **meta}
        adapter_cls = entry.cls
        supports = cls_supports_hot_words(adapter_cls)
        meta["display_name"] = entry.display_name or entry.name
        return {
            "supports_hot_words": supports,
            "supported_language_hints": list(
                getattr(adapter_cls, "SUPPORTED_LANGUAGE_HINTS", [])
            ),
            "max_language_hints": language_hint_limit(adapter_cls, model),
            **meta,
        }

    # Prefer configured active providers; fall back to built-in local ONNX factories
    # so the Meeting language UI still has options before user sets Default.
    file_cfg = config.transcription.active_file_provider
    if file_cfg is None:
        file_cfg = config.transcription.get_local_file_provider()
    rt_cfg = config.transcription.active_realtime_provider
    if rt_cfg is None:
        rt_cfg = config.transcription.get_local_realtime_provider()

    realtime_info = _info(
        rt_cfg,
        realtime_transcription_registry,
        resolve_adapter=resolve_realtime_adapter,
    )
    # Live bilingual captions: available only when a LiveTranslate provider
    # is explicitly configured in Settings → Live translation.
    supports_translation = any(
        resolve_realtime_adapter(p.adapter or "") == "dashscope_livetranslate_realtime"
        for p in (config.transcription.realtime_providers or [])
    )
    realtime_info["supports_realtime_translation"] = supports_translation

    return {
        "file": _info(
            file_cfg,
            file_transcription_registry,
            resolve_adapter=resolve_file_adapter,
        ),
        "realtime": realtime_info,
    }


@router.post("/meetings/{meeting_id}/save-transcript")
async def save_realtime_transcript(meeting_id: str, body: dict = Body()):
    """Persist a transcript produced by the realtime (WebSocket) flow.

    The realtime provider streams segments to the browser via WebSocket, but
    the backend never sees them as finished data — the WebSocket handler just
    forwards events. After the user stops recording, the browser POSTs the
    collected segments here so the meeting gets a saved transcript, a
    ``transcript_path``, and a status of ``completed``. Subsequent operations
    (Summarize, Allocate) then work the same as for file-based meetings.

    With ``body["partial"]=true`` the transcript is checkpointed without the
    status flip or pipeline side effects, so a page refresh mid-recording
    still leaves the closed segments recoverable on the server.
    """
    logger.info("[SAVE-TRANSCRIPT] Meeting %s (realtime path)", meeting_id)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[SAVE-TRANSCRIPT] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    partial = bool(body.get("partial"))
    raw_segments = body.get("segments") or []
    if not isinstance(raw_segments, list):
        raise HTTPException(400, "segments must be a list")
    try:
        segments = [TranscriptSegment(**s) for s in raw_segments]
    except Exception as exc:
        logger.warning("[SAVE-TRANSCRIPT] Invalid segment payload for %s: %s", meeting_id, exc)
        raise HTTPException(400, f"Invalid segment payload: {exc}")
    text = body.get("text") or " ".join(s.text for s in segments)
    result = TranscriptionResult(text=text, segments=segments)
    store.save_transcript(meeting_id, result)
    logger.info(
        "[SAVE-TRANSCRIPT] Saved %d segments (%d chars) for meeting %s%s",
        len(segments), len(text), meeting_id, " (partial)" if partial else "",
    )
    if partial:
        # Checkpoint only: keep status='recording' untouched and skip
        # pipeline/status side effects so the live capture state machine
        # keeps running. The final save below does the full transition.
        return {"message": "Partial transcript saved", "segments": len(segments)}

    try:
        recovered = store.finalize_recording_pcm(meeting_id)
        if recovered:
            logger.info("[SAVE-TRANSCRIPT] Finalized live PCM → %s", recovered)
    except Exception as exc:
        logger.warning("[SAVE-TRANSCRIPT] PCM finalize failed (non-fatal): %s", exc)

    # Phase 0: clean old pipeline data + normalize. Speakers gate next (or file-tx);
    # do not leave stale tabs/blueprint that would auto-unlock Studio.
    try:
        from src.meeting.pipeline import normalize_sentences

        store.delete_pipeline_data(meeting_id)

        sentences = normalize_sentences(meeting_id, segments)
        store.save_sentences(meeting_id, [s.model_dump() for s in sentences])

        logger.info(
            "[SAVE-TRANSCRIPT] Normalized %d sentences for meeting %s",
            len(sentences), meeting_id,
        )
    except Exception as e:
        logger.warning("[SAVE-TRANSCRIPT] Post-save hook failed (non-fatal): %s", e)

    store.update_meeting(
        meeting_id,
        status=MeetingStatus.completed,
        processing_state=ProcessingState.idle.value,
        summary_gen_state=GenerationState.idle.value,
        blueprint_gen_state=GenerationState.idle.value,
        summary=None,
        detail=None,
        blueprint=None,
        blueprint_taxonomy=None,
        tabs=None,
    )
    logger.info(
        "[SAVE-TRANSCRIPT] Transcript saved for %s — Speakers gate (or file-tx), then Summary",
        meeting_id,
    )
    _emit_meeting("meeting.updated", meeting_id)

    return {"message": "Transcript saved", "segments": len(segments)}


@router.post("/meetings/{meeting_id}/upload-notes")
async def upload_notes(meeting_id: str, file: UploadFile = File(...)):
    logger.info("[UPLOAD-NOTES] Meeting %s filename=%s", meeting_id, file.filename)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[UPLOAD-NOTES] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    content_bytes = await file.read()
    filename = file.filename or "notes.txt"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    # Parse based on file type
    if ext in ("md", "txt"):
        content = content_bytes.decode("utf-8", errors="replace")
    elif ext == "docx":
        try:
            import mammoth
            from src.parsers.docx import clean_mammoth_markdown

            with io.BytesIO(content_bytes) as buf:
                result = mammoth.convert_to_markdown(buf)
            content = clean_mammoth_markdown(result.value)
        except Exception:
            content = content_bytes.decode("utf-8", errors="replace")
    else:
        content = content_bytes.decode("utf-8", errors="replace")

    path = store.save_notes(meeting_id, content)
    logger.info("[UPLOAD-NOTES] Saved %d chars to %s for meeting %s", len(content), path, meeting_id)
    _emit_meeting("meeting.updated", meeting_id)
    return {"message": "Notes uploaded", "path": path, "notes_content": content}


@router.post("/meetings/{meeting_id}/images")
async def upload_meeting_note_image(meeting_id: str, file: UploadFile = File(...)):
    """Upload an image for meeting notes. Returns the URL to insert in markdown."""
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    content_bytes = await file.read()
    if not content_bytes:
        raise HTTPException(400, "Empty file")
    try:
        safe_name = store.save_note_image(meeting_id, file.filename or "image.png", content_bytes)
    except FileNotFoundError:
        raise HTTPException(404, "Meeting not found")
    except ValueError:
        raise HTTPException(400, "Invalid image path")
    url = f"/api/meetings/{meeting_id}/images/{safe_name}"
    logger.info("[IMAGE] Uploaded %s (%d bytes) for meeting %s", safe_name, len(content_bytes), meeting_id)
    return {"url": url, "filename": safe_name}


@router.get("/meetings/{meeting_id}/images/{filename}")
async def serve_meeting_note_image(meeting_id: str, filename: str):
    """Serve an uploaded meeting-notes image."""
    try:
        image_path = store.get_note_image_path(meeting_id, filename)
    except ValueError:
        raise HTTPException(400, "Invalid image path")
    if not image_path.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(str(image_path))


# ── Transcription ─────────────────────────────────────────────


@router.post("/meetings/{meeting_id}/transcribe")
async def start_transcription(meeting_id: str, body: dict | None = Body(None)):
    logger.info("[TRANSCRIBE] Request for meeting %s", meeting_id)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[TRANSCRIBE] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    if not meeting.audio_path:
        recovered = store.finalize_recording_pcm(meeting_id)
        if recovered:
            meeting = store.get_meeting(meeting_id)
            logger.info("[TRANSCRIBE] Recovered live PCM → %s", recovered)
    if not meeting or not meeting.audio_path:
        logger.warning("[TRANSCRIBE] Meeting %s has NO AUDIO", meeting_id)
        raise HTTPException(
            400,
            "No audio file was saved for this meeting. Record again or upload an audio file, then re-transcribe.",
        )

    # Check active provider exists
    provider = meeting_service.get_active_file_provider()
    if not provider:
        logger.warning("[TRANSCRIBE] No active file transcription provider configured")
        raise HTTPException(400, "No active file transcription provider configured")

    logger.info("[TRANSCRIBE] Provider found: %s, updating status to transcribing", type(provider).__name__)
    # Drop previous Studio/summary work so the client always re-enters the
    # speaker gate after file transcription (re-tx must not skip Speakers).
    try:
        store.delete_pipeline_data(meeting_id)
    except Exception as exc:
        logger.warning("[TRANSCRIBE] delete_pipeline_data failed (non-fatal): %s", exc)
    store.update_meeting(
        meeting_id,
        status=MeetingStatus.transcribing,
        transcription_error=None,
        processing_state=ProcessingState.idle.value,
        summary_gen_state=GenerationState.idle.value,
        blueprint_gen_state=GenerationState.idle.value,
        summary=None,
        detail=None,
        blueprint=None,
        blueprint_taxonomy=None,
        tabs=None,
        speaker_names=None,
    )

    language_hints = body.get("language_hints") if isinstance(body, dict) else None

    task = task_manager.create_task(
        filename=f"meeting_{meeting_id}",
        task_type="transcribe",
        collection="meetings",
        meeting_id=meeting_id,
        language_hints=language_hints,
    )
    logger.info("[TRANSCRIBE] Task created: id=%s for meeting %s", task.id, meeting_id)
    # 「工作已开始」信号：转写在后台线程跑，路由层看不到完成，所以对端收到后
    # 自轮询会议状态直到空闲（见 events 设计 §11）。
    _emit_meeting("meeting.transcription_started", meeting_id, task_id=task.id)
    return {"message": "Transcription started", "task_id": task.id}


@router.post("/meetings/{meeting_id}/cancel-transcribe")
async def cancel_transcription(meeting_id: str):
    """Cancel an in-progress transcription task and reset meeting status."""
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    # Find and cancel any running transcribe task for this meeting
    cancelled = False
    for task in task_manager.get_all_tasks():
        task_args = task_manager._task_args.get(task.id)
        if task_args and task_args[0] == "transcribe" and task_args[1].get("meeting_id") == meeting_id:
            if task_manager.cancel_task(task.id):
                cancelled = True
    store.update_meeting(meeting_id, status=MeetingStatus.created, transcription_error=None)
    _emit_meeting("meeting.updated", meeting_id)
    return {"message": "Transcription cancelled", "cancelled": cancelled}


@router.get("/meetings/{meeting_id}/tasks")
async def get_meeting_tasks(meeting_id: str):
    tasks = task_manager.get_all_tasks()
    meeting_tasks = [
        t.to_dict()
        for t in tasks
        if t.filename == f"meeting_{meeting_id}"
    ]
    pending = sum(1 for t in meeting_tasks if t["status"] == "pending")
    processing = sum(1 for t in meeting_tasks if t["status"] == "processing")
    logger.debug("[TASKS] Meeting %s: %d total, %d pending, %d processing", meeting_id, len(meeting_tasks), pending, processing)
    return {"tasks": meeting_tasks, "pending": pending, "processing": processing}


@router.get("/meetings/{meeting_id}/live-summary")
async def get_live_summary(meeting_id: str):
    """Persisted in-meeting live summary snapshot (state is None if never enabled)."""
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    return {"state": store.get_live_summary(meeting_id)}


@router.websocket("/meetings/{meeting_id}/realtime-transcribe")
async def realtime_transcribe(websocket: WebSocket, meeting_id: str):
    print(f"[REALTIME-WS] >>> handler entered, meeting={meeting_id}", flush=True)
    logger.info("[REALTIME-WS] Connection for meeting %s", meeting_id)
    await websocket.accept()
    print(f"[REALTIME-WS] >>> accepted, getting provider", flush=True)

    # Capture the loop now, while we're on the event loop's thread.
    # on_segment() is invoked from a different thread (the DashScope SDK thread)
    # and cannot use asyncio.get_event_loop() there.
    main_loop = asyncio.get_running_loop()

    # ── 录制席仲裁的身份令牌（占用与否见下方 with 块）──
    client_id_param = (websocket.query_params.get("client_id") or "").strip()
    my_recorder_token = client_id_param or f"conn-{id(websocket)}"

    def _release_recorder_slot() -> None:
        with _recorders_guard:
            if _active_recorders.get(meeting_id) == my_recorder_token:
                _active_recorders.pop(meeting_id, None)

    translation_target = websocket.query_params.get("translation_target") or None
    if translation_target:
        translation_target = translation_target.strip() or None

    if translation_target:
        meta = meeting_service.get_realtime_translation_provider_meta()
    else:
        meta = meeting_service.get_active_realtime_provider_meta()
    logger.info(
        "[REALTIME-WS] Config active realtime: id=%s adapter=%s model=%s translation=%s",
        meta.get("id"),
        meta.get("adapter"),
        meta.get("model"),
        translation_target,
    )
    # Tell client which engine will be used (before possibly slow local load)
    await websocket.send_json(
        {
            "type": "provider",
            "provider_id": meta.get("id"),
            "adapter": meta.get("adapter"),
            "name": meta.get("name"),
            "model": meta.get("model"),
        }
    )

    # ── 录制席仲裁：一场会议同时只允许一个活跃录制连接 ──
    # 放在 meta 消息之后、provider 构建之前：拒绝时对方已知道引擎信息，
    # 且任何 provider 会话都还没开始。
    with _recorders_guard:
        if len(_active_recorders) >= MAX_ACTIVE_REALTIME_SESSIONS:
            logger.warning(
                "[REALTIME-WS] global realtime session cap (%d) reached; rejecting",
                MAX_ACTIVE_REALTIME_SESSIONS,
            )
            await websocket.send_json({
                "type": "error",
                "error": "too_many_sessions",
                "message": f"Server is at its realtime session limit ({MAX_ACTIVE_REALTIME_SESSIONS}). Try again later.",
            })
            await websocket.close(code=1013)
            return
        current_holder = _active_recorders.get(meeting_id)
        if current_holder is not None and current_holder != my_recorder_token:
            logger.warning(
                "[REALTIME-WS] meeting %s already recording (held by %s); rejecting %s",
                meeting_id, current_holder, my_recorder_token,
            )
            await websocket.send_json({
                "type": "error",
                "error": "recording_in_progress",
                "message": "Another device or tab is already recording this meeting",
            })
            await websocket.close(code=1013)
            return
        _active_recorders[meeting_id] = my_recorder_token

    provider = None
    try:
        if translation_target:
            try:
                provider = meeting_service.get_realtime_translation_provider()
            except Exception as exc:
                logger.warning("[REALTIME-WS] Translation provider unavailable: %s", exc)
                await websocket.send_json({"error": str(exc)})
                await websocket.close()
                _release_recorder_slot()
                return
        else:
            # 构建放进 executor：本地 ONNX 首次加载是秒级同步活，留在事件循环上
            # 会冻结全部设备的全部请求。
            provider = await main_loop.run_in_executor(
                None, meeting_service.get_active_realtime_provider
            )
        if not provider:
            print("[REALTIME-WS] >>> NO PROVIDER CONFIGURED", flush=True)
            logger.warning("[REALTIME-WS] No active realtime transcription provider")
            await websocket.send_json(
                {"error": "No active realtime transcription provider"}
            )
            await websocket.close()
            _release_recorder_slot()
            return
    except BaseException:
        # provider 构建段抛异常（配置坏/适配器未知）时不能泄漏录制席，
        # 否则这场会议在进程重启前谁都录不了。
        _release_recorder_slot()
        raise

    print(
        f"[REALTIME-WS] >>> provider: {type(provider).__name__} adapter={meta.get('adapter')}",
        flush=True,
    )
    logger.info(
        "[REALTIME-WS] Provider instance: %s (adapter=%s)",
        type(provider).__name__,
        meta.get("adapter"),
    )

    async def _safe_send(payload):
        try:
            await websocket.send_json(payload)
        except Exception:
            pass  # client disconnected, ignore

    # ── 探针计数器（2026-09-16）：定位「漏句」到底丢在哪一段 ──
    stats = {
        "frames_in": 0,          # 客户端送来的 PCM 帧
        "silence_injected": 0,   # 看门狗喂的静音帧（保活）
        "segments_out": 0,       # 推给前端的 transcript 消息数（含 partial 更新）
        "upstream_losses": 0,    # 上游会话死亡次数（自愈/上报）
        "audio_s": 0.0,          # 收进来的**用户**音频秒数（不含静音保活）
        "text_s": 0.0,           # 转出转写的秒数（按句去重）
    }
    # 覆盖率探针：seq 只能发现「服务端发了前端没收」，抓不到「音频进去了、
    # 云端没返」——后者一条消息都不产生，seq 永远连续。用「音频秒数 vs
    # 转写秒数」才能把那一类丢句显形（云端 VAD 吞小声/重叠、语义断句吸收短句）。
    covered: dict[str, tuple[float, float]] = {}
    seq_counter = itertools.count(1)

    def _mark_upstream_lost(reason: str) -> None:
        """适配器检测到上游会话死亡时回调（可能来自 SDK 线程）。"""
        stats["upstream_losses"] += 1
        logger.warning(
            "[REALTIME-WS] upstream session lost for meeting %s: %s (total=%d)",
            meeting_id,
            reason,
            stats["upstream_losses"],
        )
        main_loop.call_soon_threadsafe(
            asyncio.create_task,
            _safe_send({"type": "provider_lost", "reason": reason}),
        )

    # 可选属性注入（不进 start() 签名，避免波及全部适配器与测试替身）
    try:
        provider.on_upstream_lost = _mark_upstream_lost
    except Exception:
        logger.debug("[REALTIME-WS] provider rejects on_upstream_lost; skipping", exc_info=True)

    def _push_summary(snapshot: dict):
        # Engine thread → event loop: ship a full live-summary snapshot.
        if main_loop.is_closed():
            return
        main_loop.call_soon_threadsafe(
            asyncio.create_task, _safe_send({"type": "live_summary", "state": snapshot})
        )

    def on_segment(segment, is_final, key):
        try:
            if is_final:
                # 覆盖率探针：同一句会反复收到 final（云端细化 end_time），按 key
                # 去重后再累计，否则转写秒数会虚高。
                covered[str(key) if key is not None else f"{segment.start}:{segment.text}"] = (
                    float(segment.start or 0.0),
                    float(segment.end or 0.0),
                )
                stats["text_s"] = round(
                    sum(max(0.0, e - b) for b, e in covered.values()), 1
                )
                eng = live_summary_mod.get_engine(meeting_id)
                if eng is not None and eng.state.engine == "running":
                    eng.ingest_segment(
                        segment.start,
                        segment.end,
                        segment.text,
                        speaker_id=segment.speaker_id,
                        key=str(key) if key is not None else None,
                    )
            payload = {
                "type": "transcript",
                "key": str(key) if key is not None else None,
                # 单调序号：前端据此发现「中间缺了几条」，用于区分
                # 「云端没返」「返了没显示」「显示了又没了」（探针）
                "seq": next(seq_counter),
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "speaker_id": segment.speaker_id,
                "translation": getattr(segment, "translation", None),
                "is_final": is_final,
            }
            stats["segments_out"] += 1
            main_loop.call_soon_threadsafe(
                asyncio.create_task, _safe_send(payload)
            )
        except Exception as exc:
            print(f"[REALTIME-WS] >>> on_segment error: {exc!r}", flush=True)

    # Track whether the SDK has already been stopped, so the finally
    # block can skip a redundant stop() (and the WS stays open long enough
    # to deliver the last in-flight event before the underlying Task
    # objects run).
    provider_already_stopped = False

    # Read language hints from query params (e.g. ?language_hints=zh&language_hints=en)
    language_hints = websocket.query_params.getlist("language_hints") or None
    # "auto" means auto-detect — strip it so the provider doesn't receive it
    if language_hints:
        language_hints = [h for h in language_hints if h != "auto"] or None
    if language_hints:
        logger.info("[REALTIME-WS] Language hints from client: %s", language_hints)

    try:
        # Load hot words only when the active realtime adapter can apply them
        hot_words = None
        meeting = store.get_meeting(meeting_id)
        if meeting and getattr(provider, "supports_hot_words", False):
            from src.hot_words.store import collect_meeting_hot_words
            hot_words = collect_meeting_hot_words(meeting) or None
            if hot_words:
                logger.info("[REALTIME-WS] Loaded %d hot words for meeting %s", len(hot_words), meeting_id)

        print(f"[REALTIME-WS] >>> calling provider.start()", flush=True)
        await provider.start(
            on_segment,
            hot_words=hot_words,
            language_hints=language_hints,
            translation_target=translation_target,
        )
        print(f"[REALTIME-WS] >>> provider.start() returned, entering receive loop", flush=True)
        await websocket.send_json({"type": "ready", "message": "realtime session ready"})

        # Reconnect resume: an already-running live-summary engine re-sends
        # its status + latest snapshot so a refreshed client catches up.
        existing_engine = live_summary_mod.get_engine(meeting_id)
        if existing_engine is not None and existing_engine.state.engine == "running":
            existing_engine.on_update = _push_summary
            await _safe_send({"type": "live_summary_status", "engine": "running"})
            await _safe_send(
                {"type": "live_summary", "state": existing_engine.snapshot()}
            )

        client_requested_stop = False
        # Engine swaps (translation toggle / language change) stop-flush and
        # reconnect within seconds; only a real recording stop may finalize
        # the live summary engine (finalize() idles it, which would freeze
        # the summary the reconnecting session is about to re-enable).
        client_wants_finalize = True
        pcm_frames = 0
        pcm_bytes = 0
        # 看门狗与探针状态：上一次真实音频到达的时刻、上一次推 stats 的时刻。
        # 注意**不能**用 `wait_for(websocket.receive(), timeout=...)` 做超时——
        # 超时会取消 receive()，而 ASGI 的 receive 一旦被取消，连接就废了。
        # 所以保活与探针跑在独立任务里，主循环照常阻塞接收。
        last_audio_at = main_loop.time()
        last_stats_at = last_audio_at
        keepalive_stop = asyncio.Event()

        async def _keepalive_loop() -> None:
            # nonlocal 必须有：下面要读写这两个「主循环也在读写」的局部变量，
            # 少了它 Python 会把它们当本函数的局部量 → 首次读取就 UnboundLocalError，
            # 任务当场静默死掉（正是这类 bug 最恶心的形态）。
            nonlocal last_audio_at, last_stats_at
            while not keepalive_stop.is_set():
                try:
                    await asyncio.wait_for(
                        keepalive_stop.wait(), timeout=REALTIME_KEEPALIVE_TICK_S
                    )
                    return
                except asyncio.TimeoutError:
                    pass
                now = main_loop.time()
                # 只对「上游会静默死亡」的云端适配器喂保活；本地模型白烧 CPU
                if (
                    getattr(provider, "wants_silence_keepalive", False)
                    and now - last_audio_at >= REALTIME_KEEPALIVE_IDLE_S
                ):
                    try:
                        await provider.send_frame(REALTIME_SILENCE_FRAME)
                        stats["silence_injected"] += 1
                        last_audio_at = now  # 喂过就当作「刚有音频」
                        if stats["silence_injected"] == 1 or stats["silence_injected"] % 12 == 0:
                            logger.info(
                                "[REALTIME-WS] keepalive: fed %d silence frames while client idle",
                                stats["silence_injected"],
                            )
                    except Exception:
                        logger.warning(
                            "[REALTIME-WS] keepalive silence frame failed", exc_info=True
                        )
                if now - last_stats_at >= REALTIME_STATS_INTERVAL_S:
                    last_stats_at = now
                    await _safe_send({"type": "stats", **stats})

        keepalive_task = asyncio.create_task(_keepalive_loop())

        def _on_keepalive_done(task: asyncio.Task) -> None:
            """保活任务异常退出必须可见——它静默死掉的表现就是「探针永远没数据」，
            排查时会被误读成「云端没返」。"""
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "[REALTIME-WS] keepalive task died: %r", exc, exc_info=exc
                )

        keepalive_task.add_done_callback(_on_keepalive_done)
        while True:
            # receive() returns a dict with "type" plus either "text" or "bytes".
            # We use this to support a JSON stop signal from the client (so
            # the SDK can flush its last sentence before we tear down).
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if "text" in message:
                try:
                    payload = json.loads(message["text"])
                except Exception:
                    payload = {}
                if payload.get("action") == "stop":
                    print(f"[REALTIME-WS] >>> client sent stop signal", flush=True)
                    client_requested_stop = True
                    client_wants_finalize = bool(payload.get("finalize", True))
                    break
                if payload.get("action") == "live_summary":
                    enabled = bool(payload.get("enabled"))
                    try:
                        eng = live_summary_mod.ensure_engine(
                            meeting_id,
                            llm_factory=live_summary_mod.resolve_live_summary_llm,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[REALTIME-WS] Live summary LLM unavailable: %s", exc
                        )
                        await _safe_send(
                            {"type": "live_summary_error", "message": str(exc)}
                        )
                        continue
                    eng.on_update = _push_summary
                    if enabled:
                        eng.start()
                        await _safe_send(
                            {"type": "live_summary_status", "engine": "running"}
                        )
                    else:
                        eng.stop()
                        await _safe_send(
                            {"type": "live_summary_status", "engine": "idle"}
                        )
                    continue
                if payload.get("action") == "live_summary_reset":
                    eng = live_summary_mod.get_engine(meeting_id)
                    if eng is not None:
                        eng.reset()
                    continue
                # Unknown text message — ignore.
                continue
            if "bytes" in message:
                data = message["bytes"] or b""
                pcm_frames += 1
                pcm_bytes += len(data)
                stats["frames_in"] += 1
                stats["audio_s"] = round(stats["audio_s"] + len(data) / 32000.0, 1)
                last_audio_at = main_loop.time()
                if pcm_frames <= 3 or pcm_frames % 50 == 0:
                    logger.info(
                        "[REALTIME-WS] pcm frame#%d +%dB total=%dB adapter=%s",
                        pcm_frames,
                        len(data),
                        pcm_bytes,
                        meta.get("adapter"),
                    )
                # send_frame only enqueues for local FunASR; must stay fast
                # so the WS receive loop does not stall behind generate().
                await provider.send_frame(data)

        # Client asked for a graceful stop. Order matters:
        #   1. Tell the SDK to finalize first (otherwise it doesn't know
        #      to flush and the last sentence is lost).
        #   2. THEN wait ~2s for the last in-flight segment to be
        #      delivered through on_event → on_segment → WebSocket send.
        # The previous version did the sleep first, so the SDK never got
        # the chance to flush — the user's last words were dropped.
        if client_requested_stop:
            print(
                f"[REALTIME-WS] >>> stopping provider first, then waiting for flush",
                flush=True,
            )
            await provider.stop()
            provider_already_stopped = True
            await asyncio.sleep(2.0)
            # Recording stopped → squeeze one final summary round (to_thread:
            # LLM latency must not block the event loop). Skipped for engine
            # swaps — finalize() idles the engine and its closing round races
            # the reconnecting client's re-enable.
            if client_wants_finalize:
                stopped_engine = live_summary_mod.get_engine(meeting_id)
                if stopped_engine is not None and stopped_engine.state.engine == "running":
                    await asyncio.to_thread(stopped_engine.finalize)
    except WebSocketDisconnect:
        print(f"[REALTIME-WS] >>> client disconnected for meeting {meeting_id}", flush=True)
        logger.info("[REALTIME-WS] Client disconnected for meeting %s", meeting_id)
    except Exception as exc:
        print(f"[REALTIME-WS] >>> ERROR for meeting {meeting_id}: {exc!r}", flush=True)
        logger.error("[REALTIME-WS] Error for meeting %s: %s", meeting_id, exc, exc_info=True)
    finally:
        # 停掉保活任务（它可能正在 await；set 让它尽快退出，cancel 兜底）
        try:
            keepalive_stop.set()
            keepalive_task.cancel()
        except Exception:
            pass
        if not provider_already_stopped:
            await provider.stop()
        _release_recorder_slot()
        print(f"[REALTIME-WS] <<< handler exiting for meeting {meeting_id}", flush=True)
        logger.info("[REALTIME-WS] Provider stopped for meeting %s", meeting_id)


# ── Summary & Allocation ──────────────────────────────────────


@router.post("/meetings/{meeting_id}/generate-summary")
async def generate_summary(meeting_id: str):
    logger.info("[SUMMARY] Generate request for meeting %s", meeting_id)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        logger.warning("[SUMMARY] Meeting %s NOT FOUND", meeting_id)
        raise HTTPException(404, "Meeting not found")
    if meeting.processing_state != ProcessingState.idle.value:
        logger.warning("[SUMMARY] Meeting %s is busy: %s", meeting_id, meeting.processing_state)
        raise HTTPException(409, f"Meeting is busy: {meeting.processing_state}")
    transcript = store.get_transcript(meeting_id)
    if not transcript:
        logger.warning("[SUMMARY] Meeting %s has NO TRANSCRIPT", meeting_id)
        raise HTTPException(400, "No transcript available")

    logger.info("[SUMMARY] Starting LLM generation for meeting %s (%d transcript segments)", meeting_id, len(transcript.segments))
    store.update_meeting(meeting_id, processing_state=ProcessingState.summarizing.value)
    task = task_manager.create_task(
        filename=f"summary:{meeting_id}",
        task_type="meeting_summary",
        meeting_id=meeting_id,
    )
    logger.info("[SUMMARY] Task created for meeting %s: task_id=%s", meeting_id, task.id)
    updated = store.get_meeting(meeting_id)
    if not updated:
        raise HTTPException(404, "Meeting not found")
    # 「工作已开始」：总结在后台跑，对端收到后轮询会议状态直到空闲（见 §11）。
    _emit_meeting("meeting.generation_started", meeting_id, task_id=task.id)
    return _serialize_meeting(updated)


@router.post("/meetings/{meeting_id}/transcript-index")
async def start_transcript_index(meeting_id: str):
    """Build or rebuild the verbatim transcript vector index."""
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if (meeting.transcript_index_status or "") == "building":
        return _serialize_meeting(meeting)
    sentences = store.get_sentences(meeting_id)
    transcript = store.get_transcript(meeting_id)
    if not sentences and not transcript:
        raise HTTPException(400, "No transcript available")
    store.update_meeting(meeting_id, transcript_index_status="building", transcript_index_error="")
    task_manager.create_task(
        filename=f"tx-index:{meeting_id}",
        task_type="meeting_transcript_index",
        meeting_id=meeting_id,
    )
    updated = store.get_meeting(meeting_id)
    if not updated:
        raise HTTPException(404, "Meeting not found")
    return _serialize_meeting(updated)


def _sse_event(event: str, data: object) -> str:
    """Format a dict as an SSE event string."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/meetings/{meeting_id}/blueprint/stream")
async def stream_blueprint(meeting_id: str):
    """Stream blueprint generation as SSE events.

    Pass 1 — General Summary (streaming tokens):
      event: state  → {"summary": "prefilling"|"streaming"|"idle"}
      event: thinking → "reasoning text chunk"
      event: token  → "markdown content chunk"
      event: summary_done → {"title": "...", "general_md": "..."}

    Pass 2 — Blueprint Decomposition:
      event: state → {"blueprint": "prefilling"|"idle"}
      event: blueprint_done → {"taxonomy": {...}, "blueprint": [...]}

    Errors:
      event: error → {"message": "..."}
    """
    require_meeting_operate(meeting_id)
    logger.info("[SSE] Blueprint stream request for meeting %s", meeting_id)
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        def _err():
            yield _sse_event("error", {"message": "Meeting not found"})
        return StreamingResponse(_err(), media_type="text/event-stream")
    if meeting.processing_state != ProcessingState.idle.value:
        def _err():
            yield _sse_event("error", {"message": f"Meeting is busy: {meeting.processing_state}"})
        return StreamingResponse(_err(), media_type="text/event-stream")
    transcript = store.get_transcript(meeting_id)
    if not transcript:
        def _err():
            yield _sse_event("error", {"message": "No transcript available"})
        return StreamingResponse(_err(), media_type="text/event-stream")

    def _stream():
        for evt in meeting_service.generate_blueprint_stream(meeting_id):
            yield _sse_event(evt["event"], evt["data"])

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.get("/meetings/{meeting_id}/sections/{tab_id}/generate-stream")
async def stream_section_generation(meeting_id: str, tab_id: str):
    """Stream single-section generation as SSE events.

    Events:
      event: state  → {"section_gen": "prefilling"|"streaming"|"idle"}
      event: thinking → "reasoning text chunk"
      event: token  → "markdown content chunk"
      event: section_done → {"tab_id": "...", "md": "..."}
      event: error → {"message": "..."}
    """
    logger.info("[SSE] Section stream request for meeting %s tab=%s", meeting_id, tab_id)
    meeting = store.get_meeting(meeting_id)
    require_meeting_operate(meeting_id, meeting)
    if not meeting:
        def _err():
            yield _sse_event("error", {"message": "Meeting not found"})
        return StreamingResponse(_err(), media_type="text/event-stream")

    def _stream():
        for evt in meeting_service.generate_section_stream(meeting_id, tab_id):
            yield _sse_event(evt["event"], evt["data"])

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/meetings/{meeting_id}/extract")
async def start_extract(meeting_id: str, body: dict = Body()):
    """Start extract for one or more section receipts (v3).

    Body: {"receipts": [{"source": "blueprint"|"custom", "tab_id"?: "...", "name": "...", "description": "..."}]}
    """
    receipts = body.get("receipts", [])
    if not receipts:
        raise HTTPException(400, "receipts is required and must not be empty")
    require_meeting_operate(meeting_id)
    logger.info("[EXTRACT] Request for meeting %s: %d receipts", meeting_id, len(receipts))
    try:
        meeting = await meeting_service.start_extract(meeting_id, receipts)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    logger.info("[EXTRACT] Started for meeting %s", meeting_id)
    _emit_meeting("meeting.generation_started", meeting_id)
    return _serialize_meeting(meeting)


@router.delete("/meetings/{meeting_id}/sections/{tab_id}")
async def delete_section(meeting_id: str, tab_id: str):
    """Delete a section and clean up its tags (Node 2.1)."""
    logger.info("[DELETE-SECTION] Meeting %s tab=%s", meeting_id, tab_id)
    try:
        meeting = await meeting_service.delete_section(meeting_id, tab_id)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    _emit_meeting("meeting.section_updated", meeting_id, tab_id=tab_id)
    return _serialize_meeting(meeting)


@router.post("/meetings/{meeting_id}/sections/{tab_id}/regenerate")
async def regenerate_section(meeting_id: str, tab_id: str):
    """Regenerate one section: clean tags + re-scan + re-summarize."""
    require_meeting_operate(meeting_id)
    logger.info("[REGENERATE] Meeting %s tab=%s", meeting_id, tab_id)
    try:
        meeting = await meeting_service.start_section_regenerate(
            meeting_id, tab_id
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    logger.info("[REGENERATE] Started for meeting %s tab=%s", meeting_id, tab_id)
    _emit_meeting("meeting.generation_started", meeting_id, tab_id=tab_id)
    return _serialize_meeting(meeting)


@router.post("/meetings/{meeting_id}/sections/{tab_id}/allocate")
async def allocate_section(meeting_id: str, tab_id: str, body: dict):
    """Allocate one section's content to a collection (speaker names resolved, refs stripped).

    Body: collection_id (required), chain_id (optional — defaults to main).
    Response is the meeting dict plus bridge fields: file_id, task_id, node_id, source, chain_id.
    """
    collection_id = body.get("collection_id", "")
    if not collection_id:
        raise HTTPException(400, "collection_id is required")
    chain_id = (body.get("chain_id") or "").strip() or None
    logger.info(
        "[ALLOCATE_SECTION] Meeting %s tab=%s → collection=%s chain=%s",
        meeting_id,
        tab_id,
        collection_id,
        (chain_id or "")[:12],
    )
    try:
        meeting, bridge = await meeting_service.allocate_section_to_collection(
            meeting_id, tab_id, collection_id, chain_id=chain_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    logger.info(
        "[ALLOCATE_SECTION] Done meeting %s tab=%s file=%s task=%s node=%s",
        meeting_id,
        tab_id,
        (bridge.get("file_id") or "")[:12],
        bridge.get("task_id"),
        (bridge.get("node_id") or "")[:12],
    )
    out = _serialize_meeting(meeting)
    out.update(bridge)
    _emit_meeting("meeting.section_updated", meeting_id, tab_id=tab_id)
    return out


@router.get("/meetings/{meeting_id}/sections/{tab_id}/todo-candidates")
async def get_section_todo_candidates(
    meeting_id: str,
    tab_id: str,
    refresh: bool = False,
):
    """Return todo candidates for a section.

    If none stored (legacy allocate before this feature) or ``refresh=1``,
    re-parse the current section MD, persist, and return.
    """
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    tab_meta: dict | None = None
    for t in meeting.tabs or []:
        td = t if isinstance(t, dict) else t.model_dump()
        if td.get("tab_id") == tab_id:
            tab_meta = td
            break
    if tab_meta is None:
        raise HTTPException(404, f"Tab '{tab_id}' not found")

    cands = list(tab_meta.get("todo_candidates") or [])
    if refresh or not cands:
        try:
            cands = meeting_service.extract_section_todo_candidates(
                meeting_id,
                tab_id,
                persist=True,
                use_llm=True,
            )
            # reload tab meta after persist
            meeting = store.get_meeting(meeting_id) or meeting
            for t in meeting.tabs or []:
                td = t if isinstance(t, dict) else t.model_dump()
                if td.get("tab_id") == tab_id:
                    tab_meta = td
                    break
        except Exception as exc:
            logger.warning(
                "extract todo candidates failed %s/%s: %s",
                meeting_id,
                tab_id,
                exc,
            )

    return {
        "meeting_id": meeting_id,
        "tab_id": tab_id,
        "section_name": (tab_meta or {}).get("name") or tab_id,
        "collection_id": (tab_meta or {}).get("associated_collection_id") or "",
        "chain_id": (tab_meta or {}).get("allocated_chain_id") or "",
        "node_id": (tab_meta or {}).get("allocated_node_id") or "",
        "candidates": cands,
    }


@router.post("/meetings/{meeting_id}/todo-candidates/mark-created")
async def mark_todo_candidates_created(meeting_id: str, body: dict):
    """After checklist create, bind candidate_id → todo_id to prevent re-offer.

    Body: ``{ "items": [ {"tab_id": "...", "candidate_id": "...", "todo_id": "..."}, ... ] }``
    """
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise HTTPException(400, "items array is required")
    try:
        meeting = meeting_service.mark_todo_candidates_created(meeting_id, items)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    _emit_meeting("meeting.section_updated", meeting_id)
    return _serialize_meeting(meeting)


@router.delete("/meetings/{meeting_id}/sections/{tab_id}/allocate")
async def delete_section_allocation(meeting_id: str, tab_id: str):
    """Remove a section's collection allocation (delete file snapshot + clear metadata)."""
    logger.info("[DELETE_ALLOCATION] Meeting %s tab=%s", meeting_id, tab_id)
    try:
        meeting = await meeting_service.delete_section_allocation(meeting_id, tab_id)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    logger.info("[DELETE_ALLOCATION] Done meeting %s tab=%s", meeting_id, tab_id)
    _emit_meeting("meeting.section_updated", meeting_id, tab_id=tab_id)
    return _serialize_meeting(meeting)


@router.get("/meetings/{meeting_id}/sections/{tab_id}/md")
async def get_section_md_content(meeting_id: str, tab_id: str):
    """Serve the rendered markdown content for a section tab."""
    from fastapi.responses import PlainTextResponse
    content = store.get_section_md(meeting_id, tab_id)
    if content is None:
        return PlainTextResponse(
            content="Section not found",
            status_code=404,
            media_type="text/plain; charset=utf-8",
        )
    return PlainTextResponse(content=content, media_type="text/markdown; charset=utf-8")


@router.post('/meetings/{meeting_id}/sections/{tab_id}/export')
async def export_summary_file(meeting_id: str, tab_id: str, body: SummaryExportRequest):
    from urllib.parse import quote
    from src.identity import authorize, get_actor
    from src.meeting.exports import (CONTENT_TYPES, ExportBusy, conversion_slot, export_filename,
                                     render_summary, summary_document)

    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(404, 'Meeting not found')
    authorize(get_actor(), 'meeting.read', {'meeting': meeting.model_dump()})
    try:
        document = summary_document(meeting, tab_id, body.language)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.format == 'md':
        data = document['markdown'].encode('utf-8')
    else:
        try:
            with conversion_slot():
                data = await run_in_threadpool(render_summary, document, body.format, meeting.id)
        except ExportBusy as exc:
            raise HTTPException(429, str(exc), headers={'Retry-After': '3'}) from exc
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            logger.exception('Meeting summary export failed (%s)', body.format)
            raise HTTPException(503, 'Export could not be completed. Please retry.') from exc
    filename = export_filename(document['title'], body.format)
    return Response(data, media_type=CONTENT_TYPES[body.format], headers={
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe=''),
        'Cache-Control': 'no-store',
        'Access-Control-Expose-Headers': 'Content-Disposition',
    })


@router.put("/meetings/{meeting_id}/sections/{tab_id}/md")
async def save_section_md_content(meeting_id: str, tab_id: str, body: dict = Body()):
    """Save edited markdown content for a section tab.

    If the tab is already allocated to a collection, mark ``needs_reingest``
    so the UI can offer manual "Update collection" (new file version).
    """
    content = body.get("content", "")
    # save_section_md 自带"正文变了就清该节译文"（唯一漏斗，见其 docstring），
    # 这里不再单独清一次——两处各清一遍迟早有一处漏或语义分叉。
    path = store.save_section_md(meeting_id, tab_id, content)
    # Invalidate cached meeting→note distillation for this tab
    try:
        from src.notes.service import invalidate_meeting_distillation
        invalidate_meeting_distillation(meeting_id, tab_id)
    except Exception:
        pass

    # Single meta write: only touch the edited tab (no full-tab MD rehash).
    needs_reingest = False
    meeting = store.get_meeting(meeting_id)
    if meeting and meeting.tabs:
        updated_tabs: list[dict] = []
        for t in meeting.tabs:
            td = dict(t) if isinstance(t, dict) else (
                t.model_dump() if hasattr(t, "model_dump") else dict(t)
            )
            if td.get("tab_id") == tab_id:
                fid = (td.get("allocated_file_id") or "").strip()
                if fid:
                    stored = (td.get("ingested_content_hash") or "").strip()
                    current = store.section_content_hash(content)
                    # No hash (legacy allocate): any save after allocate counts as dirty
                    needs_reingest = (not stored) or (current != stored)
                    td["needs_reingest"] = needs_reingest
                else:
                    td["needs_reingest"] = False
            updated_tabs.append(td)
        meeting = store.update_meeting(meeting_id, tabs=updated_tabs)

    logger.info(
        "[SAVE-SECTION-MD] Saved %s/%s (%d chars) needs_reingest=%s",
        meeting_id,
        tab_id,
        len(content),
        needs_reingest,
    )
    meeting_payload = _serialize_meeting(meeting) if meeting else None
    _emit_meeting("meeting.section_updated", meeting_id, tab_id=tab_id)
    return {
        "ok": True,
        "path": path,
        "needs_reingest": needs_reingest,
        "meeting": meeting_payload,
    }


@router.get("/meetings/{meeting_id}/sections/{tab_id}/translate/stream")
async def stream_section_translation(meeting_id: str, tab_id: str, lang: str):
    """SSE stream of a summary translation into `lang`.

    Single code path for all cases: serves the cached file instantly when one
    exists, re-attaches to an in-progress task (replaying missed tokens), or
    starts a fresh generation.  Translation files are never ingested.
    """
    from src.meeting.service import meeting_service as _svc

    require_meeting_operate(meeting_id)

    def _stream():
        for evt in _svc.generate_translation_stream(meeting_id, tab_id, lang):
            yield _sse_event(evt["event"], evt["data"])

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.get("/meetings/{meeting_id}/sections/{tab_id}/translations")
async def list_section_translations(meeting_id: str, tab_id: str):
    """List the language codes that already have a translation file."""
    from src.meeting.service import meeting_service as _svc
    return {"languages": _svc.list_summary_translations(meeting_id, tab_id)}


@router.get("/meetings/{meeting_id}/translations/active")
async def list_active_translations(meeting_id: str):
    """List the (tab_id, language) pairs currently being translated.

    Lets the frontend re-attach in-progress streams after a browser refresh.
    """
    from src.meeting.service import meeting_service as _svc
    return {"active": _svc.list_active_translations(meeting_id)}


@router.post("/meetings/{meeting_id}/generate-section-description")
async def generate_section_description(meeting_id: str, body: dict = Body()):
    """Generate a section description via LLM based on section name + General Summary."""
    from src.meeting.service import meeting_service as _svc

    require_meeting_operate(meeting_id)
    section_name = (body.get("section_name") or "").strip()
    if not section_name:
        raise HTTPException(400, "section_name is required")
    try:
        result = await _svc.generate_section_description(meeting_id, section_name)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result


