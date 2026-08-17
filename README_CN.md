<div align="center">
<img src="frontend/public/favicon.png" width="250" alt="SinkDuce logo" />

# SINKDUCE

$$\text{\textbf{Spark. Sink. Educe.}}$$

*智能、上下文隔离的个人记忆系统 —— 一键部署的 RAG 智能体，内置 MCP 服务。*

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg?style=flat-square)](https://www.python.org/)
[![React](https://img.shields.io/badge/react-19-61dafb.svg?style=flat-square)](https://react.dev/)
[![Docker](https://img.shields.io/badge/docker-ready-2496ed.svg?style=flat-square)](https://www.docker.com/)
[![MCP](https://img.shields.io/badge/MCP-56_工具-6e47ff.svg?style=flat-square)](https://modelcontextprotocol.io/)

[快速启动](#-快速启动) • [工作原理](#-工作原理) • [功能特性 · 速览](#-速览) • [详细说明](#详细说明) • [MCP 服务](#-mcp-服务) • [系统架构](#-系统架构)

</div>

---

SinkDuce 是一个**高保真认知过滤器**——不是文档垃圾桶。它把会议、笔记和多格式文件，沉入**按项目隔离**的知识库；用**过程可见、来源可点**的智能问答取回；并通过 **MCP（56 个工具）** 交给 Cursor 等外部 Agent 使用。

**一句话：** Spark 采集 → Sink 沉淀 → Educe 调用。

**v1.1 要点：** 多架构镜像 `jethrohou/sinkduce`；Library **文件夹 + 时间线链/节点**、消息与智能待办、文件版本；本地 **FunASR ONNX**（应用内从本项目 GitHub Release 下载）；**56 个 MCP 工具**、9 个领域。

---

## 🚀 快速启动

**前置条件：** [Docker](https://docs.docker.com/get-docker/)

克隆仓库主要是为了拿到 `docker-compose.yml` 和存放 `./data`。更多说明见 [`deploy/README.md`](deploy/README.md)。

**拉取预构建镜像**（推荐，无需本机编译）：

```bash
git clone https://github.com/superdd-coder/sinkduce.git
cd sinkduce
# 可选: docker login   # 匿名 pull 常被限速，登录通常更快
docker compose pull
docker compose up -d
```

**从源码构建**（开发 / 自定义镜像）：

```bash
git clone https://github.com/superdd-coder/sinkduce.git
cd sinkduce
docker compose -f docker-compose.build.yml up -d --build
```

打开 [http://localhost:18900](http://localhost:18900)，然后：

1. （可选）需要**离线语音转写**时：设置 → Local Models → **Download local models**（官方 ONNX 包来自本项目 GitHub Release）。
2. **Settings** → 添加 **LLM** 提供商（OpenAI 兼容 API 均可）。
3. 添加 **Embedding** 提供商，创建第一个 **Collection**。

> [!TIP]
> **DashScope 一键配置** — Settings → LLM Providers → OneShot Setting (DashScope API)。输入阿里云 API Key，自动配置 LLM、Embedding、Reranker 以及文件/实时转写。

> [!TIP]
> **OpenRouter 一键配置** — Settings → OneShot Setting (OpenRouter API)。输入 Key 后自动拉取并分类模型（LLM / Chat·函数调用 / Vision / Embedding / Reranker）。

> [!TIP]
> **MinerU 云端解析（可选）** — 在 [mineru.net](https://mineru.net/apiManage/token) 取 Token → Settings 开启 MinerU → 在对应 **Collection → Config** 中启用。解析失败时自动回退本地解析器。

### 升级更新

```bash
git pull
docker compose pull
docker compose up -d
```

`data/`（库、配置、会议、笔记、热词、本地模型等）经 volume 挂载，升级默认不丢数据。源码构建请用 `docker-compose.build.yml`。

---

## 💡 工作原理

围绕三个动词：**Spark（采集）→ Sink（沉淀）→ Educe（调用）**。

### Spark — 采集

两条入口：**会议**（口语）与 **Notes**（书面）。

**会议：** 麦克风 + 系统声混录，或上传音频；支持暂停/继续与弃录。转写可用**本地 ONNX** 或云端 ASR（OneShot 可一把配齐）。转写后生成**总览总结**，并按你已有的 Collection 分类习惯做 **Blueprint 分主题拆章**；各章可再生成聚焦总结、编辑后**分配入库**。总结句可点回转写时间点并同步播音频；总结支持翻译与导出。

**Notes：** 每个 Collection 内 Tiptap 富文本写作（自动保存）。支持**分屏双页**对照编辑，左轨 Notes/Meetings、右轨引用关系；拖拽**蒸馏**、变更后**传播**，一键**入库**进入检索面。也可在工作区打开会议总结与笔记并排对照。

文件侧支持多格式上传（含 OCR），可选 MinerU 处理复杂版式；Library 支持**整夹上传**。

### Sink — 沉淀

一切落入 **Collection**——按项目/主题隔离的向量库，避免串味。

**Library** 是资料工作面，而非扁平上传列表：

- **文件夹视图**：目录浏览、版本、归档、多选整理、文件详情预览。
- **时间线视图**：链（Chain）与节点（Node）呈现工作演进；可挂文件、消息流、智能待办。

重要文件可标 **Definitive** 参与集合级固化：生成总览、项目描述，并标出文档间**冲突**（可双栏对照查看）。切分支持句边界与 Markdown 标题感知，可选父子块与上下文增强（细节见下方功能表与系统架构）。

### Educe — 调用

在 **Chat** 里多轮提问：输入区可**勾选一个或多个 Collection**，可**本轮切换模型**；回答流式返回，思考与检索步骤以时间线展示；来源可从片段钻到文档再到原文件。

复杂问题走 **Agentic**（分解、多路检索、评分后合成），简单问题可走更直接的检索路径；支持混合检索与 Rerank。可选 **Web 搜索**：须先配置，并在检索前**由你确认**是否允许联网（非静默外连）。

库内与会议侧提供 **Quick Chat**；**Recall** 页面向调参与评测。同一套记忆还通过 **MCP（56 tools）** 暴露给 IDE / Agent——记忆不锁在网页里。

---

## ✨ 功能特性

### ⚡ 速览

扫读用 bullet 列表（细表见下方「详细说明」）：

#### 部署与接入

- **一键 Docker**：多架构镜像；数据落本地卷；升级不丢库。
- **OneShot + 多 Provider**：DashScope / OpenRouter 一把配齐；LLM / Embedding / Rerank 可插拔（OpenAI 兼容）。
- **本地 ONNX 语音**：应用内下载官方包；可 Load/Unload；亦可云端 ASR。
- **分角色模型**：Visual / Chat（函数调用）/ Meeting summary 可分设。
- **MinerU（可选）**：复杂版式云解析；失败回退本地。

#### 采集

- **会议一条龙**：混录/上传 → 转写 → Blueprint 分节总结 → 入库；支持**暂停/弃录**。
- **句级溯源**：点总结句回转写时间点并同步音频；说话人、热词、语言提示。
- **总结翻译与导出**，便于会后分发。
- **热词库**：Settings 双栏管理；CSV/XLSX 导入导出；可设默认库。
- **Notes 工作区**：Tiptap；**分屏双页 + 引用轨**；蒸馏 / 传播 / 入库；可并排会议总结。
- **多格式 + 整夹上传**：常见办公与网页格式（含 OCR）；文件夹上传。

#### 沉淀

- **多 Collection 隔离**：项目分库；切分与维度可配。
- **文件夹 + 时间线**：版本、消息、Definitive、归档/多选、链/节点图、智能 Todo。
- **固化与冲突对照**：集合摘要；冲突列表 + **双栏对照**。
- **智能切分**：句边界 / Markdown；可选父子块、Contextual Retrieval。

#### 调用

- **Agentic RAG 对话**：过程时间线可见；来源三层可点；**跨库勾选**、**对话内换模型**；生成中可切会话。
- **可选 Web 搜索**：配置后在对话中开启；检索前**用户确认**；使用外网来源有提示。
- **Quick Chat / Recall**：库内与会议旁快问；Recall 评测台（专业用户）。
- **MCP 56 tools**：同一进程 HTTP；库 / 文件 / 检索 / 会议 / 笔记 / 热词等。

### 详细说明

> 以下为分类细表（表格）；日常扫读优先看上方 **「⚡ 速览」** 的 bullet 列表。
### Library 与文件管理

| 功能 | 说明 |
|------|------|
| **文件夹视图** | 层级库树、多挂载文件、分组、文件详情（预览 / 版本 / 消息）。 |
| **文件版本** | 版本历史、blob 可用性、就地更新；标记 **definitive** 供检索与固化。 |
| **时间线视图** | 可视化 **链 + 节点图**：并行链、里程碑节点、节点挂文件、平移/缩放导航。 |
| **消息流** | 消息作用域：Collection / 文件夹 / 文件 / 节点；在时间线侧栏撰写与编辑。 |
| **智能待办** | 根据链上下文 LLM 建议 todo；磨砂气泡审阅后加入时间线待办。 |
| **节点预览** | 不离开图即可查看节点附件与关联上下文。 |
| **Staging 上传** | 大文件可靠上传（REST + MCP staging），再进入 Library。 |
| **MCP 文件工具** | 13 个原子工具：库树、时间线、文件夹/文件、版本、链/节点/分组、上传、definitive。 |

### 会议（Meetings）

| 功能 | 说明 |
|------|------|
| **语音转写** | 文件上传或 WebSocket 实时流。**本地 FunASR ONNX**（SenseVoice 文件 + Paraformer 实时 + VAD/标点/说话人包，来自 GitHub Release）。云端可用 DashScope / OpenAI 兼容 ASR。 |
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
| **Library 优先组织** | 文件先落在 Library（文件夹、版本、时间线），再入库向量——不只是扁平上传列表。 |
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
| **MCP 服务** | 56 个原子工具，覆盖 9 个领域。HTTP Streamable 传输协议。复用 FastAPI 进程，无需额外服务。 |
| **可插拔 Provider** | 统一适配器模式，覆盖 LLM、Embedding、Reranker、文件转写、实时转写。添加新后端只需实现接口并注册。 |
| **一键配置** | DashScope 和 OpenRouter 预配置路径。自动拉取可用模型，按类型分类创建 Provider，设置默认值。 |
| **本地优先，云端可选** | FunASR **ONNX** 包（应用内从 GitHub Release 下载）+ RapidOCR（PP-OCRv6 权重随镜像发布）。LLM/Embedding 可指向 Ollama/LM Studio/vLLM 实现离线。 |
| **预构建 Docker 镜像** | Docker Hub 多架构镜像（`linux/amd64` + `linux/arm64`）；默认 compose 拉取，无需本机编译。 |
| **异步任务系统** | 双队列架构：上传队列 + 通用队列并行处理。支持取消和重试，SSE 日志流实时进度追踪。 |

---

## 🔌 MCP 服务

SinkDuce 在 FastAPI 进程内通过 HTTP（Streamable HTTP 协议）暴露 **56 个原子 MCP 工具**。MCP 服务复用主应用的 services、task_manager 和数据库连接——无需独立进程。

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
| **File Management** | 13 | 库树、时间线、文件夹/文件、版本、链/节点/分组、staging 上传、设置 definitive |
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
│  │  OpenAI兼容 · Cohere · DashScope · FunASR ONNX 本地  │  │
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

### 技术栈

| 层级 | 技术 |
|------|------|
| **后端** | Python 3.11、FastAPI、Uvicorn、Pydantic v2、PyYAML |
| **前端** | React 19、TypeScript、Vite 6、Tailwind CSS 4、Zustand、Radix UI、Tiptap、Recharts、Lucide React |
| **向量数据库** | Qdrant v1.13+（稠密向量 + BM25 稀疏向量，RRF 混合搜索） |
| **LLM/Embedding** | OpenAI 兼容协议，多 Provider 支持，可逐 Collection 覆盖 |
| **重排序** | Cohere（`rerank-multilingual-v3.0`）、DashScope/Qwen（`qwen3-vl-rerank`）、OpenAI 兼容（原生 `/rerank` 端点 → Chat Completions logprobs 回退） |
| **文档解析** | pdfplumber（页面级文本/表格/图片 + RapidOCR 回退）、mammoth + python-docx、openpyxl、python-pptx、markdownify、BeautifulSoup、RapidOCR、MinerU 云端 API |
| **语音转写** | FunASR ONNX（SenseVoiceSmall、Paraformer streaming、FSMN-VAD、CAM++、CT-Punc，GitHub Release 包）；DashScope；OpenAI 兼容 Whisper |
| **MCP** | MCP SDK 1.x、HTTP Streamable、56 个工具 |
| **基础设施** | Docker Compose（Qdrant + app）、多架构镜像发布到 Docker Hub、GitHub Actions CI |

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

## 📜 许可证

SinkDuce 采用 **[GNU Affero General Public License v3.0 or later](LICENSE)**（AGPL-3.0-or-later）开源。

第三方开源依赖（Python 包、前端库、字体、Docker 系统包、Qdrant 等）仍遵循各自许可证。完整声明与直接依赖清单见 **[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)**。当前审查范围内的直接依赖均为开源（MIT / BSD / Apache-2.0 / ISC / OFL-1.1 等）。可选的商业**远程 API**（OpenAI、Cohere、DashScope、MinerU 云端等）不属于本仓库源码许可证范围；仅其客户端库（若使用）属于开源依赖。

---

[English](README.md)
