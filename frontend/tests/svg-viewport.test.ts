import assert from "node:assert/strict"
import { test } from "node:test"

async function helpers() {
  const viewport = await import("../src/lib/svg-viewport.ts").catch((error: { code?: string }) => {
    if (error.code === "ERR_MODULE_NOT_FOUND") return null
    throw error
  })
  assert.ok(viewport, "SVG viewport geometry helpers must be implemented")
  return viewport
}

test("inline preview proportions stay between a square-ish card and a landscape card", async () => {
  const { inlineSvgAspectRatio } = await helpers()
  assert.equal(typeof inlineSvgAspectRatio, "function", "Inline previews need bounded proportions instead of fixed height")
  assert.equal(inlineSvgAspectRatio({ width: 600, height: 2400 }), 4 / 3)
  assert.equal(inlineSvgAspectRatio({ width: 2400, height: 600 }), 16 / 9)
  assert.equal(inlineSvgAspectRatio({ width: 800, height: 500 }), 1.6)
  assert.equal(inlineSvgAspectRatio({ width: 0, height: 0 }), 1.6)
})

test("fit reduces either oversized axis while preserving the image aspect ratio", async () => {
  const { fitSvgSize } = await helpers()
  assert.deepEqual(fitSvgSize({ width: 1200, height: 600 }, { width: 800, height: 500 }), { width: 800, height: 400 })
  assert.deepEqual(fitSvgSize({ width: 400, height: 1000 }, { width: 800, height: 500 }), { width: 200, height: 500 })
})

test("fit enlarges small vector images to use the available viewport", async () => {
  const { fitSvgSize, createSvgViewport } = await helpers()
  const image = { width: 120, height: 80 }
  const container = { width: 800, height: 500 }
  assert.deepEqual(fitSvgSize(image, container), { width: 750, height: 500 })
  assert.deepEqual(createSvgViewport(image, container), { scale: 1, x: 25, y: 0 })
})

test("the initial fit centers both landscape and portrait images", async () => {
  const { createSvgViewport } = await helpers()
  assert.deepEqual(createSvgViewport({ width: 1200, height: 600 }, { width: 800, height: 500 }), { scale: 1, x: 0, y: 50 })
  assert.deepEqual(createSvgViewport({ width: 400, height: 1000 }, { width: 800, height: 500 }), { scale: 1, x: 300, y: 0 })
})

test("zoom keeps the same image point beneath a viewport-coordinate anchor", async () => {
  const { zoomSvgAt } = await helpers()
  const view = { scale: 1.5, x: -30, y: 45 }
  const anchor = { x: 140, y: 110 }
  const next = zoomSvgAt(view, 3, anchor)
  assert.deepEqual(next, { scale: 3, x: -200, y: -20 })
  assert.equal((anchor.x - next.x) / next.scale, (anchor.x - view.x) / view.scale)
  assert.equal((anchor.y - next.y) / next.scale, (anchor.y - view.y) / view.scale)
  assert.deepEqual(view, { scale: 1.5, x: -30, y: 45 }, "zoom does not mutate the previous state")
})

test("zooming back around the same anchor restores the previous view", async () => {
  const { zoomSvgAt } = await helpers()
  const view = { scale: 1, x: 20, y: 40 }
  const anchor = { x: 200, y: 160 }
  assert.deepEqual(zoomSvgAt(zoomSvgAt(view, 4, anchor), 1, anchor), view)
})

test("zoom clamps to 25–800 percent before adjusting the anchor", async () => {
  const { zoomSvgAt } = await helpers()
  const view = { scale: 1, x: 20, y: 40 }
  const anchor = { x: 100, y: 200 }
  assert.deepEqual(zoomSvgAt(view, -10, anchor), { scale: 0.25, x: 80, y: 160 })
  assert.deepEqual(zoomSvgAt(view, 100, anchor), { scale: 8, x: -540, y: -1080 })
})

test("zoom at an existing limit does not drift the image", async () => {
  const { zoomSvgAt } = await helpers()
  for (const scale of [0.25, 8]) {
    const view = { scale, x: -120, y: 35 }
    assert.deepEqual(zoomSvgAt(view, scale === 8 ? 100 : 0, { x: 450, y: 280 }), view)
  }
})

test("unmeasured or invalid image and container dimensions produce a finite empty fit", async () => {
  const { fitSvgSize, createSvgViewport } = await helpers()
  for (const value of [0, -1, NaN, Infinity, -Infinity]) {
    for (const axis of ["width", "height"] as const) {
      for (const invalidImage of [true, false]) {
        const image = { width: 200, height: 100 }
        const container = { width: 800, height: 500 }
        ;(invalidImage ? image : container)[axis] = value
        assert.deepEqual(fitSvgSize(image, container), { width: 0, height: 0 })
        const view = createSvgViewport(image, container)
        assert.equal(view.scale, 1)
        assert.ok(Number.isFinite(view.x) && Number.isFinite(view.y))
      }
    }
  }
})

