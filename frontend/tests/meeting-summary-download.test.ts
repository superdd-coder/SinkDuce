import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {after, before, test} from 'node:test'
import {fileURLToPath} from 'node:url'
import * as React from 'react'
import ts from 'typescript'
import {createServer} from 'vite'

let server: Awaited<ReturnType<typeof createServer>>
before(async()=>{
  const root=fileURLToPath(new URL('../',import.meta.url))
  server=await createServer({root,configFile:false,server:{middlewareMode:true,hmr:false,ws:false,watch:null},
    resolve:{alias:{'@':root+'src'}},optimizeDeps:{noDiscovery:true}})
})
after(async()=>{await server?.close()})

test('each format downloads the selected saved translation through the authenticated API',async()=>{
  const {downloadMeetingSummary}=await server.ssrLoadModule('/src/api/meeting.ts')
  assert.equal(typeof downloadMeetingSummary,'function')
  const oldFetch=globalThis.fetch,controller=new AbortController()
  try{
    for(const format of ['md','docx','pdf']){
      globalThis.fetch=async(url,init)=>{
        assert.equal(url,'/api/meetings/meeting%20a/sections/topic%231/export')
        assert.equal(init?.method,'POST')
        assert.equal(new Headers(init?.headers).get('Content-Type'),'application/json')
        assert.deepEqual(JSON.parse(String(init?.body)),{format,language:'FR'})
        assert.equal(init?.signal,controller.signal)
        return new Response('sample file',{headers:{'Content-Disposition':`attachment; filename*=UTF-8''${encodeURIComponent('Résumé.'+format)}`}})
      }
      const result=await downloadMeetingSummary('meeting a','topic#1',format,'FR',controller.signal)
      assert.equal(result.filename,'Résumé.'+format)
      assert.equal(await result.blob.text(),'sample file')
    }
    globalThis.fetch=async()=>new Response(JSON.stringify({detail:'Conversion unavailable'}),{status:503})
    await assert.rejects(downloadMeetingSummary('m','t','pdf'),/Conversion unavailable/)
    globalThis.fetch=async()=>new Response('content')
    assert.equal((await downloadMeetingSummary('m','t','md')).filename,'Meeting summary.md')
  }finally{globalThis.fetch=oldFetch}
})

test('downloads keep the Blob alive through the click and clean up even on failure',async()=>{
  const {downloadBlob}=await server.ssrLoadModule('/src/lib/download.ts')
  const oldDocument=Object.getOwnPropertyDescriptor(globalThis,'document')
  const oldCreate=URL.createObjectURL,oldRevoke=URL.revokeObjectURL,oldTimeout=globalThis.setTimeout
  const revoked:string[]=[],timers:Function[]=[],anchors:any[]=[]
  let fail=false
  URL.createObjectURL=()=> 'blob:sample';URL.revokeObjectURL=url=>{revoked.push(url)}
  globalThis.setTimeout=((fn:Function)=>{timers.push(fn);return 1}) as any
  Object.defineProperty(globalThis,'document',{configurable:true,value:{body:{appendChild(){}},createElement(tag:string){
    assert.equal(tag,'a','Download without a print frame or popup')
    const a={removed:false,download:'',href:'',remove(){this.removed=true},click(){assert.equal(revoked.length,0);if(fail)throw new Error('Blocked download')}}
    anchors.push(a);return a
  }}})
  try{
    downloadBlob(new Blob(['sample']),'Review.pdf')
    assert.equal(anchors[0].download,'Review.pdf');assert.equal(anchors[0].removed,true);assert.equal(revoked.length,0)
    timers.splice(0).forEach(fn=>fn());assert.deepEqual(revoked,['blob:sample'])
    revoked.length=0;fail=true
    assert.throws(()=>downloadBlob(new Blob(['sample']),'Review.pdf'),/Blocked/)
    assert.equal(anchors[1].removed,true)
    timers.splice(0).forEach(fn=>fn());assert.deepEqual(revoked,['blob:sample'])
  }finally{
    URL.createObjectURL=oldCreate;URL.revokeObjectURL=oldRevoke;globalThis.setTimeout=oldTimeout
    if(oldDocument)Object.defineProperty(globalThis,'document',oldDocument);else Reflect.deleteProperty(globalThis,'document')
  }
})

