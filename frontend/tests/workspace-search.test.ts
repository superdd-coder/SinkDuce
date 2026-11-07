import { test } from 'node:test'
import assert from 'node:assert/strict'
import { appendSearchPage, highlightSearchText } from '../src/lib/workspace-search.ts'

// User text stays literal and reconstructs exactly: no regex operators or HTML.
test('keyword highlighting preserves text and treats punctuation literally', () => {
  const text = '<script> 项目 [abc] a+b 100%_ MixedCASE'
  const pieces = highlightSearchText(text, '[abc] a+b mixedcase 项目')
  assert.equal(pieces.map(p => p.text).join(''), text)
  assert.deepEqual(pieces.filter(p => p.match).map(p => p.text), ['项目','[abc]','a+b','MixedCASE'])
  assert.deepEqual(highlightSearchText('unchanged', '   '), [{text:'unchanged',match:false}])
})
test('overlapping keywords prefer the longest full match', () => {
  assert.deepEqual(highlightSearchText('Project planning', 'pro project'), [
    {text:'Project',match:true},{text:' planning',match:false},
  ])
})
test('loading more results preserves prior pages and deduplicates changed resources', () => {
  const hit = (id: string, title = id) => ({category:'files' as const,id,title,context:'Project / Folder',snippet:''})
  const first = {category:'files' as const,total:3,items:[hit('a'),hit('b')],has_more:true,next_offset:2}
  const next = {category:'files' as const,total:3,items:[hit('b','renamed'),hit('c')],has_more:false,next_offset:3}
  const merged = appendSearchPage(first,next)
  assert.deepEqual(merged.items.map(h => h.title), ['a','renamed','c'])
  assert.equal(merged.has_more,false)
  assert.equal(merged.next_offset,3)
})
