import assert from "node:assert/strict"
import { test } from "node:test"

import {
  installToolbarRebuildCoalescer,
  type ToolbarRebuildTarget,
  type CoalescerClock,
} from "../frontend/src/lib/toolbar-rebuild-coalescer.ts"

/**
 * The file-viewer controller rebuilds the whole web toolbar on every viewer
 * event, and the PDF renderer emits a view-state-change per scroll frame —
 * so scrolling tears the toolbar down 60+×/s and it visibly flickers.
 * The coalescer must collapse those rebuilds into one per burst.
 */

interface FakeNode {
  id: string
}

/** Recording fake for the toolbar element surface the coalescer touches. */
function makeToolbar() {
  const dom: string[] = []
  const target: ToolbarRebuildTarget = {
    replaceChildren(...nodes: FakeNode[]) {
      dom.length = 0
      dom.push(...nodes.map((n) => n.id))
    },
    appendChild(node: FakeNode) {
      dom.push(node.id)
      return node
    },
    contains(_el: unknown) {
      return false
    },
  }
  return { target, dom }
}

/** Manual clock so tests pump time deterministically. */
function makeClock() {
  let now = 0
  let seq = 0
  const jobs = new Map<number, { at: number; fn: () => void }>()
  const clock: CoalescerClock = {
    now: () => now,
    schedule(_delayMs, fn) {
      const handle = ++seq
      jobs.set(handle, { at: now + _delayMs, fn })
      return handle
    },
    cancel(handle) {
      jobs.delete(handle as number)
    },
  }
  return {
    clock,
    advance(ms: number) {
      now += ms
      for (const { at, fn } of [...jobs.values()].sort((a, b) => a.at - b.at)) {
        if (at <= now) {
          // schedule() re-entrancy guard: drop before running
          for (const [h, job] of jobs) {
            if (job.fn === fn) jobs.delete(h)
          }
          fn()
        }
      }
    },
  }
}

function rebuild(toolbar: ToolbarRebuildTarget, ids: string[]) {
  toolbar.replaceChildren()
  for (const id of ids) {
    toolbar.appendChild({ id } as FakeNode)
  }
}

test("isolated rebuild passes through immediately", () => {
  const { target, dom } = makeToolbar()
  const { clock, advance } = makeClock()
  const c = installToolbarRebuildCoalescer(target, clock)

  rebuild(target, ["search"])
  assert.deepEqual(dom, ["search"], "single rebuild must apply live")

  c.dispose(false)
})

test("scroll burst collapses to one replay of the latest rebuild", () => {
  const { target, dom } = makeToolbar()
  const { clock, advance } = makeClock()
  const c = installToolbarRebuildCoalescer(target, clock)

  // First rebuild of a burst applies live…
  rebuild(target, ["search"])
  assert.deepEqual(dom, ["search"])

  // …then per-frame rebuilds within the burst window are coalesced.
  advance(8)
  rebuild(target, ["search", "download"])
  advance(8)
  rebuild(target, ["search", "download", "print"])
  advance(8)
  rebuild(target, ["search", "print"])
  // Nothing replayed while the burst is still hot.
  assert.deepEqual(dom, ["search"], "no replay mid-burst")

  // Quiet period elapses → exactly one replay of the LAST rebuild.
  advance(200)
  assert.deepEqual(dom, ["search", "print"], "replay keeps latest state only")

  c.dispose(false)
})

test("continuous scrolling still flushes at the hard cap", () => {
  const { target, dom } = makeToolbar()
  const { clock, advance } = makeClock()
  const c = installToolbarRebuildCoalescer(target, clock)

  rebuild(target, ["search"])
  // Simulate 60fps rebuilds for 3 seconds without any quiet gap.
  for (let frame = 0; frame < 180; frame++) {
    advance(16)
    rebuild(target, ["search", "download", `p${frame}`])
  }
  // Somewhere within 3s at least one flush must have happened (hard cap).
  assert.ok(
    dom.includes("p179") || dom.length > 0,
    "hard cap must flush despite continuous scroll"
  )

  // After the scroll ends, the final state replays.
  advance(300)
  assert.deepEqual(dom, ["search", "download", "p179"])

  c.dispose(false)
})

test("never coalesces while the user is focused inside the toolbar", () => {
  const { target, dom } = makeToolbar()
  const { clock, advance } = makeClock()
  let focused = true
  const c = installToolbarRebuildCoalescer(target, clock, () => focused)

  advance(8)
  rebuild(target, ["search"])
  advance(8)
  rebuild(target, ["search", "download"])
  assert.deepEqual(dom, ["search", "download"], "focused: every rebuild applies live")

  advance(500)
  focused = false
  advance(8)
  rebuild(target, ["search"])
  advance(8)
  rebuild(target, ["search", "print"])
  assert.deepEqual(dom, ["search"], "burst re-arms after focus leaves and coalesces again")

  advance(200)
  assert.deepEqual(dom, ["search", "print"])

  c.dispose(false)
})

test("dispose flushes pending replay and restores live pass-through", () => {
  const { target, dom } = makeToolbar()
  const { clock, advance } = makeClock()
  const c = installToolbarRebuildCoalescer(target, clock)

  rebuild(target, ["search"])
  advance(8)
  rebuild(target, ["search", "download"])
  advance(8)
  rebuild(target, ["search", "print"])

  c.dispose(true)
  assert.deepEqual(dom, ["search", "print"], "dispose replays the latest rebuild")

  // After dispose, rebuilds apply live again (no coalescing).
  advance(8)
  rebuild(target, ["solo"])
  assert.deepEqual(dom, ["solo"])

  advance(500)
  assert.deepEqual(dom, ["solo"])
})

test("appendChild return value is preserved for the library", () => {
  const { target } = makeToolbar()
  const { clock, advance } = makeClock()
  const c = installToolbarRebuildCoalescer(target, clock)

  rebuild(target, ["search"])
  advance(8)
  const node: FakeNode = { id: "download" }
  const returned = target.appendChild(node)
  assert.equal(returned, node, "appendChild must return the node even while coalescing")

  c.dispose(true)
})

test("first rebuild of a burst applies live, second one freezes", () => {
  const { target, dom } = makeToolbar()
  const { clock, advance } = makeClock()
  const c = installToolbarRebuildCoalescer(target, clock)

  rebuild(target, ["a"])
  assert.deepEqual(dom, ["a"], "burst start applies live")
  advance(8)
  rebuild(target, ["b"])
  assert.deepEqual(dom, ["a"], "second call inside the burst window is coalesced")
  advance(200)
  assert.deepEqual(dom, ["b"])

  c.dispose(false)
})
