"""MCP server — registers atomic tools and exposes the HTTP sub-app.

Tool inventory by domain:

Collections (5):
    list_collections, get_collection, create_collection,
    update_collection_config, delete_collection

Documents (6) — legacy file-index / Qdrant-oriented:
    list_documents, upload_document_from_staging,
    delete_document, get_file_chunks, get_document_text,
    set_document_definitive

File Management L1 (17) — library tree + timeline + folder/file/version + upload + todos:
    list_library_tree, get_timeline, list_folders, list_files, get_file,
    list_file_versions, list_chains, get_chain, get_node, list_groups,
    list_todos, create_todo, update_todo, delete_todo,
    upload_file_from_staging, upload_file_version_from_staging,
    set_file_definitive

Search (3):
    search_direct_chunks, search_agentic_chunks, get_query_history

Tasks (5):
    get_task_status, list_tasks, cancel_task, retry_task, clear_completed_tasks

Summaries (4):
    get_collection_summary, get_doc_summary, get_conflicts, trigger_consolidate

Notes (6):
    list_notes, get_note, create_note, update_note, delete_note, trigger_propagation

Meetings (11):
    list_meetings, list_meeting_catalog, get_meeting, get_section, get_meeting_transcript,
    create_meeting, update_meeting, delete_meeting,
    start_meeting_summary, upload_meeting_audio_from_staging, lookup_meeting_transcript

Hot Words (5):
    list_hot_words_libraries, get_hot_words_library, create_hot_words_library,
    update_hot_words_library, delete_hot_words_library

Total: 5 + 6 + 17 + 3 + 5 + 4 + 6 + 11 + 5 = 62 tools.

Architecture
------------
The :class:`FastMCP` instance is created at import time. Tool registration
happens via plain imports + :meth:`FastMCP.add_tool` calls in this file,
so the tool list is discoverable in one place.

The sub-app is mounted by ``src.main`` under ``/mcp`` and shares the main
app's lifespan (services, task_manager) — no MCP-specific lifespan is
required.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Server-level agent guide (shown with tools/list / initialize).
#
# **身份头是动态的**（见下方 ``_install_dynamic_identity``）：静态部分只保留"这个
# 服务是什么"，"你在跟哪个盒子说话"每次 initialize 时按当次请求算 —— 因为同一个
# Agent 可能同时连多个 SinkDuce 盒子（本机 + 若干远端），而它们的工具名、描述
# 全都一样。不给身份标识，模型分不清答案来自谁（2026-09-18 用户提出）。
_MCP_INSTRUCTIONS_BODY = """
What lives here: collections of documents (a versioned file library with folders,
plus a timeline of events), distilled notes, meeting records (transcripts and
summaries), to-dos, and hybrid search over all of it. Every piece of content was
captured by this user through the SinkDuce app — nothing here comes from the
public web.

## When to use this server

Reach for these tools whenever a task touches the user's own materials — often
the right first stop before answering from general knowledge:

- "What do my documents / notes / meetings say about X?" → search here.
- The user mentions something they saved, uploaded, recorded, or discussed in a
  meeting → look it up here instead of guessing.
- "What do I have about X?" / "Where is file Y?" → search or browse.
- Capturing knowledge: upload files, create or update notes, log meeting
  outcomes, create to-dos.
- Organizing: folders, file versions, definitive flags, collection config.

For public web facts prefer web search tools — this server only knows what the
user put into it.

## Route by goal (pick the matching tool — do not default to list_library_tree)

