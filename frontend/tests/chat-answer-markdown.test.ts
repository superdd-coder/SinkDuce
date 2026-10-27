import assert from "node:assert/strict"
import { before, test } from "node:test"
import { unified } from "unified"
import remarkParse from "remark-parse"

interface Position {
  start: { line: number; column: number }
  end: { line: number; column: number }
}

interface Helpers {
  splitAnswerStream?: (content: string, head: string) => { head: string; tail: string }
  isCodeFenceClosed?: (source: string, position?: Position) => boolean
  stabilizeAnswerMarkdown?: (source: string) => string
}

let helpers: Helpers
before(async () => {
  helpers = await import("../src/lib/chat-answer-markdown.ts").catch((error) => {
    if (error.code === "ERR_MODULE_NOT_FOUND") return {}
    throw error
  })
})

function positionOfCode(source: string): Position {
  const tree = unified().use(remarkParse).parse(source)
  const visit = (node: { type: string; position?: Position; children?: unknown[] }): Position | undefined => {
    if (node.type === "code") return node.position
    for (const child of node.children ?? []) {
      const position = visit(child as Parameters<typeof visit>[0])
      if (position) return position
    }
  }
  return visit(tree)!
}

test("citation cleanup preserves a formatted prefix during streaming", () => {
  assert.equal(typeof helpers.splitAnswerStream, "function", "stream helpers must be implemented")
  const head = "## Findings\n\nFirst finding [ref:12].\n"
  const parts = helpers.splitAnswerStream!(head + "More details", head)
  assert.equal(parts.head, "## Findings\n\nFirst finding .\n")
  assert.equal(parts.tail, "More details")
})

test("stream cleanup hides an unfinished citation without hiding ordinary links", () => {
  assert.equal(typeof helpers.splitAnswerStream, "function", "stream helpers must be implemented")
  const head = "[Documentation](https://example.com)\n"
  const parts = helpers.splitAnswerStream!(head + "Supported fact [ref:1", head)
  assert.equal(parts.head, head)
  assert.equal(parts.tail, "Supported fact ")
})

test("a rewritten prefix does not drop the newly streamed content", () => {
  assert.equal(typeof helpers.splitAnswerStream, "function", "stream helpers must be implemented")
  assert.deepEqual(helpers.splitAnswerStream!("New answer", "Old answer\n"), { head: "", tail: "New answer" })
})

test("a completed bold lead keeps its supporting live text intact across the stream boundary", () => {
  const head = "**Finding: approximately 12%.**\n"
  const tail = "Supporting **amount** with `literal code`"
  assert.deepEqual(helpers.splitAnswerStream!(head + tail, head), { head, tail })
})

test("only a real matching closing fence makes SVG ready", () => {
  assert.equal(typeof helpers.isCodeFenceClosed, "function", "fence helpers must be implemented")
  for (const source of ["```svg\n<svg />\n```", "~~~~svg\r\n<svg />\r\n~~~~~", "  ```svg\n<svg />\n  ```"]) {
    assert.equal(helpers.isCodeFenceClosed!(source, positionOfCode(source)), true, source)
  }
  for (const source of ["```svg\n<svg />\n", "````svg\n<svg />\n```", "```svg\n<svg />\n~~~", "```svg\n<svg />\n    ```"]) {
    assert.equal(helpers.isCodeFenceClosed!(source, positionOfCode(source)), false, source)
  }
})

test("fence state uses source positions for each diagram in the answer", () => {
  assert.equal(typeof helpers.isCodeFenceClosed, "function", "fence helpers must be implemented")
  const source = "Before\n\n```svg\n<svg />\n```\n\nAfter\n\n```svg\n<svg />\n"
  const nodes = unified().use(remarkParse).parse(source).children.filter((node) => node.type === "code")
  assert.equal(helpers.isCodeFenceClosed!(source, nodes[0].position), true)
  assert.equal(helpers.isCodeFenceClosed!(source, nodes[1].position), false)
})

test("display-only fence stabilization does not make an incomplete SVG complete", () => {
  assert.equal(typeof helpers.stabilizeAnswerMarkdown, "function", "fence helpers must be implemented")
  assert.equal(typeof helpers.isCodeFenceClosed, "function", "fence helpers must be implemented")
  const source = "~~~~svg\n<svg />\n"
  const display = helpers.stabilizeAnswerMarkdown!(source)
  assert.match(display, /\n~~~~$/)
  assert.equal(helpers.isCodeFenceClosed!(source, positionOfCode(display)), false)
})

test("stream cleanup keeps citation-like text inside code examples", () => {
  assert.equal(typeof helpers.splitAnswerStream, "function", "stream helpers must be implemented")
  const head = "```text\n[ref:12] is the example marker\n```\n"
  assert.deepEqual(helpers.splitAnswerStream!(head + "Tail", head), { head, tail: "Tail" })
})

test("nested list and quote fences preserve literal citation-like SVG labels", () => {
  assert.equal(typeof helpers.splitAnswerStream, "function")
  for (const head of [
    "> ```svg\n> <svg><text>[ref:12]</text></svg>\n> ```\n",
    "- ```svg\n  <svg><text>[ref:12]</text></svg>\n  ```\n",
    "Text with `[ref:12]` as a code example\n",
  ]) {
    assert.deepEqual(helpers.splitAnswerStream!(head + "Tail", head), { head, tail: "Tail" })
  }
})

