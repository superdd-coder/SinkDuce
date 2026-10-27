import assert from 'node:assert/strict'
import { test } from 'node:test'
import { passageRange, focusReviewPassage } from '../src/lib/report-review-dom.ts'

test('a saved selection spans its original paragraphs and excludes citations and unrelated text',()=>{
  const descriptors=new Map(['document','NodeFilter'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]))
  const make=(data:string,line:number,excluded=false)=>({data,length:data.length,parentElement:{closest:(selector:string)=>selector==='[data-report-line]'?{dataset:{reportLine:String(line),reportEnd:String(line)}}:excluded?{}:null}})
  const first=make('🧭 A A.',3),second=make('A A.',5)
  const nodes=[make('Unrelated.',1),first,make('1',3,true),second,make('Unrelated.',7)]
  const root={nodes} as any
  const endpoints:any={}
  try{
    Object.defineProperty(globalThis,'NodeFilter',{configurable:true,value:{SHOW_TEXT:4,FILTER_REJECT:2,FILTER_ACCEPT:1}})
    Object.defineProperty(globalThis,'document',{configurable:true,value:{
      createTreeWalker:(element:any,_:number,filter:any)=>{const accepted=element.nodes.filter((node:any)=>filter.acceptNode(node)===1);let i=0;return {nextNode:()=>accepted[i++]||null}},
      createRange:()=>({setStart:(node:any,offset:number)=>{endpoints.start={node,offset}},setEnd:(node:any,offset:number)=>{endpoints.end={node,offset}}}),
    }})
    const range=passageRange(root,{start_line:3,end_line:5,selection_start:2,selection_end:5,quote:'A. A',prefix:'',suffix:''})
    assert.ok(range)
    assert.deepEqual(endpoints.start,{node:first,offset:5})
    assert.deepEqual(endpoints.end,{node:second,offset:1})
  }finally{for(const [key,descriptor]of descriptors){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else Reflect.deleteProperty(globalThis,key)}}
})

test('focusing a deleted revision marks its original displayed region and cleans up on the next focus',()=>{
  const attributes=new Map<string,string>()
  const region={setAttribute:(key:string,value:string)=>attributes.set(key,value),removeAttribute:(key:string)=>attributes.delete(key)}
  const source={querySelector:(selector:string)=>selector.includes('data-review-primary')?region:null,querySelectorAll:()=>[]}
  const clear=focusReviewPassage(source as any,{anchor:{start_line:1,end_line:1,quote:'Deleted.',prefix:'',suffix:''},groupId:'g'})
  assert.equal(attributes.get('data-review-focused'),'change')
  clear()
  assert.equal(attributes.has('data-review-focused'),false)
})
