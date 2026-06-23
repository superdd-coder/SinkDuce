from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid

from fastapi import APIRouter, Body

from src.api.schemas import ConfigUpdateRequest
from src.config import get_config, save_config, reload_config, LLMProviderConfig, EmbeddingProviderConfig, RerankProviderConfig, TranscriptionProviderConfig
from src.services import async_reload_services
from src.providers.cache import get_or_create as cached_provider, invalidate as invalidate_provider

logger = logging.getLogger(__name__)

router = APIRouter()

# 模型列表缓存 (key: "section:base_url", value: {"models": [...], "timestamp": float})
_model_cache: dict[str, dict] = {}
MODEL_CACHE_TTL = 300  # 5分钟缓存

def _clean_error(e: Exception) -> str:
    """Extract a user-friendly error message, stripping raw HTML from HTTP errors."""
    try:
        import httpx
        if isinstance(e, httpx.HTTPStatusError):
            return f"HTTP {e.response.status_code}: {e.response.reason_phrase}"
    except ImportError:
        pass
    msg = str(e)  # intentionally str(), not _clean_error — we're inside _clean_error
    # Trim raw HTML bodies from the message
    if "<!doctype html>" in msg.lower() or "<html" in msg.lower():
        msg = msg.split("<html")[0].split("<!doctype")[0].strip()
    return msg[:500]


@router.get("/config")
def get_current_config():
    config = get_config()
    data = config.model_dump(exclude_none=True)
    return data


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
        "file_transcription": _entries(file_transcription_registry),
        "realtime_transcription": _entries(realtime_transcription_registry),
    }


# Top-level AppConfig fields that can be updated individually
_TOP_LEVEL_FIELDS = {"visual_model_id"}


@router.put("/config")
async def update_config(req: ConfigUpdateRequest):
    config = get_config()

    # Handle top-level AppConfig fields
    if req.section in _TOP_LEVEL_FIELDS:
        for key, value in req.data.items():
            if hasattr(config, key):
                setattr(config, key, value)
        save_config(config)
        reload_config()
        from src.services import services
        services.config = get_config()
        return {"message": f"Config '{req.section}' updated"}

    section_data = getattr(config, req.section, None)
    if section_data is None:
        return {"error": f"Unknown config section: {req.section}"}

    # Only allow setting declared Pydantic model fields
    allowed_keys = set(getattr(type(section_data), "model_fields", {}).keys())
    _int_fields = {"dimensions", "batch_size", "top_k"}
    for key, value in req.data.items():
        if key not in allowed_keys:
            continue
        if key in _int_fields and value is not None:
            value = int(value)
        setattr(section_data, key, value)

    save_config(config)
    reload_config()
    # Keep services.config in sync so runtime reads see updated values
    from src.services import services
    services.config = get_config()
    return {"message": f"Config section '{req.section}' updated"}


@router.post("/config/reload")
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
                if data:
                    for key, value in data.items():
                        if hasattr(provider_cfg, key) and value:
                            setattr(provider_cfg, key, value)

                from src.providers.llm import create_llm_provider
                provider = create_llm_provider(provider_cfg)
                provider.generate("Hello")
                return {"success": True, "message": "LLM connection successful"}

            elif section == "embedding":
                providers = config.embedding.providers
                if not providers:
                    return {"success": False, "error": "No embedding providers configured"}
                provider_cfg = next((p for p in providers if p.is_default), providers[0])
                if data:
                    for key, value in data.items():
                        if hasattr(provider_cfg, key) and value:
                            if key in {"dimensions", "batch_size"}:
                                value = int(value)
                            setattr(provider_cfg, key, value)

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
                if data:
                    for key, value in data.items():
                        if hasattr(provider_cfg, key) and value:
                            if key == "top_k":
                                value = int(value)
                            setattr(provider_cfg, key, value)

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
                if "api_key" in data and data["api_key"]:
                    api_key = data["api_key"]
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
                if "api_key" in data and data["api_key"]:
                    api_key = data["api_key"]
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
                if "api_key" in data and data["api_key"]:
                    api_key = data["api_key"]
                if "provider" in data and data["provider"]:
                    provider = data["provider"]
        else:
            base_url = ""
            api_key = ""
            provider = ""
            if data:
                base_url = data.get("base_url", "") or ""
                api_key = data.get("api_key", "") or ""
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
    config = get_config()
    result = []
    for p in config.llm.providers:
        d = p.model_dump()
        d["is_builtin"] = False
        d["is_loaded"] = True
        result.append(d)
    return result


