import assert from 'node:assert/strict'
import { test } from 'node:test'
import { reviewRequests, locateComment, normalizeReviewText, commentAnchor, canSendReview } from '../src/lib/report-review.ts'

test('one submission contains every pending comment, grouped by report revision', () => {
  const rows = [
    { id:'a',session_id:'s',report_id:'r',revision:2,state:'pending' },
    { id:'b',session_id:'s',report_id:'r',revision:2,state:'pending' },
    { id:'c',session_id:'s',report_id:'z',revision:1,state:'submitted' },
  ] as any
  assert.deepEqual(reviewRequests(rows), [{report_id:'r',revision:2,comment_ids:['a','b']}])
  rows.splice(0,1)
  assert.deepEqual(reviewRequests(rows)[0].comment_ids, ['b'])
  assert.ok(canSendReview('', [], true))
  assert.ok(!canSendReview('', [{status:'parsing'}] as any, true))
})

test('anchor disambiguates repeated Unicode text with context without UTF16 offsets', () => {
  const text = '第一项 🧭 收益。第二项 🧭 收益。'
  const anchor = commentAnchor(text, '🧭 收益', text.indexOf('🧭', text.indexOf('🧭')+2), 3, 3)
  assert.equal(anchor.start_line, 3)
  assert.equal(locateComment(text, anchor)?.start, text.lastIndexOf('🧭'))
  assert.equal(locateComment('收益 收益', {quote:'收益',prefix:'',suffix:''} as any), null)
  assert.equal(normalizeReviewText(' a\n b '), 'ab')
})

test('an unsaved draft follows an unchanged passage and keeps its text when reattachment is needed',async()=>{
  const {relocateReviewDraft}=await import('../src/lib/report-review.ts')
  const draft={anchor:{start_line:3,end_line:3,quote:'Original.',prefix:'',suffix:''},text:'Please improve the argument.'}
  const moved=relocateReviewDraft(draft,'## Title\n\nOriginal.','Preface.\n\n## Title\n\nOriginal.')
  assert.equal(moved.anchor.start_line,5)
  assert.equal(moved.text,draft.text)
  const changed=relocateReviewDraft(draft,'## Title\n\nOriginal.','## Title\n\nRevised.')
  assert.equal(changed.anchor_stale,true)
  assert.equal(changed.text,draft.text)
})

test('anchor context never cuts an emoji surrogate pair at its boundary',()=>{
  const text='🧭'.repeat(31)+'a目标'
  const anchor=commentAnchor(text,'目标',text.indexOf('目标'),1,1)
  assert.equal(Array.from(anchor.prefix).some(char=>{const point=char.codePointAt(0)!;return point>=0xd800&&point<=0xdfff}),false)
})

test('saved selections carry the verified first and last block identities',async()=>{
  const {withReportBlocks}=await import('../src/lib/report-review.ts')
  const blocks=[{id:'b_first',hash:'h1',start_line:3,end_line:4},{id:'b_second',hash:'h2',start_line:6,end_line:6}] as any
  const anchor={start_line:4,end_line:6,quote:'Selected text',prefix:'',suffix:''}
  assert.deepEqual(withReportBlocks(anchor,blocks),{...anchor,block_id:'b_first',block_hash:'h1',end_block_id:'b_second',end_block_hash:'h2'})
  assert.deepEqual(withReportBlocks(anchor,undefined),anchor)
})

test('drafts follow their unchanged block among repeated paragraphs rather than searching the whole document',async()=>{
  const {relocateReviewDraft}=await import('../src/lib/report-review.ts')
  const old='## First\n\nRepeated.\n\n## Second\n\nRepeated.'
  const source='Preface.\n\n'+old
  const before=[{id:'b_a',hash:'h',start_line:3,end_line:3},{id:'b_b',hash:'h',start_line:7,end_line:7}] as any
  const after=[{id:'b_a',hash:'h',start_line:5,end_line:5},{id:'b_b',hash:'h',start_line:9,end_line:9}] as any
  const draft={anchor:{start_line:7,end_line:7,quote:'Repeated.',prefix:'',suffix:'',block_id:'b_b',block_hash:'h',end_block_id:'b_b',end_block_hash:'h'},text:'Change only this one.'}
  const moved=relocateReviewDraft(draft,old,source,before,after)
  assert.equal(moved.anchor.start_line,9)
  assert.equal(moved.anchor.block_id,'b_b')
  assert.equal(moved.anchor_stale,false)
  const stale=relocateReviewDraft(draft,old,source,before,[after[0],{...after[1],hash:'changed'}])
  assert.equal(stale.anchor_stale,true)
  assert.equal(stale.anchor.quote,'Repeated.')
})

test('the exact selection locates one occurrence even when its neighboring context repeats',()=>{
  const text='🧭 '+ 'A '.repeat(200)
  const offset=text.indexOf('A')+200
  const anchor=commentAnchor(text,'A',offset,1,1)
  assert.equal(anchor.selection_start,101)
  assert.equal(anchor.selection_end,102)
  assert.deepEqual(locateComment(text,anchor),{start:offset,end:offset+1})
  assert.equal(locateComment(text,{...anchor,quote:'B'}),null)
})

test('selection offsets count Unicode code points and exclude display whitespace',()=>{
  const text='🧭\n A\t A'
  const offset=text.lastIndexOf('A')
  const anchor=commentAnchor(text,'A',offset,3,3)
  assert.equal(anchor.selection_start,2)
  assert.equal(anchor.selection_end,3)
  assert.deepEqual(locateComment(text,anchor),{start:offset,end:offset+1})
})

test('copied Unicode whitespace has the same coordinates as server validation',()=>{
  for(const space of ['\ufeff','\u0085','\u00a0']){
    const text='X'+space+'A'
    const anchor=commentAnchor(text,'A',2,1,1)
    assert.equal(anchor.selection_start,1)
    assert.deepEqual(locateComment(text,anchor),{start:2,end:3})
  }
})
