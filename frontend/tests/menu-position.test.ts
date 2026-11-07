import assert from "node:assert/strict"
import { test } from "node:test"
import { placeConnectedMenu, placeFloatingMenu } from "../src/lib/menu-position.ts"

const anchor = (left: number, top: number, width = 120, height = 36) => ({ left, top, right: left + width, bottom: top + height, width })
const viewport = { viewportWidth: 1024, viewportHeight: 768 }

test("connected menus keep the parent at the trigger and open inward at both screen edges", () => {
  const common = { ...viewport, width: 508, height: 280, rootWidth: 234 }
  const left = placeFloatingMenu({ ...common, anchor: anchor(20, 100) })
  assert.equal(left.side, "right")
  assert.equal(left.left, 20)
  const right = placeFloatingMenu({ ...common, anchor: anchor(884, 100), align: "end" })
  assert.equal(right.side, "left")
  assert.equal(right.left + common.width, 1004)
  assert.equal(right.top, 144)
})

test("menus above a bottom toolbar keep an eight pixel gap and remain fully visible", () => {
  const result = placeFloatingMenu({ ...viewport, anchor: anchor(400, 700), width: 330, height: 360 })
  assert.equal(result.flip, "up")
  assert.equal(result.top + 360, 692)
  assert.ok(result.maxHeight >= 360)
})

test("a short menu stays below even when there is more unused room above", () => {
  const result = placeFloatingMenu({ ...viewport, anchor: anchor(400, 480), width: 220, height: 100 })
  assert.equal(result.flip, "down")
  assert.equal(result.top, 524)
  assert.equal(result.maxHeight, 232)
})

test("long menus have bounded scroll space instead of covering the trigger", () => {
  const result = placeFloatingMenu({ ...viewport, anchor: anchor(900, 360), width: 330, height: 1200 })
  assert.equal(result.flip, "down")
  assert.equal(result.top, 404)
  assert.equal(result.maxHeight, 352)
  assert.equal(result.left + 330, 1012)
})

test("connected menus clamp in narrow, short viewports for every anchor corner", () => {
  for (const [viewportWidth, viewportHeight] of [[320, 240], [390, 600], [768, 480], [1440, 900]]) {
    for (const left of [12, viewportWidth / 2, viewportWidth - 92]) {
      for (const top of [12, viewportHeight / 2, viewportHeight - 48]) {
        const result = placeFloatingMenu({ anchor: anchor(left, top, 80), width: 538, height: 800, rootWidth: 234, viewportWidth, viewportHeight })
        assert.ok(result.left >= 12)
        assert.ok(result.left + Math.min(538, result.maxWidth) <= viewportWidth - 12)
        assert.ok(result.top >= 12)
        assert.ok(result.top + result.maxHeight <= viewportHeight - 12)
        assert.ok(result.maxHeight > 0)
      }
    }
  }
})

test("sidebar flyouts flip horizontally and clamp vertically", () => {
  const result = placeFloatingMenu({ ...viewport, anchor: anchor(920, 700, 80), placement: "right", width: 300, height: 200 })
  assert.equal(result.side, "left")
  assert.equal(result.left, 612)
  assert.equal(result.top, 556)
  assert.equal(result.maxHeight, 200)
})

test("center and end alignment remain attached to ordinary dropdown triggers", () => {
  const common = { ...viewport, anchor: anchor(400, 100, 100), width: 240, height: 150 }
  assert.equal(placeFloatingMenu({ ...common, align: "center" }).left, 330)
  assert.equal(placeFloatingMenu({ ...common, align: "end" }).left, 260)
})

const rootRect = (menu: ReturnType<typeof placeConnectedMenu>) => ({
  left: menu.left + menu.root.left, top: menu.top + menu.root.top,
  width: menu.root.width, height: menu.root.height,
})

test("switching between leaf rows and short or tall submenus never moves or resizes the root", () => {
  for (const align of ["start", "center", "end"] as const) {
    for (const left of [20, 450, 884]) {
      for (const top of [20, 360, 700]) {
        const options = { ...viewport, anchor: anchor(left, top), align, rootWidth: 220, rootHeight: 180, childWidth: 290 }
        const closed = placeConnectedMenu({ ...options, childHeight: null })
        for (const childHeight of [100, 320, 1200, null, 100]) {
          const current = placeConnectedMenu({ ...options, childHeight })
          assert.deepEqual(rootRect(current), rootRect(closed))
          assert.equal(current.side, closed.side)
          assert.equal(current.flip, closed.flip)
        }
      }
    }
  }
})

test("connected children stay inside the viewport and scroll independently of the root", () => {
  for (const [viewportWidth, viewportHeight] of [[320, 240], [390, 600], [768, 480], [1440, 900]]) {
    for (const left of [12, viewportWidth / 2, viewportWidth - 92]) {
      for (const top of [12, viewportHeight / 2, viewportHeight - 48]) {
        const options = { anchor: anchor(left, top, 80), rootWidth: 240, rootHeight: 320, childWidth: 260, viewportWidth, viewportHeight }
        const closed = placeConnectedMenu({ ...options, childHeight: null })
        const open = placeConnectedMenu({ ...options, childHeight: 1200 })
        assert.deepEqual(rootRect(open), rootRect(closed))
        assert.ok(open.left >= 12 && open.top >= 12)
        assert.ok(open.left + open.width <= viewportWidth - 12 + 0.001)
        assert.ok(open.top + open.height <= viewportHeight - 12 + 0.001)
        assert.ok(open.child && open.child.height < 1200)
        assert.ok(open.child.left + open.child.width <= open.width)
        assert.ok(open.child.top + open.child.height <= open.height)
      }
    }
  }
})

test("a child near the screen edge expands inward without shifting its first-level options", () => {
  for (const [left, side] of [[20, "right"], [884, "left"]] as const) {
    const options = { ...viewport, anchor: anchor(left, 700), rootWidth: 220, rootHeight: 180, childWidth: 290, align: "end" as const }
    const closed = placeConnectedMenu({ ...options, childHeight: null })
    const open = placeConnectedMenu({ ...options, childHeight: 320 })
    assert.equal(open.side, side)
    assert.deepEqual(rootRect(open), rootRect(closed))
    assert.equal(open.top + open.height, 692)
    assert.ok(open.child)
    assert.ok(side === "left" ? open.child.left < open.root.left : open.child.left > open.root.left)
  }
})
