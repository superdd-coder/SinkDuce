# SINKDUCE

[English](README.md)

$$\text{\textbf{Spark. Sink. Educe.}}$$

> **一个为“反囤积狂”设计的智能、Context-Isolated 个人记忆生态系统。**

Sinkduce 秉承一个核心哲学：**从不盲目囤积知识——只沉淀真正重要的核心。** 与那些鼓励无休止堆砌数据、最终沦为”知识坟墓”的臃肿传统 Wiki 或知识库不同，Sinkduce 被设计为一个**高保真认知过滤器**。它专为管理多个复杂项目的专业人士和奔波于多门课程的学者打造。Sinkduce 拒绝将未读的外部杂乱文本盲目塞进向量池，而是将现实世界中的对话、会议、讲座和沉淀后的概念笔记转化为精准的结构化单元，让您在完全隔离的 Context-Isolated 架构中与核心运行记忆进行深度交互。

---

## 🚀 快速启动

**前置条件**：需安装 Docker

```bash
git clone https://github.com/superdd-coder/sinkduce.git
cd sinkduce

# 可选：自定义端口
cp .env.template .env

# 构建并启动
docker compose up -d --build
```



启动后访问 [http://localhost:18900](http://localhost:18900/)。首次运行时：

1. 下载您所需的本地语音转文字（STT）模型。
2. 前往 **Settings（设置）** → 添加 **LLM 提供商**（任何兼容 OpenAI 协议的 API）。
3. 添加 **Embedding 提供商** 并创建您的项目数据库。

### 升级更新

```bash
cd sinkduce
git pull
docker compose up -d --build
```

Docker 将使用最新代码重新构建镜像，同时完整保留您的 `data/` 目录（包含数据库、配置及历史记录）。

### 推荐开箱即用配置

项目提供两套 OneShot 一键配置方案：

**阿里云百炼 (DashScope)**：前往 **Settings** → **LLM Providers** → **OneShot Setting (DashScope API)**，输入 API Key 自动配置全部服务：

* **LLM**: `deepseek-v4-flash`
* **Embedding**: `text-embedding-v4`（1024 维）
* **Reranker**: `qwen3-rerank`
* **Transcription**: `fun-asr` / `fun-asr-realtime`

**OpenRouter**：前往 **Settings** → **LLM Providers** → **OneShot Setting (OpenRouter API)**，输入 API Key 后自动拉取模型列表并分类：

* **LLM**: `deepseek/deepseek-v4-flash`
* **Chat**（自动筛选支持工具调用的模型）: `deepseek/deepseek-v4-pro`
* **Visual**（自动筛选视觉模型）: `xiaomi/mimo-v2.5`
* **Embedding**: `qwen/qwen3-embedding-4b`（默认 1536 维）
* **Reranker**: `cohere/rerank-v3.5`

## 🏗️ 三大核心支柱

### ⚡ 01. Spark: Fluid Friction Capture & Synthesis

**Spark** 负责捕捉实时音频和个人随笔，并将其转化为结构化的原始资产。

* **Full-Featured Markdown Workspace (Collection Notes)**: 创建明确绑定到特定业务上下文的结构化个人笔记。支持全功能所见即所得（WYSIWYG）编辑及 Markdown 语法（标题、表格、任务列表、代码块、图片和 YouTube 视频内嵌）。
* **Intelligent Note Distillation (Drag-to-Distill)**: 直接将现有的旧笔记或文档拖入当前工作区，系统会自动将其核心洞察提取为高密度的引用块，无需手动重写即可无缝聚合零散灵感。
* **AI Image Ingestion**: One click 触发 AI 生成精准的图像上下文文本描述，将视觉数据织入您的 Markdown 记忆网络中以供向量索引。支持图片粘贴、拖拽和带行内字幕的尺寸调整。
* **音频转录与多阶段智能管道**: 支持上传录音或通过 WebSockets 录制实时会议/讲座。语音转文字（STT）可由内置本地 FunASR 引擎或外部 API（DashScope、OpenRouter 等）处理。转写完成后自动运行多阶段分析：**Pass 1 — General Summary + Blueprint（章节结构发现）**，SSE 流式输出，动态构建章节骨架；**Pass 2 — Section Summary（逐章节深度总结）**，支持一键添加自定义 Section，仅需一个关键词即可智能生成该章节的描述与摘要；**Pass 3 — 合并产出**，最终生成 Summary、To-Do List 及 Detail 三折页产物。
* **Hot Words**: 管理特定专业领域的自定义词汇库，以显著提高特定术语的语音转写准确率。
* **Quick Chat Panel**: Collection 视图中通过悬浮菱形按钮呼出的侧边栏，使用 Direct RAG 快速问答。每个 Collection 独立会话，支持 SSE 流式输出与 thinking 过程展示。
* **Multi-turn Agentic Chat**: 主 Chat 支持多轮对话，Agent 通过工具调用（Tool-calling）自主决策是否检索、检索哪些 Collection、以何种模式检索，将 Agentic RAG 的完整能力暴露在对话交互中。

### 📥 02. Sink: Anti-Hoarding Ingestion Pipeline

**Sink** 确保您的数据被清晰地隔离并完成上下文增强。

* **Context-Isolated Collections**: 为不同的企业项目或大学课程建立独立、安全的向量数据库集合（基于 Qdrant），从根本上消除跨上下文的数据污染。
* **Multi-Project Segment-Level Semantic Router**: 真实的会议和讲座往往会在多个话题间穿插。Sinkduce 会自动将文本片段与各活跃集合的摘要进行比对，将单个音频转录文本自动切片，并将不同的对话碎片路由至各自对应的集合中。
* **Granular Document Parsing & Chunking**: 利用内置的本地解析引擎（支持 12 种格式解析器）或链接到强大的云端解析 API（如 *MinerU*）。支持基于标题、段落和最大 Token 配置的高级 **Parent-Child chunking**，同时保持完整的词边界。
* **Context Enrichment Engine**: 开启后，LLM 将评估每个切分出的数据块，并为其注入缺失的全局上下文，从而缓解检索过程中的“分块孤立”效应。
* **Auto-Summarization & Consolidation**: 每个文档都会获得一个结构化摘要。同时，集合会生成固化综述，并在文档间出现事实冲突时自动进行冲突检测与提示。
* **Auto-Updating Collection Coverage**: 集合在每次文档入库后自动触发摘要固化与变化检测，对比新旧文档并更新集合级别的主题覆盖图（Coverage Map），确保检索上下文始终反映最新的知识边界。

### 🧠 03. Educe: High-Dimensional Contextual Reasoning

**Educe** 实现了前沿的检索架构，将静态的数据转化为活跃的智能。

* **Advanced Hybrid Retrieval**: 支持标准稠密向量相似度检索、关键词-语义混合查询（基于 Qdrant 的 BM25 + Dense），并结合先进的 **Reranking算法** 来锁定顶级上下文。
* **Agentic RAG Pipeline**: 复杂查询 → 拆解为原子子查询 → 并行路由至多个 Collection → 各自执行 Variant（Rewrite + Grade loop）→ 聚合合成。全程事件流可观测（`decompose → variant_generation → scoring → synthesize_merge`），每阶段结果可追溯。
* **Multi-Collection Federated Search**: Context-Isolated 架构并不会限制高维度的知识合成。用户可以设计同时跨越多个明确集合的联合查询，系统将触发相互隔离的多路检索流水线，并通过顶层推理协调跨领域碎片。
* **Absolute Source Traceability (3-Layer Traceability)**: 允许直接穿透至原始文本，建立对 AI 回答的铁证信任。您可以立即向下钻取三层源头脉络：特定 *Vector Chunk*、*Full-Text Context* 或 *Original Source File*。
* **Recall Evaluation**: 内置基准测试功能，可通过可调参数评估检索的召回率（Recall）和精确度（Precision）。
* **Local MCP Interface**: 提供开放的模型上下文协议（MCP）服务器接口。将 Sinkduce 无缝连接到外部自主智能体框架（如 Claude Code、Cursor、Hermes），让您能够**在任何工作流中随时调用沉淀的知识**。

## 🔒 模型配置与数据安全

Sinkduce 采用可插拔且解耦的模型架构，完全通过 Web UI 进行可视管理与配置，**无需手动编辑任何 YAML 配置文件**。

* **Embedded Local Services**: 核心解析和高保真语音转文字（基于 FunASR SenseVoiceSmall）引擎原生嵌入在本地环境中，确保基础处理默认可以完全离线进行。
* **Full Customization via OpenAI Protocols**: 关键模型层——包括 **LLM（推理）**、**Embedding（向量生成）** 和 **Rerank（重排算法）**——完全兼容标准的 `OpenAI Compatible` API 协议。可以通过 `max_concurrent_requests` 设置全局并发控制。
* **Seamless Bridge to Advanced Providers**: 用户可以轻松接入行业顶尖商业模型的 API Key（如 OpenAI、Anthropic、DeepSeek, Google 或 DashScope）以及高级解析引擎（如 MinerU），以应对极度复杂的跨文档合成任务。
* **Air-Gapped Privacy Shield**: 面对涉及商业机密的日志或核心隐私数据，用户可以将所有自定义模型参数指向本地开源架构（例如通过本地运行的 Ollama、LM Studio 或 vLLM 承载权重）。在此模式下，Sinkduce 完全闭环运行，确保敏感数据绝不离开您的本地基础设施。

*All credentials 和 API 密钥均保存在本地 `data/config.yaml` 中（该文件已加入 gitignore，绝不会被提交）。*

## ⚙️ 环境变量

所有变量均为可选。复制 `.env.template` 并重命名为 `.env` 即可自定义端口：

| **Variable**       | **Default** | **Description**     |
| ------------------ | ----------- | ------------------- |
| `API_PORT`         | `18900`     | 后端服务端口        |
| `UI_PORT`          | `5173`      | Vite 开发服务器端口 |
| `QDRANT_HTTP_PORT` | `6343`      | Qdrant HTTP 端口    |
| `QDRANT_GRPC_PORT` | `6334`      | Qdrant gRPC 端口    |

## 🔌 MCP 服务器接口

Sinkduce 自带内置的 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 服务器，对外暴露 **43 个原子工具**，覆盖 8 个领域。您可以在 Claude Code、Cursor 或任何兼容 MCP 的客户端中使用，直接在 IDE 中异步检索和管理您的知识库。

### 架构说明

MCP 服务器以子应用形式挂载在同一个 FastAPI 进程的 `/mcp` 路径下（默认端口 `18900`），复用主应用的 lifespan——`services` 单例与 `task_manager` 全局共享，无需独立进程。传输层使用 [Streamable HTTP](https://modelcontextprotocol.io/) 协议。

### 快速配置（以 Claude Code 为例）

将以下配置添加至 Claude Code MCP 设置（`~/.claude/.mcp.json` 或项目根目录 `.mcp.json`）：

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

前提条件：需先启动后端（`docker compose up -d` 或 `uvicorn src.main:app --port 18900 --reload`）。Claude Code 直接连接到已运行的服务器，无需启动子进程。

### 文件上传（Staging 模式）

MCP 工具的参数会出现在 LLM 对话记录中。为防止文件内容泄露到上下文，Sinkduce 采用**旁路暂存（staging）**模式——文件通过 HTTP 端点传输，MCP 工具只传一个 36 字符的 UUID token：

```bash
# 上传文档（一步到位）
curl -F "file=@report.pdf" -F "collection=col_xxx" \
     http://localhost:18900/api/mcp/upload

# 上传会议音频（一步到位）
curl -F "file=@recording.webm" -F "meeting_id=meet_xxx" \
     http://localhost:18900/api/mcp/meeting-upload
```

LLM 使用 Bash 工具执行 curl 命令，文件字节仅通过 HTTP 传输，绝不进入 LLM 上下文。

### MCP 工具清单（43 个原子工具）

**Collections（5）**：`list_collections`、`get_collection`、`create_collection`、`update_collection_config`、`delete_collection`。`update_collection_config` 会拒绝破坏性字段（`chunk_mode`、`embedding_*`），这些字段需要完整重建索引才能修改，请通过 UI 进行。

**Documents（6）**：`list_documents`、`upload_document_from_staging`（统一上传，通过 staging token）、`delete_document`、`get_file_chunks`、`get_document_text`、`set_document_definitive`。上传文件请使用上方的 HTTP staging 端点，MCP 工具仅接受 staging token。

**Search（3）**：`search_direct_chunks`（dense/sparse/hybrid 检索）、`search_agentic_chunks`（rewrite → decompose → retrieve → grade 流水线）、`get_query_history`。

**Tasks（5）**：`list_tasks`、`get_task_status`、`cancel_task`、`retry_task`、`clear_completed_tasks`。监控异步解析/索引/摘要进度。

**Summaries（4）**：`get_collection_summary`、`get_doc_summary`、`get_conflicts`、`trigger_consolidate`。

**Notes（6）**：`list_notes`、`get_note`、`create_note`、`update_note`、`delete_note`、`trigger_propagation`。

**Meetings（9）**：`list_meetings`、`get_meeting`（元数据 + tabs 列表）、`get_section`（单 section markdown，`tab_id="general"` 为总体摘要）、`get_meeting_transcript`（分页转写文本）、`create_meeting`、`update_meeting`、`delete_meeting`、`start_meeting_summary`、`upload_meeting_audio_from_staging`。

**Hot Words（5）**：`list_hot_words_libraries`、`get_hot_words_library`、`create_hot_words_library`、`update_hot_words_library`、`delete_hot_words_library`。

所有工具均返回 JSON 字符串。Search 工具只返回检索片段（chunks），最终答案由 Agent 用自己的 LLM 生成。

## 🛠️ 技术栈与架构设计

### Backend & Frontend Stack

Python 3.11+, FastAPI, React 19, Vite, TypeScript, Tailwind CSS, Qdrant, FunASR, Zustand, Shadcn UI, SSE Streaming, Session-based Chat, Tool-calling LLM Agent.

### Directory Layout

```
frontend/          React 19 + Vite + Tailwind CSS + Shadcn UI 前端源码
src/
  api/             FastAPI 路由网关
  db/              Qdrant 数据库客户端连接器
  mcp/             MCP 协议标准服务器实现
  parsers/         12 种格式解析器（含内置解析与 MinerU 云端集成）
  providers/       LLM、Embedding、Reranker、语音转写等后端驱动
  rag/             Chunker、Retriever、Agent 编排、Reranker 及 Summary Manager
  meeting/         会议模型、流式转写流水线及路由逻辑
  hot_words/       专业领域词汇库（热词）管理模块
  tasks/           全局 LLM 并发控制的异步任务队列
data/              Runtime data、数据库及配置文件（gitignored）
```

## 🗺️ 未来路线图

* 多租户服务器端部署架构，支持团队间协同的项目级内存共享 (Enterprise Release)。