@router.post("/llm/providers")
async def add_llm_provider(provider: LLMProviderConfig):
    config = get_config()
    if not provider.id:
        provider.id = str(uuid.uuid4())
    config.llm.providers.append(provider.model_copy())
    save_config(config)
    reload_config()
    await async_reload_services()
    return provider.model_dump()


@router.put("/llm/providers/{provider_id}")
async def update_llm_provider(provider_id: str, update: dict = Body()):
    config = get_config()
    _int_fields = {"max_tokens", "max_concurrent_requests"}
    _bool_fields = {"is_default"}
    for i, p in enumerate(config.llm.providers):
        if p.id == provider_id:
            for key, value in update.items():
                if key == "id":
                    continue
                if hasattr(p, key):
                    if key in _int_fields and value is not None:
                        value = int(value)
                    elif key in _bool_fields:
                        value = bool(value)
                    setattr(config.llm.providers[i], key, value)
            save_config(config)
            reload_config()
            await async_reload_services()
            return config.llm.providers[i].model_dump()
    return {"error": f"Provider '{provider_id}' not found"}


@router.delete("/llm/providers/{provider_id}")
async def delete_llm_provider(provider_id: str):
    config = get_config()
    original_len = len(config.llm.providers)
    config.llm.providers = [p for p in config.llm.providers if p.id != provider_id]
    if len(config.llm.providers) == original_len:
        return {"error": f"Provider '{provider_id}' not found"}
    save_config(config)
    reload_config()
    return {"message": f"Provider '{provider_id}' deleted"}


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
async def set_default_llm_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.llm")
    _log.info("Set default LLM: %s", provider_id)
    config = get_config()
    found = False
    for p in config.llm.providers:
        if p.id == provider_id:
            p.is_default = True
            found = True
        else:
            p.is_default = False
    if not found:
        _log.warning("Set default LLM failed: provider '%s' not found", provider_id)
        return {"error": f"Provider '{provider_id}' not found"}
    save_config(config)
    reload_config()
    await async_reload_services()
    return {"message": f"Provider '{provider_id}' set as default"}


# ── Embedding Provider CRUD ────────────────────────────────


@router.get("/embedding/providers")
def list_embedding_providers():
    config = get_config()
    result = []
    for p in config.embedding.providers:
        d = p.model_dump()
        d["is_builtin"] = False
        result.append(d)
    return result


@router.post("/embedding/providers")
async def add_embedding_provider(provider: EmbeddingProviderConfig):
    config = get_config()
    if not provider.id:
        provider.id = str(uuid.uuid4())
    if provider.is_default:
        for p in config.embedding.providers:
            p.is_default = False
    elif not config.embedding.providers:
        provider.is_default = True
    config.embedding.providers.append(provider.model_copy())
    save_config(config)
    reload_config()
    await async_reload_services()
    return provider.model_dump()


@router.put("/embedding/providers/{provider_id}")
async def update_embedding_provider(provider_id: str, update: dict = Body()):
    config = get_config()
    _int_fields = {"dimensions", "batch_size"}
    _bool_fields = {"is_default"}
    for i, p in enumerate(config.embedding.providers):
        if p.id == provider_id:
            for key, value in update.items():
                if key == "id":
                    continue
                if hasattr(p, key):
                    if key in _int_fields and value is not None:
                        value = int(value)
                    elif key in _bool_fields:
                        value = bool(value)
                    setattr(config.embedding.providers[i], key, value)
            save_config(config)
            reload_config()
            await async_reload_services()
            return config.embedding.providers[i].model_dump()
    return {"error": f"Provider '{provider_id}' not found"}


