from __future__ import annotations

import ast
import asyncio
import functools
import json
import logging
import re
import threading
import time
import uuid
import weakref
from typing import Callable

from fastapi import APIRouter, Body, HTTPException

from src.api.errors import ApiError

from src.api.schemas import ConfigUpdateRequest
from src.config import get_config, save_config, reload_config, LLMProviderConfig, EmbeddingProviderConfig, RerankProviderConfig, TranscriptionProviderConfig, LIVE_TRANSLATE_ADAPTER
from src.services import async_refresh_llm_runtime, async_reload_services
from src.providers.cache import get_or_create as cached_provider, invalidate as invalidate_provider

logger = logging.getLogger(__name__)

router = APIRouter()

# Config-mutating endpoints each do read-modify-write of the whole
# config.yaml. They must not interleave: the oneshot dialog fires six
# provider creates in parallel, and the last stale snapshot to land used to
# wipe every provider written before it (oneshot "succeeded" with only the
# LLM provider left). Keyed per event loop so tests using asyncio.run get a
# fresh lock each time; production has a single loop.
_write_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _write_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _write_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _write_locks[loop] = lock
    return lock


def _serialize_writes(fn):
    """Run a mutating endpoint under the global config write lock."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        async with _write_lock():
            return await fn(*args, **kwargs)

    return wrapper


def _public_dump(obj) -> dict:
    from src.secrets import redact_mapping

    return redact_mapping(obj.model_dump())


def _apply_live_overrides(provider_cfg, data: dict | None) -> None:
    """Copy request fields onto a provider, ignoring masked/empty secrets."""
    from src.secrets import skip_secret_write

    if not data:
        return
    for key, value in data.items():
        if not hasattr(provider_cfg, key) or not value:
            continue
        if skip_secret_write(key, value):
            continue
        if key in {"dimensions", "batch_size", "top_k"}:
            value = int(value)
        setattr(provider_cfg, key, value)


def _request_api_key(data: dict | None, stored: str | None, *, providers=None, base_url: str | None = None) -> str:
    from src.secrets import effective_secret, is_masked_secret

    incoming = (data or {}).get("api_key")
    if incoming and not is_masked_secret(incoming):
        return incoming
    if providers and base_url:
        match = next((p for p in providers if getattr(p, "base_url", None) == base_url and p.api_key), None)
        if match:
            return match.api_key
    return effective_secret(incoming, stored)

# 模型列表缓存 (key: "section:base_url", value: {"models": [...], "timestamp": float})
_model_cache: dict[str, dict] = {}
MODEL_CACHE_TTL = 300  # 5分钟缓存

_ERROR_CODE_RE = re.compile(r"Error code:\s*(\d+)\s*-\s*(\{.*\})\s*$", re.S)
_API_KEY_ASSIGN_RE = re.compile(
    r"(api[\s_-]?key|token|secret)\s*[:=]\s*['\"]?[^\s'\",}]+",
    re.I,
)
_SK_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{4,}\b")


def _message_from_error_body(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if isinstance(err, dict) and isinstance(err.get("message"), str):
        return err["message"].strip()
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(body.get("message"), str):
        return body["message"].strip()
    return None


def _parse_openai_error_blob(raw: str) -> tuple[int | None, dict | None]:
    m = _ERROR_CODE_RE.search(raw.strip())
    if not m:
        return None, None
    status = int(m.group(1))
    blob = m.group(2)
    for loader in (ast.literal_eval, json.loads):
        try:
            parsed = loader(blob)
        except (ValueError, SyntaxError, json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return status, parsed
    return status, None


def _redact_secrets_in_error(msg: str) -> str:
    msg = _API_KEY_ASSIGN_RE.sub(r"\1", msg)
    msg = _SK_TOKEN_RE.sub("sk-…", msg)
    return msg


def _friendly_provider_error(status: int | None, msg: str, *, err_type: str = "") -> str:
    low = f"{msg} {err_type}".lower()
    if status == 401 or "authentication" in low or "invalid api key" in low or "invalid_api_key" in low:
        return "Invalid API key"
    if status == 403 or "permission" in low:
        return "Access denied — check API key permissions"
    if status == 404:
        return "Model or endpoint not found"
    if status == 429 or "rate limit" in low:
        return "Rate limited — try again later"
    if status is not None and status >= 500:
        return f"Provider server error (HTTP {status})"
    return msg


def _clean_error(e: Exception) -> str:
    """User-facing provider error: no HTML, no Python dict dumps, no API keys."""
    status: int | None = getattr(e, "status_code", None)
    if not isinstance(status, int):
        status = None
    extracted = _message_from_error_body(getattr(e, "body", None))
    err_type = ""
    body = getattr(e, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err_type = str(body["error"].get("type") or "")

    try:
        import httpx

        if isinstance(e, httpx.TimeoutException):
            return "Provider timed out"
        if isinstance(e, httpx.ConnectError):
            return "Could not connect to the provider"
        if isinstance(e, httpx.HTTPStatusError):
            status = e.response.status_code
            try:
                extracted = _message_from_error_body(e.response.json()) or extracted
            except Exception:
                extracted = extracted or (e.response.reason_phrase or "")
    except ImportError:
        pass

    raw = str(e)
    parsed_status, parsed_body = _parse_openai_error_blob(raw)
    if parsed_status is not None:
        status = status or parsed_status
    if extracted is None and parsed_body is not None:
        extracted = _message_from_error_body(parsed_body)
        if isinstance(parsed_body.get("error"), dict):
            err_type = err_type or str(parsed_body["error"].get("type") or "")

    msg = extracted or raw
    if "<!doctype html>" in msg.lower() or "<html" in msg.lower():
        msg = msg.split("<html")[0].split("<!doctype")[0].strip()
    if not msg or msg.startswith("Error code:"):
        msg = f"HTTP {status}" if status else "Connection failed"
    msg = _redact_secrets_in_error(msg)
    return _friendly_provider_error(status, msg, err_type=err_type)[:200]


def _same_realtime_kind(a, b) -> bool:
    """Exclusivity peers: LiveTranslate (translation) and realtime ASR are
    separate kinds — activating one kind never clears the other's is_active."""
    la = (a.adapter or "") == LIVE_TRANSLATE_ADAPTER
    lb = (b.adapter or "") == LIVE_TRANSLATE_ADAPTER
    return la == lb


def _add_to_provider_list(
    providers: list,
    provider,
    *,
    flag: str = "is_default",
    sweep: Callable[[object, object], bool] | None = None,
):
    """Append a provider; exclusive *flag* is is_default or is_active.

    `sweep(incoming, peer)` scopes the exclusivity clear to same-kind peers —
    used by realtime transcription so LiveTranslate providers keep their own
    is_active flag (see LIVE_TRANSLATE_ADAPTER).
    """
    if not getattr(provider, "id", None):
        provider.id = str(uuid.uuid4())
    if getattr(provider, flag, False):
        for p in providers:
            if sweep and not sweep(provider, p):
                continue
            setattr(p, flag, False)
    elif not providers:
        setattr(provider, flag, True)
    copy = provider.model_copy()
    providers.append(copy)
    return copy


