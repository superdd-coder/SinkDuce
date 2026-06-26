from __future__ import annotations

import logging
import threading

from src.config import get_config, save_config
from src.db.qdrant import QdrantManager
from src.providers.embedding import create_embedding_provider
from src.providers.llm import create_llm_provider
from src.providers.reranker import create_reranker_provider
from src.providers.cache import get_or_create as cached_provider
from src.rag.retriever import Retriever
from src.rag.reranker import Reranker
from src.rag.direct_query import DirectQueryModule
from src.rag.catalog import CollectionCatalog
from src.rag.rewrite_loop import RewriteLoop
from src.rag.decomposer import Decomposer
from src.rag.aggregator import Aggregator
from src.rag.agentic_query import AgenticQueryService
from src.rag.contextual import ContextualRetrieval
from src.rag.chunker import TextChunker, ParagraphChunker

logger = logging.getLogger(__name__)


def _preload_transcription_providers(config):
    """Load local transcription providers at startup when they are the default."""
    from src.providers.cache import invalidate as cache_invalidate
    from src.providers.load_state import get_state

    # --- File transcription ---
    file_cfg = config.transcription.active_file_provider
    if file_cfg is None:
        file_cfg = config.transcription.get_local_file_provider()
    if file_cfg.adapter.startswith("funasr_local"):
        if _is_builtin_model_downloaded(file_cfg.id):
            if get_state(file_cfg.id) != "loaded":
                reload_provider(file_cfg.id, loading=True)
        else:
            logger.info("Built-in file transcription model not downloaded, deactivating")
            for p in config.transcription.file_providers:
                if p.id == file_cfg.id:
                    p.is_active = False
            save_config(config)
    else:
        for key in list(_provider_cache_snapshot()):
            if key.startswith("file_trans:"):
                cache_invalidate(key)
                logger.info("Unloaded inactive local file transcription provider: %s", key)

    # --- Realtime transcription ---
    rt_cfg = config.transcription.active_realtime_provider
    if rt_cfg is None:
        rt_cfg = config.transcription.get_local_realtime_provider()
    if rt_cfg.adapter.startswith("funasr_local"):
        if _is_builtin_model_downloaded(rt_cfg.id):
            if get_state(rt_cfg.id) != "loaded":
                reload_provider(rt_cfg.id, loading=True)
        else:
            logger.info("Built-in realtime transcription model not downloaded, deactivating")
            for p in config.transcription.realtime_providers:
                if p.id == rt_cfg.id:
                    p.is_active = False
            save_config(config)
    else:
        for key in list(_provider_cache_snapshot()):
            if key.startswith("rt_trans:"):
                cache_invalidate(key)
                logger.info("Unloaded inactive local realtime transcription provider: %s", key)


def _provider_cache_snapshot() -> list[str]:
    """Return cached provider keys (for checking what's loaded)."""
    from src.providers.cache import _cache
    return list(_cache.keys())


def _is_builtin_model_downloaded(config_section) -> bool:
    """Check if the built-in model's files exist on disk before attempting load.

    For file transcription, checks ALL sub-models (transcription, vad,
    speaker, punc) — any missing means the provider cannot be loaded.
    """
    from src.models.download import _is_downloaded, LOCAL_MODELS

    config_to_download_ids: dict[str, list[str]] = {
        "builtin-local-file": ["transcription", "vad", "speaker", "punc"],
        "builtin-local-rt": ["realtime"],
    }
    download_ids = config_to_download_ids.get(config_section)
    if not download_ids:
        return True

    for download_id in download_ids:
        model = next((m for m in LOCAL_MODELS if m.id == download_id), None)
        if not model:
            continue
        if not _is_downloaded(model):
            missing_display = model.display_name
            logger.warning(
                "Built-in model not downloaded: %s (%s)",
                download_id, missing_display,
            )
            return False
    return True


def reload_provider(model_id: str, *, loading: bool):
    """Reload or unload a single provider without full init_services()."""
    from src.providers.load_state import set_state

    logger.info("Reload provider: %s loading=%s", model_id, loading)

    if model_id in ("builtin-local-file", "builtin-local-rt"):
        _reload_transcription_provider(model_id, loading)


def _reload_transcription_provider(model_id: str, loading: bool):
    """Handle load/unload for a single transcription provider."""
    from src.providers.cache import invalidate as cache_invalidate
    from src.providers.load_state import set_state
    config = get_config()

    if model_id == "builtin-local-file":
        cache_key = f"file_trans:{model_id}"
        provider_cfg = config.transcription.active_file_provider or config.transcription.get_local_file_provider()
        create_fn = __import__('src.meeting.transcription', fromlist=['create_file_transcription_provider']).create_file_transcription_provider
    elif model_id == "builtin-local-rt":
        cache_key = f"rt_trans:{model_id}"
        provider_cfg = config.transcription.active_realtime_provider or config.transcription.get_local_realtime_provider()
        create_fn = __import__('src.meeting.transcription', fromlist=['create_realtime_transcription_provider']).create_realtime_transcription_provider
    else:
        return

    if loading:
        if not _is_builtin_model_downloaded(model_id):
            logger.warning("Cannot load transcription provider: model not downloaded")
            return
        cache_invalidate(cache_key)
        set_state(model_id, "loading")
        logger.info("Loading transcription provider: %s (%s)", model_id, provider_cfg.adapter)

        def _load():
            try:
                from src.providers.load_state import acquire_load_slot, release_load_slot
                acquire_load_slot()
                try:
                    cached_provider(cache_key, lambda: create_fn(provider_cfg))
                    set_state(model_id, "loaded")
                    logger.info("Transcription provider loaded: %s (%s)", model_id, provider_cfg.adapter)
                finally:
                    release_load_slot()
            except Exception as e:
                set_state(model_id, "error")
                logger.warning("Failed to load transcription provider: %s (%s) - %s", model_id, provider_cfg.adapter, e)

        threading.Thread(target=_load, daemon=True).start()
    else:
        cache_invalidate(cache_key)
        set_state(model_id, "unloaded")
        logger.info("Transcription provider unloaded: %s", model_id)


