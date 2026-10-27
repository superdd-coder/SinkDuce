import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { test } from 'node:test'

// Use Vite's CSS parser through its own dependency scope, including isolated pnpm installs.
const postcss = createRequire(import.meta.resolve('vite'))('postcss')

const sheet = async (path:string) => postcss.parse(await readFile(new URL(path,import.meta.url),'utf8'))

test('evidence panels have one base paint rule so retired surfaces cannot repaint the preview',async()=>{
  const css = await sheet('../src/components/chat/chat-reports.css')
  for (const selector of ['.pm-citation-file-body','.pm-citation-file-pane','.pm-citation-excerpt-pane']) {
    const paints:string[] = []
    css.walkRules(rule=>{
      if (rule.parent?.type === 'atrule' || !rule.selectors.includes(selector)) return
      rule.walkDecls(/^(background|background-color)$/,decl=>{paints.push(decl.value)})
    })
    assert.ok(paints.length <= 1,`${selector} has competing base paints: ${paints.join(', ')}`)
  }
})

test('the evidence shell owns its paint even when the shared dialog stylesheet loads later',async()=>{
  const base = await sheet('../src/index.css')
  const citation = await sheet('../src/components/chat/chat-reports.css')
  // Both rules use class/attribute selectors; count their actual specificity components.
  const rank = (selector:string) => [...selector.matchAll(/\.[\w-]+|\[[^\]]+\]/g)].length
  let baseRank = 0, citationRank = 0
  base.walkRules(rule=>{
    if (rule.selectors.includes('.pm-dialog[data-slot="dialog-content"]')) baseRank=Math.max(baseRank,2)
  })
  citation.walkRules(rule=>{
    for (const selector of rule.selectors.filter(value=>value.includes('.pm-citation-dialog'))) {
      rule.walkDecls(/^(background|background-color)$/,decl=>{
        if (decl.important && rule.parent?.type !== 'atrule') citationRank=Math.max(citationRank,rank(selector))
      })
    }
  })
  assert.ok(citationRank>baseRank,'The citation surface is still overridden by the shared dialog paint')
})
