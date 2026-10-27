import assert from 'node:assert/strict'
import {before,after,test} from 'node:test'
import {createServer} from 'vite'
import {fileURLToPath} from 'node:url'
import {getSchema} from '@tiptap/core'
import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {ReactNodeViewContentProvider} from '@tiptap/react'
let server:Awaited<ReturnType<typeof createServer>>
before(async()=>{
  const root=fileURLToPath(new URL('../',import.meta.url))
  server=await createServer({root,configFile:false,server:{middlewareMode:true,hmr:false,ws:false,watch:null},resolve:{alias:{'@':root+'src','#edition':root+'src/edition'}},optimizeDeps:{noDiscovery:true}})
})
after(async()=>{await server?.close()})
const drawing='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 100"><text x="12" y="50">Review  →  Ship [ref:9] [spk:0]</text></svg>'

test('meeting reading and export cleaning preserve diagram source while resolving prose markers',async()=>{
  const {prepareMeetingSummaryForTiptapView}=await server.ssrLoadModule('/src/components/meeting/meeting-summary-marks.ts')
  const {prepareMeetingSummaryForNote}=await server.ssrLoadModule('/src/lib/meeting-summary-display.ts')
  const fence='```svg\n'+drawing+'\n```'
  const md='[spk:0] approved. [ref:9]\n\n'+fence
  const reading=prepareMeetingSummaryForTiptapView(md,{'0':'Casey'})
  assert.ok(reading.includes(fence),'Code remains byte-for-byte intact')
  assert.match(reading,/Casey approved\. <span data-meeting-ref="stt_0009"/)
  assert.equal(prepareMeetingSummaryForNote(md,{'0':'Casey'}),'Casey approved.\n\n'+fence)
  assert.ok(prepareMeetingSummaryForTiptapView('```svg\n'+drawing,{'0':'Casey'}).includes(drawing),'Streaming an unfinished fence also preserves its source')
})

test('meeting document schema supports readable tables, retained SVG source, and interactive citations',async()=>{
  const {meetingSummaryExtensions}=await server.ssrLoadModule('/src/components/meeting/meeting-summary-extensions.ts')
  const extensions=meetingSummaryExtensions(()=>()=>{})
  const schema=getSchema(extensions)
  const doc=schema.nodeFromJSON({type:'doc',content:[
    {type:'table',content:[{type:'tableRow',content:[{type:'tableHeader',content:[{type:'paragraph',content:[{type:'text',text:'Decision'}]}]},{type:'tableHeader',content:[{type:'paragraph',content:[{type:'text',text:'Owner'}]}]}]}]},
    {type:'codeBlock',attrs:{language:'svg'},content:[{type:'text',text:drawing}]},
    {type:'paragraph',content:[{type:'meetingRef',attrs:{ids:'stt_0009'}},{type:'meetingPriority',attrs:{level:'high'}}]},
  ]})
  doc.check()
  assert.equal(doc.child(1).textContent,drawing)
  const kit=extensions.find((extension:any)=>extension.name==='starterKit')
  const code=kit.config.addExtensions.call({parent:kit.parent.config.addExtensions.bind({options:kit.options})}).find((extension:any)=>extension.name==='codeBlock')
  assert.equal(typeof code.config.addNodeView,'function','Meeting diagrams use the shared safe image preview, not literal code')
  const {EditorCodeBlock}=await server.ssrLoadModule('/src/components/ui/svg-code-block-extension.tsx')
  const html=renderToStaticMarkup(createElement(ReactNodeViewContentProvider,{content:drawing,children:createElement(EditorCodeBlock,{node:{attrs:{language:'svg'},textContent:drawing}})}))
  assert.match(html,/<img\b/)
  assert.doesNotMatch(html,/<svg\b/)
})
