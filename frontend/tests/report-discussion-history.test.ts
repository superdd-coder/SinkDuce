import assert from 'node:assert/strict'
import {test} from 'node:test'

test('a discussion round compares its own saved changes rather than inherited pending changes',async()=>{
  const {discussionChanges}=await import('../src/lib/report-discussion-history.ts')
  const inherited={id:'earlier',before:'Earlier A',after:'Earlier B'}
  const own={id:'own',before:'Delete this paragraph.',after:''}
  const round={id:'g',result_revision:5,results:[{change_ids:['own']}],patches:[{before:'Earlier A',after:'Earlier B'},{before:'Delete this paragraph.',after:''}]}
  assert.deepEqual(discussionChanges({revision:5,changes:[inherited,own],suggestions:[round]} as any,round as any),[own])
})

test('an unannotated direct edit uses its saved paragraph comparisons, and a reply-only round has no diff',async()=>{
  const {discussionChanges}=await import('../src/lib/report-discussion-history.ts')
  const change={id:'c',before:'Before.',after:'After.'}
  const direct={id:'g',result_revision:2,comments:[],results:[],patches:[{}]}
  assert.deepEqual(discussionChanges({revision:2,changes:[change],suggestions:[direct]} as any,direct as any),[change])
  const reply={...direct,id:'reply',decision:'answered',results:[{status:'unchanged'}],patches:[]}
  assert.deepEqual(discussionChanges({revision:2,changes:[change],suggestions:[direct,reply]} as any,reply as any),[])
  assert.deepEqual(discussionChanges({revision:2,changes:[change],suggestions:[direct,reply]} as any,direct as any),[change])
})

test('independent rounds in the same save never inherit another comment\'s changes',async()=>{
  const {discussionChanges}=await import('../src/lib/report-discussion-history.ts')
  const a={id:'a',result_revision:4,results:[{change_ids:['first']}],patches:[{}]}
  const b={id:'b',result_revision:4,results:[{change_ids:['second']}],patches:[{}]}
  const changes=[{id:'first',before:'First A.',after:'First B.'},{id:'second',before:'Second A.',after:'Second B.'}]
  const report={revision:4,changes,suggestions:[a,b]}
  assert.deepEqual(discussionChanges(report as any,b as any),[changes[1]])
  assert.deepEqual(discussionChanges({...report,revision:5} as any,b as any),[])
})

test('comparison evidence includes new and old sources with one shared numbering order',async()=>{
  const {comparisonEvidence}=await import('../src/lib/report-discussion-history.ts')
  const a={id:'old',title:'Page 4',quote:'Old source',source:{},location:{page_number:4}}
  const b={id:'new',title:'Page 7',quote:'New source',source:{},location:{page_number:7}}
  const scope=comparisonEvidence({citations:[a],markdown:'Before[cite:old]'} as any,{citations:[b],markdown:'After[cite:new]'} as any)
  assert.deepEqual(scope.citations.map((e:any)=>e.id),['old','new'])
  assert.ok(scope.source.includes('[cite:old]')&&scope.source.includes('[cite:new]'))
  const {citationMarkdown,citationOrder}=await import('../src/lib/chat-citations.ts')
  assert.ok(!citationMarkdown('After[cite:new]',scope.citations).includes('〔?〕'))
  assert.deepEqual(citationOrder(scope.source),['old','new'])
})

test('revision locations use real section headings and distinguish citation-only updates',async()=>{
  const {discussionChangeLocation,discussionChangeKind}=await import('../src/lib/report-discussion-history.ts')
  const source='## Overview\n\nText.\n\n```md\n## Example\n```\n\n### Funding\n\nSources[cite:old]'
  const c={kind:'replace',old_start:11,new_start:11,before:'Sources[cite:old]',after:'Sources[cite:new]'}
  assert.equal(discussionChangeLocation(c as any,source,source),'Funding')
  assert.equal(discussionChangeKind(c as any),'citation')
  assert.equal(discussionChangeLocation({...c,after:'',old_start:3} as any,source,''),'Overview')
})

test('several changes under one heading share a single section in the revision view',async()=>{
  const {discussionSections}=await import('../src/lib/report-discussion-history.ts')
  const source='# Report\n\n## Overview\n\nFirst.\n\nSecond.\n\n## Risk\n\nLast.'
  const changes=[{id:'a',kind:'replace',before:'First A.',after:'First.',old_start:5,new_start:5},{id:'b',kind:'replace',before:'Second A.',after:'Second.',old_start:7,new_start:7},{id:'c',kind:'replace',before:'Last A.',after:'Last.',old_start:11,new_start:11}]
  const sections=discussionSections(changes as any,source,source)
  assert.deepEqual(sections.map((s:any)=>[s.title,s.changes.map((c:any)=>c.id)]),[['Overview',['a','b']],['Risk',['c']]])
})

test('a location comparison shows only that location inside its original paragraph context',async()=>{
  const {discussionChanges}=await import('../src/lib/report-discussion-history.ts')
  const markdown='Violet parcel.\n\nViolet crate.'
  const first={id:'one',location_id:'one',result_revision:2,decision:'pending',patches:[{field:'markdown',start:0,end:6,before:'Amber',after:'Violet'}]}
  const second={...first,id:'two',location_id:'two',patches:[{...first.patches[0],start:16,end:22}]}
  const changes=discussionChanges({revision:2,markdown,blocks:[{start:0,end:15},{start:16,end:29}],suggestions:[first,second]} as any,first as any)
  assert.equal(changes.length,1)
  assert.equal(changes[0].before,'Amber parcel.\n')
  assert.equal(changes[0].after,'Violet parcel.\n')
  assert.ok(!changes[0].before.includes('crate'))
})
