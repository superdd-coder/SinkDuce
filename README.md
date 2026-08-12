<div align="center">
<img src="frontend/public/favicon.png" width="250" alt="SinkDuce logo" />

# SINKDUCE

$$\text{\textbf{Spark. Sink. Educe.}}$$

*An intelligent, context-isolated personal memory system — one-click deployable RAG agent with MCP server.*

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg?style=flat-square)](https://www.python.org/)
[![React](https://img.shields.io/badge/react-19-61dafb.svg?style=flat-square)](https://react.dev/)
[![Docker](https://img.shields.io/badge/docker-ready-2496ed.svg?style=flat-square)](https://www.docker.com/)
[![MCP](https://img.shields.io/badge/MCP-56_tools-6e47ff.svg?style=flat-square)](https://modelcontextprotocol.io/)

[Quick Start](#-quick-start) • [How It Works](#-how-it-works) • [Features · At a glance](#-at-a-glance) • [Details](#details) • [MCP Server](#-mcp-server) • [Architecture](#-architecture)

</div>

---

SinkDuce is a **high-fidelity cognitive filter** — not a document dump. It turns meetings, notes, and multi-format files into **project-isolated** knowledge; answers with **visible process and citable sources**; and exposes the same memory through **MCP (56 tools)** to agents like Cursor.

**In one line:** Spark (capture) → Sink (organize) → Educe (reason).

**v1.1 highlights:** multi-arch image `jethrohou/sinkduce`; Library **folder + timeline chain/node** graph, messages, smart todos, file versions; local **FunASR ONNX** (in-app download from this project’s GitHub Releases); **56 MCP tools** across 9 domains.

---

## 🚀 Quick Start

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/)

Cloning the repo provides `docker-compose.yml` and a place for `./data`. More detail: [`deploy/README.md`](deploy/README.md).

**Pull pre-built image** (recommended — no local compile):

```bash
git clone https://github.com/superdd-coder/sinkduce.git
cd sinkduce
# Optional: docker login   # anonymous pulls are often rate-limited; login is usually faster
docker compose pull
docker compose up -d
```

**Build from source** (developers / custom images):

```bash
git clone https://github.com/superdd-coder/sinkduce.git
cd sinkduce
docker compose -f docker-compose.build.yml up -d --build
```

Open [http://localhost:18900](http://localhost:18900). Then:

1. **(Optional)** For **offline STT**: Settings → Local Models → **Download local models** (official ONNX pack from this project’s GitHub Releases).
2. **Settings** → add an **LLM** provider (any OpenAI-compatible API).
3. Add an **Embedding** provider and create your first **Collection**.

> [!TIP]
> **DashScope OneShot** — Settings → LLM Providers → OneShot Setting (DashScope API). Enter your Alibaba Cloud API key to configure LLM, Embedding, Reranker, and file/realtime transcription in one step.

> [!TIP]
> **OpenRouter OneShot** — Settings → OneShot Setting (OpenRouter API). Enter your key; models are auto-fetched and classified (LLM / Chat·function-calling / Vision / Embedding / Reranker).

> [!TIP]
> **MinerU Cloud Parsing (optional)** — Get a token at [mineru.net](https://mineru.net/apiManage/token) → enable MinerU in Settings → turn on **Cloud Parsing (MinerU)** per **Collection → Config**. Falls back to local parsers on failure.

### Updating

```bash
git pull
docker compose pull
docker compose up -d
```

`data/` (DB, config, meetings, notes, hot words, local models, …) is preserved via volume. For source builds use `docker-compose.build.yml`.

---

## 💡 How It Works

Three verbs: **Spark (capture) → Sink (organize) → Educe (reason)**.

### Spark — Capture

Two entry points: **Meetings** (speech) and **Notes** (writing).

**Meetings:** mix-record mic + system audio, or upload a file; pause/resume and discard are supported. Transcribe with **local ONNX** or cloud ASR (OneShot can wire the stack). After transcription you get a **general summary** and **Blueprint sectioning** aligned with your existing Collection taxonomy; sections can be edited and **allocated into Collections**. Summary sentences jump back to transcript timestamps with synced playback; summaries support translate and export.

**Notes:** Tiptap rich text per Collection (auto-save). **Dual-pane** editing, left rail for Notes/Meetings, right rail for citation links; drag-to-**distill**, **propagate** on source change, one-click **ingest** into search. Open meeting summaries beside notes in the same workspace.

Files: multi-format upload (incl. OCR), optional MinerU for hard layouts; Library supports **folder upload**.

### Sink — Organize

Everything lands in a **Collection** — project/theme-isolated vector stores, no cross-contamination.

**Library** is a working surface, not a flat dump:

- **Folder view:** browse, versions, archive, multi-select, file detail preview.
- **Timeline view:** chains and nodes for how work evolves; attach files, message streams, smart todos.

Mark **Definitive** sources for consolidation: collection overview, project description, and **conflicts** (side-by-side viewer). Chunking uses sentence/Markdown-aware splits, with optional parent-child and contextual enrichment (see Features tables and Architecture).

### Educe — Reason

In **Chat**, multi-turn Q&A: multi-select **Collections** in the composer, switch **provider/model** per turn; streamed answers with a thinking/retrieval **timeline**; drill sources from snippet → document → original file.

Complex questions use **Agentic** decompose → multi-path retrieve → grade → synthesize; simpler ones a more direct path; hybrid search and rerank supported. Optional **web search**: configure first, then **confirm before** any public-web call (no silent outbound search).

**Quick Chat** beside Library or Meeting; **Recall** for tuning and eval. The same memory is exposed as **MCP (56 tools)** to IDE agents — not locked in the web UI.

---

## ✨ Features

### ⚡ At a glance

Bullet list for scanning (tables under **Details** below):

#### Deploy & setup

- **One-click Docker**: multi-arch image; data on local volume; upgrades keep data.
- **OneShot + multi-provider**: DashScope / OpenRouter quick setup; pluggable LLM / Embedding / Rerank (OpenAI-compatible).
- **Local ONNX ASR**: in-app download of official packs; Load/Unload; cloud ASR also supported.
- **Role models**: separate Visual / Chat (function-calling) / Meeting summary models.
- **MinerU (optional)**: high-fidelity cloud parse; fallback to local.

#### Capture

- **Meeting pipeline**: mix-record/upload → ASR → Blueprint sections → ingest; **pause/discard**.
- **Sentence-level provenance**: click summary lines to transcript + synced audio; speakers, hot words, language hints.
- **Translate & export** summaries for sharing.
- **Hot-word libraries**: dual-pane manager in Settings; CSV/XLSX import-export; default library.
- **Notes workspace**: Tiptap; **dual-pane + citation rails**; distill / propagate / ingest; open meeting summaries side-by-side.
- **Multi-format + folder upload**: common office/web types (+ OCR); whole-folder upload.

#### Organize

- **Multi-Collection isolation**: per-project stores; configurable chunking/dimensions.
- **Folders + timeline**: versions, messages, Definitive, archive/multi-select, chain/node graph, smart todos.
- **Consolidation & conflict compare**: collection summaries; conflict list + **side-by-side** view.
- **Smart chunking**: sentence/Markdown-aware; optional parent-child, Contextual Retrieval.

#### Reason

- **Agentic RAG chat**: visible timeline; 3-layer sources; **multi-collection chips** and **in-chat model switch**; generation continues when you switch sessions.
- **Optional web search**: enable in chat after config; **user confirm** before search; banner when internet sources are used.
- **Quick Chat / Recall**: ask beside library or meeting; Recall eval lab (power users).
- **MCP 56 tools**: same process over HTTP; collections / files / search / meetings / notes / hot words, …

### Details

> Tables below for depth. Prefer the **⚡ At a glance** bullets for a quick scan.

### Library & files

| Feature | Description |
|---------|-------------|
| **Folder view** | Hierarchical library tree, multi-mount files, groups, and file-detail drawer (preview, versions, messages). |
| **File versions** | Version history, blob availability, update-in-place; mark **definitive** for search & consolidation. |
| **Timeline view** | Visual **chain + node graph**: parallel chains, milestone nodes, file attachments on nodes, pan/zoom navigation. |
| **Messages stream** | Message threads scoped to collection / folder / file / node; compose and edit from the timeline sidebar. |
| **Smart todos** | LLM suggests todos from chain context; frosted bubble UI to review and add todos on the timeline. |
| **Node preview** | Quick sheet for a node’s attachments and linked context without leaving the graph. |
| **Staging uploads** | Reliable upload path for large files (REST + MCP staging), then promote into the library. |
| **MCP file-mgmt tools** | 13 atomic tools: library tree, timeline, folders/files, versions, chains/nodes/groups, uploads, definitive. |

### Meetings

| Feature | Description |
|---------|-------------|
| **Audio Transcription** | File upload or WebSocket realtime streaming. **Local FunASR ONNX** (SenseVoice file + Paraformer streaming + VAD/punc/speaker packs from GitHub Release). DashScope and OpenAI-compatible cloud ASR for higher accuracy. |
| **Live Captions** | Real-time transcription pushed during recording, auto-distinguishing partial vs final text. Transcript scrolls in sync with audio playback. |
| **Blueprint Auto-Sectioning** | LLM auto-detects topics using your existing Collection catalog as a classification taxonomy, decomposing the meeting into sections naturally aligned with your Collections. Custom sections can be added and regenerated. |
| **Per-Section Deep Summaries** | Each section gets a focused Markdown summary (SSE-streamed), with the LLM pinpointing relevant sentences from the transcript. |
| **Editable Summaries** | All summaries are editable Markdown — General Summary, section summaries, and meeting notes saved independently. Edits persist across subsequent operations. |
| **Meeting Notes** | Each meeting has its own Markdown note page for recording thoughts during the meeting, existing alongside auto-generated summaries. Import from .md/.docx/.txt supported. |
| **Sentence-Level Provenance** | Every sentence in the summary clicks through to the source transcript timestamp with synced audio playback. Each sentence is automatically tagged with its topic section, showing how each subject thread runs through the meeting. |
| **Speaker Management** | Edit speaker names inline in the transcript panel. Speaker tab shows per-speaker info cards with sampled segments. |
| **Hot Words & Language Hints** | Attach hot words libraries (weighted terms + language codes) and multi-language hints to boost domain-specific ASR accuracy. |

### Notes (Collection Notes)

| Feature | Description |
|---------|-------------|
| **Tiptap Editor** | Full WYSIWYG editing with Markdown, headings, tables, task lists, code blocks, image paste/drag-drop, and YouTube embeds. Auto-save. |
| **Distill** | Drag a note onto the editor — LLM condenses the source note's core insights into a citation block. Results auto-cached — LLM not called again if source unchanged. |
| **Propagate** | After editing a source note, re-distill into all downstream notes → recursively chain-propagate. Preview the full update chain before confirming. |
| **Bidirectional Reference Graph** | Auto-maintained reference relationships between notes. Right sidebar shows Distill In/Out navigation. |
| **Ingest & Export** | One-click ingest: note content auto-chunked, embedded, indexed as searchable document. Images are automatically OCR'd and visually described during ingest — no manual processing needed. Removable anytime. Import/export as .md/.txt. |

### Ingestion & Organization

| Feature | Description |
|---------|-------------|
| **12 Format Parsers** | PDF (with OCR for scanned pages), DOCX, PPTX, XLSX, Markdown, HTML, CSV, JSON/JSONL, plain text, images (OCR). Connect to **MinerU** for more powerful document parsing capabilities. |
| **Library-first organization** | Files live in the Library (folders, versions, timeline) before or alongside vector ingest — not only a flat upload list. |
| **Context-Isolated Collections** | Independent Qdrant vector databases. Configurable: chunk mode, parent strategy, chunk sizes, embedding dimensions, search mode, file type allowlist, contextual enrichment, agent, MinerU cloud parsing toggles. |
| **Parent-Child Chunking** | Parents carry full context; retrieval matches smaller, more precise children but returns parent text. Three strategies: paragraph-based, heading-based, or fixed-token. |
| **Contextual Retrieval** | LLM enriches each chunk with situating context to fill in missing global information. Large documents support async batch processing. |
| **Auto-Summarization & Consolidation** | Structured per-document summaries via LLM. Collection-level consolidation with conflict detection (flags contradictions between sources). Toggle documents "definitive" to include/exclude. |
| **Collection Catalog** | Auto-maintained per-collection: definition, coverage scope, tags. Powers agent's semantic query routing. |
| **Semantic Meeting Router** | Multi-topic meetings split automatically: each section allocated to its most relevant Collection. |

### Retrieval & Reasoning

| Feature | Description |
|---------|-------------|
| **Hybrid Search** | Dense vector + BM25 sparse (LLM extracts keywords and expands synonyms). Reciprocal Rank Fusion via Qdrant. Sparse vocabulary auto-rebuilds at a threshold after document changes to keep term weights accurate. |
| **Multi-Provider Reranking** | Cohere, DashScope/Qwen, OpenAI-compatible. Pluggable architecture — switch backends as needed. |
| **Agentic RAG** | Decompose → parallel variant generation → retrieve → combined grade (relevance + gap analysis, one LLM call) → aggregate → synthesize. Fully observable pipeline. |
| **Multi-Collection Federated Search** | Query across multiple Collections. Catalog metadata routes sub-queries to the most relevant Collections. |
| **3-Layer Source Traceability** | Answer → text snippet → full document → original file preview. Verify claims layer by layer. |
| **Session-Based Chat** | Persistent multi-turn conversations. LLM agent selects search strategy autonomously. Timeline shows interleaved thinking + tool calls. Think toggle for deep reasoning. Auto-titled sessions. |
| **Per-Collection Quick Chat** | Floating slide-out panel with SSE streaming, thinking display, and source navigation. Ideal for rapid lightweight Q&A. |
| **Recall Evaluation** | Auto-generated test cases, LLM-as-judge scoring with reasoning. Metrics include recall, MRR, and quality score. Evaluation history browser. |

### Extensibility

| Feature | Description |
|---------|-------------|
| **MCP Server** | 56 atomic tools across 9 domains. HTTP Streamable transport. Shared FastAPI process — no separate server needed. |
| **Pluggable Providers** | Unified adapter pattern for LLM, Embedding, Reranker, File Transcription, Realtime Transcription. Add new backends by implementing the interface and registering. |
| **OneShot Setup** | DashScope and OpenRouter pre-configuration paths. Auto-fetches available models, classifies by type, creates providers, sets defaults. |
| **Local-First, Cloud-Ready** | FunASR **ONNX** packs + Tesseract run locally (models via in-app download from GitHub Releases). All providers can target Ollama/LM Studio/vLLM for fully air-gapped LLM/embedding. |
| **Pre-built Docker image** | Multi-arch (`linux/amd64` + `linux/arm64`) image on Docker Hub; default compose pulls — no local compile required. |
| **Async Task System** | Dual-queue architecture: upload queue + general pool with parallel processing. Cancellable and retryable tasks, live progress via SSE log stream. |

---

## 🔌 MCP Server

SinkDuce exposes **56 atomic MCP tools** over HTTP (Streamable HTTP transport) on the same FastAPI process as the REST API. The MCP server reuses the app's services, task manager, and database connections — no separate process is spawned.

### Setup

Add to `.mcp.json` at your project root (or `~/.claude/.mcp.json` for global access):

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

Start the backend first (`docker compose up -d`). The MCP client connects to the running server.

### Tool Domains

| Domain | Count | Key Capabilities |
|--------|-------|-----------------|
| **Collections** | 5 | List all, get metadata+config, create (26 configurable parameters), update config (rejects destructive fields: `chunk_mode`, `embedding_*`), delete (refuses if last remaining) |
| **Documents** | 6 | List with metadata, upload via staging token or server-local path, delete (cleans chunks + summaries + triggers sparse recalc), chunk inspection (paginated, parent/child filterable), full-text extraction (windowed), toggle definitive flag |
| **File Management** | 13 | Library tree, timeline, folders/files, versions, chains/nodes/groups, staging uploads, set definitive |
| **Search** | 3 | Direct retrieval (dense/sparse/hybrid, optional reranking, multi-collection), Agentic RAG (full pipeline, auto-discovers collections via catalog), query history (with optional detail expansion) |
| **Tasks** | 5 | List (filterable by collection, status, type), get status with progress/error, cancel (cooperative), retry (re-enqueues failed), clear completed |
| **Summaries** | 4 | Collection overview, per-document structured summary (Data/Facts/Insights), conflict list, trigger consolidation (async task) |
| **Notes** | 6 | List (with extracted/ingested flags), get (metadata + content + references), create (auto-timestamped title), update (title/content, auto-syncs injection blocks), delete (cleans chunks + backlinks), trigger propagation (synchronous re-distill with chain propagation) |
| **Meetings** | 9 | List (filterable by status/search), get (metadata + tabs + has_transcript/has_summary/has_notes flags), get section Markdown (`tab_id="general"` for summary), paginated transcript (prefers `sentences.json` with diarization), create, update (speaker names dict, hot words library, notes content), delete (cleans allocated chunks), start summary (async task), upload audio via staging |
| **Hot Words** | 5 | List libraries, get (with weighted words + language codes), create, update (full word list replacement), delete |

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   Browser (React 19)                       │
│  Chat │ Collections │ Notes │ Meetings │ Settings │ Recall │
└──────────────────────┬───────────────────────────────────┘
                       │ REST + SSE + WebSocket
┌──────────────────────▼───────────────────────────────────┐
│               FastAPI (Python 3.11)                        │
│                                                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ /api/*   │  │  /mcp    │  │  /ws     │  │ /health  │ │
│  │ REST API │  │ MCP HTTP │  │ Realtime │  │          │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────────┘ │
│       │             │             │                       │
│  ┌────▼─────────────▼─────────────▼───────────────────┐  │
│  │              Services Singleton                      │  │
│  │  Config → Qdrant → Embedding → LLM → Retriever     │  │
│  │    → Reranker → DirectQuery → VariantFetcher       │  │
│  │    → Decomposer → Aggregator → AgenticQuery        │  │
│  │    → ContextualRetrieval → Chunker → SessionStore  │  │
│  └──────────────────────┬──────────────────────────────┘  │
│                         │                                 │
│  ┌──────────────────────▼──────────────────────────────┐  │
│  │  Providers (Registry + ABC + Factory)                │  │
│  │  LLM │ Embedding │ Reranker │ Transcription         │  │
│  │  OpenAI-compat · Cohere · DashScope · FunASR local  │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Domain Modules                                      │  │
│  │  meeting/ · notes/ · hot_words/ · collections/      │  │
│  │  tasks/ (dual-queue: upload serial + general pool)   │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Qdrant (Vector Database)                     │
│  Collection A │ Collection B │ ... │ __summaries__       │
└─────────────────────────────────────────────────────────┘
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11, FastAPI, Uvicorn, Pydantic v2, PyYAML |
| **Frontend** | React 19, TypeScript, Vite 6, Tailwind CSS 4, Zustand, Radix UI, Tiptap, Recharts, Lucide React |
| **Vector DB** | Qdrant v1.13+ (dense vectors + sparse BM25 vectors, RRF hybrid search) |
| **LLM/Embedding** | OpenAI-compatible protocol, multi-provider with per-collection override |
| **Reranking** | Cohere (`rerank-multilingual-v3.0`), DashScope/Qwen (`qwen3-vl-rerank`), OpenAI-compatible (native `/rerank` endpoint → chat completions logprobs fallback) |
| **Parsing** | pdfplumber (page-level text/tables/images + Tesseract OCR fallback), mammoth + python-docx, openpyxl, python-pptx, markdownify, BeautifulSoup, Tesseract, MinerU cloud API |
| **Transcription** | FunASR ONNX (SenseVoiceSmall, Paraformer streaming, FSMN-VAD, CAM++, CT-Punc) via GitHub Release packs; DashScope; OpenAI-compatible Whisper |
| **MCP** | MCP SDK 1.x, HTTP Streamable transport, 56 tools |
| **Infrastructure** | Docker Compose (Qdrant + app), multi-arch image publish to Docker Hub, GitHub Actions CI |

---

## ⚙️ Environment Variables

All optional. Copy `.env.template` to `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_PORT` | `18900` | Backend API + MCP server port |
| `UI_PORT` | `5173` | Vite dev server port (dev only) |
| `QDRANT_HTTP_PORT` | `6343` | Qdrant HTTP API (host port) |
| `QDRANT_GRPC_PORT` | `6334` | Qdrant gRPC (host port) |

---

## 🗺️ Roadmap

- [ ] Multi-tenant server deployment for collaborative team project memory (Enterprise)

---

## 📜 License

SinkDuce is licensed under the **[GNU Affero General Public License v3.0 or later](LICENSE)** (AGPL-3.0-or-later).

Third-party open-source dependencies (Python packages, frontend libraries, fonts, Docker system packages, Qdrant, etc.) remain under their own licenses. See **[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)** for attribution and a direct-dependency inventory. All direct dependencies reviewed for this notice are open source (MIT / BSD / Apache-2.0 / ISC / OFL-1.1 / …). Optional commercial **remote APIs** (OpenAI, Cohere, DashScope, MinerU cloud, …) are not part of the source license; only their client libraries (where used) are OSS dependencies.

---

[中文文档](README_CN.md)
