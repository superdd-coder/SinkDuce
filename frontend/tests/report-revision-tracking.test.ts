import assert from 'node:assert/strict'
import {test} from 'node:test'
import {trackingSegments, latestSuggestions, suggestionDiscussion} from '../src/lib/report-revision-tracking.ts'

test('a pending block deletion is inserted at its original location without changing clean source',()=>{
  const source='Keep.\n\nNext.'
  const groups=[{id:'g',thread_ids:['c'],decision:'pending',patches:[{field:'markdown',start:7,end:7,before:'Deleted.\n\n',after:''}]}]
  const chunks=trackingSegments(source,[{start:0,end:6},{start:7,end:12}],groups as any)
  assert.equal(chunks.map(s=>s.source).join(''),source)
  const deleted=chunks.find(s=>s.kind==='change')!
  assert.equal(deleted.before,'Deleted.\n\n')
  assert.deepEqual(deleted.groupIds,['g'])
  assert.equal(chunks[0].source,'Keep.\n\n')
  assert.equal(chunks.at(-1)!.source,'Next.')
})

test('Unicode source coordinates preserve nearby Markdown and accepted edits disappear from redlines',()=>{
  const source='😀 **B**.\n\nOther.'
  const group={id:'g',thread_ids:['t'],decision:'pending',patches:[{field:'markdown',start:4,end:5,before:'A',after:'B'}]}
  const chunks=trackingSegments(source,[{start:0,end:9},{start:10,end:16}],[group] as any)
  assert.equal(chunks.find(s=>s.kind==='change')!.before,'😀 **A**.\n')
  assert.equal(chunks.map(s=>s.source).join(''),source)
  assert.ok(!trackingSegments(source,[],[{...group,decision:'accepted'}] as any).some(s=>s.kind==='change'))
})

test('tracking displays the newest round per thread while retaining independent discussion groups',()=>{
  const groups=[{id:'a',thread_ids:['t'],decision:'accepted'},{id:'other',thread_ids:['u'],decision:'pending'},{id:'b',thread_ids:['t'],decision:'rejected'}]
  assert.deepEqual(latestSuggestions(groups as any).map(g=>g.id),['other','b'])
})

test('sibling locations remain visible after an independent decision and have separate discussion lineages',()=>{
  const first={id:'first',instruction_id:'terms',location_id:'one',thread_ids:['comment'],parent_ids:[],decision:'accepted'}
  const second={id:'second',instruction_id:'terms',location_id:'two',thread_ids:['comment'],parent_ids:[],decision:'pending'}
  const continuation={...first,id:'continued',parent_ids:['first'],decision:'rejected'}
  const groups=[first,second,continuation] as any
  assert.deepEqual(latestSuggestions(groups).map(g=>g.id),['second','continued'])
  assert.deepEqual(suggestionDiscussion(groups,continuation as any).map(g=>g.id),['first','continued'])
})

test('deleted text uses real del elements across bold text while citation controls remain untouched',async()=>{
  const {reviewMarkup}=await import('../src/lib/report-review-markup.ts')
  const tree:any={type:'root',children:[{type:'element',tagName:'p',children:[{type:'text',value:'The '},{type:'element',tagName:'strong',children:[{type:'text',value:'old'}]},{type:'text',value:' words.'},{type:'element',tagName:'a',properties:{href:'#evidence-e'},children:[{type:'text',value:'1'}]}]}]}
  reviewMarkup({kind:'removed',passages:[{quote:'old words',prefix:'The ',suffix:'.'}]})(tree)
  const paragraph=tree.children[0]
  assert.equal(paragraph.children[0].value,'The ')
  assert.equal(paragraph.children[1].children[0].tagName,'del')
  assert.equal(paragraph.children[2].tagName,'del')
  assert.equal(paragraph.children.at(-1).children[0].type,'text')
})

test('a deletion inside a previously edited region does not duplicate its unchanged paragraphs',()=>{
  const source='Summary B.\n\nKeep.\n'
  const p={field:'markdown',start:0,end:Array.from(source).length,before:'Summary B.\n\nDelete.\n\nKeep.\n',after:source,display_patches:[{start:12,end:12,before:'Delete.\n\n',after:''}]}
  const chunks=trackingSegments(source,[{start:0,end:11},{start:12,end:18}],[{id:'g',decision:'pending',patches:[p]}] as any)
  assert.equal(chunks.filter(s=>s.kind==='change')[0].before,'Delete.\n\n')
  assert.equal(chunks.filter(s=>s.kind==='change')[0].source,'')
  assert.equal(chunks.map(s=>s.source).join(''),source)
})
