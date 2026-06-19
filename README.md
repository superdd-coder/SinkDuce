# SinkDuce

<p align="center"><img src="logo.png" alt="SinkDuce" width="120" /></p>

[中文](README_CN.md)

A knowledge base and meeting memory system built for project managers. Ingest project documents, transcribe meetings, and chat with your project data and meeting minutes — everything automatically organized, summarized, and searchable.

> *Note: All project data shown in screenshots is AI-generated for demonstration purposes.*

## Quick Start

**Prerequisites**: Docker

```bash
git clone https://github.com/superdd-coder/sinkduce.git
cd sinkduce

# Optional: customize ports
cp .env.template .env

# Build and start
docker compose up -d --build
```

Open [http://localhost:18900](http://localhost:18900). On first launch:
1. Download the local transcription models you need
2. Go to **Settings** → add an **LLM provider** (any OpenAI-compatible API)
3. Add an **Embedding provider** and create a Project Database

### Updating

```bash
git pull
docker compose up -d --build
```

Docker rebuilds the image with the latest code while preserving your `data/` directory (database, config, history).

### OneShot Setting with DashScope API

If you use [DashScope (阿里云百炼)](https://bailian.console.aliyun.com/), you can configure all providers — LLM, Embedding, Reranker, File Transcription, Realtime Transcription — in one step. Go to **Settings** → **LLM Providers** tab → click **OneShot Setting (DashScope API)**, enter your DashScope API Key, and each provider is created with sensible defaults:

| Service | Default Model |
|---------|---------------|
| LLM | deepseek-v4-flash |
| Embedding | text-embedding-v4 |
| Reranker | qwen3-rerank |
| File Transcription | fun-asr |
| Realtime Transcription | fun-asr-realtime |

You can override any model name in the dialog before applying. After setup, adjust individual provider settings or switch defaults as needed.

## How It Works

SinkDuce connects two worlds — meetings and documents — into a unified project memory.

### Meeting → Database Pipeline

When you transcribe a meeting, SinkDuce captures everything: the transcript with speaker diarization, auto-generated detail/summary/action items, and speaker notes. With one click, you allocate the meeting content to a Project Database:

![Meeting to Database](screenshots/meeting-ingest.jpg)

The meeting's transcript, summary, and notes become searchable documents in the project collection. You can also bulk-ingest multiple meetings at once, mapping each meeting's content to the relevant project section.

### Project Database

Each project gets its own collection with independent chunking and embedding configs. Upload contracts, proposals, spreadsheets, and more — the system automatically parses, chunks, enriches with context, and indexes everything.

![Project Database](screenshots/project-database.jpg)

**Auto-summarization**: Every document gets a structured summary (data points, facts, insights). Collections get consolidated overviews with automatic conflict detection when documents contradict each other.

### AI Chat

Chat with your project data across collections. The Agentic RAG pipeline handles query analysis, multi-collection retrieval, reranking, and iterative refinement — all with streaming responses.

![Chat](screenshots/chat.jpg)

## Features

### Meeting Transcription

![Meeting Summary](screenshots/meeting-summary.jpg)

- **File transcription** — upload recordings for offline transcription with automatic speaker identification
- **Real-time transcription** — WebSocket streaming for live meetings
- **Local models** — FunASR SenseVoiceSmall works fully offline, no cloud required
- **Cloud providers** — DashScope FunASR, OpenAI-compatible APIs
- **Auto-generated summaries** — detail, summary, and action items extracted by LLM
- **Meeting notes** — structured meeting documentation
- **Hot words** — custom vocabulary libraries for domain-specific terminology

![Realtime](screenshots/meeting-realtime.jpg)

### RAG Pipeline

- **Agentic RAG** — Analyze → Route → Retrieve → Grade → Decompose → Rerank → Synthesize
- **Hybrid search** — dense vector + sparse BM25 via Qdrant
- **Streaming responses** — SSE-based real-time chat output
- **Reranking** — Cohere, Qwen, or local models via OpenAI-compatible API
- **Recall evaluation** — built-in benchmarking with adjustable parameters

![Evaluation](screenshots/evaluation.jpg)

### Platform

- **Provider system** — pluggable LLM / Embedding / Reranker / Transcription backends
- **OpenAI-compatible** — works with OpenAI, DeepSeek, Qwen, Ollama, vLLM, LM Studio, etc.
- **Zero-config** — all settings via Web UI, no manual YAML editing
- **MCP server** — expose RAG as tools for AI agents
- **Task queue** — async document processing with global LLM concurrency control

![Settings](screenshots/setting.jpg)

## Architecture

```
frontend/          React 19 + Vite + Tailwind CSS + Shadcn UI
src/
  api/             FastAPI routes
  db/              Qdrant client
  mcp/             MCP server
  parsers/         12 format parsers
  providers/       LLM, Embedding, Reranker, Transcription
  rag/             Chunker, Retriever, Agent, Reranker, Summary Manager
  meeting/         Meeting model, transcription, routes
  hot_words/       Vocabulary library management
  tasks/           Async task queue with concurrency control
data/              Runtime data (gitignored)
```

## Recommended Setup

The project is optimized for [Alibaba Cloud Bailian (百炼)](https://bailian.console.aliyun.com/) platform:

- **Transcription** — Bailian FunASR (optimized adaptation)
- **Reranker** — Qwen3-Reranker via Bailian API

We recommend configuring Bailian API as the primary provider for the best out-of-the-box experience. More provider adaptations will be added in future releases.

## Configuration

Fully managed through **Settings** page. No manual config files.

- **LLM** — any OpenAI-compatible endpoint, configure `max_concurrent_requests` for parallelism
- **Embedding** — remote APIs (auto-detect dimensions) or local sentence-transformers
- **Reranker** — Cohere, Qwen DashScope, or local via OpenAI-compatible
- **Transcription** — device selection (CPU/GPU), cloud provider API keys

API keys are stored locally in `data/config.yaml` (gitignored, never committed).

## API

| Endpoint | Description |
|----------|-------------|
| `/api/query` | Chat with SSE streaming |
| `/api/documents` | Upload, parse, list, delete |
| `/api/collections` | Collection CRUD |
| `/api/config` | Provider & settings management |
| `/api/recall` | Recall evaluation & benchmarking |
| `/api/info` | Summaries, conflicts, project descriptions |
| `/api/meetings` | Meeting CRUD, transcription, notes |
| `/hot-words` | Vocabulary library management |

`GET /health → {"status": "ok"}`

## MCP Server

SinkDuce ships with a built-in [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes the full RAG pipeline as tools for AI coding agents. Use it with Claude Code, Cursor, or any MCP-compatible client to query your knowledge bases directly from your IDE.

### Quick Setup (Claude Code)

Add to your Claude Code MCP settings (`~/.claude/settings.json` or project-level `.claude/settings.json`):

```json
{
  "mcpServers": {
    "sinkduce": {
      "command": "python",
      "args": ["-m", "src.mcp.server"],
      "cwd": "/path/to/sinkduce"
    }
  }
}
```

The MCP server uses **stdio** transport — Claude Code launches it as a subprocess and communicates via stdin/stdout. No port needed.

### Available Tools

| Tool | Description |
|------|-------------|
| **`list_collections`** | List all knowledge bases with chunk counts |
| **`create_collection`** | Create a new collection (sensible defaults) |
| **`get_collection_config`** | Get full collection configuration |
| **`update_collection_config`** | Update collection settings (partial update) |
| **`delete_collection`** | Delete a collection and all its documents |
| **`list_documents`** | List documents in a collection |
| **`upload_document`** | Upload a document for async indexing |
| **`upload_folder`** | Batch-import documents from a server directory |
| **`delete_document`** | Remove a document and its chunks |
| **`get_task_status`** | Check async task progress |
| **`rag_query`** | Ask a question — Agentic RAG with source citations |
| **`search_chunks`** | Raw chunk retrieval with relevance scores |
| **`get_query_history`** | Retrieve past query history |
| **`get_collection_summary`** | LLM-generated collection overview |
| **`get_project_description`** | Short description of a collection |
| **`get_doc_summary`** | Structured summary of a document |
| **`get_conflicts`** | Detect contradictions across documents |
| **`trigger_consolidate`** | Rebuild collection-level summaries |

## Environment Variables

All optional. Copy `.env.template` to `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_PORT` | `18900` | Backend port |
| `UI_PORT` | `5173` | Vite dev server port |
| `QDRANT_HTTP_PORT` | `6343` | Qdrant HTTP |
| `QDRANT_GRPC_PORT` | `6334` | Qdrant gRPC |

## Tech Stack

Python 3.11+, FastAPI, React 19, Vite, TypeScript, Tailwind CSS, Qdrant, FunASR, Zustand

## License

MIT — see [LICENSE](LICENSE).