@router.delete("/embedding/providers/{provider_id}")
async def delete_embedding_provider(provider_id: str):
    config = get_config()
    target = next((p for p in config.embedding.providers if p.id == provider_id), None)
    if not target:
        return {"error": f"Provider '{provider_id}' not found"}
    config.embedding.providers = [p for p in config.embedding.providers if p.id != provider_id]
    # If we deleted the default, auto-promote the first remaining
    if target.is_default and config.embedding.providers:
        config.embedding.providers[0].is_default = True
    save_config(config)
    reload_config()
    return {"message": f"Provider '{provider_id}' deleted"}


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
async def set_default_embedding_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.embedding")
    _log.info("Set default embedding: %s", provider_id)
    config = get_config()
    found = False
    for p in config.embedding.providers:
        if p.id == provider_id:
            p.is_default = True
            found = True
        else:
            p.is_default = False
    if not found:
        _log.warning("Set default embedding failed: provider '%s' not found", provider_id)
        return {"error": f"Provider '{provider_id}' not found"}
    save_config(config)
    reload_config()
    await async_reload_services()
    return {"message": f"Provider '{provider_id}' set as default"}


# ── Rerank Provider CRUD ───────────────────────────────────


@router.get("/rerank/providers")
def list_rerank_providers():
    config = get_config()
    result = []
    for p in config.rerank.providers:
        d = p.model_dump()
        d["is_builtin"] = False
        d["is_loaded"] = True
        result.append(d)
    return result


@router.post("/rerank/providers")
async def add_rerank_provider(provider: RerankProviderConfig):
    config = get_config()
    if not provider.id:
        provider.id = str(uuid.uuid4())
    if provider.is_default:
        for p in config.rerank.providers:
            p.is_default = False
    elif not config.rerank.providers:
        provider.is_default = True
    config.rerank.providers.append(provider.model_copy())
    save_config(config)
    reload_config()
    await async_reload_services()
    return provider.model_dump()


@router.put("/rerank/providers/{provider_id}")
async def update_rerank_provider(provider_id: str, update: dict = Body()):
    config = get_config()
    _int_fields = {"top_k"}
    _bool_fields = {"is_default"}
    for i, p in enumerate(config.rerank.providers):
        if p.id == provider_id:
            for key, value in update.items():
                if key == "id":
                    continue
                if hasattr(p, key):
                    if key in _int_fields and value is not None:
                        value = int(value)
                    elif key in _bool_fields:
                        value = bool(value)
                    setattr(config.rerank.providers[i], key, value)
            save_config(config)
            reload_config()
            await async_reload_services()
            return config.rerank.providers[i].model_dump()
    return {"error": f"Provider '{provider_id}' not found"}


@router.delete("/rerank/providers/{provider_id}")
async def delete_rerank_provider(provider_id: str):
    config = get_config()
    target = next((p for p in config.rerank.providers if p.id == provider_id), None)
    if not target:
        return {"error": f"Provider '{provider_id}' not found"}
    config.rerank.providers = [p for p in config.rerank.providers if p.id != provider_id]
    # If we deleted the default, auto-promote the first remaining
    if target.is_default and config.rerank.providers:
        config.rerank.providers[0].is_default = True
    save_config(config)
    reload_config()
    return {"message": f"Provider '{provider_id}' deleted"}


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
async def set_default_rerank_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.rerank")
    _log.info("Set default reranker: %s", provider_id)
    config = get_config()
    found = False
    for p in config.rerank.providers:
        if p.id == provider_id:
            p.is_default = True
            found = True
        else:
            p.is_default = False
    if not found:
        _log.warning("Set default reranker failed: provider '%s' not found", provider_id)
        return {"error": f"Provider '{provider_id}' not found"}
    save_config(config)
    reload_config()
    await async_reload_services()
    return {"message": f"Provider '{provider_id}' set as default"}


# ── File Transcription Provider CRUD ─────────────────────────