def _apply_provider_update(
    providers: list,
    provider_id: str,
    update: dict,
    *,
    int_fields: set[str] | frozenset[str] = frozenset(),
    bool_fields: set[str] | frozenset[str] = frozenset(),
    exclusive_flag: str = "is_default",
    sweep: Callable[[object, object], bool] | None = None,
):
    """Patch one provider in *providers*. Returns it, or None if missing.

    `sweep` scopes the exclusive-flag clear to same-kind peers (realtime
    transcription vs LiveTranslate keep independent is_active flags).
    """
    from src.secrets import skip_secret_write

    ints = set(int_fields)
    bools = set(bool_fields)
    for i, p in enumerate(providers):
        if p.id != provider_id:
            continue
        for key, value in update.items():
            if key == "id":
                continue
            if skip_secret_write(key, value):
                continue
            if hasattr(p, key):
                if key in ints and value is not None:
                    value = int(value)
                elif key in bools:
                    value = bool(value)
                setattr(providers[i], key, value)
        if update.get(exclusive_flag):
            for j, other in enumerate(providers):
                if j != i and (sweep is None or sweep(providers[i], other)):
                    setattr(other, exclusive_flag, False)
        return providers[i]
    return None


def _remove_provider(
    providers: list,
    provider_id: str,
    *,
    exclusive_flag: str = "is_default",
    promote: bool = False,
):
    """Remove by id. Returns (removed, remaining). removed is None if missing."""
    target = next((p for p in providers if p.id == provider_id), None)
    if not target:
        return None, providers
    rest = [p for p in providers if p.id != provider_id]
    if promote and getattr(target, exclusive_flag, False) and rest:
        setattr(rest[0], exclusive_flag, True)
    return target, rest


def _set_exclusive_flag(providers: list, provider_id: str, *, flag: str = "is_default"):
    """Set *flag* on one provider and clear it on siblings. None if missing."""
    found = None
    for p in providers:
        if p.id == provider_id:
            setattr(p, flag, True)
            found = p
        else:
            setattr(p, flag, False)
    return found


@router.get("/config")
def get_current_config():
    from src.secrets import redact_mapping

    config = reload_config()
    data = config.model_dump(exclude_none=True)
    return redact_mapping(data)


@router.get("/config/provider-types")
def list_provider_types():
    """列出每类 provider 当前可用的实现 — 前端 dropdown 用。

    Returns:
        {
            "embedding":              [{"name": "local", "display_name": "Local (download model)"}, ...],
            "reranker":               [{"name": "local", ...}, ...],
            "llm":                    [{"name": "openai_compatible", ...}],
            "file_transcription":     [{"name": "dashscope_funasr", ...}],
            "realtime_transcription": [{"name": "dashscope_funasr_realtime", ...}],
        }
    """
    from src.providers.registry import (
        embedding_registry,
        reranker_registry,
        llm_registry,
    )
    from src.meeting.transcription.registry import (
        entry_public_dict,
        file_transcription_registry,
        realtime_transcription_registry,
    )

    def _entries(registry):
        return [
            {"name": e.name, "display_name": e.display_name}
            for e in registry.list_primary()
        ]

    return {
        "embedding": _entries(embedding_registry),
        "reranker": _entries(reranker_registry),
        "llm": _entries(llm_registry),
        # Transcription adapters also expose supports_hot_words for UI enable/disable
        "file_transcription": [
            entry_public_dict(e) for e in file_transcription_registry.list_primary()
        ],
        "realtime_transcription": [
            entry_public_dict(e) for e in realtime_transcription_registry.list_primary()
        ],
    }


# Top-level AppConfig fields that can be updated individually
_TOP_LEVEL_FIELDS = {"visual_model_id", "default_chat_model"}


def _slot_ref_valid(ref: str | None, providers: list, kind: str) -> bool:
    """True if *ref* is empty, a live ``providerId|model``, or a legacy bare name."""
    raw = (ref or "").strip()
    if not raw:
        return True
    if "|" in raw:
        pid, model = raw.split("|", 1)
        pid, model = pid.strip(), model.strip()
        found = next((p for p in providers if p.id == pid), None)
        if found is None or not model:
            return False
        ids = (
            found.visual_model_ids if kind == "visual" else found.function_call_model_ids
        ) or []
        return model in ids
    if kind == "visual":
        return any(raw in (p.visual_model_ids or []) for p in providers)
    return any(raw in (p.function_call_model_ids or []) for p in providers)


@router.put("/config")
@_serialize_writes
async def update_config(req: ConfigUpdateRequest):
    reload_config()
    config = get_config()

    if req.section == "locale":
        value = req.data.get("locale")
        if value not in ("en", "zh-CN"):
            raise ApiError(
                400,
                "invalid_locale",
                f"Invalid locale: {value!r}",
                params={"locale": value},
            )
        config.locale = value
        save_config(config)
        reload_config()
        from src.services import services
        services.config = get_config()
        return {"message": "Config 'locale' updated"}

    # Handle top-level AppConfig fields
    if req.section in _TOP_LEVEL_FIELDS:
        for key, value in req.data.items():
            if hasattr(config, key):
                setattr(config, key, value)
        save_config(config)
        reload_config()
        # Model slots (default_chat_model / visual_model_id) must reach the
        # running ChatboxAgent — without this refresh, existing sessions keep
        # calling the previous model until restart.
        await async_refresh_llm_runtime()
        from src.services import services
        services.config = get_config()
        return {"message": f"Config '{req.section}' updated"}

    section_data = getattr(config, req.section, None)
    if section_data is None:
        raise HTTPException(400, f"Unknown config section: {req.section}")

    # Only allow setting declared Pydantic model fields
    allowed_keys = set(getattr(type(section_data), "model_fields", {}).keys())
    _int_fields = {
        "dimensions", "batch_size", "top_k", "rerank_top_k",
        "max_parallel_queries", "max_parallel_context", "batch_poll_interval",
        "max_results", "confirm_timeout_sec",
    }
    _bool_fields = {"enabled", "is_ocr", "enable_formula", "enable_table", "use_reranker", "use_batch", "meeting_thinking"}
    from src.secrets import skip_secret_write

    for key, value in req.data.items():
        if key not in allowed_keys:
            continue
        if skip_secret_write(key, value):
            continue
        if key in _int_fields and value is not None:
            value = int(value)
        if key in _bool_fields and value is not None:
            value = bool(value)
        setattr(section_data, key, value)

    save_config(config)
    reload_config()
    # Keep services.config in sync so runtime reads see updated values
    from src.services import services
    services.config = get_config()
    return {"message": f"Config section '{req.section}' updated"}


@router.post("/config/reload")
@_serialize_writes
async def reload():
    reload_config()
    await async_reload_services()
    return {"message": "Config reloaded"}


