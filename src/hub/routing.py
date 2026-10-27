"""Hub → 远端盒子的**转发层**（设计文档 §2.2：MCP 只负责 Agent→Hub 这一跳，
Hub 到远端走 REST）。

## 机制

每个受 Hub 接管的工具声明「我对应的 REST 调用长什么样」（``@hub_routed``）。
装饰器在**调用时**看当前会话选中的是哪台盒子：

- 选的是**本机**（默认）→ 走工具原有的本地实现，**零改动、零开销**；
- 选的是**远端** → 把参数映射成 REST 调用打到那台盒子，再把响应整形成工具承诺的形状。

## ★ 两条铁律（都是实测踩出来的）

### 1. 参数必须**忠实**转发，不能静默丢掉

工具声明的参数，REST 侧**不一定都支持**。实测两类：

- ``list_todos(mine=True)``（筛"我的"）：REST 原本不支持 → 直接转发会返回**全部**待办，
  而调用方以为拿到的是"我的"；
- ``list_folders`` 隐含"不含未分配"：REST 的 ``get_folder_tree`` 默认
  ``include_unassigned=True`` → **"未分配"会被暴露出来**（用户明确要求它不可被浏览）。

这类错误的形态是「不报错、只是结果不对」——比报错危险得多。所以本层**默认 fail-closed**：
任何没有被 ``path`` / ``query`` 消费掉的参数，一律**拒绝转发**并说明原因，
而不是忽略（早先版本只打了个 warning，那等于埋雷）。
真有意不转发某个参数时，用 ``ignore=("param",)` 显式声明并写明理由。

### 2. 响应形状要**适配回工具承诺的样子**

REST 是**给网页用的**，与 MCP 工具面**不 1:1**（§0.1）：
- 有的工具带信封（``{collection, folders}``）而 REST 返回裸列表 → 用 ``shape`` 补信封；
- 有的 REST 返回**超集**（多出 ``pending``/``processing`` 之类的良性小字段）→ 保留；
- ⚠️ 有的 REST 返回**重得多**（``get_meeting`` 内联整段 transcript，而工具只给摘要）→
  **不能直接转发**，要先让 REST 支持 ``?lean=true``（见清单文档的批次 3）。
  直接转发会把转录全文灌进 Agent 上下文 —— 那是**功能倒退**，因为 MCP 工具面
  当初就是为省上下文设计的。

## 覆盖边界

不是所有工具都能转发。三档：

1. **已接**（``ROUTES`` 里有）→ 远端模式转发；
2. **可接但还没接** → 远端模式**拒绝**（``not_forwarded``），并指名当前盒子；
3. **不可远端化**（``upload_*_from_staging`` ×4：staging 令牌是**进程内**暂存表，
   远端没有）+ **Hub 原语**（``HUB_NATIVE_TOOLS``）→ 单独说明。

:func:`src.hub.tools.switch_box` 会把 2 里的工具列进 ``local_only_tools``，
好让模型明白「这些切了盒子也仍打本机」，而不是以为查错了盒子。
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger("hub.routing")

#: 工具名 → 它的 REST 映射（供 ``switch_box`` 与诊断使用）。
ROUTES: dict[str, dict[str, Any]] = {}

#: Hub **自有**的原语：它们不属于任何具体盒子（一个读连接清单、一个改「当前盒子」、
#: 一个扇出到全部盒子），所以既不转发、也**不该被列进 ``local_only_tools``**。
#:
#: 为什么要单独列出来：``local_only_tools`` 的语义是「**作用在某个盒子上、但还没接
#: 转发**的工具」。把 Hub 原语混进去等于告诉模型「search_all_boxes 只查本机」——
#: 那是**假话**，而且正好说反了（它查的是全部盒子）。实测发现过一次。
HUB_NATIVE_TOOLS: tuple[str, ...] = ("list_boxes", "switch_box", "search_all_boxes")

#: **本质不可远端化**的工具 → 理由。远端模式下调用它们必须拒绝并解释，
#: 而不是含糊地说「还没接转发」（那会让模型以为"再等等就行"，其实是永远不行）。
#: 理由要**完整**写出来（别用「同上」）—— 这段文字会原样给模型看，
#: 「同上」在它的上下文里没有前一行可参照。
#:
#: 批次 5（2026-09-19）后为**空**：staging 四件套曾在此（令牌是进程内表，
#: 远端解不了）——现在的解法是 Hub 在**本机**取 staging 字节、multipart 直传
#: 远端上传端点（见 ``src/hub/upload_forwards.py``）。机制保留：将来若出现
#: 真正不可远端化的工具（如依赖本机硬件），在这里登记即可生效。
NOT_REMOTABLE: dict[str, str] = {}


class RefuseForward(Exception):
    """转发层主动拒绝（参数无法忠实转发、或该工具不可远端化）。

    带上给模型看的一句话；调用方把它变成结构化 ``error`` 返回。
    """

    def __init__(self, message: str, *, code: str = "not_forwardable"):
        self.code = code
        super().__init__(message)


def _path_params(path: str) -> list[str]:
    import re

    return re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", path)


def quote_segment(
    name: str, raw: str, *, allow_slash: bool = False
) -> str:
    """把一个**路径段**安全地编码（自定义转发器与 :func:`_fill_path` 共用）。

    规则与理由见 :func:`_fill_path` 的 docstring（path traversal 实测漏洞）。
    空值 / 含 ``..`` → ``KeyError``（调用方把 KeyError 变成 ``bad_args``）。
    """
    if not raw:
        raise KeyError(f"route path param {name!r} is empty")
    if ".." in raw:
        raise KeyError(f"route path param {name!r} contains '..' — refused")
    from urllib.parse import quote

    return quote(raw, safe="/" if allow_slash else "")


def _fill_path(
    path: str, kwargs: dict[str, Any], allow_slash: tuple[str, ...] = ()
) -> str:
    """把 ``/a/{x}/b`` 里的 ``{x}`` 用同名参数填掉，并**逐段转义**。

    ## ★ 为什么要转义（这是一个实测确认的安全漏洞）

    早期实现直接 ``str.replace``，于是**工具参数可以改掉请求打到哪个端点**：

        list_notes(collection="x/../../pairing/tokens")
          → 真的读到了远端盒子的**钥匙列表**
        list_notes(collection="../hub/boxes")
          → 真的读到了远端盒子的**出站盒子清单**

    即 ``ROUTES`` 那张"工具→端点"映射**不再是边界**，参数一穿就绕过了它。
    这是典型的 path traversal，而且全程**不会报错**。

    修法：对每个路径参数做**逐段百分号编码**（``safe=""`` ⇒ ``/`` 变 ``%2F``），
    于是 ``..`` / ``/`` 都变成普通字符、path 只有一段。实测编码后
    ``/api/notes/x%2F..%2F..%2Fpairing%2Ftokens`` 得到 **404**（正确行为。
    该库不存在），而未编码时是 200 + 钥匙列表。

    ``allow_slash``：将来需要**路径型参数**的子路由（如 ``{source:path}``，
    其中 ``__file__:x`` 之类本身不含斜杠、但有些 source 是相对路径）可以显式
    声明某个参数允许斜杠 —— 但**必须显式**，默认全挡。

    另外显式拒绝含 ``..`` 的值：编码已经能挡住穿越，但显式拒绝给出更清楚的错误，
    也防止将来有人误用 ``safe="/"`` 把洞重新打开。
    """
    out = path
    for name in list(_path_params(path)):
        if name not in kwargs:
            raise KeyError(f"route needs path param {name!r}")
        raw = str(kwargs[name])
        # 空值会拼出 ``//`` 这种路径（实测：``list_todos`` 省略 collection 时
        # 转发成 ``/api/file-mgmt//todos`` → 404）。视为缺参数。
        out = out.replace("{" + name + "}", quote_segment(name, raw, allow_slash=name in allow_slash))
    return out


def hub_routed(
    method: str = "GET",
    path: str | Callable[[dict[str, Any]], str] = "",
    *,
    query: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    body: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    shape: Callable[[Any, dict[str, Any]], Any] | None = None,
    refuse: Callable[[dict[str, Any]], str | None] | None = None,
    precheck: Callable[..., Any] | None = None,
    forwards: tuple[str, ...] = (),
    ignore: tuple[str, ...] = (),
    forward: "Callable[[RemoteApi, dict[str, Any]], Awaitable[Any]] | None" = None,
    allow_slash: tuple[str, ...] = (),
    timeout: float | None = None,
):
    """把工具变成「本机走原实现 / 远端走 REST」。

    Args:
        method: HTTP 方法。
        path: 相对 ``/api`` 的路径，可含 ``{arg}`` 占位符（用同名工具参数填）；
            需要按参数算路径时传 ``Callable[[args], str]``。
        query: ``Callable[[args], dict]`` —— 返回**完整的** query 字典。
            用可调用对象而不是「参数名→REST 名」的映射，是因为实测有工具需要
            **算**参数（``list_todos`` 的 ``done`` 要从 ``include_done`` 推导）。
            ``None`` 值会被 client 丢掉（不拼进 URL）。
        body: ``Callable[[args], dict]`` —— 写类工具的 JSON 体（批次 2 起有）。
            返回的 dict 里 ``None`` 值会被丢弃（REST 侧的 Optional 字段按
            「没传」处理，发 ``null`` 会被 pydantic 拒掉或语义漂移）。
            它消费了哪些参数**无法内省** → 照样必须列进 ``forwards``。
        shape: ``Callable[[payload, args], tool_output]`` —— 把 REST 响应整形成
            工具**承诺的形状**。缺省表示原样返回。
        refuse: ``Callable[[args], str | None]`` —— 返回非空字符串表示**拒绝转发**
            （参数在当前 REST 面上无法忠实执行）。这是"静默陷阱"的防线。
        precheck: ``async Callable[[remote, args], str | None]`` —— 在真正转发**之前**
            对远端做一次校验，返回非空字符串表示"拒绝并把这个原因告诉调用方"。
            ``remote`` 是一个绑定到该盒子的异步调用器（``await remote.get(path)``）。

            **为什么必须有**：工具的本地实现会在动手前做前置校验
            （``require_collection``、参数枚举校验…），而 REST 端点**未必有**
            —— 实测 ``GET /api/notes/不存在的库`` 返回 **200 + 空列表**。
            于是同一个坏 id：本地报"库不存在"，转发却回一个**空的成功结果**
            （"不报错、只是结果不对"，正是本层要消灭的形态）。
            写类更险：``POST /api/notes/不存在的库`` 会**静默建出**一个孤儿笔记。
            所以接转发的工具要把前置校验显式写进 ``precheck``。
        forwards: **本路由消费的所有工具参数名**（含路径参数、查询参数、body 参数）。
            必须显式列全 —— 这是"参数守卫"的依据：调用时出现的、既不在
            ``forwards`` 也不在 ``ignore`` 的参数 → **拒绝转发**。
            为什么不自动推断：``query`` / ``body`` 是回调，无法内省它读了哪些键；
            靠自动推断必然漏，而漏掉的后果就是「静默忽略参数」。
            显式列全虽啰嗦，但**可检查**（测试会验证它覆盖了签名）。
        ignore: 明确声明「有意不转发」的参数名（必须同时在注释里写清理由）。
        timeout: 本工具专属的出站超时（秒）。缺省用全局 ``DEFAULT_TIMEOUT``
            （20s）。**慢工具必须显式加长**——实测 agentic 检索（重写/分解/
            评分多轮）与文档重解析都会超过 20s，不加长的话远端还没算完 Hub
            就先报 timeout，调用方误判为「盒子连不上」。
        forward: ``async Callable[[api, args], payload]`` —— **自定义多步转发器**
            （批次 2 起）。单请求模型装不下的工具用这个：批量语义（todo 三件套
            要循环 N 次 REST 再聚合）、两步序列（create_note 带 content 要
            POST + PUT）、先读后写（cancel_task 要先 GET 状态区分「已完成」与
            「真取消」）。``api`` 是 :class:`RemoteApi`（GET/POST/PATCH/PUT/DELETE
            都有）；抛 :class:`RefuseForward` 照常变成结构化拒绝；返回值仍会过
            ``shape``（给了的话）。**这不是**设计文档 §2.3 否决的「通用分发端点」
            —— 那条红线是在**盒子侧**开一个吞所有工具的网络口子；这里是
            Hub 进程内、逐工具写的转发逻辑，每个工具的每步都是具体端点。

    **失败一律变成结构化 error 返回**，不抛：工具的错误面是「返回带 error 的 JSON」，
    抛出会被 MCP 包成协议级异常，模型看到的是 traceback 而不是可操作的原因。
    """

    def _deco(fn):
        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any):
            from src.hub import state as _state

            # 先按签名把位置参数归一成 kwargs（REST 路径/查询参数都从 kwargs 取）。
            # **不归一化会踩这个坑**（实测）：位置调用 ``list_notes("col_x")`` 时
            # kwargs 里没有 collection → 路径拼不出来 → 报 bad_args。
            call_kwargs = _bind_kwargs(fn, args, kwargs)
            box_id = _state.current_box_id(_ctx_session(fn, args, kwargs))
            if not box_id:
                return await fn(*args, **kwargs)

            from src.hub import store as _store
            from src.hub.client import HubCallError, call as _call
            from src.mcp.common import err as _err, to_json as _to_json

            box = _store.get_box(box_id)
            if box is None:
                return _to_error(
                    fn,
                    _err(
                        f"box {box_id!r} is not connected any more — call list_boxes",
                        code="unknown_box",
                    ),
                )

            ctx_name = _ctx_param_name(fn)
            tool_args = {k: v for k, v in call_kwargs.items() if k != ctx_name}

            try:
                # 顺序有讲究：``refuse``（工具自己的语义判断）与参数守卫**先于**
                # 拼路径。否则「空 collection」这类情况会先被 `_fill_path` 以
                # 「path param is empty」拒掉 —— 结果虽也是拒绝，但提示语对调用方
                # 毫无指导意义（它不知道该怎么办）。让工具能给出**可操作的**原因。
                if refuse:
                    reason = refuse(tool_args)
                    if reason:
                        raise RefuseForward(reason)
                _check_leftovers(fn.__name__, tool_args, forwards, ignore)
                if precheck is not None:
                    reason = await precheck(_Remote(box, _call), tool_args)
                    if reason:
                        raise RefuseForward(reason, code="not_found")
                if forward is not None:
                    # 自定义多步转发（批量/两步/先读后写）：结构化请求构造整段
                    # 交给工具自己的转发器；RefuseForward/HubCallError 的处理
                    # 与单请求路径**完全一致**（见下），错误形状不会分叉。
                    payload = await forward(
                        RemoteApi(box, _call, default_timeout=timeout), tool_args
                    )
                else:
                    url_path, params = _build_request(
                        fn.__name__, tool_args, path, query, allow_slash=allow_slash
                    )
                    payload = await _call(
                        box["addr"],
                        "/api" + url_path,
                        box.get("token"),
                        method=method,
                        json_body=None if body is None else _drop_nones(body(tool_args)),
                        timeout=_timeout_for(timeout),
                        params=params or None,
                    )
            except RefuseForward as exc:
                logger.info("refuse forward %s: %s", fn.__name__, exc)
                return _to_error(fn, _err(str(exc), code=exc.code,
                                           box=box.get("box_name") or box_id))
            except KeyError as exc:
                return _to_error(fn, _err(str(exc), code="bad_args"))
            except HubCallError as exc:
                payload = _err(
                    f"{box.get('box_name') or box_id}: {exc}",
                    code=exc.code or "hub_call_failed",
                    box=box.get("box_name") or box_id,
                )
                # 远端 detail 的附加字段（suggested_name 等）合并进错误信封——
                # 不覆盖既有键（批次 5 审查 #5：409 的重试建议不能只在本地有）
                for k, v in (exc.extra or {}).items():
                    payload.setdefault(k, v)
                return _to_error(fn, payload)

            if shape:
                payload = shape(payload, tool_args)
            # ★ 返回形状必须与**本地路径一致**，否则同一条工具两条路形状不同。
            #
            # 实测踩过两次（都是真 bug，且都只有真客户端才暴露）：
            # 1. ``list_folders`` / ``list_groups``（注解 ``-> dict[str, Any]``）本地走
            #    ``_await_result`` → **CallToolResult**；转发却返回 JSON **字符串** →
            #    FastMCP 按注解校验时报
            #    「1 validation error for DictModel / Input should be a valid dictionary」。
            # 2. 反过来，``-> str`` 的工具（``list_notes`` / ``get_note`` / …）声明了
            #    ``outputSchema = {required: ["result"]}``，如果转发返回的 dict **没有
            #    ``result`` 键**，lowlevel handler 的输出校验同样会失败
            #    （表现为客户端拿到空 structuredContent）。
            #
            # ⇒ 形状要**按工具的返回注解**选，与本地实现保持一致：
            #    ``-> str`` → ``to_json``（FastMCP 会包成 ``{"result": "<json>"}``）；
            #    其他（dict / Any）→ ``mcp_result``（CallToolResult）。
            return _to_tool_result(payload, fn)

        ROUTES[fn.__name__] = {
            "method": method,
            "path": path if isinstance(path, str) else "(computed)",
            "forwards": tuple(forwards),
            "ignore": tuple(ignore),
            "has_shape": shape is not None,
            "has_guard": refuse is not None,
            "has_precheck": precheck is not None,
            "has_body": body is not None,
            "custom_forward": forward is not None,
        }
        return _wrapper

    return _deco


def _drop_nones(d: dict[str, Any]) -> dict[str, Any]:
    """body 构造结果里去掉 ``None``（见 ``hub_routed(body=…)`` 的说明）。"""
    return {k: v for k, v in d.items() if v is not None}


def body_without(*path_params: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """``body=`` 构造器：透传全部参数，但**剔除已进路径的参数**。

    为什么必须剔除（实测踩过）：REST 侧若是 ``store.update_library(id, **body)``
    这类解包写法，body 里再带一次同名参数就是
    ``TypeError: got multiple values for argument 'library_id'`` → 500。
    pydantic schema 的端点会静默忽略多余键 —— 两种行为都不可依赖。
    """
    def _build(args: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in args.items() if k not in path_params}
    return _build


def _build_request(
    tool: str,
    args: dict[str, Any],
    path: str | Callable[[dict[str, Any]], str],
    query: Callable[[dict[str, Any]], dict[str, Any]] | None,
    *,
    allow_slash: tuple[str, ...] = (),
) -> tuple[str, dict[str, Any]]:
    consumed: set[str] = set()
    if callable(path):
        url_path = str(path(args))
        # 计算型路径：参数由回调自己消费，这里无法逐个归属 → 交给 ``ignore`` 检查兜底
    else:
        url_path = _fill_path(path, args, allow_slash=allow_slash)
    params = dict(query(args) if query else {})
    return url_path, {k: v for k, v in params.items() if v is not None}


def _check_leftovers(
    tool: str, args: dict[str, Any], forwards: tuple[str, ...], ignore: tuple[str, ...]
) -> None:
    """没被消费的参数 → **拒绝**（fail-closed），不静默丢。

    为什么是拒绝而不是 warning：参数被静默忽略会产出「看起来对、其实错」的结果
    （``list_todos(mine=True)`` 返回全部待办就是实例），而调用方**没有任何线索**。
    拒绝至少让错误可见，并告诉它怎么正确操作（切回本机）。
    """
    allowed = set(forwards) | set(ignore)
    leftover = sorted(
        k for k, v in args.items() if v is not None and k not in allowed
    )
    if leftover:
        raise RefuseForward(
            f"cannot forward {tool} to this box: parameter(s) {leftover} have no "
            f"equivalent on the remote API. Refusing rather than silently ignoring "
            f'them — call switch_box with name="local" to run it here.',
            code="not_forwardable",
        )


def _to_tool_result(payload: Any, fn: Any) -> Any:
    """把转发拿到的 JSON 包成与**本地路径同形**的返回值。

    **按工具的返回注解选形状**（这是本函数存在的全部理由）：
    - ``-> str``：本地实现是 ``to_json(...)``，FastMCP 会把字符串包成
      ``{"result": "<json>"}``，且其 ``outputSchema`` **要求** ``result`` 键。
      所以这里返回**同一个 JSON 字符串**。
    - 其他（``-> dict`` / ``-> Any``）：本地走 ``mcp_result(...)`` → ``CallToolResult``。
      返回它，结构化载荷与一行摘要都在。

    两条路形状不一致的后果不是"报错信息不好看"，而是**该工具在跨盒子时直接不可用**
    （按注解的输出校验会拒掉它）。实测两种方向都踩过。
    """
    from src.mcp.common import mcp_result, to_json

    if _returns_str(fn):
        return to_json(payload)
    return mcp_result(payload if isinstance(payload, dict) else {"result": payload})


class _Remote:
    """转发前预检用的"远端读取器"：只暴露只读 GET，避免预检误改远端状态。"""

    def __init__(self, box: dict[str, Any], call: Any):
        self._box = box
        self._call = call

    async def get(self, path: str, **params: Any) -> Any:
        from src.hub.client import HubCallError

        try:
            return await self._call(
                self._box["addr"],
                "/api" + path,
                self._box.get("token"),
                method="GET",
                params={k: v for k, v in params.items() if v is not None} or None,
            )
        except HubCallError:
            # 预检失败（连不上/无权限）→ 不在这里下结论，让后续正式调用去报真正的原因
            return None


class RemoteApi(_Remote):
    """给自定义 ``forward=`` 钩子的**全方法**远端调用器（批次 2 起）。

    ``default_timeout``（批次 5）：从 ``hub_routed(timeout=)`` 透传——
    没有它，装饰器声明的 300s 只对单请求分支生效，``forward=`` 分支里
    每个调用都悄悄退回 20s 默认（审查 #7）。

    与预检用的 :class:`_Remote` 区别：写方法（POST/PATCH/PUT/DELETE）在这里才有
    —— 预检**只许读**，自定义转发器才是真正去写远端的那一方。

    失败抛 :class:`~src.hub.client.HubCallError`（与单请求路径同一个异常，
    外层把它变成结构化 error），所以转发器里**按需 catch**：批量语义要
    「逐条失败、聚合进 errors」的就地 catch，整体失败的透传。
    """

    def __init__(self, box: dict[str, Any], call: Any, default_timeout: float | None = None):
        super().__init__(box, call)
        self.default_timeout = default_timeout

    async def _req(self, method: str, path: str, json_body: Any = None, **params: Any) -> Any:
        from src.hub.client import DEFAULT_TIMEOUT

        return await self._call(
            self._box["addr"],
            "/api" + path,
            self._box.get("token"),
            method=method,
            json_body=json_body,
            params={k: v for k, v in params.items() if v is not None} or None,
            timeout=self.default_timeout or DEFAULT_TIMEOUT,
        )

    async def post(self, path: str, json_body: Any = None, **params: Any) -> Any:
        return await self._req("POST", path, json_body, **params)

    async def patch(self, path: str, json_body: Any = None, **params: Any) -> Any:
        return await self._req("PATCH", path, json_body, **params)

    async def put(self, path: str, json_body: Any = None, **params: Any) -> Any:
        return await self._req("PUT", path, json_body, **params)

    async def delete(self, path: str, **params: Any) -> Any:
        return await self._req("DELETE", path, None, **params)

    async def post_multipart(
        self, path: str, *, data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None, params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """multipart 上传（批次 5：Hub 把本机 staging 的字节直传远端）。

        传输时间与体积成正比 → **必须显式给大 timeout**（默认 20s 对上传
        毫无意义——远端还在收字节 Hub 就先报「连不上」）。

        ⚠️ ``params`` 是 **query** 参数、``data`` 是 **form** 字段——两者不
        可互换。端点把参数声明成 ``str = "default"``（裸 str）时 FastAPI 绑
        query、``Form()`` 才绑 form；传错一边参数会被**静默忽略**（批次 5
        审查 CRITICAL：collection 传成 form 字段 → 上传全部落进 default 库
        而信封谎报目标库）。
        """
        from src.hub.client import DEFAULT_TIMEOUT

        return await self._call(
            self._box["addr"],
            "/api" + path,
            self._box.get("token"),
            method="POST",
            files=files,
            data=data,
            params={k: v for k, v in (params or {}).items() if v is not None} or None,
            timeout=timeout if timeout is not None
            else (self.default_timeout or DEFAULT_TIMEOUT),
        )


async def _require_collection_on_remote(remote: "_Remote", args: dict[str, Any]) -> str | None:
    """前置校验：**目标库在远端必须存在**（与本地 ``require_collection`` 同语义）。

    为什么不能省：远端 ``GET /api/notes/{不存在的库}`` 返回 200 + 空列表，
    而工具本地实现会先报"库不存在"。少了这一步，同一个坏 id 在本地是错误、
    转发却是"空的成功" —— 调用方会以为那个库真的没有笔记。
    """
    col = args.get("collection")
    if not col:
        return None
    cols = await remote.get("/collections")
    if cols is None:
        return None  # 拿不到列表（连不上/无权限）→ 交给正式调用报错
    if not isinstance(cols, list):
        return None
    known = {str(c.get("id")) for c in cols if isinstance(c, dict)}
    # 远端也接受显示名（历史行为），两种都认
    known |= {str(c.get("name")) for c in cols if isinstance(c, dict)}
    if str(col) not in known:
        return f"Collection '{col}' does not exist"
    return None


def _to_error(fn: Any, payload: dict[str, Any]) -> Any:
    """错误返回也要**按注解选形状** —— 与 :func:`_to_tool_result` 同一套规则。

    **实测踩过（真 bug）**：错误分支曾一律 ``to_json(...)`` 返回字符串。对
    ``-> str`` 的工具没问题，但对 ``-> dict`` 的工具（``list_folders`` /
    ``list_groups``）会撞上 FastMCP 按注解做的输出校验：

        Error executing tool list_folders:
          1 validation error for DictModel
          Input should be a valid dictionary
          [input_value='{"error": "PDead: cannot reach …"}', input_type=str]

    ⇒ 连"库不存在"这种普通错误都变成一个**协议级校验错误**，客户端看不到原因。
    所以错误路径必须和成功路径走同一套形状选择。
    """
    return _to_tool_result(payload, fn)


def _returns_str(fn: Any) -> bool:
    """工具的返回注解是不是裸 ``str``。"""
    try:
        ann = inspect.signature(fn, eval_str=True).return_annotation
    except Exception:
        try:
            ann = inspect.signature(fn).return_annotation
        except Exception:
            return True  # 判不出来时按"返回字符串"处理（更常见的约定）
    return ann is str


def _timeout_for(override: float | None = None) -> float:
    """转发超时：工具级覆盖优先，否则全局 ``DEFAULT_TIMEOUT``（20s）。

    **不按 method 区分** —— 实测读写都可能慢（检索比删除慢得多），按方法
    给不同超时只会引入一个没人验证过的假设。粒度就该按**工具**声明。
    """
    from src.hub.client import DEFAULT_TIMEOUT

    return override if override is not None else DEFAULT_TIMEOUT


def _bind_kwargs(fn: Callable[..., Any], args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """把 ``(args, kwargs)`` 按签名归一成一份 kwargs（供路径/查询参数取用）。

    绑定失败（实参对不上）时原样返回 kwargs——后续会因缺参数而报 ``bad_args``，
    那是清晰可查的失败，比猜一个值好。
    """
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except TypeError:
        return dict(kwargs)
    merged = dict(kwargs)
    for name, value in bound.arguments.items():
        merged.setdefault(name, value)
    return merged


def _ctx_session(fn: Callable[..., Any], args: tuple, kwargs: dict[str, Any]) -> Any:
    """从工具调用里取 MCP 会话对象。

    **按签名绑定，而不是只查 ``kwargs["ctx"]``**：SDK 目前用**关键字**传 Context
    （``{context_kwarg: context}``，见 ``fastmcp/tools/base.py::run``），所以只查
    kwargs 在真实调用下能工作。但它有个**危险方向**的失败模式：万一以后 SDK 改成
    位置传参、或有人直接位置调用，这里取不到会话 → 回落成「本机」→ **静默查错盒子
    而不报错**。签名绑定则两种传法都取得到。

    取不到就返回 None（= 本机语义）。没有 Context 参数的工具永远走本机，是正确的默认。
    """
    name = _ctx_param_name(fn)
    if name is None:
        return None
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except TypeError:
        # 参数对不上（测试用非法实参调用等）→ 退回只查 kwargs，绝不抛。
        ctx = kwargs.get(name)
    else:
        ctx = bound.arguments.get(name)
    return getattr(ctx, "session", None) if ctx is not None else None


def _ctx_param_name(fn: Callable[..., Any]) -> str | None:
    """工具签名里那个 Context 参数叫什么（通常叫 ``ctx``）。

    按**注解是不是 Context** 判定（与 FastMCP 生成 schema 时跳过它的同一依据），
    而不是猜名字——名字可以随便取。

    ⚠️ ``Context`` 是**泛型别名**（``Context[ServerSessionT, ...]``），所以不能只靠
    ``isinstance(ann, type)``：裸写 ``ctx: Context`` 时它是 ``_GenericAlias``，
    要经 ``typing.get_origin`` 才能拿到真正的类；``ctx: Context | None`` 则是
    ``Union``，得看 ``__args__``。两种写法都实测过。

    ⚠️⚠️ **必须 ``eval_str=True``**：本仓的工具模块都带
    ``from __future__ import annotations``（PEP 563 延迟求值），此时
    ``param.annotation`` 是**字符串** ``"'Context | None'"`` 而不是类型对象。
    实测后果：不加这个参数时，注解识别**全部落空**，只剩「参数名叫 ctx」的兜底命中 →
    看起来一切正常，但换个参数名就会取不到会话 → **静默回落到本机、查错盒子**。
    FastMCP 自己也是用 ``inspect.signature(func, eval_str=True)`` 的，保持一致。
    """
    import typing

    try:
        params = inspect.signature(fn, eval_str=True).parameters
    except (TypeError, ValueError, NameError):
        # 注解无法求值（引用不到的名字等）→ 退回字符串形态，走名字兜底
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return None

    def _is_context_annotation(ann: Any) -> bool:
        if ann is None or ann is inspect.Parameter.empty:
            return False
        # 裸类 / 泛型别名：先直接比，再取 origin 比
        for candidate in (ann, typing.get_origin(ann)):
            if isinstance(candidate, type) and issubclass(candidate, _CONTEXT_TYPE):
                return True
        # Optional[Context] / Context | None
        for arg in typing.get_args(ann) or ():
            if _is_context_annotation(arg):
                return True
        return False

    for param in params.values():
        if _is_context_annotation(param.annotation):
            return param.name
    # 兜底：注解拿不到时按约定名字找
    if "ctx" in params:
        return "ctx"
    return None


def _context_type():
    from mcp.server.fastmcp import Context

    return Context


_CONTEXT_TYPE = _context_type()


def is_routed(tool_name: str) -> bool:
    return tool_name in ROUTES


def routed_tool_names() -> list[str]:
    return sorted(ROUTES)


__all__ = [
    "HUB_NATIVE_TOOLS",
    "NOT_REMOTABLE",
    "ROUTES",
    "RefuseForward",
    "RemoteApi",
    "body_without",
    "hub_routed",
    "is_routed",
    "quote_segment",
    "routed_tool_names",
]