test("a completed list fence does not gain an extra streamed code block", () => {
  assert.equal(typeof helpers.stabilizeAnswerMarkdown, "function")
  const source = "- ```svg\n  <svg />\n  ```\n\nAfter"
  assert.equal(helpers.stabilizeAnswerMarkdown!(source), source)
})

test("real closing fences inside combined quote/list containers are recognized", () => {
  assert.equal(typeof helpers.isCodeFenceClosed, "function")
  for (const source of [
    "> 10. ```svg\n>     <svg />\n>     ```",
    "> - - ```svg\n>     <svg />\n>     ```",
    "- - > ```svg\n    > <svg />\n    > ```",
  ]) {
    assert.equal(helpers.isCodeFenceClosed!(source, positionOfCode(source)), true, source)
  }
})

test("stream tail cleanup retains its ordinary open code-fence context", () => {
  assert.equal(typeof helpers.splitAnswerStream, "function")
  const head = "```text\n"
  const tail = "<svg><text>fact [ref:1"
  assert.deepEqual(helpers.splitAnswerStream!(head + tail, head), { head, tail })
})

test("multiline inline code crossing the formatted prefix retains literal markers", () => {
  const head = "Example `[ref:12]\n"
  const content = head + "literal` continued"
  assert.deepEqual(helpers.splitAnswerStream!(content, head), { head: "", tail: content })
})

test("a citation crossing the formatted prefix is removed as one marker", () => {
  const head = "Fact [ref:\n"
  assert.deepEqual(helpers.splitAnswerStream!(head + "12] more", head), { head: "", tail: "Fact  more" })
})

test("SVG source is hidden before the first formatted snapshot while preceding prose is visible", () => {
  const source = "Before\n\n```svg\n<svg><polyline points=\"0,0 10,10\""
  assert.deepEqual(helpers.splitAnswerStream!(source, ""), { head: "", tail: "Before\n\n" })
})

test("an SVG already in the formatted head never leaks growing source into the external tail", () => {
  const head = "Before\n\n```svg\n<svg>\n"
  const parts = helpers.splitAnswerStream!(head + "<text>[ref:12]</text>\n<polyline points=\"1,2", head)
  assert.deepEqual(parts, { head, tail: "" })
})

test("split closing-fence tokens stay hidden and following prose remains immediate", () => {
  const head = "```svg\n<svg>\n"
  const source = "<text>[ref:12]</text>\n</svg>\n```\nAfter [ref:2] now"
  for (let end = 0; end <= source.length; end++) {
    const tail = helpers.splitAnswerStream!(head + source.slice(0, end), head).tail
    assert.doesNotMatch(tail, /svg|text|`/, `token prefix ${end}`)
    if (source.slice(0, end).includes("After")) assert.match(tail, /^After/)
  }
  assert.equal(helpers.splitAnswerStream!(head + source, head).tail, "After  now")
  const closedHead = head + "<text>[ref:12]</text>\n</svg>\n```\n"
  assert.deepEqual(helpers.splitAnswerStream!(closedHead + "After", closedHead), { head: closedHead, tail: "After" })
})

test("a complete SVG received between snapshots remains hidden until it joins the head", () => {
  const head = "Intro\n\n"
  const tail = "```svg\n<svg />\n```\n\nAfter"
  assert.deepEqual(helpers.splitAnswerStream!(head + tail, head), { head, tail: "\nAfter" })
})

test("SVG fences inside quote and list containers do not leak source", () => {
  for (const [opening, body, closing] of [
    ["> ```svg", "> <svg />", "> ```"],
    ["- ```svg", "  <svg />", "  ```"],
    ["> 10. ```svg", ">     <svg />", ">     ```"],
    ["> - - ```svg", ">     <svg />", ">     ```"],
    ["- - > ```svg", "    > <svg />", "    > ```"],
  ]) {
    const source = `${opening}\n${body}\n${closing}\nAfter`
    assert.equal(helpers.splitAnswerStream!(source, "").tail, "After", opening)
    const head = `${opening}\n`
    assert.equal(helpers.splitAnswerStream!(source, head).tail, "After", opening)
  }
})

test("SVG suppression respects marker length, case, and tilde fences", () => {
  const head = "~~~~SVG extra\n<svg />\n"
  assert.equal(helpers.splitAnswerStream!(head + "~~~\nstill source", head).tail, "")
  assert.equal(helpers.splitAnswerStream!(head + "~~~~~\nAfter", head).tail, "After")
  assert.equal(helpers.splitAnswerStream!("````svg\n<svg />\n```\nstill source", "").tail, "")
})

test("ordinary code examples, inline code, and root indented code keep their source", () => {
  for (const source of [
    "````text\n```svg\n<svg />\n```\n````\nAfter",
    "Use `svg` and ```svg as text.",
    "    ```svg\n    <svg />\n    ```",
    "```python\nprint('[ref:12]')\n```\nAfter",
  ]) assert.deepEqual(helpers.splitAnswerStream!(source, ""), { head: "", tail: source })
})

test("leaving a quoted SVG container releases outside prose without a closing fence", () => {
  const head = "> ```svg\n> <svg>\n"
  assert.deepEqual(helpers.splitAnswerStream!(head + "Outside [ref:2] text", head), { head, tail: "Outside  text" })
})

test("multiple SVG blocks between snapshots do not hide ordinary code or final prose", () => {
  const source = "Before\n```svg\n<svg />\n```\n```js\nconst marker = '[ref:12]'\n```\n~~~svg\n<svg />\n~~~\nAfter"
  assert.equal(helpers.splitAnswerStream!(source, "").tail, "Before\n```js\nconst marker = '[ref:12]'\n```\nAfter")
})
