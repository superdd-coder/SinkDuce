# SINKDUCE

[中文](README_CN.md)

$$\text{\textbf{Spark. Sink. Educe.}}$$

> **An Intelligent, Context-Isolated Personal Memory Ecosystem Designed for the "Anti-Hoarder."**

Sinkduce is built on a strict philosophy: **Never hoard knowledge—only sink what truly matters.** Unlike massive, bloated traditional wikis or knowledge bases that encourage endless data hoarding (turning into "knowledge graveyards"), Sinkduce is designed as a **high-fidelity cognitive filter**. It is tailored for professionals managing multiple complex projects and students navigating multiple courses. Instead of blindly filling the vector pool with unread external text files, Sinkduce turns real-world conversations, lectures, and curated conceptual notes into precise structural units, allowing you to interact with your core operational memory under a context-isolated architecture.

## 🚀 Quick Start

**Prerequisites**: Docker

```bash
git clone https://github.com/superdd-coder/sinkduce.git
cd sinkduce

# Optional: customize ports
cp .env.template .env

# Build and start
docker compose up -d --build
```

Open [http://localhost:18900](http://localhost:18900/). On first launch:

1. Download the local transcription models you need.
2. Go to **Settings** → add an **LLM provider** (any OpenAI-compatible API).
3. Add an **Embedding provider** and create a Project Database.

### Updating

```bash
cd sinkduce
git pull
docker compose up -d --build
```

Docker rebuilds the image with the latest code while preserving your `data/` directory (database, config, history).

### Recommended Out-of-the-Box Setup

Two OneShot configuration paths are available:

**DashScope** (Alibaba Cloud): Go to **Settings** → **LLM Providers** → **OneShot Setting (DashScope API)**:

* **LLM**: `deepseek-v4-flash`
* **Embedding**: `text-embedding-v4` (1024d)
* **Reranker**: `qwen3-rerank`
* **Transcription**: `fun-asr` / `fun-asr-realtime`

**OpenRouter**: Go to **Settings** → **LLM Providers** → **OneShot Setting (OpenRouter API)**. Enter your API Key; models are auto-fetched and classified:

* **LLM**: `deepseek/deepseek-v4-flash`
* **Chat** (function-calling, tools-filtered): `deepseek/deepseek-v4-pro`
* **Visual** (vision models): `xiaomi/mimo-v2.5`
* **Embedding**: `qwen/qwen3-embedding-4b` (default 1536d)
* **Reranker**: `cohere/rerank-v3.5`

## 🏗️ Core Pillars

### ⚡ 01. Spark: Fluid Friction Capture & Synthesis

**Spark** captures live audio and personal notes, turning them into structured raw assets.

* **Full-Featured Markdown Workspace (Collection Notes)**: Create structured personal notes explicitly bound to specific operational contexts, featuring full WYSIWYG editing with markdown support (headings, tables, task lists, code blocks, images, YouTube embeds).
* **Intelligent Note Distillation (Drag-to-Distill)**: Drag an existing old note or document into your current workspace, and the system automatically extracts the core insights into a dense citation block, seamlessly aggregating scattered ideas without manual rewriting.
* **AI Image Ingestion**: One click prompts the AI to generate a precise contextual text description, weaving visual data into your markdown memory map for vector indexing. Supports image pasting, drag-and-drop, and resizing with inline captions.
* **Audio Transcription & Multi-Phase Pipeline**: Upload recordings or record live meetings/lectures via WebSockets. Transcription (STT) can be handled by the embedded local FunASR engine or external APIs (DashScope, OpenRouter, etc.). After transcription, a multi-phase analysis runs: **Pass 1 — General Summary + Blueprint** (SSE streaming, builds section structure); **Pass 2 — Section Summary** (one-click custom sections, one keyword generates description and summary); **Pass 3 — Final Merge** producing Summary, To-Do List, and Detail artifacts.
* **Hot Words**: Manage custom vocabulary libraries for domain-specific terminology to improve transcription accuracy.
* **Quick Chat Panel**: A floating side panel in the Collection view for rapid Q&A via Direct RAG. Diamond button with animation, per-collection sessions, SSE streaming with thinking step display.
* **Multi-turn Agentic Chat**: The main Chat supports multi-turn conversation with tool-calling Agent that autonomously decides whether, where, and how to search — exposing the full Agentic RAG capability through conversational interaction.

### 📥 02. Sink: Anti-Hoarding Ingestion Pipeline

**Sink** ensures that your data is cleanly separated and contextually enriched.

* **Context-Isolated Collections**: Spin up separate, secure vector database collections (via Qdrant) for different enterprise projects or university courses, strictly eliminating cross-context data pollution.
* **Multi-Project Segment-Level Semantic Router**: Meetings and lectures often drift across topics. Sinkduce automatically compares text segments against active Collection summaries, splitting a single audio transcript and routing distinct conversation shards into their respective collections.
* **Granular Document Parsing & Chunking**: Utilizes the natively embedded parsing engine (supporting 12 format parsers) or links to powerful cloud parsing APIs (e.g., *MinerU*). Supports advanced **Parent-Child chunking** by headings, paragraphs, and max token configurations while keeping word boundaries intact.
* **Context Enrichment Engine**: When enabled, an LLM evaluates each split chunk and injects its missing global context, mitigating the "chunk isolation" effect during retrieval.
* **Auto-Summarization & Consolidation**: Every document gets a structured summary. Collections get consolidated overviews with automatic conflict detection when documents contradict each other.
* **Auto-Updating Collection Coverage**: Collection-level coverage maps update automatically after each ingestion, comparing new and existing documents to keep retrieval context aligned with the latest knowledge boundaries.

### 🧠 03. Educe: High-Dimensional Contextual Reasoning

**Educe** implements a cutting-edge retrieval architecture to turn cold data into active intelligence.

* **Advanced Hybrid Retrieval**: Supports dense vector similarity search, keyword-semantic hybrid querying (BM25 + Dense via Qdrant), and advanced **Reranking** algorithms to surface top-tier context.
* **Agentic RAG Pipeline**: Complex queries are decomposed into atomic sub-queries, routed to multiple Collections in parallel, each executing a Variant loop (Rewrite + Grade), then aggregated and synthesized. Full event stream observability (`decompose → variant_generation → scoring → synthesize_merge`).
* **Multi-Collection Federated Search**: Context isolation does not limit high-dimensional synthesis. Users can choreograph inquiries spanning multiple explicit Collections simultaneously, harmonizing cross-domain shards via top-level reasoning.
* **Absolute Source Traceability (3-Layer Traceability)**: Build bulletproof trust in AI answers by drilling straight to the raw text. Instantly inspect the source lineage across three deep-dive levels: the specific *Vector Chunk*, the *Full-Text Context*, or the *Original Source File*.
* **Recall Evaluation**: Built-in benchmarking with adjustable parameters to evaluate retrieval recall and precision.
* **Local MCP Interface**: Features an open Model Context Protocol (MCP) server interface. Seamlessly connect Sinkduce to external autonomous agent frameworks (e.g., Claude Code, Cursor, Hermes), enabling you to **chat with your curated knowledge anywhere**. 

## 🔒 Model Configurations & Data Security

Sinkduce adopts a pluggable and decoupled model architecture, fully managed and configured through the Web UI with **zero manual YAML configuration files** required.

* **Embedded Local Services**: Core parsing and speech-to-text (FunASR SenseVoiceSmall) engines are natively embedded directly into the local environment, ensuring basic processing can happen completely offline by default.
* **Full Customization via OpenAI Protocols**: Key model layers—including **LLM (reasoning)**, **Embedding (vector generation)**, and **Rerank (re-ranking algorithm)**—fully adhere to the standard `OpenAI Compatible` API protocol. Global concurrency control can be limited via `max_concurrent_requests`.
* **Seamless Bridge to Advanced Providers**: Users can effortlessly plug in API keys from leading commercial cloud model providers (such as OpenAI, Anthropic, DeepSeek, Google, or DashScope) and advanced cloud parsers (like MinerU) for hyper-complex cross-document synthesis.
* **Air-Gapped Privacy Shield**: For proprietary enterprise logs or confidential data, users can point all custom model parameters to local open-source setups (e.g., via Ollama, LM Studio, or vLLM running weights locally). In this setup, Sinkduce runs entirely air-gapped, ensuring sensitive data never leaves your infrastructure.

*All credentials and API keys are stored locally in `data/config.yaml` (gitignored, never committed).*

## ⚙️ Environment Variables

All optional. Copy `.env.template` to `.env` to customize ports:

| **Variable**       | **Default** | **Description**      |
| ------------------ | ----------- | -------------------- |
| `API_PORT`         | `18900`     | Backend port         |
| `UI_PORT`          | `5173`      | Vite dev server port |
| `QDRANT_HTTP_PORT` | `6343`      | Qdrant HTTP          |
| `QDRANT_GRPC_PORT` | `6334`      | Qdrant gRPC          |

## 🔌 MCP Server Interface

Sinkduce ships with a built-in [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes **43 atomic tools** across 8 domains for AI coding agents. Use it with Claude Code, Cursor, or any MCP-compatible client to query and manage your knowledge bases directly from your IDE.

### Architecture

The MCP server is mounted under `/mcp` on the same FastAPI process as the REST API (default port `18900`). It shares the main app lifespan — the `services` singleton and `task_manager` are reused, so no separate process is needed. Transport is HTTP via the [Streamable HTTP](https://modelcontextprotocol.io/) protocol.

### Quick Setup (Claude Code)

Add to your Claude Code MCP settings (`~/.claude/.mcp.json` or project-level `.mcp.json`):

```json
{
  "mcpServers": {
    "sinkduce": {
      "type": "http",
      "url": "http://localhost:18900/mcp"
    }
  }
}
```

For Claude Code, the simplest setup is a `.mcp.json` file at the project root:

```json
{
  "mcpServers": {
    "sinkduce": {
      "type": "http",
      "url": "http://localhost:18900/mcp"
    }
  }
}
```

Prerequisite: start the backend first (`docker compose up -d` or `uvicorn src.main:app --port 18900 --reload`). Claude Code connects to the running server — no subprocess is spawned.

### File Upload via Staging

MCP tool parameters appear in the LLM conversation transcript. To prevent file content from leaking into context, Sinkduce uses a **side-channel staging** pattern. Instead of passing file bytes through MCP tools, use the HTTP staging endpoint:

```bash
# Upload a document (one-shot)
curl -F "file=@report.pdf" -F "collection=col_xxx" \
     http://localhost:18900/api/mcp/upload

# Upload meeting audio (one-shot)
curl -F "file=@recording.webm" -F "meeting_id=meet_xxx" \
     http://localhost:18900/api/mcp/meeting-upload
```

The LLM uses its Bash tool to execute the curl command — file bytes travel over HTTP only and never enter the LLM context.

### Available MCP Tools (43 atomic tools)

**Collections (5)**: `list_collections`, `get_collection`, `create_collection`, `update_collection_config`, `delete_collection`. `update_collection_config` rejects destructive fields (`chunk_mode`, `embedding_*`) that would require a full re-index — change those via the UI.

**Documents (6)**: `list_documents`, `upload_document_from_staging` (unified upload via staging token), `delete_document`, `get_file_chunks`, `get_document_text`, `set_document_definitive`. For file upload, use the HTTP staging endpoint above — the MCP tool only accepts a staging token.

**Search (3)**: `search_direct_chunks` (dense/sparse/hybrid retrieval), `search_agentic_chunks` (rewrite → decompose → retrieve → grade pipeline), `get_query_history`.

**Tasks (5)**: `list_tasks`, `get_task_status`, `cancel_task`, `retry_task`, `clear_completed_tasks`. Monitor async parsing/indexing/summary progress.

**Summaries (4)**: `get_collection_summary`, `get_doc_summary`, `get_conflicts`, `trigger_consolidate`.

**Notes (6)**: `list_notes`, `get_note`, `create_note`, `update_note`, `delete_note`, `trigger_propagation`.

**Meetings (9)**: `list_meetings`, `get_meeting` (metadata + tabs), `get_section` (per-tab markdown, `tab_id="general"` for summary), `get_meeting_transcript` (paginated), `create_meeting`, `update_meeting`, `delete_meeting`, `start_meeting_summary`, `upload_meeting_audio_from_staging`.

**Hot Words (5)**: `list_hot_words_libraries`, `get_hot_words_library`, `create_hot_words_library`, `update_hot_words_library`, `delete_hot_words_library`.

All tools return JSON strings. Search tools return chunks only — the agent is expected to generate the final answer with its own LLM.

## 🛠️ Tech Stack & Architecture

### Backend & Frontend Stack

Python 3.11+, FastAPI, React 19, Vite, TypeScript, Tailwind CSS, Qdrant, FunASR, Zustand, Shadcn UI, SSE Streaming, Session-based Chat, Tool-calling LLM Agent.

### Directory Layout

```
frontend/          React 19 + Vite + Tailwind CSS + Shadcn UI
src/
  api/             FastAPI routes
  db/              Qdrant client
  mcp/             MCP server
  parsers/         12 format parsers (including embedded & MinerU cloud integration)
  providers/       LLM, Embedding, Reranker, Transcription backends
  rag/             Chunker, Retriever, Agent, Reranker, Summary Manager
  meeting/         Meeting model, transcription pipelines, routes
  hot_words/       Vocabulary library management
  tasks/           Async task queue with global LLM concurrency control
data/              Runtime data, database, configs (gitignored)
```

## 🗺️ Future Roadmap

* [ ] Multi-tenant server deployment architecture for collaborative team project memory (Enterprise Release).
