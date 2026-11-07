import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"
import ts from "typescript"

const read = (file: string) => readFileSync(new URL(`../src/components/${file}`, import.meta.url), "utf8")

test("Q&A gives Quick Chat a rail beneath its toolbar throughout the workspace", () => {
  const project = read("database/database-view.tsx")
  assert.match(project.match(/railActive=\{([^}]+)\}/)?.[1] || "", /activeTab !== "config"/)

  const qa = read("qa/qa-workspace.tsx")
  const content = qa.split('<div className="qa-content">')[1]?.split('<Dialog open=')[0] || ""
  assert.ok(content.includes('className="qa-quick-chat-rail" data-pm-rail-anchor'))
  assert.ok(content.lastIndexOf("data-pm-rail-anchor") > content.lastIndexOf("QaMaterialPreview"))

  const css = read("qa/qa.css")
  assert.match(css, /\.qa-stage\.is-reading,\s*\.qa-quick-chat-slot\s*\{[^}]*grid-template-columns:[^}]*7fr[^}]*3fr[^}]*gap: var\(--qa-stage-gap\)/)
  assert.match(css, /\.qa-quick-chat-slot\s*\{[^}]*inset: var\(--qa-stage-inset\)/)
})

test("a kept-alive project stops exposing the body-portaled Quick Chat hint when the user leaves", () => {
  const project = ts.createSourceFile("database-view.tsx", read("database/database-view.tsx"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let visibility: ts.Expression | undefined
  function visit(node: ts.Node) {
    if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(project) === "QuickChat") {
      const prop = node.attributes.properties.find(item => ts.isJsxAttribute(item) && item.name.getText(project) === "fabVisible")
      if (prop && ts.isJsxAttribute(prop) && prop.initializer && ts.isJsxExpression(prop.initializer)) visibility = prop.initializer.expression
    }
    ts.forEachChild(node, visit)
  }
  visit(project)
  assert.ok(visibility)
  // Evaluate the actual launcher visibility passed by the parent. The project tab
  // remains Q&A after the app switches to another view, so only `active` changes.
  const showLauncher = new Function("active", "activeTab", `return (${visibility.getText(project)})`) as (active: boolean, tab: string) => boolean
  assert.equal(showLauncher(true, "qa"), true)
  assert.equal(showLauncher(false, "qa"), false)
  assert.equal(showLauncher(true, "config"), false)
  assert.equal(showLauncher(true, "timeline"), true)
})