class Services:
    config = None
    db: QdrantManager = None
    embedding = None
    llm = None
    reranker_provider = None
    retriever: Retriever = None
    reranker: Reranker = None
    direct_query: DirectQueryModule = None
    rewrite_loop: RewriteLoop = None
    catalog: CollectionCatalog = None
    decomposer: Decomposer = None
    aggregator: Aggregator = None
    agentic_query: AgenticQueryService = None
    contextual: ContextualRetrieval = None
    chunker: TextChunker = None


services = Services()


def reload_services():
    """Reinitialize services after config change with rollback on failure."""
    global services
    old_services = services
    try:
        init_services()
    except Exception:
        services = old_services
        raise


async def async_reload_services():
    """Async version — runs init_services() in a thread to avoid blocking the event loop."""
    import asyncio
    loop = asyncio.get_running_loop()
    global services
    old_services = services

    def _do_reload():
        nonlocal old_services
        old_services = services
        init_services()

    try:
        await loop.run_in_executor(None, _do_reload)
    except Exception:
        services = old_services
        raise


def init_services():
    config = get_config()
    services.config = config

    services.db = QdrantManager(host=config.qdrant.host, port=config.qdrant.port)

    # Embedding provider — only from user config
    emb_cfg = config.embedding.default
    try:
        services.embedding = create_embedding_provider(emb_cfg) if emb_cfg else None
    except Exception as e:
        logger.error("Failed to create embedding provider '%s': %s", emb_cfg.name if emb_cfg else "none", e)
        services.embedding = None

    # LLM provider — user-configured (OpenAI, Ollama, etc.)
    if config.llm.providers:
        services.llm = create_llm_provider(config.llm)
        logger.info("LLM provider created from user config")

    # Reranker provider — only from user config
    rerank_cfg = config.rerank.default
    if rerank_cfg:
        logger.info("Reranker config found: name=%s, provider=%s, model=%s, is_default=%s",
                     rerank_cfg.name, rerank_cfg.provider, rerank_cfg.model, rerank_cfg.is_default)
    else:
        logger.warning("No default reranker provider in config (providers=%d)",
                       len(config.rerank.providers))
    try:
        services.reranker_provider = create_reranker_provider(rerank_cfg) if rerank_cfg else None
    except Exception as e:
        logger.error("Failed to create reranker provider '%s': %s", rerank_cfg.name if rerank_cfg else "none", e)
        services.reranker_provider = None
    logger.info("Reranker provider initialized: %s", type(services.reranker_provider).__name__ if services.reranker_provider else "None")

    # Auto-create default collection only on first run
    default_col = config.qdrant.default_collection
    if services.embedding and services.embedding.dimensions > 0:
        try:
            if not services.db.collection_exists(default_col):
                existing = services.db.list_collections()
                if not existing:
                    services.db.create_collection(default_col, vector_size=services.embedding.dimensions)
        except Exception:
            # Race condition or transient Qdrant error — ignore, collection likely exists
            pass

    services.chunker = ParagraphChunker(
        max_tokens=config.parsing.default_chunk_size,
        buffer_ratio=0.5,
    ) if services.embedding else None

    services.retriever = Retriever(db=services.db, embedding=services.embedding) if services.embedding else None
    services.reranker = Reranker(provider=services.reranker_provider, top_k=config.rag.rerank_top_k) if services.reranker_provider else None

    # LLM + embedding dependent services
    if services.llm and services.retriever:
        services.direct_query = DirectQueryModule(
            retriever=services.retriever,
            db=services.db,
            reranker=services.reranker,
            llm=services.llm,
        )
        services.rewrite_loop = RewriteLoop(
            direct_module=services.direct_query,
            llm=services.llm,
            reranker=services.reranker,
        )
        services.catalog = CollectionCatalog(
            db=services.db,
            llm=services.llm,
        )
        services.decomposer = Decomposer(llm=services.llm)
        services.aggregator = Aggregator(llm=services.llm)
        services.agentic_query = AgenticQueryService(
            direct_module=services.direct_query,
            rewrite_loop=services.rewrite_loop,
            catalog=services.catalog,
            decomposer=services.decomposer,
            aggregator=services.aggregator,
            llm=services.llm,
        )
        services.contextual = ContextualRetrieval(
            llm=services.llm,
            context_window=1,
        )
    else:
        services.direct_query = None
        services.rewrite_loop = None
        services.catalog = None
        services.decomposer = None
        services.aggregator = None
        services.agentic_query = None
        services.contextual = None

    # Clean up inactive transcription providers from cache
    _preload_transcription_providers(config)
