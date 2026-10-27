# Changelog

Kept newest-first. CI extracts the section matching the release tag into
`latest.json → notes` (shown in the desktop update dialog), so headings must
stay in the exact `## vX.Y.Z` form.

## v1.6.1

- 改善报告修订阅读：表格内直接显示文字增删，短文本改动更紧凑，修订卡片定位和选中状态更清晰
- 同一条修订的继续讨论合并展示；全文替换按段落或表格行分组，可逐处审阅或接受同一指令下的全部修改
- 整篇重写或切换报告语言时生成独立文件，保留原报告；批注答复跟随用户批注的语言
- 会议纪要更精炼，支持表格和 SVG 图示，并统一提供 Word、PDF、Markdown 下载与译文导出
- macOS 桌面版内置 PDF 转换组件和中文字体，无需另行安装办公软件
- 修复快捷聊天创建笔记后缺少结果卡片的问题
- Improve report redlines inside tables, compact local text changes, and clarify revision selection and navigation
- Keep follow-up discussions together and review broad replacements by paragraph or table row, with actions scoped to one instruction
- Preserve the source report when creating a full rewrite or translation, and reply in the language of the user's comment
- Refine meeting minutes with concise structure, tables and SVG diagrams, plus Word, PDF and Markdown downloads for summaries and translations
- Bundle PDF conversion and CJK fonts in the macOS app, removing the separate Office installation requirement
- Restore Note result cards in Quick Chat replies

## v1.6.0

- 新增聊天附件：主聊天、知识库快捷聊天和会议聊天均可上传文件，预览内容并用于问答
- 新增带引用的报告工作台：支持报告生成、批注讨论、逐项接受或拒绝修订、历史恢复与导出
- 增强 Collection Notes 编辑：支持原位修改、笔记链接与 SVG 图表预览
- 新增局域网设备配对、远程连接模式、多设备状态同步及跨知识盒 MCP 检索
- 新增知识库归档，改善检索证据保留、来源预览、文档解析与聊天恢复
- 统一发布全功能社区版，包含自定义角色、权限管理和访问控制；保留 `ee/` 模块结构，统一采用 AGPL 许可，并兼容已有权限数据
- 改善会议实时转写与 macOS 录音稳定性，支持录音权限拒绝后的恢复，并防止删除正在录制的会议
- Add conversation attachments across Chat, Collection Quick Chat, and meeting chats, with file previews and document-backed answers
- Add a cited report workspace with report generation, anchored discussions, per-change review decisions, history restoration, and exports
- Improve Collection Notes with in-place edits, note links, and SVG diagram previews
- Add LAN device pairing, remote connection mode, multi-device state sync, and cross-box MCP search
- Add collection archiving and improve retrieval evidence preservation, source previews, document parsing, and restored chat sessions
- Publish a full-feature Community distribution with custom roles, permission management, and access controls; retain the `ee/` module boundary under AGPL and preserve existing permission data
- Improve live transcription and macOS recording resilience, recover after recording permission denial, and protect actively recording meetings from deletion

## v1.5.4

- 修复 macOS 26.6.2 WebKit 下「会前准备」把手渲染异常（文字横排溢出窗口边缘）
- 新增 Windows 一键部署引导：`install.ps1` 自动装 WSL2 + Docker Desktop 并拉起 compose
- Fix meeting prepare handle rendering broken on macOS 26.6.2 WebKit (text laid out horizontally, clipped past the window edge)
- New Windows one-line bootstrap: `install.ps1` sets up WSL2 + Docker Desktop and starts compose automatically

## v1.5.3

- 修复「立即更新」被 Tauri ACL 拦截导致应用内更新失败的问题（需手动更新到本版本一次，之后的版本即可全自动应用内更新）
- Fix in-app "Update now" being blocked by Tauri's ACL ("Command not allowed") — update manually to this version once; all later versions then auto-update in-app

## v1.5.2

- 更新弹窗显示每个版本的更新内容
- 更新状态文案优化：已是最新版本不再误报为失败，失败时显示具体原因
- Docker 构建提速：依赖层与版本号解耦，发版构建 ~12 分钟 → ~5 分钟
- Update dialog now shows per-version release notes
- Clearer update states: "already latest" no longer reported as a failure; failures show the underlying reason
- Faster Docker builds: dependency layers decoupled from version bumps, ~12 min → ~5 min

## v1.5.1

- 新增应用内一键更新：下载 → 验签 → 换包 → 自动重启，全程无 Gatekeeper 弹窗
- 新增一行命令安装器：`curl -fsSL <R2>/install.sh | sh`，首次安装零弹窗
- macOS 26 液态玻璃应用图标
- In-app one-click update: download → signature check → bundle swap → auto restart, zero Gatekeeper prompts
- One-line installer: `curl -fsSL <R2>/install.sh | sh` for a quarantine-free first install
- macOS 26 Liquid Glass app icon

## v1.5.0

- 桌面版分发与更新检查迁移至 Cloudflare R2
- 更新弹窗改版：版本对比与手动下载入口
- Desktop distribution and update check moved to Cloudflare R2
- Reworked update dialog with version compare and manual download entry
