# SINKDUCE

[中文](README_CN.md)

$$\text{\textbf{Spark. Sink. Educe.}}$$

> **An Intelligent, Context-Isolated Personal Memory Ecosystem Designed for the "Anti-Hoarder."**

Sinkduce is built on a strict philosophy: **Never hoard knowledge—only sink what truly matters.** Unlike massive, bloated traditional wikis or knowledge bases that encourage endless data hoarding (turning into "knowledge graveyards"), Sinkduce is designed as a **high-fidelity cognitive filter**. It is tailored for professionals managing multiple complex projects and students navigating multiple courses. Instead of blindly filling the vector pool with unread external text files, Sinkduce turns real-world conversations, lectures, and curated conceptual notes into precise structural units, allowing you to interact with your core operational memory under a context-isolated architecture.

> *Note: Sinkduce is currently optimized as a hyper-efficient personal system. Server-side deployments and multi-user collaborative project memory are planned in future enterprise releases.*

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
* **Local MCP Interface**: Features an open Model Context Protocol (MCP) server interface. Seamlessly connect Sinkduce to external autonomous agent frameworks (e.g., Claude Code, Cursor, Hermes), enabling you to **chat with your curated knowledge anywhere**. (Prototype, to be optimized in future release.)

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

> *Prototype, to be optimized in future release.*

Sinkduce ships with a built-in [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes the full Agentic RAG pipeline as tools for AI coding agents. Use it with Claude Code, Cursor, or any MCP-compatible client to query your knowledge bases directly from your IDE.

### Quick Setup (Claude Code)

Add to your Claude Code MCP settings (`~/.claude/settings.json` or project-level `.claude/settings.json`):

JSON

```
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

### Available MCP Tools

* **`list_collections`** / **`create_collection`** / **`delete_collection`**: Knowledge base CRUD operations.
* **`get_collection_config`** / **`update_collection_config`**: Manage specific chunking and embedding settings.
* **`list_documents`** / **`upload_document`** / **`delete_document`**: Document lifecycle management.
* **`upload_folder`**: Batch-import documents from a server directory.
* **`get_task_status`**: Check async parsing and indexing queue progress.
* **`rag_query`**: Multi-collection Agentic RAG query with source citations.
* **`search_chunks`**: Raw vector / hybrid chunk retrieval with relevance scores.
* **`get_doc_summary`** / **`get_collection_summary`** / **`get_conflicts`**: Access high-level summaries and data contradictions.

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