def _check_models_downloaded(adapter: str, model: str | None) -> bool:
    """Check if the model files for a local adapter exist on disk."""
    from src.models.download import LOCAL_MODELS, _is_downloaded

    if adapter == "funasr_local":
        required_ids = ["transcription", "vad", "speaker", "punc"]
    elif adapter == "funasr_local_realtime":
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
    config = get_config()
    result = []
    # Built-in local provider
    local = config.transcription.get_local_file_provider()
    d = local.model_dump()
    downloaded = _check_models_downloaded(local.adapter, local.model)
    d["models_downloaded"] = downloaded
    d["is_loaded"] = downloaded
    d["is_active"] = downloaded and any(p.id == "builtin-local-file" and p.is_active for p in config.transcription.file_providers)
    result.append(d)
    # User-configured cloud providers
    for p in config.transcription.file_providers:
        if p.id.startswith("builtin-"):
            continue
        d = p.model_dump()
        if p.adapter.startswith("funasr_local"):
            d["models_downloaded"] = _check_models_downloaded(p.adapter, p.model)
            d["is_loaded"] = d["models_downloaded"]
        else:
            d["is_loaded"] = True
        result.append(d)
    return result


@router.post("/transcription/file-providers")
async def add_file_transcription_provider(provider: TranscriptionProviderConfig):
    config = get_config()
    if not provider.id:
        provider.id = str(uuid.uuid4())
    if not config.transcription.file_providers:
        provider.is_active = True
    # Only one active provider at a time
    if provider.is_active:
        for p in config.transcription.file_providers:
            p.is_active = False
    config.transcription.file_providers.append(provider.model_copy())
    save_config(config)
    reload_config()
    return provider.model_dump()


@router.put("/transcription/file-providers/{provider_id}")
async def update_file_transcription_provider(provider_id: str, update: dict = Body()):
    config = get_config()
    _bool_fields = {"is_active"}
    invalidate_provider(f"file_trans:{provider_id}")
    for i, p in enumerate(config.transcription.file_providers):
        if p.id == provider_id:
            for key, value in update.items():
                if key == "id":
                    continue
                if hasattr(p, key):
                    if key in _bool_fields:
                        value = bool(value)
                    setattr(config.transcription.file_providers[i], key, value)
            # Deactivate all other providers when activating this one
            if update.get("is_active"):
                for j, other in enumerate(config.transcription.file_providers):
                    if j != i:
                        other.is_active = False
            save_config(config)
            reload_config()
            return config.transcription.file_providers[i].model_dump()
    return {"error": f"Provider '{provider_id}' not found"}


@router.delete("/transcription/file-providers/{provider_id}")
async def delete_file_transcription_provider(provider_id: str):
    config = get_config()
    invalidate_provider(f"file_trans:{provider_id}")
    original_len = len(config.transcription.file_providers)
    config.transcription.file_providers = [
        p for p in config.transcription.file_providers if p.id != provider_id
    ]
    if len(config.transcription.file_providers) == original_len:
        return {"error": f"Provider '{provider_id}' not found"}
    save_config(config)
    reload_config()
    return {"message": f"Provider '{provider_id}' deleted"}


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
        return {"error": "Provider not found"}
    try:
        from src.meeting.transcription import create_file_transcription_provider

        cache_key = f"file_trans:{provider_id}"

        def _test():
            return cached_provider(cache_key, lambda: create_file_transcription_provider(provider))

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _test)

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

        _log.info("Test file transcription passed: %s (%s)", provider.name, provider.adapter)
        return {
            "success": True,
            "message": f"Adapter '{provider.adapter}' loaded successfully",
        }
    except Exception as e:
        _log.warning("Test file transcription failed: %s (%s) - %s", provider.name, provider.adapter, e)
        return {"success": False, "error": _clean_error(e)}


@router.post("/transcription/file-providers/{provider_id}/set-active")
async def set_active_file_transcription_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.transcription")
    _log.info("Set active file transcription: %s", provider_id)
    config = get_config()
    found = False
    for p in config.transcription.file_providers:
        if p.id == provider_id:
            p.is_active = True
            found = True
            _log.info("Activated file transcription provider: %s (%s)", p.name, p.adapter)
        else:
            p.is_active = False
    if not found:
        if provider_id.startswith("builtin-"):
            # Builtin provider: deactivate all, then persist active state
            for p in config.transcription.file_providers:
                p.is_active = False
            builtin = config.transcription.get_local_file_provider()
            builtin.is_active = True
            config.transcription.file_providers.append(builtin)
            _log.info("Activated builtin file transcription provider")
        else:
            _log.warning("Set active file transcription failed: provider '%s' not found", provider_id)
            return {"error": f"Provider '{provider_id}' not found"}
    save_config(config)
    reload_config()
    return {"message": f"Provider '{provider_id}' set as active"}