@router.post("/config/test/{section}")
async def test_connection(section: str, data: dict | None = Body(default=None)):
    """测试模型连通性 - 使用用户输入的配置"""
    try:
        config = get_config()

        def _test():
            if section == "llm":
                # Find default provider or use first one
                providers = config.llm.providers
                if not providers:
                    return {"success": False, "error": "No LLM providers configured"}
                provider_cfg = next((p for p in providers if p.is_default), providers[0])
                _apply_live_overrides(provider_cfg, data)

                from src.providers.llm import create_llm_provider
                provider = create_llm_provider(provider_cfg)
                provider.generate("Hello")
                return {"success": True, "message": "LLM connection successful"}

            elif section == "embedding":
                providers = config.embedding.providers
                if not providers:
                    return {"success": False, "error": "No embedding providers configured"}
                provider_cfg = next((p for p in providers if p.is_default), providers[0])
                _apply_live_overrides(provider_cfg, data)

                from src.providers.embedding import create_embedding_provider
                provider = create_embedding_provider(provider_cfg)
                embeddings = provider.embed_texts(["test"])
                if embeddings and len(embeddings) > 0:
                    return {"success": True, "message": "Embedding connection successful"}
                else:
                    return {"success": False, "error": "No embeddings returned"}

            elif section == "rerank":
                providers = config.rerank.providers
                if not providers:
                    return {"success": False, "error": "No rerank providers configured"}
                provider_cfg = next((p for p in providers if p.is_default), providers[0])
                _apply_live_overrides(provider_cfg, data)

                from src.providers.reranker import create_reranker_provider
                provider = create_reranker_provider(provider_cfg)
                results = provider.rerank("test", ["test document"])
                if results:
                    return {"success": True, "message": "Rerank connection successful"}
                else:
                    return {"success": False, "error": "No results returned"}

            else:
                return {"success": False, "error": f"Unknown section: {section}"}

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _test)

    except Exception as e:
        return {"success": False, "error": _clean_error(e)}


@router.post("/config/models/{section}")
async def get_available_models(section: str, data: dict | None = Body(default=None)):
    """获取可用模型列表 (带缓存) - 使用用户输入的配置"""
    try:
        config = get_config()

        # 使用用户输入的配置或当前配置
        if section == "llm":
            providers = config.llm.providers
            if providers:
                provider_cfg = next((p for p in providers if p.is_default), providers[0])
                base_url = provider_cfg.base_url
                api_key = provider_cfg.api_key
            else:
                base_url = ""
                api_key = ""
            if data:
                if "base_url" in data and data["base_url"]:
                    base_url = data["base_url"]
                api_key = _request_api_key(
                    data, api_key, providers=providers, base_url=base_url
                )
        elif section == "embedding":
            providers = config.embedding.providers
            if providers:
                provider_cfg = next((p for p in providers if p.is_default), providers[0])
                base_url = provider_cfg.base_url
                api_key = provider_cfg.api_key
            else:
                base_url = ""
                api_key = ""
            if data:
                if "base_url" in data and data["base_url"]:
                    base_url = data["base_url"]
                api_key = _request_api_key(
                    data, api_key, providers=providers, base_url=base_url
                )
        elif section == "rerank":
            providers = config.rerank.providers
            if providers:
                provider_cfg = next((p for p in providers if p.is_default), providers[0])
                base_url = provider_cfg.base_url or None
                api_key = provider_cfg.api_key
                provider = provider_cfg.provider
            else:
                base_url = None
                api_key = ""
                provider = "none"
            if data:
                if "base_url" in data and data["base_url"]:
                    base_url = data["base_url"]
                api_key = _request_api_key(
                    data, api_key, providers=providers, base_url=base_url
                )
                if "provider" in data and data["provider"]:
                    provider = data["provider"]
        else:
            base_url = ""
            api_key = ""
            provider = ""
            if data:
                base_url = data.get("base_url", "") or ""
                api_key = _request_api_key(data, api_key)
                provider = data.get("provider", "") or ""

        # 检查缓存 (key 包含 URL 和 API Key 的哈希)
        import hashlib
        api_key_hash = hashlib.md5((api_key or "").encode()).hexdigest()[:8]
        cache_key = f"{section}:{base_url}:{api_key_hash}"
        if cache_key in _model_cache:
            cached = _model_cache[cache_key]
            if time.time() - cached["timestamp"] < MODEL_CACHE_TTL:
                return {"models": cached["models"], "cached": True}

        def _fetch():
            if section == "llm":
                if not base_url:
                    return {"models": []}

                from openai import OpenAI
                client = OpenAI(base_url=base_url, api_key=api_key or "dummy")
                models = client.models.list()
                # 返回所有模型，让用户通过搜索过滤
                model_names = [m.id for m in models.data]

                return {"models": sorted(model_names)}

            elif section == "embedding":
                if not base_url:
                    return {"models": []}

                from openai import OpenAI
                client = OpenAI(base_url=base_url, api_key=api_key or "dummy")
                models = client.models.list()
                # 返回所有模型，让用户通过搜索过滤
                model_names = [m.id for m in models.data]

                return {"models": sorted(model_names)}

            elif section == "rerank":
                if provider == "qwen":
                    return {
                        "models": [
                            "qwen3-vl-rerank",
                            "gte-rerank",
                        ]
                    }
                elif provider in ("openai_compatible", "remote") and base_url:
                    from openai import OpenAI
                    client = OpenAI(base_url=base_url, api_key=api_key or "dummy")
                    models = client.models.list()
                    model_names = [m.id for m in models.data]
                    return {"models": sorted(model_names)}
                return {"models": []}

            else:
                if base_url:
                    from openai import OpenAI
                    client = OpenAI(base_url=base_url, api_key=api_key or "dummy")
                    models = client.models.list()
                    return {"models": sorted([m.id for m in models.data])}
                return {"models": []}

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _fetch)

        # 更新缓存
        if "models" in result and result["models"]:
            _model_cache[cache_key] = {
                "models": result["models"],
                "timestamp": time.time()
            }

        return result

    except Exception as e:
        return {"models": [], "error": _clean_error(e)}


# ── LLM Provider CRUD ──────────────────────────────────────


@router.get("/llm/providers")
def list_llm_providers():
    config = reload_config()
    result = []
    from src.secrets import redact_mapping

    for p in config.llm.providers:
        d = redact_mapping(p.model_dump())
        d["is_builtin"] = False
        d["is_loaded"] = True
        result.append(d)
    return result


@router.post("/llm/providers")
@_serialize_writes
async def add_llm_provider(provider: LLMProviderConfig):
    config = get_config()
    added = _add_to_provider_list(config.llm.providers, provider)
    loop = asyncio.get_running_loop()
    mode = await loop.run_in_executor(None, _probe_provider_thinking, added)
    if mode:
        added.thinking_mode = mode
    save_config(config)
    reload_config()
    await async_refresh_llm_runtime()
    from src.secrets import redact_mapping

    return redact_mapping(added.model_dump())


