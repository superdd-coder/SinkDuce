"""写类工具的**自定义多步转发器**（批次 2）。

`@hub_routed(forward=...)` 的单请求模型装不下的三类工具住在这里：

1. **批量语义** —— ``create_todo`` / ``update_todo`` / ``delete_todo`` 的工具契约
   是「一次调用、多条目、聚合 errors」（``todos`` / ``updates`` / ``todo_ids``
   列表），而 REST 是单条端点 → 循环 N 次 + 按本地实现**同构**聚合。
2. **两步序列** —— ``create_note(collection, title, content)``：REST 的 create
   **不接受 content**，要 POST 建笔记再 PUT 写内容（PUT 才有引用同步）。
3. **先读后写** —— ``cancel_task`` / ``retry_task``：REST 把「不存在 / 已完成 /
   状态不对」合并成一档 400，而工具契约要**区分**（已完成 → 温和的
   ``cancelled:false``；只有真失败才报错）。先 GET 状态再决定。

**忠实性纪律**（与 routing.py 同源）：
- 路径参数一律走 :func:`src.hub.routing.quote_segment`（防穿越规则只有一份实现）；
- 「目标不存在 → 静默创建」是写类头号陷阱，collection 维度由 ``precheck=``
  在转发前拦（见各工具接线处），这里的转发器假定 collection 已验过；
- 错误文案尽量**从工具模块导入同一常量**（``_ME_UNSET`` 等），两侧永不漂移；
- 聚合信封逐字段复刻本地实现（见各工具 ``_run``），不发明新形状。
"""

from __future__ import annotations

from typing import Any

from src.hub.client import HubCallError
from src.hub.routing import RemoteApi, quote_segment
from src.mcp.common import err, ok


def _q(name: str, value: Any) -> str:
    """quote_segment 的薄封装：非字符串先 str()。"""
    return quote_segment(name, str(value or ""))


# ── People-Me：在**目标盒子**上解析 ────────────────────────────


async def require_fm_collection_on_remote(
    remote: Any, args: dict[str, Any]
) -> str | None:
    """file-mgmt 类工具（读+写）的前置校验 = 本地 ``_require_fm_collection`` 的远端复现。

    两道：
    1. 库存在，且以 **ID** 给出（file-mgmt 端点只认 ID——`get_collection_meta`
       按路径取 meta.json，显示名 404。比共享的 ``_require_collection_on_remote``
       更严：那个连显示名也放行，是给 notes 那类宽松端点的）；
    2. **file-mgmt DB 已初始化** —— 实测抓到的静默创建陷阱：REST 的 file-mgmt
       端点经 ``_open_db`` **幂等自动建库**，而本地 MCP 工具对未初始化的库是
       **拒绝**。不加这道，同一个调用本地报错、转发却悄悄替远端建库（读端点
       也会建：list_todos/list_folders/list_groups 全走 _open_db）。
       探测走 ``GET /file-mgmt/{c}/db-status``（纯读，绝不触发建库）。
    """
    col = str(args.get("collection") or "").strip()
    if not col:
        return None
    cols = await remote.get("/collections")
    if isinstance(cols, list):
        by_id = {str(c.get("id")) for c in cols if isinstance(c, dict)}
        by_name = {str(c.get("name")) for c in cols if isinstance(c, dict)}
        if col not in by_id:
            if col in by_name:
                return f"Collection '{col}' is a display name — use its collection ID"
            return f"Collection '{col}' does not exist"
    # /collections 拿不到（连不上/无权限）→ 不在这里下结论，交给 db-status /
    # 正式调用去报真正的原因；db-status 探不到则下方 fail-closed 拒绝。
    status = await remote.get(f"/file-mgmt/{quote_segment('collection', col)}/db-status")
    if isinstance(status, dict) and status.get("initialized") is True:
        return None
    if isinstance(status, dict) and status.get("initialized") is False:
        return (
            f"File-management DB not available for collection '{col}': "
            "meta.db not found. Has init_collection_db been called?"
        )
    return (
        f"cannot verify file-management DB state for collection '{col}' on this box "
        "(its /file-mgmt/{{c}}/db-status endpoint is unavailable — the box may be "
        "running an older version). Refusing rather than risking an implicit "
        "database creation; update that box or run this on it directly."
    )