# ── Realtime Transcription Provider CRUD ─────────────────────


@router.get("/transcription/realtime-providers")
def list_realtime_transcription_providers():
    config = get_config()
    result = []
    # Built-in local provider
    local = config.transcription.get_local_realtime_provider()
    d = local.model_dump()
    downloaded = _check_models_downloaded(local.adapter, local.model)
    d["models_downloaded"] = downloaded
    d["is_loaded"] = downloaded
    d["is_active"] = downloaded and any(p.id == "builtin-local-rt" and p.is_active for p in config.transcription.realtime_providers)
    result.append(d)
    # User-configured cloud providers
    for p in config.transcription.realtime_providers:
        if p.id.startswith("builtin-"):
            continue
        d = p.model_dump()
        if p.adapter.startswith("funasr_local"):
            d["models_downloaded"] = _check_models_downloaded(p.adapter, p.model)
            d["is_loaded"] = d["models_downloaded"]
        else:
            d["is_loaded"] = True
        result.append(d)
    return result


@router.post("/transcription/realtime-providers")
async def add_realtime_transcription_provider(provider: TranscriptionProviderConfig):
    config = get_config()
    if not provider.id:
        provider.id = str(uuid.uuid4())
    if not config.transcription.realtime_providers:
        provider.is_active = True
    # Only one active provider at a time
    if provider.is_active:
        for p in config.transcription.realtime_providers:
            p.is_active = False
    config.transcription.realtime_providers.append(provider.model_copy())
    save_config(config)
    reload_config()
    return provider.model_dump()


@router.put("/transcription/realtime-providers/{provider_id}")
async def update_realtime_transcription_provider(provider_id: str, update: dict = Body()):
    config = get_config()
    invalidate_provider(f"rt_trans:{provider_id}")
    _bool_fields = {"is_active"}
    for i, p in enumerate(config.transcription.realtime_providers):
        if p.id == provider_id:
            for key, value in update.items():
                if key == "id":
                    continue
                if hasattr(p, key):
                    if key in _bool_fields:
                        value = bool(value)
                    setattr(config.transcription.realtime_providers[i], key, value)
            # Deactivate all other providers when activating this one
            if update.get("is_active"):
                for j, other in enumerate(config.transcription.realtime_providers):
                    if j != i:
                        other.is_active = False
            save_config(config)
            reload_config()
            return config.transcription.realtime_providers[i].model_dump()
    return {"error": f"Provider '{provider_id}' not found"}


@router.delete("/transcription/realtime-providers/{provider_id}")
async def delete_realtime_transcription_provider(provider_id: str):
    config = get_config()
    invalidate_provider(f"rt_trans:{provider_id}")
    original_len = len(config.transcription.realtime_providers)
    config.transcription.realtime_providers = [
        p for p in config.transcription.realtime_providers if p.id != provider_id
    ]
    if len(config.transcription.realtime_providers) == original_len:
        return {"error": f"Provider '{provider_id}' not found"}
    save_config(config)
    reload_config()
    return {"message": f"Provider '{provider_id}' deleted"}


@router.post("/transcription/realtime-providers/{provider_id}/test")
async def test_realtime_transcription_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.test_realtime_transcription")
    _log.info("Testing realtime transcription provider (direct): %s", provider_id)
    config = get_config()
    provider = next(
        (p for p in config.transcription.realtime_providers if p.id == provider_id),
        None,
    )
    if not provider and provider_id == "builtin-local-rt":
        provider = config.transcription.get_local_realtime_provider()
    if not provider:
        _log.warning("Test realtime transcription: provider '%s' not found", provider_id)
        return {"error": "Provider not found"}
    try:
        from src.meeting.transcription import create_realtime_transcription_provider

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, create_realtime_transcription_provider, provider)
        _log.info("Test realtime transcription passed (direct): %s (%s)", provider.name, provider.adapter)
        return {
            "success": True,
            "message": f"Adapter '{provider.adapter}' initialized successfully",
        }
    except Exception as e:
        _log.warning("Test realtime transcription failed (direct): %s (%s) - %s", provider.name, provider.adapter, e)
        return {"success": False, "error": _clean_error(e)}