@router.put("/llm/providers/{provider_id}")
@_serialize_writes
async def update_llm_provider(provider_id: str, update: dict = Body()):
    config = get_config()
    found = _apply_provider_update(
        config.llm.providers,
        provider_id,
        update,
        bool_fields={"is_default"},
    )
    if not found:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    if "visual_model_ids" in update:
        if not _slot_ref_valid(config.visual_model_id, config.llm.providers, "visual"):
            config.visual_model_id = None
    if "function_call_model_ids" in update:
        if not _slot_ref_valid(config.default_chat_model, config.llm.providers, "chat"):
            config.default_chat_model = None
    # Endpoint/model/key changed → re-classify the thinking posture, unless
    # the caller pinned thinking_mode explicitly in the same update.
    endpoint_changed = bool(
        {"model", "base_url", "api_key", "default_model"} & set(update)
    )
    if endpoint_changed and "thinking_mode" not in update:
        loop = asyncio.get_running_loop()
        mode = await loop.run_in_executor(None, _probe_provider_thinking, found)
        if mode:
            found.thinking_mode = mode
    save_config(config)
    reload_config()
    await async_refresh_llm_runtime()
    return _public_dump(found)


def _provider_display_name(provider, fallback_id: str = "") -> str:
    """Human-readable label for toasts (prefer name over uuid)."""
    name = (getattr(provider, "name", None) or "").strip()
    if name:
        return name
    pid = (getattr(provider, "id", None) or fallback_id or "").strip()
    return pid or "Provider"


def _probe_provider_thinking(provider) -> str:
    """Classify a model's thinking posture with one tiny request.

    Runs at save time so real tasks never pay the trial-and-error cost.
    Probes the effective model (default_model overrides model) — the one
    runtime calls will actually use. Returns "" (keep previous value) when
    inconclusive.
    """
    model = (provider.default_model or provider.model or "").strip()
    if not (
        (provider.base_url or "").strip()
        and (provider.api_key or "").strip()
        and model
    ):
        return ""
    from src.providers.llm.thinking import probe_thinking_mode

    try:
        return probe_thinking_mode(provider.base_url, provider.api_key, model)
    except Exception as e:
        import logging
        logging.getLogger("api.llm").warning(
            "Thinking probe failed for %s: %s", model, e,
        )
        return ""


@router.delete("/llm/providers/{provider_id}")
@_serialize_writes
async def delete_llm_provider(provider_id: str):
    config = get_config()
    target, rest = _remove_provider(config.llm.providers, provider_id)
    if not target:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    label = _provider_display_name(target, provider_id)
    config.llm.providers = rest
    if not _slot_ref_valid(config.visual_model_id, config.llm.providers, "visual"):
        config.visual_model_id = None
    if not _slot_ref_valid(config.default_chat_model, config.llm.providers, "chat"):
        config.default_chat_model = None
    save_config(config)
    reload_config()
    return {"message": f"Provider '{label}' deleted"}


@router.post("/llm/providers/{provider_id}/test")
async def test_llm_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.test_llm")
    config = get_config()
    provider = None
    for p in config.llm.providers:
        if p.id == provider_id:
            provider = p
            break
    if not provider:
        return {"success": False, "error": f"Provider '{provider_id}' not found"}

    try:
        def _test():
            from src.providers.llm import create_llm_provider
            _log.info("Testing LLM provider: %s (%s)", provider.name, provider.provider)
            llm = create_llm_provider(provider)
            llm.generate("Hello")
            _log.info("LLM provider test passed: %s", provider.name)
            return {"success": True, "message": "LLM connection successful"}

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _test)
    except Exception as e:
        _log.warning("LLM provider test failed: %s — %s", provider.name, e)
        return {"success": False, "error": _clean_error(e)}


@router.post("/llm/providers/{provider_id}/set-default")
@_serialize_writes
async def set_default_llm_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.llm")
    _log.info("Set default LLM: %s", provider_id)
    config = get_config()
    found = _set_exclusive_flag(config.llm.providers, provider_id)
    if not found:
        _log.warning("Set default LLM failed: provider '%s' not found", provider_id)
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    display_name = _provider_display_name(found, provider_id)
    save_config(config)
    reload_config()
    await async_refresh_llm_runtime()
    return {"message": f"Provider '{display_name}' set as default"}


# ── Embedding Provider CRUD ────────────────────────────────


@router.get("/embedding/providers")
def list_embedding_providers():
    config = get_config()
    result = []
    for p in config.embedding.providers:
        d = _public_dump(p)
        d["is_builtin"] = False
        result.append(d)
    return result


@router.post("/embedding/providers")
@_serialize_writes
async def add_embedding_provider(provider: EmbeddingProviderConfig):
    config = get_config()
    added = _add_to_provider_list(config.embedding.providers, provider)
    save_config(config)
    reload_config()
    await async_reload_services(preload_transcription=False)
    return _public_dump(added)


@router.put("/embedding/providers/{provider_id}")
@_serialize_writes
async def update_embedding_provider(provider_id: str, update: dict = Body()):
    config = get_config()
    found = _apply_provider_update(
        config.embedding.providers,
        provider_id,
        update,
        int_fields={"dimensions", "batch_size"},
        bool_fields={"is_default"},
    )
    if not found:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    save_config(config)
    reload_config()
    await async_reload_services(preload_transcription=False)
    return _public_dump(found)


@router.delete("/embedding/providers/{provider_id}")
@_serialize_writes
async def delete_embedding_provider(provider_id: str):
    config = get_config()
    target, rest = _remove_provider(
        config.embedding.providers, provider_id, promote=True
    )
    if not target:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    label = _provider_display_name(target, provider_id)
    config.embedding.providers = rest
    save_config(config)
    reload_config()
    return {"message": f"Provider '{label}' deleted"}


@router.post("/embedding/providers/{provider_id}/test")
async def test_embedding_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.test_embedding")
    config = get_config()
    provider = next((p for p in config.embedding.providers if p.id == provider_id), None)
    if not provider:
        return {"success": False, "error": f"Provider '{provider_id}' not found"}
    try:
        def _test():
            from src.providers.embedding import create_embedding_provider
            _log.info("Testing embedding provider: %s (%s)", provider.name, provider.provider)
            emb = create_embedding_provider(provider)
            embeddings = emb.embed_texts(["test"])
            if embeddings and len(embeddings) > 0:
                _log.info("Embedding provider test passed: %s (dim=%d)", provider.name, len(embeddings[0]))
                return {"success": True, "message": "Embedding connection successful"}
            return {"success": False, "error": "No embeddings returned"}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _test)
    except Exception as e:
        _log.warning("Embedding provider test failed: %s — %s", provider.name, e)
        return {"success": False, "error": _clean_error(e)}


@router.post("/embedding/providers/{provider_id}/set-default")
@_serialize_writes
async def set_default_embedding_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.embedding")
    _log.info("Set default embedding: %s", provider_id)
    config = get_config()
    found = _set_exclusive_flag(config.embedding.providers, provider_id)
    if not found:
        _log.warning("Set default embedding failed: provider '%s' not found", provider_id)
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    display_name = _provider_display_name(found, provider_id)
    save_config(config)
    reload_config()
    await async_reload_services(preload_transcription=False)
    return {"message": f"Provider '{display_name}' set as default"}