test("invalid zoom requests retain a usable current view", async () => {
  const { zoomSvgAt } = await helpers()
  const view = { scale: 2, x: 15, y: -10 }
  for (const scale of [NaN, Infinity, -Infinity]) {
    assert.deepEqual(zoomSvgAt(view, scale, { x: 100, y: 50 }), view)
  }
})

test("invalid prior state and anchor coordinates cannot contaminate the next view", async () => {
  const { zoomSvgAt } = await helpers()
  assert.deepEqual(zoomSvgAt({ scale: NaN, x: Infinity, y: NaN }, 2, { x: NaN, y: Infinity }), { scale: 2, x: 0, y: 0 })
  const next = zoomSvgAt({ scale: 1, x: -Number.MAX_VALUE, y: Number.MAX_VALUE }, 8, { x: Number.MAX_VALUE, y: -Number.MAX_VALUE })
  assert.ok(Object.values(next).every(Number.isFinite))
})

async function constrainer() {
  const { constrainSvgViewport } = await helpers()
  assert.equal(typeof constrainSvgViewport, "function", "SVG viewport bounds must be implemented")
  return constrainSvgViewport
}

test("bounds center every axis where the scaled image fits", async () => {
  const constrain = await constrainer()
  assert.deepEqual(constrain({ scale: 1, x: -2000, y: 2000 }, { width: 400, height: 100 }, { width: 800, height: 500 }), { scale: 1, x: 200, y: 200 })
  assert.deepEqual(constrain({ scale: 2, x: -20, y: 40 }, { width: 400, height: 250 }, { width: 800, height: 500 }), { scale: 2, x: 0, y: 0 })
})

test("bounds stop oversized images at either edge while retaining valid pans", async () => {
  const constrain = await constrainer()
  const fitted = { width: 800, height: 400 }
  const container = { width: 800, height: 500 }
  assert.deepEqual(constrain({ scale: 2, x: 1000, y: -2000 }, fitted, container), { scale: 2, x: 0, y: -300 })
  assert.deepEqual(constrain({ scale: 2, x: -2000, y: 1000 }, fitted, container), { scale: 2, x: -800, y: 0 })
  const view = { scale: 2, x: -250, y: -50 }
  assert.deepEqual(constrain(view, fitted, container), view)
  assert.deepEqual(view, { scale: 2, x: -250, y: -50 }, "bounds do not mutate the caller's state")
})

test("one axis can pan while the other remains centered", async () => {
  const constrain = await constrainer()
  assert.deepEqual(constrain({ scale: 2, x: -3000, y: -3000 }, { width: 800, height: 100 }, { width: 800, height: 500 }), { scale: 2, x: -800, y: 150 })
})

test("zooming out from an edge recenters the image once it fits again", async () => {
  const constrain = await constrainer()
  const { zoomSvgAt } = await helpers()
  const zoomedOut = zoomSvgAt({ scale: 4, x: -2000, y: -800 }, 0.5, { x: 100, y: 100 })
  assert.deepEqual(constrain(zoomedOut, { width: 800, height: 400 }, { width: 800, height: 500 }), { scale: 0.5, x: 200, y: 150 })
})

test("unmeasured bounds preserve sanitized positions without forcing a new center", async () => {
  const constrain = await constrainer()
  for (const value of [0, -1, NaN, Infinity, -Infinity]) {
    for (const axis of ["width", "height"] as const) {
      for (const invalidImage of [true, false]) {
        const fitted = { width: 200, height: 100 }
        const container = { width: 800, height: 500 }
        ;(invalidImage ? fitted : container)[axis] = value
        assert.deepEqual(constrain({ scale: 20, x: 15, y: -30 }, fitted, container), { scale: 8, x: 15, y: -30 })
      }
    }
  }
  assert.deepEqual(constrain({ scale: NaN, x: Infinity, y: NaN }, { width: 0, height: 0 }, { width: 0, height: 0 }), { scale: 1, x: 0, y: 0 })
})

test("bounds apply zoom limits before calculating edges and keep extreme values finite", async () => {
  const constrain = await constrainer()
  assert.deepEqual(constrain({ scale: 0.01, x: -100, y: -100 }, { width: 800, height: 400 }, { width: 800, height: 500 }), { scale: 0.25, x: 300, y: 200 })
  const next = constrain({ scale: 8, x: -100, y: 100 }, { width: Number.MAX_VALUE, height: Number.MAX_VALUE }, { width: 800, height: 500 })
  assert.ok(Object.values(next).every(Number.isFinite))
})
