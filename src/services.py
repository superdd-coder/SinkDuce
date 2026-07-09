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
from src.rag.variant_fetcher import VariantFetcher
from src.rag.decomposer import Decomposer
from src.rag.aggregator import Aggregator
from src.rag.agentic_query import AgenticQueryService
from src.rag.contextual import ContextualRetrieval
from src.rag.chunker import TextChunker, ParagraphChunker

logger = logging.getLogger(__name__)


def _preload_transcription_providers(config):
    """Load local transcription providers at startup when they are the default.

    Loads models with a staggered start so the first model can acquire the
    global load semaphore before the second one tries, serializing the
    actual model loading to avoid OOM from loading two large FunASR models
    simultaneously.  The semaphore itself (Semaphore(1)) handles the rest.
    """
    import time as _time
    from src.providers.cache import invalidate as cache_invalidate
    from src.providers.load_state import get_state

    def _trigger_load(cfg, providers, label):
        """Fire-and-forget load for one transcription provider."""
        if not cfg or not cfg.adapter.startswith("funasr_local"):
            return
        if not _is_builtin_model_downloaded(cfg.id):
            logger.info("Built-in %s transcription model not downloaded, deactivating", label)
            for p in providers:
                if p.id == cfg.id:
                    p.is_active = False
            save_config(config)
            return
        if get_state(cfg.id) in ("loaded", "loading"):
            logger.info("Transcription provider %s already %s, skipping", cfg.id, get_state(cfg.id))
            return
        reload_provider(cfg.id, loading=True)

    # --- File transcription (starts first) ---
    _trigger_load(
        config.transcription.active_file_provider,
        config.transcription.file_providers,
        "file",
    )
    # Clean up inactive providers from cache
    for key in list(_provider_cache_snapshot()):
        if key.startswith("file_trans:"):
            file_cfg = config.transcription.active_file_provider
            if not file_cfg or not file_cfg.adapter.startswith("funasr_local"):
                cache_invalidate(key)
                logger.info("Unloaded inactive local file transcription provider: %s", key)

    # Stagger: let file model acquire the semaphore before realtime starts
    _time.sleep(3)

    # --- Realtime transcription (starts 3s later, waits on semaphore) ---
    _trigger_load(
        config.transcription.active_realtime_provider,
        config.transcription.realtime_providers,
        "realtime",
    )
    for key in list(_provider_cache_snapshot()):
        if key.startswith("rt_trans:"):
            rt_cfg = config.transcription.active_realtime_provider
            if not rt_cfg or not rt_cfg.adapter.startswith("funasr_local"):
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
    from src.providers.load_state import set_state, get_state
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
        if get_state(model_id) in ("loaded", "loading"):
            return  # Already loaded or loading — avoid duplicate load
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


def _resolve_chat_llm(config):
    """Find an LLM provider with function_call_model_ids for ChatboxAgent.

    Preference order:
    1. Provider matching config.default_chat_model (by model name)
    2. Provider with is_default=True and function_call_model_ids
    3. First provider with function_call_model_ids
    """
    from src.providers.llm import create_llm_for_provider

    eligible = [p for p in config.llm.providers if p.function_call_model_ids]
    if not eligible:
        return None

    # Prefer default_chat_model
    if config.default_chat_model:
        for p in eligible:
            if p.default_model == config.default_chat_model or p.model == config.default_chat_model:
                return create_llm_for_provider(p)
        # Try by provider id
        for p in eligible:
            if p.id == config.default_chat_model:
                return create_llm_for_provider(p)

    # Prefer the is_default provider (same as RAG default)
    default_eligible = [p for p in eligible if p.is_default]
    if default_eligible:
        return create_llm_for_provider(default_eligible[0])

    # Fallback: first eligible
    return create_llm_for_provider(eligible[0])


class Services:
    config = None
    db: QdrantManager = None
    embedding = None
    llm = None
    reranker_provider = None
    retriever: Retriever = None
    reranker: Reranker = None
    direct_query: DirectQueryModule = None
    variant_fetcher: VariantFetcher = None
    catalog: CollectionCatalog = None
    decomposer: Decomposer = None
    aggregator: Aggregator = None
    agentic_query: AgenticQueryService = None
    contextual: ContextualRetrieval = None
    chunker: TextChunker = None
    session_store = None
    chatbox_agent = None


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

    # Invalidate enrichment LLM provider cache so a config change (e.g. base_url,
    # api_key, model) takes effect on the next upload. The enrichment path caches
    # its provider instance to avoid per-upload cold-start (TCP+TLS handshake)
    # on the slow summary LLM call — without invalidation, a stale provider would
    # silently keep being used after the user updates Settings.
    from src.providers.cache import invalidate as _cache_invalidate
    for _key in list(_provider_cache_snapshot()):
        if _key.startswith("llm:enrich:"):
            _cache_invalidate(_key)

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
        services.variant_fetcher = VariantFetcher(
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
            variant_fetcher=services.variant_fetcher,
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
        services.variant_fetcher = None
        services.catalog = None
        services.decomposer = None
        services.aggregator = None
        services.agentic_query = None
        services.contextual = None

    # Clean up inactive transcription providers from cache
    _preload_transcription_providers(config)

    # Session store (sqlite3, zero new deps)
    from src.db.sessions import SessionStore
    services.session_store = SessionStore()

    # ChatboxAgent — requires an LLM provider with function_call_model_ids
    services.chatbox_agent = None
    if services.agentic_query and services.session_store:
        chat_llm = _resolve_chat_llm(config)
        if chat_llm:
            from src.chatbox.agent import ChatboxAgent
            services.chatbox_agent = ChatboxAgent(
                session_store=services.session_store,
                chat_llm=chat_llm,
                agentic_service=services.agentic_query,
                direct_module=services.direct_query,
            )
            logger.info("ChatboxAgent initialized with model=%s",
                        getattr(chat_llm, "_model", "unknown"))
        else:
            logger.warning(
                "No chat LLM with function_call_model_ids configured — "
                "chat endpoint will return 503. Enable Function Calling "
                "on model(s) in LLM Settings and configure default_chat_model."
            )