async function harness(api:Record<string,any>){
  const key='__meeting_export_'+Math.random(),cells:any[]=[],effects:Function[]=[]
  let cursor=0
  ;(globalThis as any)[key]={...api,
    useState(initial:any){const i=cursor++;if(!(i in cells))cells[i]=initial;return[cells[i],(v:any)=>{cells[i]=typeof v==='function'?v(cells[i]):v}]},
    useRef(initial:any){const i=cursor++;return cells[i]??={current:initial}},
    useEffect(effect:Function,deps:any[]){const i=cursor++;if(cells[i]&&deps.every((v,j)=>Object.is(v,cells[i].deps[j])))return;effects.push(()=>{cells[i]?.cleanup?.();cells[i]={deps,cleanup:effect()}})},
  }
  const data=(s:string)=>'data:text/javascript,'+encodeURIComponent(s)
  const proxy=(names:string[])=>data(names.map(n=>`export const ${n}=(...args)=>globalThis[${JSON.stringify(key)}].${n}(...args);`).join('\n'))
  const imports:Record<string,string>={react:proxy(['useState','useRef','useEffect']),
    'react/jsx-runtime':import.meta.resolve('react/jsx-runtime'),'lucide-react':import.meta.resolve('lucide-react'),
    '@/api/client':proxy(['downloadMeetingSummary']),'@/lib/download':proxy(['downloadBlob']),
    '@/i18n/use-t':data('const t=key=>key;export const useT=()=>t'),
    '@/components/ui/dropdown-select':data('export function DropdownSelect(){}'),
    sonner:data(`export const toast={error:(...a)=>globalThis[${JSON.stringify(key)}].onError(...a)};`)}
  const source=await readFile(new URL('../src/components/meeting/meeting-summary-export.tsx',import.meta.url),'utf8')
  const compiled=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
    .replace(/from ["']([^"']+)["']/g,(s,path)=>imports[path]?`from ${JSON.stringify(imports[path])}`:s)
  const {MeetingSummaryExport}=await import(data(compiled))
  function elements(n:any):any[]{return Array.isArray(n)?n.flatMap(elements):React.isValidElement(n)?[n,...elements((n.props as any).children)]:[]}
  return{render(props:any){cursor=0;const tree=elements(MeetingSummaryExport(props));effects.splice(0).forEach(fn=>fn());return tree.find(e=>Array.isArray(e.props.options))?.props},
    dispose(){cells.forEach(c=>c?.cleanup?.());delete (globalThis as any)[key]}}
}
const selectors={meetingId:'m',tabId:'general',language:'FR'}

test('export control shows progress, blocks duplicate clicks, and can retry after a visible failure',async()=>{
  let resolve:any,calls:any[]=[],downloads:any[]=[],errors:any[]=[]
  const h=await harness({downloadMeetingSummary:(...args:any[])=>{calls.push(args);return new Promise(r=>{resolve=r})},downloadBlob:(...a:any[])=>downloads.push(a),onError:(e:any)=>errors.push(e)})
  try{
    let control=h.render(selectors)
    assert.deepEqual(control.options.map((o:any)=>o.label),['Word','PDF','Markdown'])
    const pending=control.onChange('pdf');control.onChange('docx')
    control=h.render(selectors)
    assert.equal(calls.length,1);assert.deepEqual(calls[0].slice(0,4),['m','general','pdf','FR'])
    assert.equal(control.disabled,true);assert.match(control.placeholder,/PDF.*preparing/)
    const blob=new Blob(['pdf']);resolve({blob,filename:'Sample.pdf'});await pending
    assert.deepEqual(downloads,[[blob,'Sample.pdf']]);assert.equal(h.render(selectors).disabled,false)
    const retry=h.render(selectors).onChange('docx')
    resolve(Promise.reject(new Error('Conversion unavailable')));await retry
    assert.deepEqual(errors,['Conversion unavailable']);assert.equal(h.render(selectors).disabled,false)
    const success=h.render(selectors).onChange('docx');resolve({blob,filename:'Sample.docx'});await success
    assert.equal(downloads[1][1],'Sample.docx')
  }finally{h.dispose()}
})

test('changing summary or unmounting aborts requests and suppresses late downloads',async()=>{
  const pending:any[]=[],downloads:any[]=[],errors:any[]=[]
  const h=await harness({downloadMeetingSummary:(...args:any[])=>new Promise(resolve=>pending.push({args,resolve})),downloadBlob:(...a:any[])=>downloads.push(a),onError:(e:any)=>errors.push(e)})
  const first=h.render(selectors).onChange('pdf')
  h.render({...selectors,tabId:'other'})
  assert.equal(pending[0].args[4].aborted,true)
  pending[0].resolve({blob:new Blob(['late']),filename:'Old.pdf'});await first
  assert.deepEqual(downloads,[])
  const second=h.render({...selectors,tabId:'other'}).onChange('docx')
  h.dispose();assert.equal(pending[1].args[4].aborted,true)
  pending[1].resolve({blob:new Blob(['late']),filename:'Old.docx'});await second
  assert.deepEqual(downloads,[]);assert.deepEqual(errors,[])
})
