/**
 * 录制结束后必须**解锁**（2026-09-17 用户报）。
 *
 * **用户原话**：「Meeting 被锁住之后，创建者切换到其他会议，要想再回去也被锁在外面，
 * 所有人都锁在外面了。」
 *
 * **两个原因叠在一起**：
 *
 * 1. **服务端不释放**：`recording_is_live()` 按 PCM 文件 mtime 判"此刻是否真在录"，
 *    而 `finalize` 之后 PCM 文件一直留着 → 录制停了仍被算作"正在录"90 秒；
 *    且 `finalize` 只在 `recovery=true` 时把 status 放回 `created`，
 *    **正常停止那条路把 status 留在 "recording"**。
 * 2. **本机列表不刷新（`meetings` 不在 echo 豁免名单里，发起方会跳过自己的事件）**：
 *    停止录制只更新了详情，列表里那行仍是 `recording_live: true` → 那行被
 *    `lockedByPeerRecording` 锁住，**连刚停下来的创建者自己都进不去**。
 *
 * 服务端两处修在 `finalize_recording_pcm` / `finalize-recording` 路由；
 * 前端修在 `handleStopRecording` 里补 `fetchMeetings()`。
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"

const VIEW = readFileSync(
  new URL("../src/components/meeting/meeting-view.tsx", import.meta.url),
  "utf8",
)
const LIST = readFileSync(
  new URL("../src/components/meeting/meeting-list.tsx", import.meta.url),
  "utf8",
)

/**
 * 取出 `handleStopRecording` 的函数体。
 *
 * **必须按括号配对切**：先前用"从声明到下一个 `}, [`"的粗切法，结果一刀切到 7000+
 * 字符、把后面好几个 handler 也框进来了（`fetchMeetings()` 在里面出现 5 次），
 * 于是"删掉那一处调用"的变异根本影响不到断言 —— 变异校验抓出了这个假测试。
 */
function stopBody(): string {
  const start = VIEW.indexOf("const handleStopRecording = useCallback(")
  assert.ok(start > -1, "找不到 handleStopRecording")
  const open = VIEW.indexOf("(", start)
  // 从 useCallback( 的左括号开始配对，找到它闭合的位置
  let depth = 0
  let end = -1
  for (let k = open; k < VIEW.length; k++) {
    const ch = VIEW[k]
    if (ch === "(") depth++
    else if (ch === ")") {
      depth--
      if (depth === 0) {
        end = k
        break
      }
    }
  }
  assert.ok(end > open, "无法定位 useCallback 的闭合括号")
  // 再把依赖数组 ] 也吃进来（它在紧接的 }, [...] 里）
  const tail = VIEW.slice(end, end + 400)
  const depEnd = tail.indexOf("])")
  return VIEW.slice(start, depEnd > -1 ? end + depEnd + 2 : end + 1)
}

test("停止录制后刷新列表（否则那行永远停在『录制中不可进入』）", () => {
  const body = stopBody()
  assert.match(
    body,
    /fetchMeetings\(\)/,
    "handleStopRecording 必须在 finalize 之后调 fetchMeetings()：列表里的 " +
      "recording_live 决定锁，服务端事件又被本机 echo 抑制跳过",
  )
})

test("刷新列表发生在 finalize 成功之后（不能提前）", () => {
  const body = stopBody()
  const fin = body.indexOf("finalizeMeetingRecording")
  const fetch_ = body.indexOf("fetchMeetings()")
  assert.ok(fin > -1 && fetch_ > -1, "两个调用都要在")
  assert.ok(fetch_ > fin, "要先 finalize 再刷新，否则拿到的是旧状态")
})

test("fetchMeetings 进了 useCallback 依赖数组", () => {
  const i = VIEW.indexOf("const handleStopRecording = useCallback(")
  const deps = VIEW.slice(i, i + 2000)
  assert.match(deps, /^\s*fetchMeetings,\s*$/m, "deps 里要有 fetchMeetings")
})

test("列表的锁判据用 recording_live 且排除自己那一行", () => {
  assert.match(LIST, /const lockedByPeerRecording =/, "锁判据应当存在")
  assert.match(
    LIST,
    /recording_live && recordingMeetingId !== m\.id/,
    "自己正在录的那行不该被锁",
  )
})

test("点击/键盘/拖拽三条入口都被锁拦住", () => {
  // 点数与可访问性：锁住时不能进
  const locks = LIST.match(/if \(lockedByPeerRecording\) return/g) ?? []
  assert.ok(locks.length >= 2, `点击与键盘两条路都要拦，实得 ${locks.length}`)
  assert.match(LIST, /draggable=\{!lockedByPeerRecording\}/, "锁住时也不该可拖拽")
})