# ── Rerank Provider CRUD ───────────────────────────────────


@router.get("/rerank/providers")
def list_rerank_providers():
    config = get_config()
    result = []
    for p in config.rerank.providers:
        d = _public_dump(p)
        d["is_builtin"] = False
        d["is_loaded"] = True
        result.append(d)
    return result


@router.post("/rerank/providers")
@_serialize_writes
async def add_rerank_provider(provider: RerankProviderConfig):
    config = get_config()
    added = _add_to_provider_list(config.rerank.providers, provider)
    save_config(config)
    reload_config()
    await async_reload_services(preload_transcription=False)
    return _public_dump(added)


@router.put("/rerank/providers/{provider_id}")
@_serialize_writes
async def update_rerank_provider(provider_id: str, update: dict = Body()):
    config = get_config()
    found = _apply_provider_update(
        config.rerank.providers,
        provider_id,
        update,
        int_fields={"top_k"},
        bool_fields={"is_default"},
    )
    if not found:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    save_config(config)
    reload_config()
    await async_reload_services(preload_transcription=False)
    return _public_dump(found)


@router.delete("/rerank/providers/{provider_id}")
@_serialize_writes
async def delete_rerank_provider(provider_id: str):
    config = get_config()
    target, rest = _remove_provider(config.rerank.providers, provider_id, promote=True)
    if not target:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    label = _provider_display_name(target, provider_id)
    config.rerank.providers = rest
    save_config(config)
    reload_config()
    return {"message": f"Provider '{label}' deleted"}


@router.post("/rerank/providers/{provider_id}/test")
async def test_rerank_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.test_rerank")
    config = get_config()
    provider = next((p for p in config.rerank.providers if p.id == provider_id), None)
    if not provider:
        return {"success": False, "error": f"Provider '{provider_id}' not found"}
    try:
        def _test():
            from src.providers.reranker import create_reranker_provider
            _log.info("Testing rerank provider: %s (%s)", provider.name, provider.provider)
            reranker = create_reranker_provider(provider)
            results = reranker.rerank("test", ["test document"])
            if not results:
                return {"success": False, "error": "No results returned"}
            scores = [s for _, s in results]
            if all(s == 0.0 for s in scores):
                return {"success": False, "error": "All scores are zero — the model may be offline or not a reranker model"}
            _log.info("Rerank provider test passed: %s (%d results)", provider.name, len(results))
            return {"success": True, "message": "Rerank connection successful"}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _test)
    except Exception as e:
        _log.warning("Rerank provider test failed: %s — %s", provider.name, e)
        return {"success": False, "error": _clean_error(e)}


@router.post("/rerank/providers/{provider_id}/set-default")
@_serialize_writes
async def set_default_rerank_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.rerank")
    _log.info("Set default reranker: %s", provider_id)
    config = get_config()
    found = _set_exclusive_flag(config.rerank.providers, provider_id)
    if not found:
        _log.warning("Set default reranker failed: provider '%s' not found", provider_id)
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    display_name = _provider_display_name(found, provider_id)
    save_config(config)
    reload_config()
    await async_reload_services(preload_transcription=False)
    return {"message": f"Provider '{display_name}' set as default"}


# ── File Transcription Provider CRUD ─────────────────────────


def _check_models_downloaded(adapter: str, model: str | None) -> bool:
    """Check if the model files for a local ONNX adapter exist on disk.

    Readiness is determined by ``_is_downloaded`` (prefers ONNX packs under
    ``data/models/onnx/``).
    """
    from src.models.download import LOCAL_MODELS, _is_downloaded
    from src.meeting.transcription import (
        is_local_file_adapter,
        is_local_realtime_adapter,
    )

    if is_local_file_adapter(adapter):
        required_ids = ["transcription", "vad", "speaker", "punc"]
    elif is_local_realtime_adapter(adapter):
        required_ids = ["realtime"]
    else:
        return False

    for mid in required_ids:
        m = next((m for m in LOCAL_MODELS if m.id == mid), None)
        if not m or not _is_downloaded(m):
            return False
    return True


@router.get("/transcription/file-providers")
def list_file_transcription_providers():
    from src.providers.load_state import get_state

    config = get_config()
    result = []
    # Built-in local provider
    local = config.transcription.get_local_file_provider()
    d = _public_dump(local)
    downloaded = _check_models_downloaded(local.adapter, local.model)
    d["models_downloaded"] = downloaded
    # Memory load state (not the same as downloaded-on-disk)
    d["is_loaded"] = get_state("builtin-local-file") == "loaded"
    d["load_state"] = get_state("builtin-local-file")
    d["is_active"] = any(
        p.id == "builtin-local-file" and p.is_active for p in config.transcription.file_providers
    )
    result.append(d)
    # User-configured cloud providers
    for p in config.transcription.file_providers:
        if p.id.startswith("builtin-"):
            continue
        d = _public_dump(p)
        from src.meeting.transcription import is_local_file_adapter

        if is_local_file_adapter(p.adapter):
            d["models_downloaded"] = _check_models_downloaded(p.adapter, p.model)
            d["is_loaded"] = get_state(p.id) == "loaded"
            d["load_state"] = get_state(p.id)
        else:
            d["is_loaded"] = True
            d["load_state"] = "loaded"
        result.append(d)
    return result


@router.post("/transcription/file-providers")
@_serialize_writes
async def add_file_transcription_provider(provider: TranscriptionProviderConfig):
    config = get_config()
    added = _add_to_provider_list(
        config.transcription.file_providers, provider, flag="is_active"
    )
    save_config(config)
    reload_config()
    return _public_dump(added)


@router.put("/transcription/file-providers/{provider_id}")
@_serialize_writes
async def update_file_transcription_provider(provider_id: str, update: dict = Body()):
    config = get_config()
    invalidate_provider(f"file_trans:{provider_id}")
    found = _apply_provider_update(
        config.transcription.file_providers,
        provider_id,
        update,
        bool_fields={"is_active"},
        exclusive_flag="is_active",
    )
    if not found:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    save_config(config)
    reload_config()
    return _public_dump(found)


@router.delete("/transcription/file-providers/{provider_id}")
@_serialize_writes
async def delete_file_transcription_provider(provider_id: str):
    config = get_config()
    target, rest = _remove_provider(
        config.transcription.file_providers, provider_id, exclusive_flag="is_active"
    )
    if not target:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    label = _provider_display_name(target, provider_id)
    invalidate_provider(f"file_trans:{provider_id}")
    config.transcription.file_providers = rest
    save_config(config)
    reload_config()
    return {"message": f"Provider '{label}' deleted"}


