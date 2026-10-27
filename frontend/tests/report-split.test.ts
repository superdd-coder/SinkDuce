import assert from "node:assert/strict"
import { test } from "node:test"
import { createViewRailResizeController } from "../src/lib/view-rail-resize.ts"

test("report split preferences retain valid ratios and reject corrupt persisted values", async () => {
  const helpers = await import("../src/lib/report-split.ts").catch(() => null)
  assert.ok(helpers, "Shared report split sizing must exist")
  for (const [input, expected] of [[0.46, 0.46], [0, 0.28], [1, 0.7], [NaN, 0.46], ["0.5", 0.46], [null, 0.46]] as const) {
    assert.equal(helpers.normalizeReportSplit(input), expected)
  }
  assert.equal(helpers.reportSplitKeyboardRatio(0.46, "ArrowRight", false), 0.48)
  assert.equal(helpers.reportSplitKeyboardRatio(0.46, "ArrowLeft", true), 0.41)
})

function styleDeclaration() {
  const values = new Map<string, { value: string; priority: string }>()
  return {
    getPropertyValue: (name: string) => values.get(name)?.value ?? "",
    getPropertyPriority: (name: string) => values.get(name)?.priority ?? "",
    setProperty(name: string, value: string, priority = "") { values.set(name, { value, priority }) },
    removeProperty(name: string) { const value = values.get(name)?.value ?? ""; values.delete(name); return value },
  }
}

test("report dragging previews its own pane width, clamps both panes and commits only on release", () => {
  const host = { style: styleDeclaration() }, documentStyle = styleDeclaration()
  const captures = new Set<number>(), committed: number[] = []
  const target = {
    setPointerCapture(id: number) { captures.add(id) }, hasPointerCapture: (id: number) => captures.has(id),
    releasePointerCapture(id: number) { captures.delete(id) },
  }
  const resize = createViewRailResizeController({ onPreview() {}, onCommit: (width) => committed.push(width) }, {
    propertyName: "--pm-report-left-w", normalizeWidth: (width: number) => Math.max(320, Math.min(628, width)),
  })
  host.style.setProperty("--pm-report-left-w", "46%")
  resize.start({ pointerId: 1, clientX: 460, width: 460, button: 0, isPrimary: true, target, host, documentStyle })
  resize.move(1, 580)
  assert.equal(host.style.getPropertyValue("--pm-report-left-w"), "580px")
  assert.equal(host.style.getPropertyValue("--pm-view-rail-w"), "")
  assert.deepEqual(committed, [])
  resize.finish(1, 900)
  assert.deepEqual(committed, [628])
  assert.equal(captures.size, 0)
  resize.start({ pointerId: 2, clientX: 500, width: 500, button: 0, isPrimary: true, target, host, documentStyle })
  resize.move(2, 50)
  assert.equal(host.style.getPropertyValue("--pm-report-left-w"), "320px")
  resize.cancel()
  assert.equal(host.style.getPropertyValue("--pm-report-left-w"), "628px")
  assert.deepEqual(committed, [628])
})

test('dragging a widened report starts from the visible divider without subtracting its review offset twice',async()=>{
  const {reportResizeStartingWidth,createReportResizeController}=await import('../src/lib/report-split.ts')
  const host={style:styleDeclaration()},documentStyle=styleDeclaration(),captures=new Set<number>()
  const target={setPointerCapture(id:number){captures.add(id)},hasPointerCapture:(id:number)=>captures.has(id),releasePointerCapture(id:number){captures.delete(id)}}
  let committed=0
  const resize=createReportResizeController({onPreview(){},onCommit:value=>{committed=value}})
  resize.setHostWidth(1200)
  resize.start({pointerId:1,clientX:392,width:reportResizeStartingWidth(392,160),button:0,isPrimary:true,target,host,documentStyle})
  resize.move(1,432)
  assert.equal(parseFloat(host.style.getPropertyValue('--pm-report-left-w'))-160,432)
  resize.finish(1,432)
  assert.ok(Math.abs(committed-592/1200)<0.0001)
})