@router.post("/transcription/realtime-providers/{provider_id}/set-active")
async def set_active_realtime_transcription_provider(provider_id: str):
    import logging
    _log = logging.getLogger("api.transcription")
    _log.info("Set active realtime transcription: %s", provider_id)
    config = get_config()
    found = False
    for p in config.transcription.realtime_providers:
        if p.id == provider_id:
            p.is_active = True
            found = True
            _log.info("Activated realtime transcription provider: %s (%s)", p.name, p.adapter)
        else:
            p.is_active = False
    if not found:
        if provider_id.startswith("builtin-"):
            # Builtin provider: deactivate all, then persist active state
            for p in config.transcription.realtime_providers:
                p.is_active = False
            builtin = config.transcription.get_local_realtime_provider()
            builtin.is_active = True
            config.transcription.realtime_providers.append(builtin)
            _log.info("Activated builtin realtime transcription provider")
        else:
            _log.warning("Set active realtime transcription failed: provider '%s' not found", provider_id)
            return {"error": f"Provider '{provider_id}' not found"}
    save_config(config)
    reload_config()
    return {"message": f"Provider '{provider_id}' set as active"}


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
    from src.providers.load_state import get_all_states
    return {
        "llm_loaded": services.llm is not None,
        "embedding_loaded": services.embedding is not None,
        "reranker_loaded": services.reranker_provider is not None,
        "load_states": get_all_states(),
    }


@router.post("/models/download")
def start_model_download(body: dict = Body(default={})):
    """Start downloading models in the background.

    Body: {"hf_token": "hf_xxx", "model_ids": ["llm", "embedding"]} or {} for all
    """
    from src.models.download import download_model, start_download_all

    hf_token = body.get("hf_token")
    model_ids = body.get("model_ids")

    if model_ids:
        for mid in model_ids:
            t = threading.Thread(target=download_model, args=(mid, hf_token), daemon=True)
            t.start()
    else:
        start_download_all(hf_token)

    return {"success": True, "message": "Download started"}


@router.post("/models/{model_id}/toggle-load")
async def toggle_model_load(model_id: str):
    """Toggle a built-in model between loaded and unloaded."""
    import logging
    _log = logging.getLogger("api.models")
    from src.providers.load_state import set_state
    from src.services import reload_provider, _is_builtin_model_downloaded

    # Check current load state
    from src.providers.load_state import get_state
    current = get_state(model_id)
    if current in ("unloaded", "error"):
        # Verify model files exist before attempting load
        if not _is_builtin_model_downloaded(model_id):
            _log.warning("Load denied: %s — model not downloaded", model_id)
            return {
                "success": False,
                "model_id": model_id,
                "loaded": False,
                "error": (
                    "Model files are not fully downloaded. "
                    "Please download them first via Settings → Local Models → Download."
                ),
            }
        loading = True
        _log.info("Load requested: %s", model_id)
    else:
        set_state(model_id, "unloaded")
        loading = False
        _log.info("Unload requested: %s", model_id)

    reload_provider(model_id, loading=loading)

    loaded = loading
    _log.info("Toggle complete: %s loaded=%s", model_id, loaded)
    return {"success": True, "model_id": model_id, "loaded": loaded}


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
    """Read the application version from pyproject.toml."""
    import re
    from pathlib import Path
    pyproject = Path(__file__).resolve().parent.parent.parent.parent / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
            if m:
                return m.group(1)
        except Exception:
            pass
    return "0.0.0"


@router.get("/version")
def get_version():
    """Return the current application version and repo info for update checking."""
    return {
        "version": _get_app_version(),
        "repo": "superdd-coder/sinkduce",
    }