| Goal | Use first | Avoid |
|------|-----------|-------|
| Content facts / "what does X say" | **search_direct_chunks** (simple) or **search_agentic_chunks** (multi-hop) | list_library_tree, get_timeline, list_documents as first step |
| Collection overview | **get_collection_summary** | browsing whole tree |
| What was said in a meeting | **lookup_meeting_transcript** (spoken words) or **get_section** (summary sections); resolve IDs via **list_meeting_catalog** | paging get_meeting_transcript from the start |
| Distilled notes on a topic | **list_notes** → **get_note** | searching chunks when a note already covers it |
| Contradictions across documents | **get_conflicts** | — |
| Folder/file **layout** map | **list_library_tree** | search tools; list_folders + N× list_files; list_documents |
| Flat unique file list + mounts | **list_files**(scope=all) | list_documents; list_library_tree when you only need a flat list |
| Timeline / events / node graph | **get_timeline** | list_library_tree; list_chains + N× get_chain |
| One node attachments/messages | **get_node** | — |
| Collection to-dos (open/mine/all) | **list_todos** | list_library_tree; get_timeline |
| Create / edit / complete a to-do | **create_todo** / **update_todo** | — |
| Delete a to-do (destructive) | **delete_todo** | confirm with the user first |
| Known file full text | **get_document_text**(file_id=…) | inventing text; browsing tree first |
| What was indexed for a file | **get_file_chunks**(file_id=…) | — |
| Version history / blob_available | **list_file_versions** | get_document_text on every version blindly |
| File paths + linked nodes/messages | **get_file** | — |
| "My other box / other device / another machine" | **list_boxes** → **switch_box** | assuming everything lives here |
| "Search everything / all boxes" | **search_all_boxes** (grouped per box) | switch_box + N separate searches |

## Rules of thumb

1. Resolve collection **ID** with list_collections when unknown; resolve meeting IDs with list_meeting_catalog / list_meetings.
1b. **This box is a Hub**: `list_boxes` shows the other boxes it is linked to and which
   one is *current*; `switch_box` changes it (pass `"local"` to come back). Read the
   `tool_routing` / `local_only_tools` fields it returns before assuming a switch moved
   your data source — tools that are not yet forwarded keep acting on this machine.
   `search_all_boxes` searches every box at once and returns results **grouped per box**;
   its scores are not comparable between boxes, so never merge them into one ranking.
2. Content questions → **search first**. list_library_tree is only for library layout.
3. Prefer **file_id** over hand-built source strings. Canonical Qdrant source is ``__file__:{file_id}`` (``file:{id}`` is an accepted alias).
4. list_library_tree: ``file_count`` is always real; ``files=[]`` + ``truncated=true`` means payload omitted, not empty folder.
5. get_document_text: only pin version_id when list_file_versions shows blob_available=true; else extract_status=blob_missing. Chunks are the **current index only**.
6. Multi-mount: one file_id can appear under several folders/nodes — not multiple copies.
7. After MCP code deploy, restart this server and refresh the client tool schema.