async def _remote_me_person_id(api: RemoteApi) -> str | None:
    """目标盒子的 People-Me person_id（拿不到 = None，调用方按 Me 未设置处理）。

    本地 ``_me_person_id()`` 读的是**本机**全局设置；转发时 Me 必须在**远端**
    解析——「我的待办」指的是**那台盒子主人**的"我"，不是 Hub 这边的人。
    """
    try:
        payload = await api.get("/speakers/me")
    except HubCallError:
        return None
    if isinstance(payload, dict):
        pid = payload.get("person_id")
        if pid:
            return str(pid)
    return None


async def _resolve_assignee_remote(
    api: RemoteApi, assign_to_me: bool, assignee_person_id: str, me_cache: dict
) -> tuple[str | None, dict[str, Any] | None]:
    """远端版 ``_resolve_assignee``：显式 pid 优先；``assign_to_me`` 用远端 Me。

    返回形状与本地版一致 ``(assignee, err_payload)``，文案同源（``_ME_UNSET``）。
    ``me_cache`` 是本次调用内的单次缓存 dict（多条待办只查一次远端 Me）。
    """
    explicit = (assignee_person_id or "").strip() or None
    if explicit:
        return explicit, None
    if assign_to_me:
        if "me" not in me_cache:
            me_cache["me"] = await _remote_me_person_id(api)
        if not me_cache["me"]:
            from src.mcp.tools.file_mgmt import _ME_UNSET

            return None, err(_ME_UNSET)
        return me_cache["me"], None
    return None, None


# ── create_note：POST + （有 content 时）PUT ──────────────────


async def create_note_forward(api: RemoteApi, args: dict[str, Any]) -> dict[str, Any]:
    collection = str(args.get("collection") or "")
    title = str(args.get("title") or "").strip()
    content = args.get("content")

    payload = await api.post(
        f"/notes/{_q('collection', collection)}",
        # REST create 只认 title；空标题由远端按自己的默认（时间戳）补，
        # 与本地 ``title.strip() or datetime.now().strftime(...)`` 同语义。
        json_body={"title": title} if title else {},
    )
    if not isinstance(payload, dict) or not payload.get("id"):
        return err("remote note creation returned no id")
    if content:
        # REST 的 PUT 才带引用同步（:::distill-block 解析 + 双向引用簿记），
        # 与本地 ``_save_content_with_refs`` 同一条路径。
        await api.put(
            f"/notes/{_q('collection', collection)}/{_q('note_id', payload['id'])}",
            json_body={"content": content},
        )
    return ok(
        id=payload.get("id"),
        title=payload.get("title"),
        collection=payload.get("collection", collection),
        created_at=payload.get("created_at"),
        updated_at=payload.get("updated_at"),
        has_content=bool(content),
    )


# ── cancel_task / retry_task：先 GET 状态，按工具契约分流 ─────


async def cancel_task_forward(api: RemoteApi, args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "")
    path = f"/documents/tasks/{_q('task_id', task_id)}"
    try:
        task = await api.get(path)
    except HubCallError as exc:
        if exc.status == 404:
            return err(f"Task '{task_id}' not found")
        raise
    status = str((task or {}).get("status") or "")

    if status in ("completed", "failed"):
        # 工具契约：已结束的任务 → 温和的 ``cancelled:false``（幂等），
        # 不是错误。REST 直接 POST 会吃一个 400 —— 那是**另一个语义**。
        return ok(
            task_id=task_id,
            cancelled=False,
            message=f"Task is already {status}",
        )

    try:
        await api.post(f"{path}/cancel")
    except HubCallError as exc:
        if exc.status == 400:
            # GET 与 POST 之间任务恰好收尾（罕见竞态）：REST 只会笼统地说
            # "cannot be cancelled"。如实转告 + 不建议重试。
            return err(
                f"Task '{task_id}' could not be cancelled ({exc.detail}). "
                "Do not retry; check list_tasks for its current state.",
                code="task_not_cancellable",
            )
        raise
    # 与本地同口径：cancel_task 成功后任务状态就是 failed（task_manager 的
    # 协作取消把 status 置 FAILED）。报 POST **之前**读到的 processing/pending
    # 会让轮询 "failed" 的调用方一直等（批次 2 审查抓到的形状漂移）。
    return ok(task_id=task_id, cancelled=True, status="failed")


