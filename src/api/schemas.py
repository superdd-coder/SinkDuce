from __future__ import annotations

from pydantic import BaseModel


class QueryRequest(BaseModel):
    question: str
    collection: str = "default"
    collections: list[str] | None = None  # Multi-collection support
    use_agent: bool = True
    search_mode: str | None = None  # dense / hybrid — per-query, not per-collection
    sparse_llm_tokenize: bool | None = None  # per-query override for LLM keyword extraction
    top_k: int | None = None
    rerank_top_k: int | None = None
    use_reranker: bool | None = None  # per-query override
    max_iterations: int | None = None  # per-query override for agentic iterations
    min_score: float | None = None  # similarity threshold (0.0-1.0)
    provider_id: str | None = None
    model: str | None = None
    temperature: float | None = None  # per-query temperature override


class SourceItem(BaseModel):
    text: str
    score: float
    metadata: dict = {}


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem]
    iterations: int
    query_used: str


class CollectionCreateRequest(BaseModel):
    name: str
    dimensions: int | None = 1024
    chunk_mode: str = "normal"
    parent_strategy: str = "paragraph"
    chunk_size: int = 512
    chunk_overlap: int = 64
    buffer_ratio: float = 0.5
    parent_chunk_size: int = 1024
    parent_chunk_overlap: int = 128
    child_chunk_size: int = 128
    child_chunk_overlap: int = 32
    # Per-collection config: search
    search_mode: str = "dense"
    sparse_llm_tokenize: bool = True
    # Per-collection config: contextual
    contextual_enabled: bool = True
    contextual_window: int = 1
    # Per-collection config: agentic
    agent_enabled: bool = True
    agent_max_iterations: int = 3
    # Per-collection config: embedding (optional, uses global defaults)
    embedding_provider_id: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_batch_size: int | None = None
    # Per-collection config: rerank (optional, uses global defaults)
    rerank_provider_id: str | None = None
    rerank_provider: str | None = None
    rerank_model: str | None = None
    rerank_base_url: str | None = None
    rerank_api_key: str | None = None
    rerank_top_k: int = 5
    # Per-collection config: file type restriction
    allowed_file_types: list[str] | None = None


class CollectionInfo(BaseModel):
    name: str
    vectors_count: int
    points_count: int
    status: str


class CollectionConfigUpdateRequest(BaseModel):
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    buffer_ratio: float | None = None
    parent_strategy: str | None = None
    parent_chunk_size: int | None = None
    parent_chunk_overlap: int | None = None
    child_chunk_size: int | None = None
    child_chunk_overlap: int | None = None
    search_mode: str | None = None
    sparse_llm_tokenize: bool | None = None
    contextual_enabled: bool | None = None
    contextual_window: int | None = None
    agent_enabled: bool | None = None
    agent_max_iterations: int | None = None
    embedding_provider_id: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_batch_size: int | None = None
    enriching_llm_provider: str | None = None
    enriching_llm_model: str | None = None
    rerank_provider_id: str | None = None
    rerank_provider: str | None = None
    rerank_model: str | None = None
    rerank_base_url: str | None = None
    rerank_api_key: str | None = None
    rerank_top_k: int | None = None
    allowed_file_types: list[str] | None = None
    cloud_parsing: bool | None = None


class ConfigUpdateRequest(BaseModel):
    section: str
    data: dict


# ── Recall ─────────────────────────────────────────────────


class RecallSearchRequest(BaseModel):
    query: str
    collections: list[str] = ["default"]
    search_mode: str | None = None  # dense / hybrid — None falls back to collection config → global
    top_k: int | None = None  # None falls back to collection config → global config
    rerank_top_k: int | None = None  # None falls back to collection config → global config
    use_reranker: bool | None = None  # None falls back to True (matching chat behavior)
    use_agent: bool = False
    min_score: float = 0.0  # similarity threshold
    sparse_llm_tokenize: bool | None = None  # per-query override for LLM keyword extraction in hybrid mode
    rerank_provider_id: str | None = None  # temporary rerank provider override
    max_iterations: int | None = None  # per-query override for agentic iterations


class RecallResult(BaseModel):
    id: str
    text: str
    score: float
    source: str
    collection: str
    chunk_index: int
    chunk_type: str  # normal / parent / child
    context: str | None = None
    parent_id: str | None = None
    children: list[RecallResult] | None = None  # for parent-child grouped display


class RecallSearchResponse(BaseModel):
    results: list[RecallResult]
    time_ms: int
    total: int
    query_used: str
    agent_iterations: int = 0
    search_params: dict = {}
    context: str = ""


class RecallBenchmarkRequest(BaseModel):
    collections: list[str] = ["default"]
    queries: list[str]
    top_k: int | None = None
    use_agent: bool = False
    min_score: float | None = None  # relevance threshold for benchmark metrics


class BenchmarkResult(BaseModel):
    total_queries: int
    avg_time_ms: float
    results: list[dict]
    metrics: dict


# ── Recall Evaluation ─────────────────────────────────────


class EvalTestCase(BaseModel):
    id: str
    query: str
    target_chunk_id: str = ""
    target_source: str = ""
    created_at: str = ""


class EvalRequest(BaseModel):
    collection: str
    top_k: int | None = None
    search_mode: str | None = None
    use_reranker: bool | None = None
    rerank_top_k: int | None = None
    min_score: float = 0.0  # similarity threshold passed through to retrieval
    sparse_llm_tokenize: bool | None = None  # per-query override for LLM keyword extraction in hybrid mode
    rerank_provider_id: str | None = None  # temporary rerank provider override
