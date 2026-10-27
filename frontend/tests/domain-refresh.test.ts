/**
 * Cross-view refresh signal for domains whose data lives in component-local state
 * rather than a store (meetings, notes, hot words, people).
 *
 * A monotonic counter per domain is enough: the app-wide event stream bumps it,
 * and a view's fetch effect depends on it — the same idiom the timeline already
 * uses with `timelineRefreshEpoch`. This keeps the event layer from reaching into
 * view internals.
 *
 * 跑法：node --test frontend/tests/
 */
import assert from "node:assert/strict"
import { test } from "node:test"

import {
  bumpDomain,
  domainEpoch,
  subscribeDomain,
} from "../src/lib/domain-refresh.ts"

test("epoch starts at zero for an unknown domain", () => {
  assert.equal(domainEpoch("meetings-never-touched"), 0)
})

test("bumping advances the epoch", () => {
  const before = domainEpoch("meetings")
  bumpDomain("meetings")
  assert.equal(domainEpoch("meetings"), before + 1)
})

test("domains are independent", () => {
  const meetings = domainEpoch("meetings")
  const notes = domainEpoch("notes")
  bumpDomain("notes")
  assert.equal(domainEpoch("meetings"), meetings)
  assert.equal(domainEpoch("notes"), notes + 1)
})

test("subscribers are notified on bump", () => {
  let calls = 0
  const off = subscribeDomain("hot_words", () => {
    calls += 1
  })
  bumpDomain("hot_words")
  assert.equal(calls, 1)
  off()
})

test("unsubscribing stops notification", () => {
  let calls = 0
  const off = subscribeDomain("people", () => {
    calls += 1
  })
  off()
  bumpDomain("people")
  assert.equal(calls, 0)
})

test("only subscribers of the bumped domain fire", () => {
  let meetings = 0
  let notes = 0
  const offM = subscribeDomain("meetings", () => {
    meetings += 1
  })
  const offN = subscribeDomain("notes", () => {
    notes += 1
  })
  bumpDomain("meetings")
  assert.deepEqual([meetings, notes], [1, 0])
  offM()
  offN()
})

test("multiple subscribers for one domain all fire", () => {
  let a = 0
  let b = 0
  const offA = subscribeDomain("meetings", () => {
    a += 1
  })
  const offB = subscribeDomain("meetings", () => {
    b += 1
  })
  bumpDomain("meetings")
  assert.deepEqual([a, b], [1, 1])
  offA()
  offB()
})

test("a subscriber that unsubscribes during notification does not break the pass", () => {
  let first = 0
  let second = 0
  const offFirstHolder: { off?: () => void } = {}
  const offFirst = subscribeDomain("notes", () => {
    first += 1
    offFirstHolder.off?.()
  })
  offFirstHolder.off = offFirst
  const offSecond = subscribeDomain("notes", () => {
    second += 1
  })
  bumpDomain("notes")
  assert.deepEqual([first, second], [1, 1])
  offSecond()
})
