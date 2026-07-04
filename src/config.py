from __future__ import annotations

import logging
import uuid
from pathlib import Path

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DATA_DIR = Path("data").resolve()
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
    enrichment_model: str = ""  # provider id for enrichment LLM; "" = use default LLM
    meeting_model: str = ""     # provider id for meeting summary LLM; "" = use default LLM
    meeting_thinking: bool = True  # enable thinking mode for meeting summary


class QdrantConfig(BaseModel):
    host: str = "qdrant"
    port: int = 6333
    default_collection: str = "default"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    api_port: int = 18900
    ui_port: int = 18901
    mcp_port: int = 18902


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
        """Return a built-in local file transcription provider."""
        return TranscriptionProviderConfig(
            id="builtin-local-file",
            name="default-Local-Transcription",
            adapter="funasr_local",
            model="FunAudioLLM/SenseVoiceSmall",
            is_active=False,
            device=self.local_device,
        )

    def get_local_realtime_provider(self) -> TranscriptionProviderConfig:
        """Return a built-in local realtime transcription provider."""
        return TranscriptionProviderConfig(
            id="builtin-local-rt",
            name="default-Local-Realtime",
            adapter="funasr_local_realtime",
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
    enrichment: EnrichmentConfig = EnrichmentConfig()
    visual_model_id: str | None = None
    default_chat_model: str | None = None


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

    # Filter unknown keys to prevent ValidationError
    valid_keys = set(AppConfig.model_fields.keys())
    filtered = {k: v for k, v in raw.items() if k in valid_keys}

    return AppConfig(**filtered)


def save_config(config: AppConfig, path: str | Path | None = None) -> None:
    save_path = Path(path) if path else CONFIG_PATH
    save_path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(exclude_none=True)
    with open(save_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    logger.info("Config saved: %s", save_path)


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> AppConfig:
    global _config
    _config = load_config()
    return _config
