"""staging 上传四件套的**跨盒子转发器**（批次 5）。

## 为什么此前「不可远端化」，现在可以了

staging 令牌是**进程内**暂存表（`src/mcp/staging.py`）：Agent 把字节
stage 到 **Hub 这台盒子**，令牌只有 Hub 进程解得开——远端盒子拿到令牌也
是睁眼瞎。所以批次 1-4.5 一直把它们登记在 ``NOT_REMOTABLE`` 里。

本批的解法：**Hub 在本机取出 staging 字节，multipart 直传远端的上传端点**——
令牌永远不出 Hub 进程，远端只收到「一个正常的 multipart 上传」（与它自家
网页走的是同一条路、同一套闸门）。

``file_path`` 变体同理：那是 **Hub 机器上**的 data/ 内文件，转发器同样在
本机读出字节再传（复用 ``_load_staged_bytes`` 的全部校验：路径必须在
data/ 下、token 过期文案等）。

## 忠实性要点

- 文件名校验（``safe_filename``）与冲突枚举校验在 **Hub 侧**先做——与本地
  实现同一顺序，坏名字不浪费一次跨盒传输；
- 上传传输时间 ∝ 体积：``hub_routed(timeout=300)``（默认 20s 对 500MB 上传
  毫无意义——远端还在收字节 Hub 就先报「连不上」）；
- staging 令牌是**取走即焚**（``take`` 弹出）：转发失败令牌也消耗了——与
  本地行为一致（本地也是先 take 再处理），不是新风险；
- 信封逐字段复刻本地实现。
"""

from __future__ import annotations

from typing import Any

from src.hub.client import HubCallError
from src.hub.routing import RemoteApi, quote_segment
from src.mcp.common import err, ok

#: 上传传输的出站超时（秒）。staging 上限 500MB；LAN 百兆现实峰值 ~10MB/s。
UPLOAD_TIMEOUT = 300.0


async def _load_bytes(staging_token: str, file_path: str, filename: str):
    """本机取 staging / file_path 字节（复用工具侧的全部校验与文案）。"""
    from src.mcp.tools.file_mgmt import _load_staged_bytes

    return await _load_staged_bytes(staging_token, file_path, filename)


def _safe_name(use_filename: str) -> tuple[str, dict[str, Any] | None]:
    from src.mcp.common import safe_filename

    try:
        return safe_filename(use_filename), None
    except ValueError as exc:
        return "", err(str(exc))


async def upload_document_forward(api: RemoteApi, args: dict[str, Any]) -> dict[str, Any]:
    from src.mcp.common import require_collection  # noqa: F401  (precheck 已在路由层做)

    loaded = await _load_bytes(
        str(args.get("staging_token") or ""),
        str(args.get("file_path") or ""),
        str(args.get("filename") or ""),
    )
    if isinstance(loaded, dict):
        return loaded
    raw, use_filename = loaded
    safe_name, name_err = _safe_name(use_filename)
    if name_err:
        return name_err
    collection = str(args.get("collection") or "default")

    payload = await api.post_multipart(
        "/documents/upload",
        # ⚠️ collection 必须走 **query**：端点声明是裸 str（FastAPI 绑 query），
        # 传成 form 字段会被静默忽略 → 上传落进 default 库而信封谎报目标库
        # （审查 CRITICAL，真机实证）。params=data 不 interchangeable。
        params={"collection": collection},
        files={"files": (safe_name, raw)},
        timeout=UPLOAD_TIMEOUT,
    )
    # REST 回 {message, tasks: [task_dict+file_id]}（单文件上传取第一条）
    tasks = payload.get("tasks") or [] if isinstance(payload, dict) else []
    if not tasks:
        return err(f"remote upload returned no task: {payload}")
    t = tasks[0]
    return ok(
        message="Content queued for processing",
        task_id=t.get("id"),
        file_id=t.get("file_id"),
        filename=t.get("filename") or safe_name,
        size_bytes=len(raw),
        # 回报**远端解析后的 ID**（task.collection）——传显示名时 arg 不是
        # 可用的 ID，后续 get_task_status/list_documents 会拿它扑空（审查 #6）
        collection=t.get("collection") or collection,
    )