@router.post("/transcription/file-providers/{provider_id}/test")
async def test_file_transcription_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.test_file_transcription")
    _log.info("Testing file transcription provider: %s", provider_id)
    config = get_config()
    provider = next(
        (p for p in config.transcription.file_providers if p.id == provider_id),
        None,
    )
    if not provider and provider_id == "builtin-local-file":
        provider = config.transcription.get_local_file_provider()
    if not provider:
        _log.warning("Test file transcription: provider '%s' not found", provider_id)
        raise HTTPException(404, "Provider not found")
    try:
        from src.meeting.transcription import create_file_transcription_provider
        from src.providers.cache import peek
        from src.providers.load_state import get_state

        from src.meeting.transcription import is_local_file_adapter

        cache_key = f"file_trans:{provider_id}"
        is_local = is_local_file_adapter(provider.adapter)

        # Local ONNX FunASR: never re-load on Test — require Load first (instant feedback).
        if is_local:
            cached = peek(cache_key)
            state = get_state(provider_id)
            if cached is None and state != "loaded":
                return {
                    "success": False,
                    "code": "not_loaded",
                    "error": (
                        "Model is not loaded in memory. "
                        "Click Load first (CPU may take 10–60s), then Test."
                    ),
                }
            instance = cached
            if instance is None:
                # State says loaded but cache empty — recover via cache create
                loop = asyncio.get_running_loop()
                instance = await loop.run_in_executor(
                    None,
                    lambda: cached_provider(
                        cache_key, lambda: create_file_transcription_provider(provider)
                    ),
                )
            effective_model = (
                getattr(instance, "_model_name", None)
                or getattr(instance, "_model", None)
                or provider.model
                or "(default)"
            )
            return {
                "success": True,
                "message": (
                    f"OK — {provider.adapter} ready in memory "
                    f"(model={effective_model}). No ASR call."
                ),
            }

        def _test():
            return cached_provider(cache_key, lambda: create_file_transcription_provider(provider))

        loop = asyncio.get_running_loop()
        instance = await loop.run_in_executor(None, _test)

        # Connectivity check for remote providers with base_url
        if provider.base_url:
            import httpx
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        f"{provider.base_url.rstrip('/')}/models",
                        headers={"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {},
                    )
                    if resp.status_code >= 500:
                        return {"success": False, "error": f"Server error: HTTP {resp.status_code}"}
            except Exception as e:
                return {"success": False, "error": f"Connectivity check failed: {e}"}

        effective_model = getattr(instance, "_model", None) or provider.model or "(default)"
        _log.info(
            "Test file transcription passed: %s (adapter=%s model=%s)",
            provider.name, provider.adapter, effective_model,
        )
        return {
            "success": True,
            "message": (
                f"OK — adapter '{provider.adapter}', model '{effective_model}' "
                f"(connectivity/load check only)"
            ),
        }
    except Exception as e:
        _log.warning("Test file transcription failed: %s (%s) - %s", provider.name, provider.adapter, e)
        return {"success": False, "error": _clean_error(e)}


def _invalidate_transcription_caches(*, kind: str) -> None:
    """Drop cached file/realtime provider instances so the next use matches config."""
    from src.providers.cache import invalidate as cache_invalidate
    from src.providers.cache import keys as cache_keys

    prefix = "file_trans:" if kind == "file" else "rt_trans:"
    for key in cache_keys():
        if key.startswith(prefix):
            cache_invalidate(key)


def _maybe_autoload_local(provider_id: str, adapter: str) -> None:
    """If activating a local ONNX FunASR provider, kick off memory load when files exist."""
    from src.meeting.transcription import is_local_asr_adapter

    if not is_local_asr_adapter(adapter):
        return
    if provider_id not in ("builtin-local-file", "builtin-local-rt"):
        return
    from src.providers.load_state import get_state
    from src.services import reload_provider, _is_builtin_model_downloaded

    if get_state(provider_id) in ("loaded", "loading"):
        return
    if not _is_builtin_model_downloaded(provider_id):
        return
    reload_provider(provider_id, loading=True)


@router.post("/transcription/file-providers/{provider_id}/set-active")
@_serialize_writes
async def set_active_file_transcription_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.transcription")
    _log.info("Set active file transcription: %s", provider_id)
    config = get_config()
    found = False
    display_name = provider_id
    adapter = ""
    for p in config.transcription.file_providers:
        if p.id == provider_id:
            p.is_active = True
            found = True
            display_name = (p.name or "").strip() or provider_id
            adapter = p.adapter
            _log.info("Activated file transcription provider: %s (%s)", p.name, p.adapter)
        else:
            p.is_active = False
    if not found:
        if provider_id.startswith("builtin-"):
            for p in config.transcription.file_providers:
                p.is_active = False
            # Replace any stale builtin entry, then append active one
            config.transcription.file_providers = [
                p for p in config.transcription.file_providers if p.id != provider_id
            ]
            builtin = config.transcription.get_local_file_provider()
            builtin.is_active = True
            config.transcription.file_providers.append(builtin)
            display_name = (builtin.name or "").strip() or provider_id
            adapter = builtin.adapter
            _log.info("Activated builtin file transcription provider: %s", adapter)
        else:
            _log.warning("Set active file transcription failed: provider '%s' not found", provider_id)
            raise HTTPException(404, f"Provider '{provider_id}' not found")
    save_config(config)
    reload_config()
    _invalidate_transcription_caches(kind="file")
    _maybe_autoload_local(provider_id, adapter)
    return {
        "message": f"Provider '{display_name}' set as active",
        "provider_id": provider_id,
        "adapter": adapter,
        "name": display_name,
    }


# ── Realtime Transcription Provider CRUD ─────────────────────


@router.get("/transcription/realtime-providers")
def list_realtime_transcription_providers():
    from src.providers.load_state import get_state

    config = get_config()
    result = []
    # Built-in local provider
    local = config.transcription.get_local_realtime_provider()
    d = _public_dump(local)
    downloaded = _check_models_downloaded(local.adapter, local.model)
    d["models_downloaded"] = downloaded
    d["is_loaded"] = get_state("builtin-local-rt") == "loaded"
    d["load_state"] = get_state("builtin-local-rt")
    d["is_active"] = any(
        p.id == "builtin-local-rt" and p.is_active for p in config.transcription.realtime_providers
    )
    result.append(d)
    # User-configured cloud providers
    for p in config.transcription.realtime_providers:
        if p.id.startswith("builtin-"):
            continue
        d = _public_dump(p)
        from src.meeting.transcription import is_local_realtime_adapter

        if is_local_realtime_adapter(p.adapter):
            d["models_downloaded"] = _check_models_downloaded(p.adapter, p.model)
            d["is_loaded"] = get_state(p.id) == "loaded"
            d["load_state"] = get_state(p.id)
        else:
            d["is_loaded"] = True
            d["load_state"] = "loaded"
        result.append(d)
    return result


@router.post("/transcription/realtime-providers")
@_serialize_writes
async def add_realtime_transcription_provider(provider: TranscriptionProviderConfig):
    config = get_config()
    added = _add_to_provider_list(
        config.transcription.realtime_providers,
        provider,
        flag="is_active",
        sweep=_same_realtime_kind,
    )
    save_config(config)
    reload_config()
    return _public_dump(added)