Write tools (upload_*, delete_*, create_*) change data — confirm intent before destructive calls.
""".strip()


def _build_instructions(box_name: str, is_local: bool) -> str:
    """拼身份头 + 静态主体。

    **本地 vs 远端必须说清**：两者的权限模型完全不同 —— 本机走回环即 owner
    （无钥匙、全权），远端是对方签发的受限钥匙。原来的文案写死 "running locally
    on their machine"，在远端连接时**是假的**，会误导模型（以为能拿到本机数据）。
    """
    if is_local:
        head = (
            f'SinkDuce MCP — the knowledge base of **{box_name}**.\n'
            f'You are running on that same machine, connected over loopback; you have '
            f'full owner access and no key is involved.\n'
        )
    else:
        head = (
            f'SinkDuce MCP — the knowledge base of the remote SinkDuce box '
            f'named **{box_name}**.\n'
            f'You reached it over the network. This is **not** the machine you run on — '
            f'it is a different box whose owner gave you a key. Your access is limited '
            f'to what that key allows; if a call is refused for permissions, say so and '
            f'do not retry the same call.\n'
        )
    multi = (
        # 不再是「如果你连了多个盒子」——Hub 让**这一台**就能代表多台，
        # 所以来源标注从「可选礼貌」变成「默认要求」。
        '\nThis box can reach other SinkDuce boxes (see list_boxes). Whenever content '
        'came from somewhere other than this machine, say which box it came from, and '
        'never mix two boxes\' contents together.\n'
    )
    return head + multi + "\n" + _MCP_INSTRUCTIONS_BODY


# Pinned to mcp>=1.26,<2 (see pyproject.toml). Do not switch to MCPServer
# unless the whole monorepo upgrades mcp major in a dedicated PR.
#
# ── stateless_http=False（2026-09-18 二次拍板，从 stateless 切回）──
# 前一天切 stateless 是为了修两件事，今天两条的**危害前提都消失了**：
#
#   1. 身份冻结：有状态会话在 initialize 时起一个长生命周期任务跑整个会话，
#      身份（contextvar）被冻结在握手那一刻 → 换钥匙/改角色对已建会话无效。
#   2. 会话劫持 = 提权：SDK 的会话归属校验只在启用它自带 auth 时生效，我们没用，
#      于是任何钥匙都能借用别人会话的身份。
#
# **两者的前提都是「MCP 面上存在多个身份」。** 现在 /mcp 只认回环（见
# ``src/pairing/middleware.py`` 的 /mcp 收敛回环），能进来的恒为 owner：
# 冻结的是 owner、能劫到的也是 owner，而 owner 本来就是本机能拿到的东西。
# 前提消失 ⇒ 危害消失。
#
# ⚠️ **顺序是承重的**：切回 stateful 必须与「/mcp 只认回环」同时成立。
# 如果 MCP 还暴露在局域网上而又切成有状态，会话劫持立刻复活（那时存在 member /
# visitor 等更低身份，劫持才有意义）。将来做云端中继放开 MCP 之前，
# 必须先补调用者权限判定，再谈放开。
#
# 顺带收益（都要靠会话级状态）：
#   - ``elicit``（MCP 原生「服务端问用户」）在 stateless 下不可用 —— 将来写操作确认
#     多一条路。**机制已实测定位**（不是"卡住"那么含糊）：elicit 需要服务端**反向**
#     向客户端发请求，客户端把结果回传时落在**另一条连接**上，而 stateless 每条请求
#     用完即弃、没有维系 pending request 的地方，于是服务端认不出这个响应
#     （服务端日志：``Received response with an unknown request ID``），结果被丢弃，
#     客户端一直等 → 超时。有状态会话有那条反向通道，所以正常。
#   - Hub 的 ``switch_box``「当前盒子」有地方存（会话级状态，天然合适）；
#     stateless 下它是无主的，得每次调用都带 box_id。
#
# 代价：断线续传不需要了，但**服务端重启会让旧 session 失效**（客户端重连即愈，
# 不是 bug）。
#
# ── transport_security（DNS rebinding 保护）────────────────────
# SDK 内建一层**静态** Host 白名单（只支持精确匹配和 `host:*` 前缀）。这里让它
# 保持关闭，由配对门卫（`src/pairing/middleware.py`）做等价的**动态**校验：
#   - Host 必须是回环或本机自身地址（防 DNS rebinding：攻击者页面 Host 仍是 evil.com）；
#   - Origin 必须是本机来源或缺省（浏览器跨源必带 Origin，脚本没有）；
#   - /mcp 还额外要求「是本机」（上面那条不变式）。
# 即：等价保护 + 跟随实际地址（本机地址会漂移、桌面壳端口是 18910 而非 18900，
# 静态清单只会带来「配错就 421」）。
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    name="sinkduce",
    instructions=_build_instructions("SinkDuce", True),  # 占位，真值见下方动态覆盖
    stateless_http=False,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


def _install_dynamic_identity() -> None:
    """把 ``initialize`` 的 serverInfo.name / instructions 变成**按当次请求算**。

    **为什么要动态**（2026-09-18 用户提出）：一个 Agent 可能同时连多个 SinkDuce
    盒子（本机 + 若干远端）。所有盒子的工具名、工具描述**完全一样**，而协议里
    ``tools/call`` 只带工具名、不带盒子标识 —— 模型无法从工具清单分辨"这个
    ``list_collections`` 是哪个盒子的"。所以身份只能从**握手时**注入：

    - ``serverInfo.name`` → 客户端通常会拼进工具名（如 Claude Code 的
      ``mcp__<server>__<tool>``），名字带上盒子名就天然区分开了；
    - ``instructions`` → 模型在 initialize 时读一次，明确知道"我在跟哪个盒子说话、
      是本机还是远端、权限边界在哪"。

    **为什么覆盖方法而不是改实例属性**：多个会话可以同时在世（每个会话一个任务），
    改 ``mcp.name``/``instructions`` 会有竞态（A 会话读到 B 的值）。
    在方法内**基于当次 context 计算并返回**，不写实例状态 → 无竞态。

    **为什么能读到当次请求**：会话任务由 ``SessionManager.handle_request`` 里的
    ``task_group.start()`` 起，而它会 ``copy_context()`` 继承调用方（= 当次 HTTP
    请求）的 ``contextvars``，所以门卫在请求里设的 actor 在这里可见。

    注：``/mcp`` 已收敛回环（见 ``src/pairing/middleware.py``），所以 ``is_local``
    现在恒为真 —— 这套动态逻辑**不再是承重件**，保留是因为它无害、且将来若经中继
    放开还能直接用。
    """
    from mcp.server.lowlevel.server import InitializationOptions  # noqa: F401

    server = mcp._mcp_server
    original = server.create_initialization_options

    def _dynamic_create_init(*args, **kwargs):
        opts = original(*args, **kwargs)
        try:
            from src.identity import get_actor
            from src.pairing.service import get_box_info

            box_name = str((get_box_info() or {}).get("box_name") or "").strip()
            box_name = box_name or "SinkDuce"
            actor = get_actor()
            is_local = actor is not None and getattr(actor, "kind", "") == "local"
            opts.server_name = f"SinkDuce-{box_name}"
            opts.instructions = _build_instructions(box_name, is_local)
        except Exception:
            # 身份注入失败不能挡住 MCP 本身 —— 保留默认（静态）值即可。
            logger.exception("dynamic MCP identity failed; keeping static defaults")
        return opts

    server.create_initialization_options = _dynamic_create_init


_install_dynamic_identity()

# ── Resolve base URL for docs that mention the HTTP API ──────
from src.config import get_config as _get_config
_cfg = _get_config()
_base_url = f"http://localhost:{_cfg.server.api_port}"

# ── Collections ──────────────────────────────────────────────
from src.mcp.tools.collections import (
    list_collections,
    get_collection,
    create_collection,
    update_collection_config,
    delete_collection,
)
for _t in (list_collections, get_collection, create_collection, update_collection_config, delete_collection):
    mcp.add_tool(_t)

# ── Documents ────────────────────────────────────────────────
from src.mcp.tools.documents import (
    list_documents,
    upload_document_from_staging,
    delete_document,
    get_file_chunks,
    get_document_text,
    set_document_definitive,
)
for _t in (list_documents, upload_document_from_staging, delete_document, get_file_chunks, get_document_text, set_document_definitive):
    desc = _t.__doc__
    if desc and "{base_url}" in desc:
        desc = desc.replace("{base_url}", _base_url)
    mcp.add_tool(_t, description=desc)

# ── File Management (L1) ─────────────────────────────────────
from src.mcp.tools.file_mgmt import (
    list_library_tree,
    get_timeline,
    list_folders,
    list_files as fm_list_files,
    get_file,
    list_file_versions,
    list_chains,
    get_chain,
    get_node,
    list_groups,
    list_todos,
    create_todo,
    update_todo,
    delete_todo,
    upload_file_from_staging,
    upload_file_version_from_staging,
    set_file_definitive,
)
# list_files is registered under the function name; import alias avoids
# clashing with documents helpers if any. Tool public name = fn.__name__.
for _t in (
    list_library_tree,
    get_timeline,
    list_folders,
    fm_list_files,
    get_file,
    list_file_versions,
    list_chains,
    get_chain,
    get_node,
    list_groups,
    list_todos,
    create_todo,
    update_todo,
    delete_todo,
    upload_file_from_staging,
    upload_file_version_from_staging,
    set_file_definitive,
):
    desc = _t.__doc__
    if desc and "{base_url}" in desc:
        desc = desc.replace("{base_url}", _base_url)
    mcp.add_tool(_t, description=desc)

# ── Search & Query ───────────────────────────────────────────
from src.mcp.tools.search import (
    search_direct_chunks,
    search_agentic_chunks,
    get_query_history,
)
for _t in (search_direct_chunks, search_agentic_chunks, get_query_history):
    mcp.add_tool(_t)

# ── Tasks ────────────────────────────────────────────────────
from src.mcp.tools.tasks import (
    get_task_status,
    list_tasks,
    cancel_task,
    retry_task,
    clear_completed_tasks,
)
for _t in (get_task_status, list_tasks, cancel_task, retry_task, clear_completed_tasks):
    mcp.add_tool(_t)

# ── Summaries ────────────────────────────────────────────────
from src.mcp.tools.summaries import (
    get_collection_summary,
    get_doc_summary,
    get_conflicts,
    trigger_consolidate,
)
for _t in (get_collection_summary, get_doc_summary, get_conflicts, trigger_consolidate):
    mcp.add_tool(_t)

# ── Notes ────────────────────────────────────────────────────
from src.mcp.tools.notes import (
    list_notes,
    get_note,
    create_note,
    update_note,
    delete_note,
    trigger_propagation,
)
for _t in (list_notes, get_note, create_note, update_note, delete_note, trigger_propagation):
    mcp.add_tool(_t)

# ── Meetings ─────────────────────────────────────────────────
from src.mcp.tools.meetings import (
    list_meetings,
    list_meeting_catalog,
    get_meeting,
    get_section,
    get_meeting_transcript,
    create_meeting,
    update_meeting,
    delete_meeting,
    start_meeting_summary,
    upload_meeting_audio_from_staging,
    lookup_meeting_transcript,
)
for _t in (list_meetings, list_meeting_catalog, get_meeting, get_section, get_meeting_transcript, create_meeting, update_meeting, delete_meeting, start_meeting_summary, upload_meeting_audio_from_staging, lookup_meeting_transcript):
    desc = _t.__doc__
    if desc and "{base_url}" in desc:
        desc = desc.replace("{base_url}", _base_url)
    mcp.add_tool(_t, description=desc)

# ── Hot Words ────────────────────────────────────────────────
from src.mcp.tools.hot_words import (
    list_hot_words_libraries,
    get_hot_words_library,
    create_hot_words_library,
    update_hot_words_library,
    delete_hot_words_library,
)
for _t in (list_hot_words_libraries, get_hot_words_library, create_hot_words_library, update_hot_words_library, delete_hot_words_library):
    mcp.add_tool(_t)

# ── Hub (federation) ─────────────────────────────────────────
# 「本机盒子的 MCP 就是 Hub」：Agent 只连它，它再用远端盒子的钥匙走 REST 代理其余。
# 这两个原语本身不操作任何盒子（它们只读连接清单 / 记下当前选择），所以不受
# 「转发等价性」那套约束（见 src/hub/tools.py 的模块 docstring）。
from src.hub.tools import list_boxes, search_all_boxes, switch_box
for _t in (list_boxes, switch_box, search_all_boxes):
    mcp.add_tool(_t)

# 本进程注册的**全部工具名**。
#
# 用途：Hub 的 list_boxes / switch_box 要如实告诉模型「哪些工具在切盒子后仍打本机」。
# **必须从 mcp 自身导出而不是手写清单** —— 手写的清单会随工具增减而过时，而
# 过时的清单会直接变成**对模型的谎话**（它据此以为某个工具已经切到别的盒子上）。
MCP_TOOL_NAMES: tuple[str, ...] = tuple(sorted(t.name for t in mcp._tool_manager.list_tools()))


def _refusal_result(text: str):
    """守卫拒绝时返回的 CallToolResult，**形状必须匹配工具的 outputSchema**。

    工具声明了 ``outputSchema``（FastMCP 按返回注解推断；本仓 46 个工具是
    ``{"required": ["result"]}``），而 **MCP 客户端会校验** structuredContent ——
    返回 ``{"error": ...}`` 这种不匹配的形状会被判为
    ``RuntimeError: Invalid structured content returned by tool ...``，
    把一次正确的拒绝伪装成协议错误（实测：真客户端 SDK 直接抛）。

    正确形状就是 FastMCP 对 ``-> str`` 工具自己会产出的那个：把字符串包进
    ``{"result": ...}``（见 ``FuncMetadata.convert_result`` 的 ``wrap_output`` 分支）。
    另外 10 个工具的输出 schema 是开放对象（``additionalProperties: true``），
    9 个没有 outputSchema —— 三者都接受 ``{"result": text}``，所以这里统一用它。
    """
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        # 形状必须是 {"result": text}（匹配 outputSchema，见上一段），
        # 但 **isError=True** —— 这是"这次调用**没能执行**"，正是 isError 的语义。
        # 只带结构化文本而 isError=False 时，按 isError 判定的客户端会把"被拒绝"
        # 读成"成功但结果为空"（实测过）。structuredContent 里也带上 error 键，
        # 让按 `structuredContent.error` 判定的客户端同样能识别。
        structuredContent={"result": text, "error": text},
        isError=True,
    )


# ── ★ 跨盒子守卫：切到远端后，**没接转发**的工具必须拒绝而不是静默打本机 ──
#
# **为什么必须有**（2026-09-18 复盘发现的真实漏洞）：
# 转发目前只接了少数工具（见 src/hub/routing.py）。若 `switch_box("Office")` 之后
# 调用一个**没接转发**的工具（例如 `delete_note`），它会照旧在**本机**执行：
#
#   - Agent / 用户以为在操作 Office；
#   - 确认卡（第 5 步）也会照常弹，用户点了「允许」以为删的是 Office 的笔记；
#   - 实际上删的是**本机**同 id 的笔记 —— 本地没有就报个错，**本地恰好有就是删错东西**。
#
# 这类「静默作用在错误对象上」比报错危险得多。所以这里**fail-closed**：切到远端后，
# 凡是不在 ROUTES（未接转发）且不是 Hub 原语的工具，一律拒绝，并说明怎么办。
#
# **为什么挂在 `call_tool` 而不是给 61 个工具逐个加装饰器**：这里一处覆盖全部工具，
# 包括**将来新增**的工具 —— 不可能忘。装饰器方案恰恰会在「新加工具时忘了挂」失效，
# 而那正是要防的事。
#
# 本机（默认）路径**完全不变**：`current_box_id` 为空串就直通，零开销、零行为差异。
_original_call_tool = mcp.call_tool


async def _box_scoped_call_tool(name, arguments):
    try:
        from src.hub.routing import HUB_NATIVE_TOOLS, ROUTES
        from src.hub.state import current_box_id

        ctx = mcp.get_context()
        box_id = current_box_id(getattr(ctx, "session", None))
        if not box_id and name not in HUB_NATIVE_TOOLS:
            # 远程模式瘦壳：默认目标（本机）根本不存在——未切盒的内容类
            # 工具必须拒绝并指路，绝不能落进本机执行（services 未初始化）。
            from src.config import is_remote_shell_runtime

            if is_remote_shell_runtime():
                logger.info("refused %s: remote shell, no box selected", name)
                return _refusal_result(
                    f"Remote shell mode: this machine runs no backend, so "
                    f"{name} has no local content to act on. Call switch_box "
                    f"with a box name from list_boxes first, then retry {name}."
                )
        if box_id and name not in ROUTES and name not in HUB_NATIVE_TOOLS:
            from src.hub.routing import NOT_REMOTABLE

            logger.info("refused %s: not forwarded, current box is %s", name, box_id)
            # ⚠️ 这两类要**说清区别**：「还没接转发」（再等等就行）vs
            # 「本质不可远端化」（永远不行）。含糊地说前者会让模型一直重试
            # 一个不可能成功的调用。``NOT_REMOTABLE`` 此前只是文档里的死代码，
            # 这里真正用起来。
            if why := NOT_REMOTABLE.get(name):
                reason = (
                    f"{name} cannot work on another box at all: {why}. "
                    f"It only runs on this machine — call switch_box with "
                    f'name="local" and retry there.'
                )
            else:
                reason = (
                    f"{name} only works on this machine for now, but the current box is "
                    f"{box_id!r} — refusing rather than acting on the wrong box. "
                    f'Call switch_box with name="local" to run it here, or use a tool '
                    f"that supports the current box (see list_boxes.routed_tools)."
                )
            return _refusal_result(reason)
    except Exception:
        # 守卫本身出错时**不能静默落到本机执行**：此时我们连「当前是不是本机
        # 模式」都无法确认，落下去的方向可能是"在错误的盒子上执行"——正是这条
        # 守卫要消灭的危害（2026-09-19 审查：原 fail-open 的失败方向并不总是
        # "回到改造前"，也可能是"作用在错误对象"）。改判**拒绝**并给出恢复
        # 动作：模型切回 local 重试即可，代价是一次重试，而不是删错东西。
        logger.exception("box scope guard failed; refusing instead of falling through")
        return _refusal_result(
            "The box-scope guard hit an internal error and cannot verify which box "
            "this tool would run on, so it refused. Call switch_box with "
            'name="local" and retry; if that still fails, restart the session.'
        )
    return await _original_call_tool(name, arguments)



# ⚠️ **两处都要挂**，原因是一处不够：
#
# 1. `mcp.call_tool = ...`：覆盖 FastMCP 的方法，给**直接调用**它的代码（测试、
#    内部）一致的行为。
# 2. `mcp._mcp_server.call_tool(validate_input=False)(...)`：**真实 MCP 流量的路径**。
#    FastMCP 在**构造时**就执行了 `self._mcp_server.call_tool(...)(self.call_tool)`，
#    把当时的绑定方法封进了 lowlevel handler 的闭包（见
#    `mcp/server/lowlevel/server.py::call_tool`）。所以**事后改属性对线上调用毫无影响**
#    —— 实测：只做第 1 步时单测全绿、真客户端仍能删错东西（正是「测试绿、生产坏」）。
#    重注册一次才会替换 `request_handlers[CallToolRequest]`。
# `validate_input=False` 必须与 FastMCP 原本的取值一致，否则等于顺手改了校验行为。
mcp.call_tool = _box_scoped_call_tool
mcp._mcp_server.call_tool(validate_input=False)(_box_scoped_call_tool)

# Cached ASGI wrapper. MCP SDK's streamable_http_app() creates a *new*
# StreamableHTTPSessionManager on every call and overwrites mcp.session_manager.
# If main mounts one instance and session_lifespan() builds another, the mounted
# app's manager never enters run() → every /mcp request 500s with
# "Task group is not initialized".
_http_app_cache: dict[str, object] = {}


def get_http_app(mount_path: str = "/mcp"):
    """Return a **singleton** ASGI app for the FastMCP Streamable HTTP endpoint.

    Usage in ``src/main.py``::

        from src.mcp.server import get_http_app
        app.add_route("/mcp", get_http_app(), methods=["GET", "POST", "DELETE"])
        app.add_route(
            "/mcp/{path:path}", get_http_app(), methods=["GET", "POST", "DELETE"]
        )

    The returned app wraps the FastMCP sub-app with a small ASGI middleware
    that strips ``mount_path`` from ``scope["path"]`` before delegating to
    the inner app — so the inner route ``/mcp`` can stay unchanged and the
    outer route can match both ``/mcp`` and ``/mcp/anything``.

    Implementation notes
    --------------------
    1. FastMCP's ``streamable_http_app()`` registers its route at ``/mcp``
       without an explicit ``methods=`` argument, so Starlette defaults to
       ``["GET"]``.  MCP Streamable HTTP requires ``POST`` (and optionally
       ``DELETE`` for session termination).  We re-register the inner route
       with ``["GET", "POST", "DELETE"]`` so all three methods work.

    2. ``Starlette.Mount`` builds its path regex as ``<prefix>/{path:path}``
       which forces a trailing slash and never matches the bare mount path
       (e.g. ``/mcp``).  Using ``app.mount("/mcp", subapp)`` therefore drops
       every request that hits exactly ``/mcp``.  To keep the public URL
       clean we expose the sub-app via ``app.add_route`` (no mount) and
       rewrite the path inside the ASGI wrapper.

    3. **Must be singleton**: only one ``streamable_http_app()`` call per
       process so ``session_lifespan()`` runs the same manager the routes use.

    The sub-app shares the main app's lifespan (services and task_manager
    are singletons in ``src.services`` and ``src.tasks.task_manager``).
    """
    from starlette.routing import Route

    key = mount_path.rstrip("/") or "/"
    cached = _http_app_cache.get(key)
    if cached is not None:
        return cached

    base = mcp.streamable_http_app()
    new_routes = []
    replaced = False
    for route in base.router.routes:
        if isinstance(route, Route) and route.path == "/mcp":
            # Mounting via ``app.add_route`` + the ASGI wrapper below rewrites
            # ``scope["path"]`` so the inner app always sees ``/``. Re-register
            # the inner route at ``/`` with all Streamable HTTP methods so MCP
            # clients can connect.
            new_routes.append(
                Route(
                    "/",
                    route.endpoint,
                    methods=["GET", "POST", "DELETE"],
                )
            )
            replaced = True
        else:
            new_routes.append(route)
    if replaced:
        base.router.routes = new_routes

    mount_prefix = key

    class _MCPASGIApp:
        """ASGI wrapper that strips ``mount_prefix`` from ``scope['path']`` and
        forwards to the FastMCP sub-app.

        Starlette's ``Route`` distinguishes between plain ``async def`` callables
        (treated as ``func(request) -> Response``) and ASGI callables (any
        non-function object with ``__call__(scope, receive, send)``). By using
        a class instance we ensure we are routed through the ASGI branch.
        """

        def __init__(self, inner):
            self._inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] in ("http", "websocket"):
                path = scope.get("path", "") or "/"
                if path == mount_prefix:
                    scope = {**scope, "path": "/"}
                elif path.startswith(mount_prefix + "/"):
                    scope = {**scope, "path": path[len(mount_prefix):]}
            return await self._inner(scope, receive, send)

    wrapper = _MCPASGIApp(base)
    _http_app_cache[key] = wrapper
    return wrapper


def session_lifespan():
    """Async context manager that runs the FastMCP session manager.

    FastMCP's ``StreamableHTTPSessionManager`` keeps per-session background
    tasks alive in an ``anyio`` task group.  That task group must be running
    before ``handle_request`` is called — otherwise every request fails with
    ``RuntimeError: Task group is not initialized``.

    Usage in ``src.main``'s lifespan::

        from src.mcp.server import session_lifespan
        async with session_lifespan():
            yield

    Important: call :func:`get_http_app` only once (it is cached) so the
    manager entered here is the same instance mounted on the FastAPI routes.
    """
    from contextlib import asynccontextmanager

    # Ensure the singleton HTTP app (and its session manager) exists. Prefer
    # reusing the instance already created at import time in main.py.
    _ = get_http_app()

    @asynccontextmanager
    async def _runner():
        sm = mcp.session_manager
        if sm is None:
            # No HTTP app ever requested (unlikely after get_http_app()) — yield
            # a no-op so the lifespan still works.
            yield
            return
        async with sm.run():
            yield

    return _runner()


__all__ = ["mcp", "get_http_app", "session_lifespan", "MCP_TOOL_NAMES"]