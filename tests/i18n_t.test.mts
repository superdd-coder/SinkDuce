import assert from "node:assert/strict"
import { test } from "node:test"

import { normalizeLocale, t, translate } from "../frontend/src/i18n/index.ts"
import {
  systemFolderDisplayName,
  systemFolderDisplayPath,
} from "../frontend/src/i18n/system-folder.ts"

test("normalizeLocale defaults unknown and empty values to en", () => {
  assert.equal(normalizeLocale("en"), "en")
  assert.equal(normalizeLocale("zh-CN"), "zh-CN")
  assert.equal(normalizeLocale("fr"), "en")
  assert.equal(normalizeLocale(""), "en")
  assert.equal(normalizeLocale(undefined), "en")
  assert.equal(normalizeLocale(null), "en")
  assert.equal(normalizeLocale(1), "en")
})

test("t looks up nested keys in English", () => {
  assert.equal(t("en", "nav.chat"), "Chat")
  assert.equal(t("en", "settings.interfaceLanguage"), "Interface language")
})

test("t looks up nested keys in zh-CN", () => {
  assert.equal(t("zh-CN", "nav.chat"), "聊天")
  assert.equal(t("zh-CN", "settings.interfaceLanguage"), "界面语言")
})

test("zh-CN uses 项目 instead of 资料库", () => {
  assert.equal(t("zh-CN", "nav.library"), "项目")
  assert.equal(t("zh-CN", "library.createCollection"), "新建项目")
  assert.equal(t("zh-CN", "library.collection"), "项目")
  assert.equal(t("en", "nav.library"), "Library")
})

test("system folder display names localize without changing identity", () => {
  const zh = (key: string) => t("zh-CN", key)
  const en = (key: string) => t("en", key)
  assert.equal(systemFolderDisplayName("Meeting", en), "Meeting")
  assert.equal(systemFolderDisplayName("Meeting", zh), "会议")
  assert.equal(systemFolderDisplayName("Notes", zh), "笔记")
  assert.equal(systemFolderDisplayName("Note", zh), "笔记")
  assert.equal(systemFolderDisplayName("Archived", zh), "归档")
  assert.equal(systemFolderDisplayName("Archive", zh), "归档")
  assert.equal(systemFolderDisplayName("archive", zh), "归档")
  assert.equal(systemFolderDisplayName("My Folder", zh), "My Folder")
  assert.equal(systemFolderDisplayPath("/Meeting/sub", zh), "/会议/sub")
  assert.equal(systemFolderDisplayPath("/Archived", zh), "/归档")
})

test("file detail side rail chrome is localized", () => {
  assert.equal(t("en", "fileMgmt.paths"), "Paths")
  assert.equal(t("zh-CN", "fileMgmt.paths"), "路径")
  assert.equal(t("en", "fileMgmt.log"), "Log")
  assert.equal(t("zh-CN", "fileMgmt.log"), "日志")
  assert.equal(t("en", "fileMgmt.versionUpdateLower"), "version update")
  assert.equal(t("zh-CN", "fileMgmt.versionUpdateLower"), "版本更新")
})

test("timeline message-stream chrome is localized", () => {
  assert.equal(t("en", "fileMgmt.groups"), "Groups")
  assert.equal(t("zh-CN", "fileMgmt.groups"), "分组")
  assert.equal(t("en", "fileMgmt.mainChain"), "Main chain")
  assert.equal(t("zh-CN", "fileMgmt.mainChain"), "主链")
  assert.equal(t("en", "fileMgmt.focusLabel", { name: "Main chain" }), "Focus: Main chain")
  assert.equal(t("zh-CN", "fileMgmt.focusLabel", { name: "主链" }), "焦点：主链")
  assert.equal(
    t("en", "fileMgmt.noMessagesInScope"),
    "No messages in this scope. Click + to add at focus layer.",
  )
  assert.equal(
    t("zh-CN", "fileMgmt.noMessagesInScope"),
    "此范围内没有消息。点 + 在焦点层添加。",
  )
  assert.equal(t("en", "fileMgmt.branches"), "Branches")
  assert.equal(t("zh-CN", "fileMgmt.branches"), "分支")
})

test("t interpolates {var} placeholders", () => {
  assert.equal(
    t("en", "errors.name_conflict", { name: "a.pdf", suggested_name: "a 2.pdf" }),
    "“a.pdf” already exists. Suggested: a 2.pdf",
  )
  assert.equal(
    t("zh-CN", "errors.name_conflict", { name: "a.pdf", suggested_name: "a 2.pdf" }),
    "“a.pdf” 已存在。建议使用：a 2.pdf",
  )
})

test("t leaves unknown placeholders intact", () => {
  assert.match(t("en", "errors.name_conflict", { name: "x" }), /\{suggested_name\}/)
})

test("t falls back to the key when English is also missing", () => {
  assert.equal(t("en", "does.not.exist"), "does.not.exist")
})

test("translate falls back to English then the key", () => {
  const catalogs = {
    en: { greet: "Hello {name}", onlyEn: "English only" },
    "zh-CN": { greet: "你好 {name}" },
  }
  assert.equal(translate(catalogs, "zh-CN", "greet", { name: "Ada" }), "你好 Ada")
  assert.equal(translate(catalogs, "zh-CN", "onlyEn"), "English only")
  assert.equal(translate(catalogs, "zh-CN", "missing"), "missing")
})

test("t treats an unknown locale as English", () => {
  assert.equal(t("fr", "nav.chat"), "Chat")
})

test("dialog chrome keys exist in both catalogs", () => {
  assert.equal(
    t("en", "fileMgmt.rollbackBody"),
    "Make this the live revision. Later revisions are permanently deleted. This cannot be undone.",
  )
  assert.equal(
    t("zh-CN", "fileMgmt.rollbackBody"),
    "将此版本设为当前。之后的版本将被永久删除。此操作无法撤销。",
  )
  assert.equal(
    t("en", "fileMgmt.deleteVersionLead"),
    "Permanently remove",
  )
  assert.equal(t("zh-CN", "fileMgmt.deleteVersionLead"), "永久删除")
  assert.equal(t("en", "settings.llmKicker"), "LLM")
  assert.equal(t("zh-CN", "settings.llmKicker"), "大模型")
  assert.equal(t("en", "fileMgmt.queuing"), "Queuing…")
  assert.equal(t("zh-CN", "fileMgmt.queuing"), "排队中…")
  assert.equal(t("en", "common.close"), "Close")
  assert.equal(t("zh-CN", "common.close"), "关闭")
})
