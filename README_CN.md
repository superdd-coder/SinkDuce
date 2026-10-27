<div align="center">
<img src="assets/logo-banner-dark.png" alt="SinkDuce logo" width="100%" />

<img src="assets/logo-wordmark.png" alt="SinkDuce" width="340" />

$$\text{\textbf{Spark. Sink. Educe.}}$$

*智能、按项目隔离的个人记忆：会议、笔记、文件沉入 Collection，可引用的问答，并给外部 Agent 提供 MCP。*

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg?style=flat-square)](https://www.python.org/)
[![React](https://img.shields.io/badge/react-19-61dafb.svg?style=flat-square)](https://react.dev/)
[![Docker](https://img.shields.io/badge/docker-ready-2496ed.svg?style=flat-square)](https://www.docker.com/)
[![MCP](https://img.shields.io/badge/MCP-65_工具-6e47ff.svg?style=flat-square)](https://modelcontextprotocol.io/)

[快速启动](#快速启动) · [报告与修订](#报告与修订) · [功能](#功能) · [MCP](#mcp) · [English](README.md)

</div>

SinkDuce 是一套可自托管的 **RAG 知识系统**，用于个人与项目记忆。支持 macOS 桌面应用或 Docker 部署。会议、笔记与文档按 **Collection** 隔离存储；问答结果附带来源，可回溯至原文。同一资料库同时作为 **MCP 服务** 对外提供，供 Cursor 等编辑器检索与写入。

LLM、Embedding 与语音转写既可使用云端 API，也可接入 **本地模型**（Ollama、LM Studio、vLLM，以及本机 ONNX 语音）。资料保存在你的部署中；选择云端服务时，当前任务所需的内容会发送到相应服务商。安装并配置本地模型后，可以把推理留在本机。

**Spark** 采集 → **Sink** 沉淀 → **Educe** 调用。

**从资料到成稿：** 在聊天中上传附件、生成带引用的报告，添加批注并逐项审阅修改。会议纪要支持表格和 SVG 图示，可导出 Word、PDF、Markdown；macOS 桌面版已内置 PDF 转换组件。

源码统一采用 **AGPL-3.0-or-later**，包含角色与权限管理。历次更新见 [CHANGELOG](CHANGELOG.md)。

---

## 快速启动

### macOS（Apple Silicon）

1. 下载 **[SinkDuce v1.6.2（Apple Silicon）](https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev/SinkDuce-macos-arm64-v1.6.2.dmg)**（[SHA-256 校验文件](https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev/SinkDuce-macos-arm64-v1.6.2.dmg.sha256)）。
2. 打开磁盘映像，把 **SinkDuce** 拖进 Applications。
3. 应用采用 ad-hoc 签名，尚未经 Apple 公证。如果 macOS 拦截，请在**系统设置 → 隐私与安全性 → 仍要打开**中允许并确认。
4. 升级后请 **Cmd+Q 再开**——点红灯只是藏到菜单栏。

数据在 `~/Library/Application Support/SinkDuce`。API 和 MCP 默认 `127.0.0.1:18910`（被占用则顺延）。

也可以运行安装器：自动下载最新桌面版、检查 SHA-256 后安装。

```bash
curl -fsSL https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev/install.sh | sh
```

然后：Settings → 添加 **LLM** 和 **Embedding**（或用下面的 OneShot）→ 建 Collection → 上传文件或录制会议 → 开始提问。后续可在应用内更新；[发布说明](https://github.com/superdd-coder/SinkDuce/releases)可公开访问。

### Docker

```bash
mkdir sinkduce && cd sinkduce
curl -fLO https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev/docker-compose.yml
docker compose up -d
```

打开 [http://localhost:18900](http://localhost:18900)。需预先安装 Docker Desktop 或带 Compose 的 Docker Engine。局域网通过 `18901` 端口和设备配对访问；主人入口保持仅本机可达。

升级前先备份 `data/`，再在原部署目录运行：

```bash
docker compose pull
docker compose up -d
```

`data/` 保存资料库、配置、会议、笔记和模型。重建容器会保留这个挂载目录。

需要从公开源码构建时：

```bash
git clone https://github.com/superdd-coder/SinkDuce.git
cd SinkDuce
docker compose -f docker-compose.build.yml up -d --build
```

其它部署布局见 [`deploy/README.md`](deploy/README.md)。

> [!TIP]
> **DashScope / OpenRouter 一键配置** — Settings → LLM Providers。一把 API Key 可配齐 LLM、Embedding、Rerank、Vision、转写。
>
> **离线语音** — Settings → Local Models → Download local models。ONNX 模型包从公开发布镜像（Cloudflare R2）下载，并校验文件完整性。
>
> **MinerU（可选）** — 在 [mineru.net](https://mineru.net/apiManage/token) 取 Token，按 Collection 开启；失败回退本地解析。

---

## 工作原理

### Spark — 采集

**会议：** 麦克风 + 系统声混录，或上传音频；可暂停、弃录。转写可用**本地 ONNX** 或云端 ASR。生成精炼总结，以及按现有 Collection 拆开的 **Blueprint 章节**；改完再入库。纪要优先呈现结论、关键依据、行动项和待确认问题，按需使用表格或 SVG 图示。点总结句可跳回转写时间点并播音频。总结及译文均可导出 **Word、PDF、Markdown**。

**会前（PREPARE）：** 预选参会人、写议程；SinkDuce 从历史会议沉淀**人物侧写**，并围绕你的议程合成**一页简报** — 谁参会、待办、上次的进展。

**会中：** 实时字幕，加**实时纪要**（要点 / 决策 / 待澄清 / 行动项边说边出）与**实时翻译** — 端到端同传模型输出双语字幕，录制中可随时开关或换目标语言，采集不中断。

**说话人匹配：** 维护一份人物库。会后会建议是谁在说话，不必每次重命名。

**Notes：** 每个 Collection 里的 Tiptap 编辑器（自动保存）。分屏编辑，蒸馏成引用、源变更后传播、一键入库。可以和会议总结并排打开。

**文件：** PDF、Office、Markdown、HTML、CSV、图片等（含 OCR）。复杂版式可用 MinerU。Library 支持整夹上传。

### Sink — 沉淀

一切落入 **Collection**，按项目或主题隔离。

**Library** 是对应资料的工作界面：

- **文件夹** — 浏览、版本、移动 / 归档、列表或网格视图、文件预览。
- **时间线** — 用链和节点看工作怎么演进；可挂文件、消息、待办。

重要来源标 **Definitive**，参与集合总览和**冲突**对照。切分感知句子和 Markdown 标题；可选父子块和上下文增强。

### Educe — 调用

**Chat：** 选择一个或多个 Collection，本轮可换模型，也可直接上传对话附件；流式回答，思考 / 检索步骤可见。点击引用，可并排查看渲染后的原文依据与源文件预览，再进入文件详情。复杂问题走 Agentic，简单问题走直接检索。可选**联网搜索**，检索前会先问你。

**会议对话：** 在会议里直接追问 — Quick Chat 可检索该会议转写；**群聊**可跨一系列后续会议，引用可回指每场会。

Library / 会议旁有 **Quick Chat**。要评测召回用 **Recall**。同一套记忆通过 **MCP** 对外，不锁在网页里。

---

## 报告与修订

在 Chat 或 Quick Chat 中，要求 Agent 根据对话、上传附件和检索资料生成报告。报告在聊天旁的可调宽工作区打开，右上角 **Output / Sources** 卡片集中展示生成文件与来源。

1. 选中文字，或对表格、图片添加批注。批注合并在一个聊天标签中，可补充整体要求后一并发送。
2. 查看 Agent 的简短答复与对应修改。新增文字显示为绿色，删除文字用琥珀色删除线；表格内的文字修订直接显示在原表格中。
3. 接受、拒绝或继续讨论。不同位置的改动可以分别审阅，也可以处理同一条指令下的一组修改。
4. 在**历史记录**中查看、恢复已保存的文档状态，或导出 **Word、PDF、HTML**。整篇重写或切换语言时另生成报告，保留原文。

## 功能

| 领域 | 你能做什么 |
|------|------------|
| **会议** | 混录或上传 → 转写（本地或云）→ 精炼总结 + 按主题拆章 → 入库。表格、SVG 图示、句级回跳、跨会议说话人匹配、翻译；Word / PDF / Markdown 导出。 |
| **PREPARE** | 预选参会人、写议程；历史会议沉淀**人物侧写**；**一页式会前简报**（参会人 + 待办 + 上次回顾）。 |
| **会中实时** | 实时字幕 + **实时纪要**（要点 / 决策 / 行动项）；**实时翻译**双语字幕，录制中可开关、可换语言。 |
| **Notes** | 所见即所得编辑（Markdown、表格、图片、任务）。蒸馏、传播引用、入库（图片做 OCR 和视觉描述）。 |
| **Library** | 文件夹、版本、时间线（链 / 节点）、消息、智能待办。移动 / 归档、列表或网格视图。Definitive、集合总览、冲突对照。 |
| **入库** | 常见办公 / 网页格式 + OCR；可选 MinerU。图片会生成检索用描述。 |
| **Chat** | 跨库提问、对话附件、可见步骤、源文件预览、对话内换模型。可选、需确认的联网搜索。Quick Chat + Recall、跨后续会议的**会议群聊**。 |
| **报告** | 聊天旁生成带引用的文档；锚定批注、关联修订讨论、逐项接受 / 拒绝、分组审阅、历史恢复，以及 Word / PDF / HTML 导出。 |
| **配置** | DashScope / OpenRouter 一键配置。Chat / Vision / 会议总结可分模型。Settings 内下载本地 ONNX 语音。中英双语界面。 |
| **MCP** | 同一进程 65 个工具（只经回环） — 库、文件、检索、会议、笔记、热词、任务，外加访问其他盒子的 **Hub** 工具。 |

---

## MCP

MCP 和 API 在同一进程，**只经回环可达** —— 本机上的 Agent 能连，网络上任何东西都连不上。
先启动 SinkDuce，再让客户端连上来：

```json
{
  "mcpServers": {
    "sinkduce": {
      "type": "http",
      "url": "http://127.0.0.1:18900/mcp"
    }
  }
}
```

不涉及钥匙：回环即「本机」，所以 Agent 就是主人，跟应用自己的前端完全一致。
设置里的 **MCP** 卡片会替你生成这段 JSON，端口也是对的（桌面版通常绑定 `18910`）。

**这台盒子同时是 Hub。** 连上它的 Agent 可以用 `list_boxes` / `switch_box` 看到本机
已连接的其他盒子，并用 `search_all_boxes` 一次搜遍全部。Agent 永远不需要那些盒子的
钥匙 —— 由这台盒子保管并代为调用。

**有意不提供远程 / 局域网 MCP**，配对钥匙也打不开它 —— 别的机器请求 `/mcp` 会被拒，
而且拒绝的方式不会暴露「这个路径存在」。要操作别的盒子，就在那台机器上跑 Agent、
连它自己的 MCP。

| | 地址 |
|---|------|
| Docker | `http://127.0.0.1:18900/mcp` |
| 桌面 | 应用实际绑定的端口，一般是 `http://127.0.0.1:18910/mcp` |

| 域 | 工具数 | 用途 |
|----|------:|------|
| Collections | 5 | 列表、创建、配置、删除 |
| Documents | 6 | 列表、上传、全文、分块、definitive |
| File management | 17 | 库树、时间线、文件夹 / 文件 / 版本、上传 |
| Search | 3 | 直接检索、Agentic 检索、历史 |
| Tasks | 5 | 列表、状态、取消、重试 |
| Summaries | 4 | 总览、文档摘要、冲突、固化 |
| Notes | 6 | CRUD + 传播 |
| Meetings | 11 | CRUD、转写、总结、音频 |
| Hot words | 5 | 热词库 |
| Hub | 3 | 列出盒子、切换当前盒子、一次搜遍所有盒子 |

---

## 技术栈

| | |
|---|---|
| 应用 | Python 3.11、FastAPI、React 19、Vite、Tailwind、Zustand、Tiptap |
| 检索 | Qdrant（稠密 + BM25 混合）、OpenAI 兼容的 LLM / Embedding / Rerank（Cohere、DashScope 等） |
| 解析与语音 | pdfplumber、Office 解析、RapidOCR；可选 MinerU；本地 FunASR ONNX 或云端 ASR |
| 发布 | Docker Hub `jethrohou/sinkduce`（`linux/amd64` + `linux/arm64`）；macOS 桌面是同一套应用套在 Tauri 里 |

---

SinkDuce（包含保留在 `ee/` 下的实现）统一采用 **[AGPL-3.0-or-later](LICENSE)**。第三方声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

对外统一发布全功能社区版，包含自定义角色、领域与知识库访问控制、设备钥匙角色管理。`ee/` 目录和前端版本适配层继续保留，方便以后重新拆分。社区版沿用现有的应用名称、数据目录和更新渠道，并兼容已有的角色、ACL 和设备钥匙数据；升级时保留原有权限规则。内部企业版构建入口及其独立桌面数据目录仍然保留。

### 开发环境

默认源码构建为全功能社区版。开发时使用独立的数据目录和端口，避免影响日常部署：

```bash
SINKDUCE_EDITION=community SINKDUCE_DATA="$HOME/.sinkduce-dev/community" \
  API_PORT=18930 EXTERNAL_PORT=18931 \
  docker compose -p sinkduce-dev-ce -f docker-compose.build.yml up -d --build

SINKDUCE_EDITION=enterprise SINKDUCE_DATA="$HOME/.sinkduce-dev/enterprise" \
  API_PORT=18940 EXTERNAL_PORT=18941 \
  docker compose -p sinkduce-dev-ee -f docker-compose.build.yml up -d --build
```

社区版打开 `http://127.0.0.1:18930`，企业版打开 `http://127.0.0.1:18940`。前端热更新在 `frontend/` 运行，切换版别时重启 Vite：

```bash
SINKDUCE_EDITION=community API_PORT=18930 UI_PORT=5175 pnpm dev
SINKDUCE_EDITION=enterprise API_PORT=18940 UI_PORT=5176 pnpm dev
```

停止开发栈用 `docker compose -p sinkduce-dev-ce -f docker-compose.build.yml down`，或把项目名换成 `sinkduce-dev-ee`；绑定的数据目录会保留。内部企业版使用独立的应用名称和桌面数据目录，尚未配置独立发布渠道时不启用应用内更新。

## 参与开发

欢迎在公开仓提交 Issue 和 PR。代码、测试、截图与问题附件中均不要放入凭据、运行时数据、个人资料或真实客户／项目材料；请使用独立编造的例子复现问题。

本地依赖按锁文件安装：后端运行 `uv sync --frozen --extra dev`，前端在 `frontend/` 运行 `pnpm install --frozen-lockfile`。后端检查用 `uv run --frozen --extra dev pytest`，前端构建用 `pnpm build`。需要运行服务时，使用上方隔离的开发环境。

[English](README.md)
