/**
 * 热词库选择的权限一致性（2026-09-17 用户报）。
 *
 * **用户报的现象**：「我是 member 为什么选择热词的时候它提示『更新热词失败：没有权限
 * 执行这个操作』但是又选上了？」
 *
 * **两个毛病叠在一起**：
 *
 * 1. **写了注定 403 的请求**：选择热词库 = 写会议（`update_meeting`），需要 meeting 的
 *    `operate` 档；member 默认不在该档。前端却照样发请求 → 后端 403 →
 *    用户看到"没有权限"。
 * 2. **乐观更新没有回滚**：`persistOrDraft` 先 `setDraftIds(next)` 把选项点亮，
 *    然后 `onSelectLibraries(next)` 失败只弹了 toast、**没有撤销本地状态**
 *    → 界面停在"已选上"而服务端根本没变。**这就是"报错了却选上了"。**
 *
 * 修法两面：
 * - `meeting-view` 先查 `canOperateMeeting(meeting)`，没权限就**不发请求**并抛错；
 * - `hot-words-selector` 拿到异常后**回滚**乐观更新（`setDraftIds(prevDraft)`）。
 *
 * 另：`disabled` 时支持 `disabledTitle`，让禁用态说明"是权限问题"而不是
 * 让人误以为是"转写中不可用"。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8")

const SELECTOR = read("../src/components/meeting/hot-words-selector.tsx")
const VIEW = read("../src/components/meeting/meeting-view.tsx")
const MEDIA_BAR = read("../src/components/meeting/media-bar.tsx")
const CAPTURE = read("../src/components/meeting/meeting-capture-stages.tsx")
const STUDIO = read("../src/components/meeting/meeting-studio-stage.tsx")
const ZH = JSON.parse(read("../src/i18n/zh-CN.json"))
const EN = JSON.parse(read("../src/i18n/en.json"))

// ── ① 回滚：修掉"报错了却选上了" ──────────────────────────────

test("乐观更新失败后必须回滚 draftIds（否则界面停在假选中）", () => {
  assert.match(
    SELECTOR,
    /const prevDraft = draftIds/,
    "要先记住旧值才能回滚",
  )
  assert.match(
    SELECTOR,
    /catch\s*\{[\s\S]{0,400}?setDraftIds\(prevDraft\)/,
    "写入失败时必须 setDraftIds(prevDraft) 回滚",
  )
})

test("写入是 await 的（不等就无从知道成败）", () => {
  assert.match(SELECTOR, /await onSelectLibraries\(next\)/)
})

test("父级写入函数会把失败抛出去（否则 selector 收不到）", () => {
  assert.match(VIEW, /catch \(err\) \{[\s\S]{0,300}?throw err/)
})

test("onSelectLibraries 的类型允许返回 Promise", () => {
  assert.match(SELECTOR, /onSelectLibraries: \(libraryIds: string\[\]\) => void \| Promise<unknown>/)
})

// ── ② 不发注定 403 的请求 ────────────────────────────────────

test("meeting-view 先查权限，没权限就不发请求", () => {
  assert.match(VIEW, /canOperateMeeting\(meeting\)/)
  assert.match(VIEW, /hotWordsNoPermission/)
  assert.match(VIEW, /throw new Error\("hot-words selection denied/)
})

test("权限判断在发请求之前（顺序不能反）", () => {
  const fn = VIEW.slice(VIEW.indexOf("const handleSelectHotWordsLibraries"))
  const permAt = fn.indexOf("canOperateMeeting(meeting)")
  const reqAt = fn.indexOf("await updateMeeting(")
  assert.ok(permAt > -1 && reqAt > -1, "两个都要在函数里")
  assert.ok(permAt < reqAt, "权限判断必须在 updateMeeting 之前")
})

// ── ③ 禁用态要说清是权限问题 ─────────────────────────────────

test("selector 支持 disabledTitle（覆盖默认的『转写中不可用』）", () => {
  assert.match(SELECTOR, /disabledTitle\?: string/)
  assert.match(SELECTOR, /disabledTitle \|\| t\("meeting\.unavailableWhileTx"\)/)
})

test("三处入口都把 canEditMeeting 传下去", () => {
  assert.match(MEDIA_BAR, /canEditMeeting\?: boolean/)
  assert.match(CAPTURE, /disabled=\{!canEditMeeting\}/)
  assert.match(MEDIA_BAR, /disabled=\{!canEditMeeting\}/)
  assert.match(STUDIO, /canEditMeeting=\{canEditMeeting\}/)
})

test("meeting-view 把 canEditMeeting 算出来并传下去", () => {
  assert.match(VIEW, /canEditMeeting=\{canOperateMeeting\(meeting\)\}/)
})

// ── ④ 文案两边都有 ───────────────────────────────────────────

test("hotWordsNoPermission 中英都有", () => {
  assert.ok(ZH.meeting.hotWordsNoPermission, "缺中文文案")
  assert.ok(EN.meeting.hotWordsNoPermission, "缺英文文案")
})