@router.put("/transcription/realtime-providers/{provider_id}")
@_serialize_writes
async def update_realtime_transcription_provider(provider_id: str, update: dict = Body()):
    config = get_config()
    invalidate_provider(f"rt_trans:{provider_id}")
    found = _apply_provider_update(
        config.transcription.realtime_providers,
        provider_id,
        update,
        bool_fields={"is_active"},
        exclusive_flag="is_active",
        sweep=_same_realtime_kind,
    )
    if not found:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    save_config(config)
    reload_config()
    return _public_dump(found)


@router.delete("/transcription/realtime-providers/{provider_id}")
@_serialize_writes
async def delete_realtime_transcription_provider(provider_id: str):
    config = get_config()
    target, rest = _remove_provider(
        config.transcription.realtime_providers, provider_id, exclusive_flag="is_active"
    )
    if not target:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    label = _provider_display_name(target, provider_id)
    invalidate_provider(f"rt_trans:{provider_id}")
    config.transcription.realtime_providers = rest
    save_config(config)
    reload_config()
    return {"message": f"Provider '{label}' deleted"}


@router.post("/transcription/realtime-providers/{provider_id}/test")
async def test_realtime_transcription_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.test_realtime_transcription")
    _log.info("Testing realtime transcription provider: %s", provider_id)
    config = get_config()
    provider = next(
        (p for p in config.transcription.realtime_providers if p.id == provider_id),
        None,
    )
    if not provider and provider_id == "builtin-local-rt":
        provider = config.transcription.get_local_realtime_provider()
    if not provider:
        _log.warning("Test realtime transcription: provider '%s' not found", provider_id)
        raise HTTPException(404, "Provider not found")
    try:
        from src.meeting.transcription import create_realtime_transcription_provider
        from src.providers.cache import peek
        from src.providers.load_state import get_state

        from src.meeting.transcription import is_local_realtime_adapter

        cache_key = f"rt_trans:{provider_id}"
        is_local = is_local_realtime_adapter(provider.adapter)

        if is_local:
            # CRITICAL: do not create a second model instance on Test — that was multi-10s lag.
            cached = peek(cache_key)
            state = get_state(provider_id)
            if cached is None and state != "loaded":
                return {
                    "success": False,
                    "code": "not_loaded",
                    "error": (
                        "Model is not loaded in memory. "
                        "Click Load first (CPU may take 10–60s), then Test."
                    ),
                }
            instance = cached
            if instance is None:
                loop = asyncio.get_running_loop()
                instance = await loop.run_in_executor(
                    None,
                    lambda: cached_provider(
                        cache_key,
                        lambda: create_realtime_transcription_provider(provider),
                    ),
                )
            effective_model = (
                getattr(instance, "_model_name", None)
                or provider.model
                or "(default)"
            )
            _log.info(
                "Test realtime transcription (cached): %s adapter=%s model=%s",
                provider.name, provider.adapter, effective_model,
            )
            return {
                "success": True,
                "message": (
                    f"OK — {provider.adapter} ready in memory "
                    f"(model={effective_model}). No ASR call."
                ),
            }

        loop = asyncio.get_running_loop()
        instance = await loop.run_in_executor(
            None,
            lambda: cached_provider(
                cache_key,
                lambda: create_realtime_transcription_provider(provider),
            ),
        )
        effective_model = getattr(instance, "_model", None) or provider.model or "(default)"
        return {
            "success": True,
            "message": (
                f"OK — adapter '{provider.adapter}', model '{effective_model}' "
                f"(connectivity/load check only)"
            ),
        }
    except Exception as e:
        _log.warning(
            "Test realtime transcription failed: %s (%s) - %s",
            provider.name, provider.adapter, e,
        )
        return {"success": False, "error": _clean_error(e)}


@router.post("/transcription/realtime-providers/{provider_id}/set-active")
@_serialize_writes
async def set_active_realtime_transcription_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.transcription")
    _log.info("Set active realtime transcription: %s", provider_id)
    config = get_config()
    display_name = provider_id
    adapter = ""
    providers = config.transcription.realtime_providers
    target = next((p for p in providers if p.id == provider_id), None)
    if target is not None:
        target_kind_lt = (target.adapter or "") == LIVE_TRANSLATE_ADAPTER
        for p in providers:
            if p is target:
                p.is_active = True
            elif ((p.adapter or "") == LIVE_TRANSLATE_ADAPTER) == target_kind_lt:
                # Same kind only: realtime ASR and LiveTranslate defaults are
                # independent — activating one never clears the other.
                p.is_active = False
        display_name = (target.name or "").strip() or provider_id
        adapter = target.adapter
        _log.info("Activated realtime transcription provider: %s (%s)", target.name, target.adapter)
    elif provider_id.startswith("builtin-"):
        for p in providers:
            if (p.adapter or "") != LIVE_TRANSLATE_ADAPTER:
                p.is_active = False
        config.transcription.realtime_providers = [
            p for p in providers if p.id != provider_id
        ]
        builtin = config.transcription.get_local_realtime_provider()
        builtin.is_active = True
        config.transcription.realtime_providers.append(builtin)
        display_name = (builtin.name or "").strip() or provider_id
        adapter = builtin.adapter
        _log.info("Activated builtin realtime transcription provider: %s", adapter)
    else:
        _log.warning("Set active realtime transcription failed: provider '%s' not found", provider_id)
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    save_config(config)
    reload_config()
    _invalidate_transcription_caches(kind="realtime")
    _maybe_autoload_local(provider_id, adapter)
    return {
        "message": f"Provider '{display_name}' set as active",
        "provider_id": provider_id,
        "adapter": adapter,
        "name": display_name,
    }

# ---------------------------------------------------------------------------
# Local model management
# ---------------------------------------------------------------------------

@router.get("/models/status")
def get_models_status():
    """Check download status of all local models."""
    from src.models.download import check_models_status
    return check_models_status()


@router.get("/models/state")
def get_models_state():
    """Check which models are actually loaded in memory."""
    from src.services import services
    from src.providers.load_state import get_all_details, get_all_states
    return {
        "llm_loaded": services.llm is not None,
        "embedding_loaded": services.embedding is not None,
        "reranker_loaded": services.reranker_provider is not None,
        "load_states": get_all_states(),
        # Per-provider status for Settings UI (message / error / load_s)
        "load_details": get_all_details(),
    }


@router.post("/models/download")
def start_model_download(body: dict = Body(default={})):
    """Start downloading local ONNX ASR packs from the GitHub Release (background).

    Body: ``{"model_ids": ["transcription", "realtime"]}`` or ``{}`` for full pack.
    ``hf_token`` is accepted for backward compatibility and ignored — there is no
    HuggingFace fallback; only the official release zip is used.
    """
    from src.models.download import download_model, start_download_all

    model_ids = body.get("model_ids")
    # hf_token intentionally ignored (API compat only)

    if model_ids:
        # One background job is enough — package download is serialized internally
        first = model_ids[0]
        t = threading.Thread(target=download_model, args=(first, None), daemon=True)
        t.start()
    else:
        start_download_all(None)

    return {
        "success": True,
        "message": "Download started (GitHub Release ONNX pack)",
    }