async def upload_file_forward(api: RemoteApi, args: dict[str, Any]) -> dict[str, Any]:
    loaded = await _load_bytes(
        str(args.get("staging_token") or ""),
        str(args.get("file_path") or ""),
        str(args.get("filename") or ""),
    )
    if isinstance(loaded, dict):
        return loaded
    raw, use_filename = loaded
    safe_name, name_err = _safe_name(use_filename)
    if name_err:
        return name_err
    collection = str(args.get("collection") or "default")

    conflict = (str(args.get("on_name_conflict") or "error").strip()) or "error"
    if conflict not in ("error", "auto_rename"):
        return err("on_name_conflict must be 'error' or 'auto_rename'")
    fid = str(args.get("folder_id") or "").strip() or None

    payload = await api.post_multipart(
        f"/file-mgmt/{_q('collection', collection)}/files/upload",
        data={
            "folder_id": fid or "",
            "on_name_conflict": conflict,
        },
        files={"file": (safe_name, raw)},
        timeout=UPLOAD_TIMEOUT,
    )
    # REST 回 service.upload_file_to_folder 的结果（file 字典 + task_id），
    # 与本地 _dump(result) 同一来源 → 同形透传
    if not isinstance(payload, dict):
        return err(f"remote upload returned unexpected payload: {payload!r:.120}")
    return ok(
        message="File uploaded; ingest may be async",
        collection=collection,
        file=payload,
        file_id=payload.get("file_id"),
        task_id=payload.get("task_id"),
        folder_id=fid,
    )


async def upload_file_version_forward(api: RemoteApi, args: dict[str, Any]) -> dict[str, Any]:
    # 校验顺序与本地一致：file_id 先于取令牌（令牌**取走即焚**——顺序反了
    # 会让一次必败调用白烧掉 500MB 的重传凭证，审查 #3）
    fid = str(args.get("file_id") or "").strip()
    if not fid:
        return err("file_id is required")
    loaded = await _load_bytes(
        str(args.get("staging_token") or ""),
        str(args.get("file_path") or ""),
        str(args.get("filename") or ""),
    )
    if isinstance(loaded, dict):
        return loaded
    raw, use_filename = loaded
    safe_name, name_err = _safe_name(use_filename)
    if name_err:
        return name_err
    collection = str(args.get("collection") or "default")
    commit = str(args.get("commit_message") or "")

    payload = await api.post_multipart(
        f"/file-mgmt/{_q('collection', collection)}/files/{_q('file_id', fid)}/versions",
        data={"commit_message": commit},
        files={"file": (safe_name, raw)},
        timeout=UPLOAD_TIMEOUT,
    )
    if not isinstance(payload, dict):
        return err(f"remote upload returned unexpected payload: {payload!r:.120}")
    return ok(
        message="New version uploaded; ingest may be async",
        collection=collection,
        file=payload,
        file_id=fid,
        task_id=payload.get("task_id"),
    )


async def upload_meeting_audio_forward(api: RemoteApi, args: dict[str, Any]) -> dict[str, Any]:
    from src.mcp.staging import staging_store

    token = str(args.get("staging_token") or "")
    meeting_id = str(args.get("meeting_id") or "")
    entry = await staging_store.take(token)
    if entry is None:
        return err(
            f"Staging token '{token}' not found or expired. "
            "Tokens expire after 10 minutes. Re-stage the audio and try again."
        )

    payload = await api.post_multipart(
        f"/meetings/{_q('meeting_id', meeting_id)}/upload-audio",
        files={"file": (entry.filename or "audio.webm", entry.content)},
        timeout=UPLOAD_TIMEOUT,
    )
    # REST 回 _serialize_meeting(updated)——audio_path/status 都在 model_dump 里；
    # filename/size_bytes 本地已知（staged entry）。
    if not isinstance(payload, dict):
        return err(f"remote upload returned unexpected payload: {payload!r:.120}")
    return ok(
        message="Audio uploaded via staging",
        meeting_id=meeting_id,
        audio_path=payload.get("audio_path"),
        filename=entry.filename,
        size_bytes=len(entry.content),
        status=payload.get("status"),
    )


def _q(name: str, value: Any) -> str:
    return quote_segment(name, str(value or ""))


__all__ = [
    "UPLOAD_TIMEOUT",
    "upload_document_forward",
    "upload_file_forward",
    "upload_file_version_forward",
    "upload_meeting_audio_forward",
]