async def retry_task_forward(api: RemoteApi, args: dict[str, Any]) -> dict[str, Any]:
    task_id = str(args.get("task_id") or "")
    path = f"/documents/tasks/{_q('task_id', task_id)}"
    try:
        task = await api.get(path)
    except HubCallError as exc:
        if exc.status == 404:
            return err(f"Task '{task_id}' not found")
        raise
    status = str((task or {}).get("status") or "")

    if status != "failed":
        # 工具契约：非 failed 的任务要**带上当前状态**报错（模型据此决定
        # 等待还是放弃）；REST 的 400 不含状态，等于让模型盲猜。
        return err(
            f"Task '{task_id}' is {status}, only failed tasks can be retried",
            status=status,
        )

    try:
        await api.post(f"{path}/retry")
    except HubCallError as exc:
        if exc.status == 400:
            # REST 的 400 在这里只剩一种含义：原任务参数已不可得。
            return err(
                f"Cannot retry task '{task_id}' (original arguments no longer available)"
            )
        raise
    return ok(task_id=task_id, status="pending", message="Re-queued for processing")


# ── todo 三件套：批量循环 + 同构聚合 ──────────────────────────


async def create_todo_forward(api: RemoteApi, args: dict[str, Any]) -> dict[str, Any]:
    col = str(args.get("collection") or "").strip()

    items: list[dict[str, Any]] = []
    if isinstance(args.get("todos"), list):
        items.extend(x for x in args["todos"] if isinstance(x, dict))
    title = str(args.get("title") or "").strip()
    if title:
        items.insert(
            0,
            {
                "title": title,
                "body": args.get("body") or "",
                "ddl": args.get("ddl") or "",
                "assign_to_me": bool(args.get("assign_to_me") or False),
                "assignee_person_id": args.get("assignee_person_id") or "",
            },
        )
    if not items:
        return err("title or todos is required")

    created: list[dict[str, Any]] = []
    errors: list[str] = []
    me_cache: dict[str, Any] = {}
    # 与本地实现同构：创建中任何一条的 HTTP 错误**中止整批**（本地是把
    # HTTPException 直接抛出到批次外）；只有「空标题」进 errors 继续。
    for it in items:
        t = str(it.get("title") or "").strip()
        if not t:
            errors.append("empty title")
            continue
        assignee, aerr = await _resolve_assignee_remote(
            api,
            bool(it.get("assign_to_me") or False),
            str(it.get("assignee_person_id") or ""),
            me_cache,
        )
        if aerr:
            return aerr
        body: dict[str, Any] = {"title": t}
        if str(it.get("body") or "").strip():
            body["body"] = str(it.get("body")).strip()
        if str(it.get("ddl") or "").strip():
            body["ddl"] = str(it.get("ddl")).strip()
        if assignee:
            body["assignee_person_id"] = assignee
        todo = await api.post(f"/file-mgmt/{_q('collection', col)}/todos", json_body=body)
        if isinstance(todo, dict):
            created.append(todo)

    if not created:
        return err("; ".join(errors) or "title or todos is required")
    return ok(
        collection=col,
        todo=created[0],
        todos=created,
        total=len(created),
        errors=errors or None,
    )


