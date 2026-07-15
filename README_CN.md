<div align="center">
<img src="frontend/public/favicon.png" width="250" alt="SinkDuce logo" />

# SINKDUCE

$$\text{\textbf{Spark. Sink. Educe.}}$$

*智能、上下文隔离的个人记忆系统 —— 一键部署的 RAG 智能体，内置 MCP 服务。*

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg?style=flat-square)](https://www.python.org/)
[![React](https://img.shields.io/badge/react-19-61dafb.svg?style=flat-square)](https://react.dev/)
[![Docker](https://img.shields.io/badge/docker-ready-2496ed.svg?style=flat-square)](https://www.docker.com/)
[![MCP](https://img.shields.io/badge/MCP-43_工具-6e47ff.svg?style=flat-square)](https://modelcontextprotocol.io/)

[快速启动](#-快速启动) • [工作原理](#-工作原理) • [功能特性](#-功能特性) • [MCP 服务](#-mcp-服务) • [系统架构](#-系统架构)

</div>

---

SinkDuce 是一个**高保真认知过滤器**——不是文档垃圾桶。它将会议、讲座、笔记和文件转化为结构化、上下文隔离的知识，让你可以跨项目边界进行查询、提炼和联邦检索。每个回答都能通过三层溯源追溯到原始出处。

---

## 🚀 快速启动

**前置条件**：[Docker](https://docs.docker.com/get-docker/)

```bash
git clone https://github.com/superdd-coder/sinkduce.git
cd sinkduce
docker compose up -d --build
```

打开 [http://localhost:18900](http://localhost:18900)。首次启动后：

1. 如需离线语音转写，下载本地模型（FunASR SenseVoiceSmall）。
2. 前往 **Settings（设置）** → 添加 **LLM 提供商**（兼容 OpenAI 协议的 API 均可）。
3. 添加 **Embedding 提供商**，创建你的第一个 Collection（知识库）。

> [!TIP]
> **DashScope 一键配置** — Settings → LLM Providers → OneShot Setting (DashScope API)。输入阿里云 API Key，自动配置 LLM（`deepseek-v4-flash`）、Embedding（`text-embedding-v4`，1024 维）、Reranker（`qwen3-rerank`）以及文件/实时转写（`fun-asr`、`fun-asr-realtime`）。

> [!TIP]
> **OpenRouter 一键配置** — Settings → LLM Providers → OneShot Setting (OpenRouter API)。输入 Key 后自动拉取模型列表并分类为 LLM、Chat（支持函数调用）、Vision（视觉模型）、Embedding 和 Reranker。

> [!TIP]
> **MinerU 云端解析（可选）** — 高质量 PDF 解析，保留表格、公式和版面结构：
>
> 1. 在 [mineru.net/apiManage/token](https://mineru.net/apiManage/token) 免费获取 API Token。
> 2. 前往 **Settings（设置）** → 滚动到 **MinerU CLOUD PARSING** → 打开 **ENABLE** → 粘贴 Token。
> 3. 进入具体 **Collection → Config**，开启 **Cloud Parsing (MinerU)** 为该知识库激活云端解析。
>
> 开启后，上传的 PDF/DOCX/PPTX/图片将由 MinerU 云端 API 解析，解析失败时自动回退到本地解析器。

### 升级更新

```bash
git pull && docker compose up -d --build
```

`data/` 目录（Qdrant 数据库、配置、聊天记录、会议、笔记、热词库）通过 Docker volume 挂载，重建镜像不会丢失数据。

---

## 💡 工作原理

SinkDuce 围绕三个动词构建：

### Spark（激发）—— 捕捉

SinkDuce 提供两种独立的捕捉入口：**会议**处理口语和音频，**笔记**处理书面思考和结构化写作。

#### 会议（Meetings）

录制会议（同时采集麦克风和系统音频）或上传音频文件。**FunASR SenseVoiceSmall** 模型在本地完成离线语音转写。如需更高精度，可接入 DashScope 或 OpenAI 兼容的云端转录模型——DashScope 接口经过专门优化，推荐通过 **OneShot Setting (DashScope API)** 一键完成 LLM + Embedding + Reranker + 转录的全套配置。实时 **WebSocket 流式转写**在说话时同步显示字幕，自动区分临时结果和最终文本。支持说话人分离、VAD 和标点恢复，进一步提升转写质量。

转写完成后，**双流程 LLM 管线**启动：

1. **General Summary + Blueprint 自动拆章**：第一流程通过 SSE 流式生成会议总结；同时第二个调用生成 Blueprint——系统将用户已有的 **Collection 目录（名称、定义、覆盖范围）** 作为分类依据传入 LLM，LLM 自动识别会议内容中涉及的各项议题，将其拆分为语义独立的章节。已有的 Collection 会被直接对接；会议中出现了用户尚未建立 Collection 的新议题时，**LLM 会继承用户的分类逻辑，识别出相同维度的独立议题并建议开设新的 Collection**，保持整个知识库分类体系的一致性。
2. **逐章节深度总结**：对每个自动识别的章节，LLM 从转写文本中精准定位相关句子，生成聚焦的 Markdown 总结（SSE 流式输出）。

总结中每个句子都可以点击跳转回原始转写片段的对应时间点并同步播放音频。章节可以一键**分配到 Collection**——内容自动分块、嵌入、存入向量数据库，成为可检索的知识文档。你还可以手动添加自定义章节、修改章节描述、重新生成单个章节的总结。所有总结均为可编辑 Markdown，修改不会被后续操作覆盖。

会议管理界面提供：会议列表、可编辑标题、音频播放控制条（实时字幕开关、热词库选择器、多语言提示选择器）、分标签页查看总结/笔记/转写文本/说话人信息。转写面板支持全文搜索、说话人名称编辑、章节标签定位。

#### 笔记（Collection Notes）

在每个 Collection 中创建结构化笔记，使用完整的 **Tiptap WYSIWYG 编辑器**撰写——支持 Markdown 语法、标题、表格、任务列表、代码块、图片粘贴/拖拽和 YouTube 嵌入。笔记自动保存。

核心能力是**蒸馏（Distill）**和**传播（Propagate）**：

- **蒸馏**：将左侧笔记列表中的任意笔记**拖拽**到当前编辑器中，LLM 自动提炼源笔记的核心内容，生成引用块嵌入当前位置。蒸馏结果自动缓存——源笔记未修改时不会重复调用 LLM。
- **传播**：当一篇被引用的源笔记内容变更时，点击「传播变更」按钮，系统会重新蒸馏源笔记，更新所有下游笔记中的对应引用块，并**递归链式传播**——下游笔记如果被更下游的笔记引用，也会一并更新。传播前可预览完整的更新链路。
- **双向引用图**：系统自动维护笔记间的引用关系，右侧边栏展示 Distill In（谁被我引用）和 Distill Out（谁引用了我）导航。

笔记还可以**一键摄入（Ingest）**到 Collection 中——内容自动分块、嵌入、索引，成为可检索的文档。摄入后可随时移除。支持从 .md/.txt 导入、导出为 .md 文件。

### Sink（沉淀）—— 组织

一切内容都归入 **Collection**——一个隔离的 Qdrant 向量数据库。每个项目、课程或领域拥有独立的 Collection，零交叉污染。

上传文档支持 **12 种格式**：PDF（含扫描版 OCR）、DOCX、PPTX、XLSX、Markdown、HTML、CSV、JSON/JSONL、纯文本、图片（OCR）。还可以选择 **MinerU 云端解析器**，提供更高质量的 PDF/DOCX/PPTX/图片提取，支持版面保留、公式识别和表格结构检测。

每个文档被解析后进行**分块（Chunking）**。系统按段落或 Markdown 标题层级智能切分，在句子边界处断开，兼容中英文标点。表格超长时按行截断但各自保留表头；图片块和 distill 块保持完整不截断。支持 **父子模式（Parent-Child）**：父块携带完整上下文；检索时匹配更精准的子块，但返回父块的完整文本。

如果启用了**上下文检索（Contextual Retrieval）**，LLM 会为每个块生成一段背景上下文，补全该块在原文中被省略的全局信息。大文档支持异步批量处理。

分块完成后，LLM 自动为每个文档生成结构化摘要（关键数据 / 事实 / 洞察）。

当有文档被新增或移出 "definitive" 标记时，**固化（Consolidation）** 自动触发：LLM 读取所有标记为 "definitive" 的文档摘要，生成集合级总览、项目描述以及冲突报告（标记文档之间的矛盾）。

系统还自动维护**集合目录（Collection Catalog）**——每个集合的定义、覆盖范围和标签，供 Agent 进行语义查询路由。

### Educe（引出）—— 推理

提出问题，系统检索、评分、合成。

**基于会话的聊天**通过 SSE 流式返回响应，Agent 的思考过程和工具调用以**时间线**形式交织呈现——思考文本和检索步骤交替展示，全程可观测。用户可以切换 **Think** 按钮启用深度推理模式。

两种搜索模式，由 LLM Agent 根据问题复杂度自主选择：

- **Direct（直接检索）**：单次混合检索。查询同时进行稠密向量和 BM25 稀疏编码（LLM 自动提取关键词并扩展同义词），Qdrant 通过倒数排名融合（RRF）合并结果。可选的重排序器对候选文档重新打分，确保最相关的内容排在前面。
- **Agentic（智能检索）**：完整的多步推理管线。LLM 将复杂问题分解为原子子查询，利用 Collection 目录的元数据（定义、标签、覆盖范围）将每个子查询路由到最相关的 Collection；对每个子查询生成多个语义变体并行检索，去重后 LLM 一次性完成相关性判断和缺口分析；最后聚合所有任务的上下文，合成最终答案。

每个回答都具备**三层溯源**：点击任意来源，可以从具体文本片段 → 所在文档的完整上下文 → 原始文件预览，逐层深入验证。

内置的**召回评估（Recall Evaluation）** 套件自动生成测试用例，LLM 作为评判者对检索结果逐条打分并给出理由，同时给出整体 "can_answer" 判定。指标包括召回率、MRR 和质量分数。

SinkDuce 还内置了 **MCP 服务**（43 个原子工具），让沉淀的记忆不止停留在 Web UI 里。接入 Claude Code、Cursor 等 MCP 兼容客户端后，AI 编程助手可以直接检索你的知识库、管理文档、操作会议和笔记——**让记忆流动到任何你工作的场景中**。

---

## ✨ 功能特性

### 会议（Meetings）

| 功能 | 说明 |
|------|------|
| **语音转写** | 文件上传或 WebSocket 实时流。FunASR 本地离线运行，也可接入 DashScope 或 OpenAI 兼容云端模型获取更高精度。支持说话人分离、VAD 和标点恢复。 |
| **实时字幕** | 录制时实时推送转写结果，自动区分临时/最终文本。音频播放时同步滚动到对应段落。 |
| **Blueprint 自动拆章** | 基于用户已有的 Collection 分类体系，LLM 自动识别会议中的各项议题，拆分为语义独立的章节——每个章节天然对接对应的 Collection。支持手动添加章节和重新生成。 |
| **逐章节深度总结** | 每个章节由 LLM 从转写文本中精准定位相关句子，生成聚焦的 Markdown 总结（SSE 流式输出）。 |
| **可编辑总结** | 所有总结均为可编辑 Markdown，General Summary、章节总结、会议笔记各自独立保存，修改不会被后续操作覆盖。 |
| **会议笔记** | 每个会议提供独立的 Markdown 笔记页，可在会议过程中随时记录，与自动生成的总结并存。支持从 .md/.docx/.txt 上传导入。 |
| **句级溯源** | 总结中每个句子可点击跳转到原始转写片段对应时间点，同步音频播放。每句话自动归入所属的话题章节，可清晰看到各议题在会议中的分布脉络。 |
| **说话人管理** | 转写面板中编辑说话人名称，说话人标签页展示每个说话人的信息卡片和采样段落。 |
| **热词库 & 语言提示** | 会议关联热词库（加权词汇 + 语言代码）和多语言提示，提升特定领域术语的转写准确率。 |

### 笔记（Collection Notes）

| 功能 | 说明 |
|------|------|
| **Tiptap 编辑器** | 完整 WYSIWYG 编辑体验，支持 Markdown、标题、表格、任务列表、代码块、图片粘贴/拖拽、YouTube 嵌入。自动保存。 |
| **蒸馏（Distill）** | 拖拽笔记到编辑器中，LLM 自动提炼源笔记核心内容，生成引用块。结果自动缓存——源未变不重复调用 LLM。 |
| **传播（Propagate）** | 源笔记变更后点击「传播」，重新蒸馏 → 更新所有下游笔记引用块 → 递归链式传播。传播前可预览完整更新链路。 |
| **双向引用图** | 自动维护笔记间的引用关系。右侧边栏展示「谁被我引用」和「谁引用了我」导航。 |
| **摄入与导出** | 一键摄入：笔记内容自动分块、嵌入、索引为可检索文档，摄入过程中自动对图片进行 OCR 和视觉描述，无需手动处理。可随时移除。支持 .md/.txt 导入导出。 |

### 摄入与组织

| 功能 | 说明 |
|------|------|
| **12 种格式解析** | PDF（含扫描版 OCR）、DOCX、PPTX、XLSX、Markdown、HTML、CSV、JSON/JSONL、纯文本、图片（OCR）。可接入 **MinerU** 以获得更强大的文件解析能力。 |
| **上下文隔离的 Collection** | 独立 Qdrant 向量数据库。可配置：分块模式、父块策略、分块大小、嵌入维度、搜索模式、文件类型白名单、上下文增强、Agent、MinerU 云端解析等开关。 |
| **父子分块** | 父块携带完整上下文，检索匹配更精准的子块但返回父块文本。支持按段落、按 Markdown 标题层级或按固定 token 数三种策略。 |
| **上下文检索** | LLM 为每个块补充背景上下文，补全被省略的全局信息。大文档支持异步批量处理。 |
| **自动摘要与固化** | LLM 自动为每个文档生成结构化摘要。集合级固化总览与冲突检测（标记文档间的矛盾）。标记为 "definitive" 的文档参与固化。 |
| **集合目录** | 自动维护每个集合的定义、覆盖范围、标签。供 Agent 语义查询路由使用。 |
| **语义会议路由** | 跨主题会议自动切分：每章节通过目录匹配最合适的 Collection。 |

### 检索与推理

| 功能 | 说明 |
|------|------|
| **混合搜索** | 稠密向量 + BM25 稀疏（LLM 自动提取关键词并扩展同义词）。Qdrant 倒数排名融合（RRF）。文档增删后自动在达到阈值时重建稀疏词表，避免词权重漂移。 |
| **多提供商重排序** | Cohere、DashScope/Qwen、OpenAI 兼容。可插拔架构，按需切换。 |
| **Agentic RAG** | 分解 → 并行变体生成 → 检索 → 合并评分（相关性 + 缺口分析，一次 LLM 调用）→ 聚合 → 合成。全程可观测。 |
| **多 Collection 联邦搜索** | 跨多个 Collection 并行查询。利用目录元数据将子查询路由到最相关的 Collection。 |
| **三层溯源** | 回答 → 文本片段 → 完整文档 → 原始文件预览，逐层深入验证。 |
| **会话式聊天** | 持久化多轮对话，LLM Agent 自主选择搜索策略。时间线展示思考过程 + 工具调用。Think 按钮切换深度推理。会话自动命名。 |
| **Collection 快速问答** | 悬浮滑出面板，SSE 流式输出，思考过程展示，来源导航。适合轻量快速查询。 |
| **召回评估** | 自动生成测试用例，LLM 评判逐条打分并给出理由。指标包括召回率、MRR 和质量分数。评估历史可回溯。 |

### 可扩展性

| 功能 | 说明 |
|------|------|
| **MCP 服务** | 43 个原子工具，覆盖 8 个领域。HTTP Streamable 传输协议。复用 FastAPI 进程，无需额外服务。 |
| **可插拔 Provider** | 统一适配器模式，覆盖 LLM、Embedding、Reranker、文件转写、实时转写。添加新后端只需实现接口并注册。 |
| **一键配置** | DashScope 和 OpenRouter 预配置路径。自动拉取可用模型，按类型分类创建 Provider，设置默认值。 |
| **本地优先，云端可选** | FunASR 和 Tesseract 本地运行。所有 Provider 可指向 Ollama/LM Studio/vLLM，实现完全离线运行。 |
| **异步任务系统** | 双队列架构：上传队列 + 通用队列并行处理。支持取消和重试，SSE 日志流实时进度追踪。 |

---

## 🔌 MCP 服务

SinkDuce 在 FastAPI 进程内通过 HTTP（Streamable HTTP 协议）暴露 **43 个原子 MCP 工具**。MCP 服务复用主应用的 services、task_manager 和数据库连接——无需独立进程。

### 配置方式

在项目根目录 `.mcp.json` 中添加（或 `~/.claude/.mcp.json` 全局配置）：

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

先启动后端（`docker compose up -d`），MCP 客户端连接到已运行的服务器即可。

### 工具域

| 域 | 数量 | 核心能力 |
|------|------|----------|
| **Collections** | 5 | 全量列表、获取元数据+配置、创建（26 个可配置参数）、更新配置（拒绝破坏性字段：`chunk_mode`、`embedding_*`）、删除（拒删最后一个） |
| **Documents** | 6 | 列表（含元数据）、通过 staging token 或服务器本地路径上传、删除（清理块+摘要+触发稀疏重算）、分块查看（分页，可按父子过滤）、全文提取（窗口化）、切换 definitive 标记 |
| **Search** | 3 | 直接检索（dense/sparse/hybrid，可选重排序，多 Collection）、Agentic RAG（全管线，通过目录自动发现 Collection）、查询历史（可选详情展开） |
| **Tasks** | 5 | 列表（可按 Collection、状态、类型过滤）、查看状态（含进度和错误）、取消（协作式）、重试（重新入队失败任务）、清除已完成 |
| **Summaries** | 4 | Collection 总览、文档结构化摘要（Data/Facts/Insights）、冲突列表、触发固化（异步任务） |
| **Notes** | 6 | 列表（含 extracted/ingested 标记）、查看（元数据+内容+引用关系）、创建（自动时间戳标题）、更新（标题/内容，自动同步注入块）、删除（清理块+反向链接）、触发传播（同步重新蒸馏，链式传播） |
| **Meetings** | 9 | 列表（可按状态/搜索过滤）、查看（元数据+tabs+has_transcript/has_summary/has_notes 标记）、获取章节 Markdown（`tab_id="general"` 为总摘要）、分页转写文本（优先 `sentences.json` 含说话人分离）、创建、更新（说话人名称字典、热词库、笔记内容）、删除（清理已分配的块）、启动摘要（异步任务）、通过 staging 上传音频 |
| **Hot Words** | 5 | 列表、查看（含加权词+语言代码）、创建、更新（全量词表替换）、删除 |

---

## 🏗️ 系统架构

```
┌──────────────────────────────────────────────────────────┐
│                   浏览器 (React 19)                       │
│Chat │ Collections │ Notes │ Meetings │ Settings │ Recall │
└──────────────────────┬───────────────────────────────────┘
                       │ REST + SSE + WebSocket
┌──────────────────────▼───────────────────────────────────┐
│                FastAPI (Python 3.11)                     │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ /api/*   │  │  /mcp    │  │  /ws     │  │ /health  │  │
│  │ REST API │  │ MCP HTTP │  │ 实时转写  │  │ 健康检查   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────────┘  │
│       │             │             │                      │
│  ┌────▼─────────────▼─────────────▼───────────────────┐  │
│  │              Services 单例                          │  │
│  │  Config → Qdrant → Embedding → LLM → Retriever     │  │
│  │    → Reranker → DirectQuery → VariantFetcher       │  │
│  │    → Decomposer → Aggregator → AgenticQuery        │  │
│  │    → ContextualRetrieval → Chunker → SessionStore  │  │
│  └──────────────────────┬─────────────────────────────┘  │
│                         │                                │
│  ┌──────────────────────▼─────────────────────────────┐  │
│  │  Providers（Registry + ABC + Factory 可插拔架构）     │  │
│  │  LLM │ Embedding │ Reranker │ Transcription        │  │
│  │  OpenAI兼容 · Cohere · DashScope · FunASR 本地       │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  领域模块                                            │  │
│  │  meeting/ · notes/ · hot_words/ · collections/     │  │
│  │  tasks/（双队列：上传串行 + 通用并发池）                 │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│              Qdrant（向量数据库）                           │
│  Collection A │ Collection B │ ... │ __summaries__       │
└──────────────────────────────────────────────────────────┘
```

### 目录结构

```
frontend/            React 19 + Vite 6 + Tailwind CSS 4 + Zustand + Tiptap + Recharts
src/
  main.py            FastAPI 入口：lifespan、中间件、路由挂载、SPA 兜底
  config.py           Pydantic AppConfig → data/config.yaml（含向后兼容迁移逻辑）
  services.py         Services 单例：init_services() / reload_services()（失败自动回滚）
  prompts.py          集中式 Prompt 注册表——所有 LLM prompt 统一存放
  api/
    schemas.py        共享 Pydantic 请求/响应模型（QueryRequest、SourceItem 等）
    routes/           REST 端点：sessions、query、documents、collections、config、
                      recall、logs、info、visual、meetings、notes、hot_words
  rag/
    agent.py          AgenticQueryService：分解 → 并行扇出 → 评分 → 合成
    variant_fetcher.py 并行变体生成 + 单轮合并评分
    decomposer.py     使用 Collection Catalog 元数据进行查询分解
    aggregator.py     带 <sub_query> 包装器的上下文组装 + LLM 合成
    retriever.py      Dense / Hybrid (RRF) / Sparse 检索
    reranker.py       多提供商重排序编排
    chunker.py        ParagraphChunker（句子边界感知，兼容中英文标点）
    markdown_chunker.py Markdown 感知分块：标题面包屑、原子代码块/表格
    contextual.py     上下文检索（Batch API + 并行回退）
    summary_manager.py 文档/集合摘要、冲突检测、项目描述
  parsers/            12 种格式解析器：PDF、DOCX、PPTX、XLSX、MD、HTML、CSV、
                      JSON/JSONL、TXT、图片 (OCR)、MinerU 云端
  providers/          LLM、Embedding、Reranker 适配器（Registry + ABC 模式）
    embedding/        OpenAI 兼容（支持 Matryoshka/截断）
    llm/              OpenAI 兼容（流式、Vision、Batch API）
    reranker/         Cohere、DashScope/Qwen、OpenAI 兼容（原生 /rerank + logprobs 回退）
  meeting/            会议模块：转写、Blueprint、章节提取
    transcription/    文件 + 实时转写 Provider（FunASR 本地、DashScope、OpenAI 兼容）
  notes/              Collection Notes：蒸馏、传播、注入块解析
  hot_words/          带权重的热词库管理（用于 ASR）
  tasks/              双队列异步任务管理器，支持协作取消
  mcp/                MCP 服务：43 个工具覆盖 8 个领域，HTTP Streamable 传输
  models/             HuggingFace/ModelScope 模型下载管理
data/                 运行时数据（全部 gitignored）：qdrant/、config.yaml、history/、
                      meetings/、notes/、hot_words/、models/、collections/
tests/                pytest 测试套件（asyncio_mode = auto）
```

### 技术栈

| 层级 | 技术 |
|------|------|
| **后端** | Python 3.11、FastAPI、Uvicorn、Pydantic v2、PyYAML |
| **前端** | React 19、TypeScript、Vite 6、Tailwind CSS 4、Zustand、Radix UI、Tiptap、Recharts、Lucide React |
| **向量数据库** | Qdrant v1.13+（稠密向量 + BM25 稀疏向量，RRF 混合搜索） |
| **LLM/Embedding** | OpenAI 兼容协议，多 Provider 支持，可逐 Collection 覆盖 |
| **重排序** | Cohere（`rerank-multilingual-v3.0`）、DashScope/Qwen（`qwen3-vl-rerank`）、OpenAI 兼容（原生 `/rerank` 端点 → Chat Completions logprobs 回退） |
| **文档解析** | pdfplumber（页面级文本/表格/图片 + Tesseract OCR 回退）、mammoth + python-docx、openpyxl、python-pptx、markdownify、BeautifulSoup、Tesseract、MinerU 云端 API |
| **语音转写** | FunASR（SenseVoiceSmall、Paraformer streaming、FSMN-VAD、CAM++ 说话人分离、CT-Transformer 标点恢复）、DashScope、OpenAI 兼容 Whisper |
| **MCP** | MCP SDK 1.0+、HTTP Streamable 传输协议 |
| **基础设施** | Docker Compose（Qdrant + app）、GitHub Actions CI（Docker 构建 + pytest + tsc） |

---

## ⚙️ 环境变量

所有变量均为可选。复制 `.env.template` 为 `.env` 即可自定义：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `API_PORT` | `18900` | 后端 API + MCP 服务端口 |
| `UI_PORT` | `5173` | Vite 开发服务器端口（仅开发环境） |
| `QDRANT_HTTP_PORT` | `6343` | Qdrant HTTP API 端口（宿主机端口） |
| `QDRANT_GRPC_PORT` | `6334` | Qdrant gRPC 端口（宿主机端口） |

---

## 🗺️ 未来路线图

- [ ] 多租户服务端部署架构，支持团队协同项目记忆（企业版）

---

[English](README.md)
