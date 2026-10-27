import assert from 'node:assert/strict'
import { test } from 'node:test'

test('comment cards start beside their text and collisions move later cards downward', async () => {
  const { layoutCommentRail } = await import('../src/lib/report-comment-layout.ts')
  const layout=layoutCommentRail([
    {id:'first',anchorTop:120,fullHeight:180,height:180},
    {id:'draft',anchorTop:160,fullHeight:220,height:220},
    {id:'last',anchorTop:700,fullHeight:150,height:150},
  ])
  assert.deepEqual(layout.cards.map(c=>[c.id,c.top]),[['first',120],['draft',312],['last',700]])
  assert.equal(layout.groups[0].ids.length,2)
  assert.ok(layout.height>=850)
})

test('folding keeps clusters stable while compact cards make room for the active editor',async()=>{
  const { layoutCommentRail }=await import('../src/lib/report-comment-layout.ts')
  const rows=[{id:'a',anchorTop:200,fullHeight:180,height:40},{id:'b',anchorTop:220,fullHeight:220,height:220}]
  const folded=layoutCommentRail(rows)
  const expanded=layoutCommentRail(rows.map(r=>({...r,height:r.fullHeight})))
  assert.deepEqual(folded.groups.map(g=>g.ids),expanded.groups.map(g=>g.ids))
  assert.equal(folded.cards[1].top,252)
  assert.equal(expanded.cards[1].top,392)
  assert.ok(expanded.cards.every((card,index)=>index===0||card.top>=expanded.cards[index-1].bottom+12))
})

test('moving text after a resize updates its card anchor without losing item order',async()=>{
  const { layoutCommentRail }=await import('../src/lib/report-comment-layout.ts')
  const layout=layoutCommentRail([{id:'b',anchorTop:500,fullHeight:180,height:180},{id:'a',anchorTop:100,fullHeight:180,height:180}])
  assert.deepEqual(layout.cards.map(c=>[c.id,c.top]),[['a',100],['b',500]])
  assert.ok(Number.isFinite(layout.height))
})

test('opening discussion details never changes which nearby cards form a group',async()=>{
  const {layoutCommentRail}=await import('../src/lib/report-comment-layout.ts')
  const rows=[{id:'a',anchorTop:100,fullHeight:180,height:180},{id:'b',anchorTop:140,fullHeight:180,height:44},{id:'c',anchorTop:520,fullHeight:180,height:180}]
  const before=layoutCommentRail(rows)
  const after=layoutCommentRail(rows.map(r=>r.id==='a'?{...r,fullHeight:1100,height:1100}:r))
  assert.deepEqual(after.groups.map(g=>g.ids),before.groups.map(g=>g.ids))
  assert.ok(after.cards.every((card,i)=>!i||card.top>=after.cards[i-1].bottom+12))
})