async def update_todo_forward(api: RemoteApi, args: dict[str, Any]) -> dict[str, Any]:
    # 复用工具本体 的条目→TodoUpdate 字段逻辑（注入远端 assignee 解析器），
    # 保证「clear_body / clear_ddl / assign_to_me 优先级」这些细节永不漂移。
    from src.mcp.tools.file_mgmt import _todo_update_fields

    col = str(args.get("collection") or "").strip()

    items: list[dict[str, Any]] = []
    if isinstance(args.get("updates"), list):
        items.extend(x for x in args["updates"] if isinstance(x, dict))
    if str(args.get("todo_id") or "").strip():
        items.insert(
            0,
            {
                "todo_id": args.get("todo_id"),
                "title": args.get("title") or "",
                "body": args.get("body") or "",
                "clear_body": bool(args.get("clear_body") or False),
                "ddl": args.get("ddl") or "",
                "clear_ddl": bool(args.get("clear_ddl") or False),
                "done": args.get("done"),
                "assignee_person_id": args.get("assignee_person_id") or "",
                "assign_to_me": bool(args.get("assign_to_me") or False),
                "clear_assignee": bool(args.get("clear_assignee") or False),
            },
        )
    if not items:
        return err("todo_id or updates is required")

    me_cache: dict[str, Any] = {}
    fetched: list[dict[str, Any]] | None = None  # 懒加载：仅「空字段项」需要回读

    async def _resolve_remote(to_me: bool, pid: str):
        return await _resolve_assignee_remote(api, to_me, pid, me_cache)

    patched: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for it in items:
        tid = str(it.get("todo_id") or "").strip()
        if not tid:
            errors.append({"todo_id": "", "error": "todo_id is required"})
            continue
        fields, aerr = _todo_update_fields(it, resolve_assignee=_resolve_remote)
        if aerr:
            return aerr
        if not fields:
            # 工具契约：给了 id 但没给任何字段 → 返回**当前** todo（幂等读）。
            # REST 没有单条 GET，回读一次列表（懒加载，只在需要时发生）。
            if fetched is None:
                try:
                    fetched = await api.get(f"/file-mgmt/{_q('collection', col)}/todos")
                except HubCallError as exc:
                    return err(str(exc.detail or exc), code=exc.code or "hub_call_failed")
                fetched = fetched if isinstance(fetched, list) else []
            current = next(
                (t for t in fetched if isinstance(t, dict) and t.get("todo_id") == tid),
                None,
            )
            if current is None:
                # 与本地**同构**：本地的「无字段回读」在 get_todo 抛 404 时不被
                # 逐项 try 包住 → 整个调用变错误（批次 2 审查抓到的信封漂移：
                # 逐项继续会把它变成「成功 + errors」）。
                return err(f"Todo '{tid}' not found", status_code=404)
            patched.append(current)
            continue
        try:
            todo = await api.patch(
                f"/file-mgmt/{_q('collection', col)}/todos/{_q('todo_id', tid)}",
                json_body=fields,
            )
            if isinstance(todo, dict):
                patched.append(todo)
        except HubCallError as exc:
            # 与本地同构：单条失败（远端答复了 4xx/5xx）→ 记入 errors 继续；
            # 整台盒子失联（status None）→ 抛给外层报「盒子连不上」。
            if exc.status is None:
                raise
            errors.append({"todo_id": tid, "error": exc.detail or str(exc)})

    if not patched and errors:
        return err(errors[0]["error"], results=errors)
    return ok(
        collection=col,
        todo=patched[0] if patched else None,
        todos=patched,
        total=len(patched),
        errors=errors or None,
    )


async def delete_todo_forward(api: RemoteApi, args: dict[str, Any]) -> dict[str, Any]:
    col = str(args.get("collection") or "").strip()

    ids: list[str] = []
    seen: set[str] = set()
    for raw in list(args.get("todo_ids") or []) + [args.get("todo_id")]:
        tid = str(raw or "").strip()
        if tid and tid not in seen:
            seen.add(tid)
            ids.append(tid)
    if not ids:
        return err("todo_id or todo_ids is required")

    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    for tid in ids:
        try:
            await api.delete(f"/file-mgmt/{_q('collection', col)}/todos/{_q('todo_id', tid)}")
            deleted.append(tid)
        except HubCallError as exc:
            if exc.status is None:
                raise
            errors.append({"todo_id": tid, "error": exc.detail or str(exc)})

    if not deleted and errors:
        return err(errors[0]["error"], results=errors)
    return ok(
        collection=col,
        deleted=True,
        todo_id=deleted[0] if len(deleted) == 1 else None,
        todo_ids=deleted,
        errors=errors or None,
        total=len(deleted),
    )


__all__ = [
    "cancel_task_forward",
    "create_note_forward",
    "create_todo_forward",
    "delete_todo_forward",
    "require_fm_collection_on_remote",
    "retry_task_forward",
    "update_todo_forward",
]