# Map download-registry model ids → built-in provider that must be unloaded
_MODEL_ID_TO_PROVIDER: dict[str, str] = {
    "transcription": "builtin-local-file",
    "vad": "builtin-local-file",
    "speaker": "builtin-local-file",
    "punc": "builtin-local-file",
    "realtime": "builtin-local-rt",
}


def _unload_providers_for_models(model_ids: list[str]) -> list[str]:
    """Unload in-memory FunASR providers that depend on deleted model files."""
    from src.providers.load_state import get_state, set_state
    from src.services import reload_provider

    unloaded: list[str] = []
    providers = { _MODEL_ID_TO_PROVIDER[mid] for mid in model_ids if mid in _MODEL_ID_TO_PROVIDER }
    for provider_id in providers:
        state = get_state(provider_id)
        if state in ("loaded", "loading", "error"):
            reload_provider(provider_id, loading=False)
            set_state(provider_id, "unloaded")
            unloaded.append(provider_id)
    return unloaded


@router.delete("/models/{model_id}")
def delete_local_model(model_id: str):
    """Delete a single local model's files from HF cache and unload dependents."""
    from src.models.download import delete_model

    unloaded = _unload_providers_for_models([model_id])
    result = delete_model(model_id)
    if not result.get("success"):
        return result
    result["unloaded_providers"] = unloaded
    return result


@router.post("/models/delete")
def delete_local_models(body: dict = Body(default={})):
    """Delete one or more local model packs from disk.

    Body: ``{"model_ids": ["transcription", "vad", ...]}``
    """
    from src.models.download import delete_models

    model_ids = body.get("model_ids") or []
    if not isinstance(model_ids, list) or not model_ids:
        return {"success": False, "error": "model_ids required"}
    ids = [str(x) for x in model_ids]
    unloaded = _unload_providers_for_models(ids)
    result = delete_models(ids)
    result["unloaded_providers"] = unloaded
    return result


@router.post("/models/{model_id}/toggle-load")
async def toggle_model_load(model_id: str, body: dict = Body(default={})):
    """Load or unload a built-in local model.

    Body (optional)::

        {"action": "load"} | {"action": "unload"}

    If ``action`` is omitted, toggles: unloaded/error → load, loaded → unload.
    While a load is in progress, ``status`` is always ``"loading"`` (never
    pretends to be loaded). Poll ``GET /models/state`` until ``loaded``/``error``.
    """
    import logging
    _log = logging.getLogger("api.models")
    from src.providers.load_state import get_detail, get_state, set_state
    from src.services import reload_provider, _is_builtin_model_downloaded

    action = (body or {}).get("action")
    if action is not None:
        action = str(action).strip().lower()
        if action not in ("load", "unload"):
            return {
                "success": False,
                "model_id": model_id,
                "status": get_state(model_id),
                "loaded": get_state(model_id) == "loaded",
                "error": "action must be 'load' or 'unload'",
            }

    current = get_state(model_id)

    # Explicit or implicit intent
    if action == "load":
        want_load = True
    elif action == "unload":
        want_load = False
    else:
        # toggle
        want_load = current in ("unloaded", "error")

    if want_load:
        if current == "loaded":
            detail = get_detail(model_id)
            return {
                "success": True,
                "model_id": model_id,
                "status": "loaded",
                "loaded": True,
                "message": detail.get("message") or "Already loaded in memory",
            }
        if current == "loading":
            detail = get_detail(model_id)
            return {
                "success": True,
                "model_id": model_id,
                "status": "loading",
                "loaded": False,
                "message": detail.get("message")
                or "Already loading into memory…",
            }
        if not _is_builtin_model_downloaded(model_id):
            _log.warning("Load denied: %s — model not downloaded", model_id)
            return {
                "success": False,
                "model_id": model_id,
                "status": "error",
                "loaded": False,
                "error": (
                    "Model files are not fully downloaded. "
                    "Download them first, then click Load."
                ),
            }
        _log.info("Load requested: %s", model_id)
        reload_provider(model_id, loading=True)
        # Clamp: while background thread runs, clients must see loading —
        # never report loaded unless state is actually loaded.
        status = get_state(model_id)
        if status not in ("loaded", "error"):
            status = "loading"
            # ensure detail exists even if reload returned early
            if get_state(model_id) != "loading":
                set_state(
                    model_id,
                    "loading",
                    message="Loading into memory on CPU…",
                )
        detail = get_detail(model_id)
        return {
            "success": True,
            "model_id": model_id,
            "status": status,
            "loaded": status == "loaded",
            "message": detail.get("message")
            or (
                "Loading into memory on CPU — typically 10–60s. "
                "Status updates automatically."
                if status == "loading"
                else "Ready in memory"
            ),
        }

    # unload
    if current == "unloaded":
        return {
            "success": True,
            "model_id": model_id,
            "status": "unloaded",
            "loaded": False,
            "message": "Already unloaded",
        }
    _log.info("Unload requested: %s", model_id)
    reload_provider(model_id, loading=False)
    return {
        "success": True,
        "model_id": model_id,
        "status": "unloaded",
        "loaded": False,
        "message": "Unloaded from memory. Disk files kept.",
    }


@router.get("/models/setup-status")
def get_setup_status():
    """Check if first-run model setup is needed."""
    from src.models.download import check_models_status
    models = check_models_status()
    return {
        "setup_completed": True,  # No more first-run embedding/reranker setup
        "models": models,
        "categories": ["transcription"],
    }


@router.post("/models/setup-complete")
def mark_setup_complete():
    """Mark the first-run model setup as completed (no-op: local models are transcription-only)."""
    return {"success": True, "message": "Model setup marked as completed"}


# ── Version ───────────────────────────────────────────────────


def _get_app_version() -> str:
    """Installed package metadata first; then nearest pyproject.toml."""
    try:
        from importlib.metadata import version

        return version("sinkduce")
    except Exception:
        pass
    import re
    from pathlib import Path

    here = Path(__file__).resolve()
    for parent in here.parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            text = pyproject.read_text(encoding="utf-8")
            m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
            if m:
                return m.group(1)
        except Exception:
            break
    return "0.0.0"


@router.get("/version")
def get_version():
    """Return the current application version and repo info for update checking."""
    return {
        "version": _get_app_version(),
        "repo": "superdd-coder/sinkduce",
    }


@router.post("/desktop/open-url")
def desktop_open_url(payload: dict = Body(...)):
    """Open an http(s) URL in the system browser. Desktop runtime only."""
    from src.config import is_desktop_runtime
    from src.desktop_open import allowed_external_url, open_external_url

    if not is_desktop_runtime():
        raise HTTPException(status_code=403, detail="desktop only")
    raw = payload.get("url") if isinstance(payload, dict) else None
    url = allowed_external_url(raw if isinstance(raw, str) else "")
    if not url:
        raise HTTPException(status_code=400, detail="url must be http(s)")
    open_external_url(url)
    return {"ok": True}


