from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def get_data_dir() -> Path:
    """Data root. ``SINKDUCE_DATA`` overrides; otherwise cwd ``data/`` (Docker)."""
    raw = (os.environ.get("SINKDUCE_DATA") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("data").resolve()


def get_models_dir() -> Path:
    """Model cache. ``HF_HOME`` wins (Docker sets it); else ``<data>/models``."""
    raw = (os.environ.get("HF_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return get_data_dir() / "models"


def get_frontend_dist() -> Path:
    """SPA build. ``SINKDUCE_FRONTEND_DIST`` overrides; else repo ``frontend/dist``."""
    raw = (os.environ.get("SINKDUCE_FRONTEND_DIST") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"


def resolve_qdrant_host(cfg_host: str) -> str:
    """Prefer ``SINKDUCE_QDRANT_HOST``. Ignore generic ``QDRANT_HOST``."""
    return (os.environ.get("SINKDUCE_QDRANT_HOST") or "").strip() or cfg_host


def resolve_qdrant_port(cfg_port: int) -> int:
    raw = (os.environ.get("SINKDUCE_QDRANT_PORT") or "").strip()
    return int(raw) if raw else cfg_port


def resolve_bind_host(cfg_host: str) -> str:
    return (os.environ.get("SINKDUCE_HOST") or "").strip() or cfg_host


def resolve_bind_port(cfg_port: int) -> int:
    raw = (os.environ.get("SINKDUCE_PORT") or "").strip()
    return int(raw) if raw else cfg_port


def is_desktop_runtime() -> bool:
    return (os.environ.get("SINKDUCE_DESKTOP") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _advertise_host(bind_host: str) -> str:
    """Host clients should use. Wildcard binds are not a connectable URL."""
    host = (bind_host or "").strip()
    if not host or host in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return host


def advertised_listen() -> tuple[str, int]:
    """Public (host, port) for this process — env first, then config.yaml."""
    env_host = (os.environ.get("SINKDUCE_HOST") or "").strip()
    env_port = (os.environ.get("SINKDUCE_PORT") or "").strip()
    cfg_host = ""
    cfg_port = 18900
    if not env_host or not env_port:
        try:
            cfg = get_config()
            cfg_host = cfg.server.host
            cfg_port = int(cfg.server.api_port)
        except Exception:
            pass
    host = _advertise_host(env_host or cfg_host)
    port = int(env_port) if env_port else cfg_port
    return host, port


def sysaudio_helper_reachable(url: str, timeout: float = 0.15) -> bool:
    """True if the desktop PCM helper is accepting connections.

    WKWebView fetch to a dead 127.0.0.1:18950 throws TypeError: Load failed.
    /health must not advertise the helper unless it is actually up.
    """
    from urllib.parse import urlparse
    import socket

    raw = (url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def health_payload() -> dict:
    host, port = advertised_listen()
    body: dict = {
        "status": "ok",
        "host": host,
        "port": port,
        "mcp_url": f"http://{host}:{port}/mcp",
    }
    if is_desktop_runtime():
        body["desktop"] = True
        audio = (os.environ.get("SINKDUCE_SYS_AUDIO") or "").strip().rstrip("/")
        if audio and sysaudio_helper_reachable(audio):
            body["system_audio"] = audio
    if (get_data_dir() / "mock-update").is_file():
        body["mock_update"] = True
    return body


DATA_DIR = get_data_dir()
CONFIG_PATH = DATA_DIR / "config.yaml"
TEMPLATE_PATH = Path("config.yaml.template")


class LLMProviderConfig(BaseModel):
    id: str = ""
    name: str = ""
    provider: str = "openai_compatible"
    model: str = "deepseek-chat"
    base_url: str = ""
    api_key: str = ""
    is_default: bool = False
    selected_models: list[str] = []
    default_model: str = ""
    visual_model_ids: list[str] = []
    function_call_model_ids: list[str] = []


class LLMConfig(BaseModel):
    providers: list[LLMProviderConfig] = []


class EmbeddingProviderConfig(BaseModel):
    id: str = ""
    name: str = ""
    provider: str = "openai_compatible"  # local, openai_compatible (alias: remote)
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    dimensions: int = 0  # auto-detected from collection at query time
    batch_size: int = 10
    is_default: bool = False


class EmbeddingConfig(BaseModel):
    providers: list[EmbeddingProviderConfig] = []

    @property
    def default(self) -> EmbeddingProviderConfig | None:
        return next((p for p in self.providers if p.is_default), None)


class RerankProviderConfig(BaseModel):
    id: str = ""
    name: str = ""
    provider: str = "none"  # local, cohere, qwen
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    top_k: int = 0  # 0 = use query-time parameter
    is_default: bool = False


class RerankConfig(BaseModel):
    providers: list[RerankProviderConfig] = []

    @property
    def default(self) -> RerankProviderConfig | None:
        return next((p for p in self.providers if p.is_default), None)


class ParsingConfig(BaseModel):
    default_chunk_size: int = 512
    supported_file_types: list[str] = ["pdf", "docx", "txt", "md", "xlsx", "pptx", "csv", "json", "html"]


class RAGConfig(BaseModel):
    """Agentic RAG defaults."""
    top_k: int = 20
    rerank_top_k: int = 5
    max_parallel_queries: int = 10
    max_iterations: int = 4  # most queries converge in 2-3 iterations
    default_search_mode: str = "hybrid"  # "dense" or "hybrid"
    min_score: float = 0.0  # dense mode similarity threshold


class DirectRAGConfig(BaseModel):
    """Direct RAG defaults — used when Agentic is not enabled."""
    top_k: int = 20
    rerank_top_k: int = 5
    default_search_mode: str = "hybrid"
    use_reranker: bool = True
    min_score: float = 0.0  # dense mode similarity threshold


class EnrichmentConfig(BaseModel):
    use_batch: bool = False
    batch_poll_interval: int = 30
    max_parallel_context: int = 50
    enrichment_model: str = ""  # Library LLM: "providerId|modelName"; "" = default card
    meeting_model: str = ""     # Meeting Summary: "providerId|modelName"; "" = default card
    live_summary_model: str = ""  # In-meeting live summary; "" = follow meeting_model
    agentic_query_model: str = ""  # Agentic/Direct/recall/variants/keywords; "" = default card
    note_distill_model: str = ""   # Note distillation; "" = default card
    meeting_thinking: bool = False  # meeting summary thinking (off by default)
    meeting_thinking_effort: str = "low"  # low | medium | high (DeepSeek: low/high/max; DashScope token budget)


class QdrantConfig(BaseModel):
    host: str = "qdrant"
    port: int = 6333
    default_collection: str = "default"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    api_port: int = 18900
    ui_port: int = 18901
    # NOTE: MCP now shares the FastAPI process and is mounted under /mcp on the
    # same api_port. The legacy ``mcp_port`` field has been removed because no
    # code reads it — MCP is reached at ``http://<host>:<api_port>/mcp``.


class TranscriptionProviderConfig(BaseModel):
    id: str = ""
    name: str = ""
    # Plugin registry name. Valid values depend on which transcription
    # adapter packages are installed; see
    # file_transcription_registry / realtime_transcription_registry.
    adapter: str = ""
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None
    is_active: bool = False
    # --- Optional: DashScope LiveTranslate dedicated workspace ---
    # When set, the realtime translation session targets
    # wss://{workspace_id}.<region>.maas.aliyuncs.com/api-ws/v1/realtime
    # instead of the shared dashscope endpoint.
    workspace_id: str | None = None
    # --- Optional fields for local FunASR providers ---
    device: str | None = None          # "cpu" | "cuda" | "mps"
    vad_model: str | None = None       # VAD model name (default: fsmn-vad)
    punc_model: str | None = None      # Punctuation model (default: ct-punc)
    spk_model: str | None = None       # Speaker model (default: cam++)
    # --- Optional: custom language hints for this provider ---
    # Each entry: {"code": "zh", "label": "中文"}
    language_hints_config: list[dict] | None = None


class TranscriptionConfig(BaseModel):
    file_providers: list[TranscriptionProviderConfig] = []
    realtime_providers: list[TranscriptionProviderConfig] = []
    local_device: str = "cpu"  # "cpu" | "cuda" | "mps" | "auto"

    @property
    def active_file_provider(self) -> TranscriptionProviderConfig | None:
        return next((p for p in self.file_providers if p.is_active), None)

    @property
    def active_realtime_provider(self) -> TranscriptionProviderConfig | None:
        return next((p for p in self.realtime_providers if p.is_active), None)

    def get_local_file_provider(self) -> TranscriptionProviderConfig:
        """Return a built-in local file transcription provider (ONNX runtime)."""
        return TranscriptionProviderConfig(
            id="builtin-local-file",
            name="default-Local-Transcription",
            adapter="funasr_onnx",
            model="FunAudioLLM/SenseVoiceSmall",
            vad_model="funasr/fsmn-vad",
            punc_model="funasr/ct-punc",
            spk_model="funasr/campplus",
            is_active=False,
            device=self.local_device,
        )

    def get_local_realtime_provider(self) -> TranscriptionProviderConfig:
        """Return a built-in local realtime transcription provider (ONNX)."""
        return TranscriptionProviderConfig(
            id="builtin-local-rt",
            name="default-Local-Realtime",
            adapter="funasr_onnx_realtime",
            model="funasr/paraformer-zh-streaming",
            is_active=False,
            device=self.local_device,
        )


class MinerUConfig(BaseModel):
    enabled: bool = False
    api_token: str = ""
    base_url: str = "https://mineru.net/api/v4"
    model_version: str = "pipeline"  # pipeline | vlm | MinerU-HTML
    is_ocr: bool = False
    enable_formula: bool = True
    enable_table: bool = True
    language: str = "ch"  # ch, en, japan, korean, latin, arabic, cyrillic, etc.
    poll_interval: float = 3.0  # seconds between status polls
    poll_timeout: float = 300.0  # max wait time in seconds


class WebSearchConfig(BaseModel):
    """Internet search credentials for Chat (Tavily).

    Settings only stores the API key (and optional depth/limits).
    The on/off switch lives in the Chat UI and is sent per request as
    ``web_search_enabled``. Even when on, each search still requires HITL
    confirmation. Results are tagged ``source_type=web``.
    """
    # Legacy field — ignored for the master switch (Chat UI owns that).
    # Kept so old config.yaml with enabled: true still loads.
    enabled: bool = False
    provider: str = "tavily"
    api_key: str = ""
    max_results: int = 5
    search_depth: str = "basic"  # basic | advanced
    # Seconds to wait for the user confirm dialog during streaming chat
    confirm_timeout_sec: int = 120


class AppConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    rerank: RerankConfig = RerankConfig()
    rag: RAGConfig = RAGConfig()
    direct_rag: DirectRAGConfig = DirectRAGConfig()
    parsing: ParsingConfig = ParsingConfig()
    qdrant: QdrantConfig = QdrantConfig()
    server: ServerConfig = ServerConfig()
    transcription: TranscriptionConfig = TranscriptionConfig()
    mineru: MinerUConfig = MinerUConfig()
    web_search: WebSearchConfig = WebSearchConfig()
    enrichment: EnrichmentConfig = EnrichmentConfig()
    visual_model_id: str | None = None
    default_chat_model: str | None = None
    locale: Literal["en", "zh-CN"] = "en"


def _resolve_config_path(path: str | Path | None = None) -> Path:
    """Resolve config path: explicit arg > data/config.yaml > config.yaml."""
    if path:
        return Path(path)
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    return Path("config.yaml")


def _migrate_embedding(raw: dict) -> dict:
    """Migrate old single-embedding format to providers list."""
    if "providers" in raw:
        return raw
    if "provider" not in raw and "model" not in raw:
        return raw
    provider = EmbeddingProviderConfig(
        id=str(uuid.uuid4()),
        name=raw.get("model", "Default"),
        provider=raw.get("provider", "openai_compatible"),
        model=raw.get("model", ""),
        base_url=raw.get("base_url", ""),
        api_key=raw.get("api_key", ""),
        dimensions=raw.get("dimensions", 512),
        batch_size=raw.get("batch_size", 10),
        is_default=True,
    )
    return {"providers": [provider.model_dump()]}


def _migrate_rerank(raw: dict) -> dict:
    """Migrate old single-rerank format to providers list."""
    if "providers" in raw:
        return raw
    if "provider" not in raw and "model" not in raw:
        return raw
    provider = RerankProviderConfig(
        id=str(uuid.uuid4()),
        name=raw.get("model", "Default"),
        provider=raw.get("provider", "none"),
        model=raw.get("model", ""),
        base_url=raw.get("base_url", ""),
        api_key=raw.get("api_key", ""),
        top_k=raw.get("top_k", 5),
        is_default=True,
    )
    return {"providers": [provider.model_dump()]}


def _migrate_transcription(raw: dict) -> dict:
    """Map removed pytorch FunASR adapter names to ONNX in config.yaml.

    Local ASR is ONNX-only. Old configs may still say ``funasr_local`` /
    ``funasr_local_realtime``; rewrite so registry lookups and language hints work.
    """
    if not raw:
        return raw
    mapping = {
        "funasr_local": "funasr_onnx",
        "funasr_local_realtime": "funasr_onnx_realtime",
    }
    for key in ("file_providers", "realtime_providers"):
        providers = raw.get(key)
        if not isinstance(providers, list):
            continue
        for p in providers:
            if not isinstance(p, dict):
                continue
            adapter = p.get("adapter")
            if adapter in mapping:
                p["adapter"] = mapping[adapter]
    return raw


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = _resolve_config_path(path)
    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raw = {}

    # Backward compat: convert old single-provider LLM format to providers list
    llm_raw = raw.get("llm", {})
    if "providers" not in llm_raw and ("model" in llm_raw or "base_url" in llm_raw):
        provider = LLMProviderConfig(
            id=str(uuid.uuid4()),
            name=llm_raw.get("model", "default"),
            provider=llm_raw.get("provider", "openai_compatible"),
            model=llm_raw.get("model", "deepseek-chat"),
            base_url=llm_raw.get("base_url", ""),
            api_key=llm_raw.get("api_key", ""),
            is_default=True,
        )
        raw["llm"] = {"providers": [provider.model_dump()]}

    # Backward compat: convert old single-embedding format to providers list
    emb_raw = raw.get("embedding", {})
    if emb_raw:
        raw["embedding"] = _migrate_embedding(emb_raw)

    # Backward compat: convert old single-rerank format to providers list
    rerank_raw = raw.get("rerank", {})
    if rerank_raw:
        raw["rerank"] = _migrate_rerank(rerank_raw)

    # Legacy FunASR pytorch adapter names → ONNX
    tx_raw = raw.get("transcription")
    if isinstance(tx_raw, dict):
        raw["transcription"] = _migrate_transcription(tx_raw)

    # Filter unknown keys to prevent ValidationError
    valid_keys = set(AppConfig.model_fields.keys())
    filtered = {k: v for k, v in raw.items() if k in valid_keys}

    return AppConfig(**filtered)


def save_config(config: AppConfig, path: str | Path | None = None) -> None:
    from src.atomic_io import write_text_atomic

    save_path = Path(path) if path else CONFIG_PATH
    data = config.model_dump(exclude_none=True)
    text = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    write_text_atomic(save_path, text)
    logger.info("Config saved: %s", save_path)


_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Read the on-disk config.

    Always reload so a stale in-memory snapshot cannot be saved back and
    wipe providers written by a concurrent request (oneshot / Settings).
    """
    return reload_config()


def reload_config() -> AppConfig:
    global _config
    _config = load_config()
    return _config
