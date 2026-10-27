import {unified} from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import remarkRehype from 'remark-rehype'

interface Tree {type:string;value?:string;tagName?:string;properties?:Record<string,unknown>;children?:Tree[]}
interface Run<T> {kind:'equal'|'removed'|'added';items:T[]}
interface Atom {key:string;char:string;path:Tree[];node?:Tree}
const parser=unified().use(remarkParse).use(remarkGfm).use(remarkRehype)
const clone=<T,>(value:T):T=>structuredClone(value)
const element=(tagName:string,children:Tree[],className?:string):Tree=>({type:'element',tagName,properties:className?{className:[className]}:{},children})
const signature=(node:Tree):string=>node.type==='text'?node.value||'':`${node.tagName}:${JSON.stringify(node.properties||{})}[${(node.children||[]).map(signature).join('')}]`
const isInline=(node:Tree)=>['p','h1','h2','h3','h4','h5','h6','td','th'].includes(node.tagName||'')||(node.tagName==='li'&&!node.children?.some(child=>['p','ul','ol','blockquote','pre','table'].includes(child.tagName||'')))

/** Bounded sequence alignment; long replacements keep their shared prefix/suffix. */
function align<T>(before:T[],after:T[],key:(item:T)=>string):Run<T>[] {
  const runs:Run<T>[]=[]
  const add=(kind:Run<T>['kind'],item:T)=>{const last=runs.at(-1);if(last?.kind===kind)last.items.push(item);else runs.push({kind,items:[item]})}
  const a=before.map(key),b=after.map(key),n=a.length,m=b.length
  if(n*m>250_000){
    let start=0,end=0
    while(start<Math.min(n,m)&&a[start]===b[start]){add('equal',after[start]);start++}
    while(end<Math.min(n,m)-start&&a[n-end-1]===b[m-end-1])end++
    before.slice(start,n-end).forEach(item=>add('removed',item));after.slice(start,m-end).forEach(item=>add('added',item))
    after.slice(m-end).forEach(item=>add('equal',item));return runs
  }
  const width=m+1,lcs=new Uint32Array((n+1)*width)
  for(let i=n-1;i>=0;i--)for(let j=m-1;j>=0;j--)lcs[i*width+j]=a[i]===b[j]?1+lcs[(i+1)*width+j+1]:Math.max(lcs[(i+1)*width+j],lcs[i*width+j+1])
  let i=0,j=0
  while(i<n||j<m){
    if(i<n&&j<m&&a[i]===b[j]){add('equal',after[j]);i++;j++}
    else if(i<n&&(j===m||lcs[(i+1)*width+j]>=lcs[i*width+j+1]))add('removed',before[i++])
    else add('added',after[j++])
  }
  return runs
}

function atoms(nodes:Tree[],path:Tree[]=[]):Atom[] {
  return nodes.flatMap(node=>{
    if(node.type==='text')return Array.from(node.value||'',char=>({key:JSON.stringify(path.map(p=>[p.tagName,p.properties]))+char,char,path}))
    if(node.type!=='element')return []
    if(node.tagName==='a'||node.tagName==='img'||node.tagName==='br'||node.tagName==='code'){
      const cite=node.tagName==='a'&&String(node.properties?.href||'').startsWith('#evidence-')
      return [{key:cite?String(node.properties!.href):signature(node),char:'\ufffc',path,node}]
    }
    return atoms(node.children||[],[...path,{...node,children:[]}])
  })
}

function sentences(nodes:Tree[]):Atom[][] {
  const values=atoms(nodes),units:Atom[][]=[]
  let current:Atom[]=[]
  for(let i=0;i<values.length;i++){
    const item=values[i]
    if(!current.length&&item.node?.tagName==='a'&&units.length){units.at(-1)!.push(item);continue}
    current.push(item)
    const decimal=item.char==='.'&&/\d/.test(values[i-1]?.char||'')&&/\d/.test(values[i+1]?.char||'')
    if(/[。！？!?；;]/.test(item.char)||(item.char==='.'&&!decimal)){units.push(current);current=[]}
  }
  if(current.length)units.push(current)
  return units
}

function renderAtoms(values:Atom[]):Tree[] {
  const result:Tree[]=[]
  let lastPath='',leaf:Tree|undefined
  for(const item of values){
    const path=JSON.stringify(item.path.map(p=>[p.tagName,p.properties]))
    if(!item.node&&leaf&&path===lastPath){leaf.value=(leaf.value||'')+item.char;continue}
    let node=item.node?clone(item.node):{type:'text',value:item.char} as Tree
    leaf=item.node?undefined:node;lastPath=path
    for(const ancestor of [...item.path].reverse())node={...clone(ancestor),children:[node]}
    result.push(node)
  }
  return result
}

function inlineMark(kind:'added'|'removed',values:Atom[]):Tree[] {
  let start=0,end=values.length
  while(start<end&&!values[start].node&&/\s/.test(values[start].char))start++
  while(end>start&&!values[end-1].node&&/\s/.test(values[end-1].char))end--
  if(start===end)return renderAtoms(values)
  const marked=element(kind==='added'?'ins':'del',renderAtoms(values.slice(start,end)),`pm-review-${kind}`)
  if(kind==='removed')marked.properties!['data-review-deletion']='true'
  return [...renderAtoms(values.slice(0,start)),marked,...renderAtoms(values.slice(end))]
}

function inlineChildren(before:Tree[],after:Tree[]):Tree[] {
  const key=(unit:Atom[])=>unit.map(a=>a.key).join('').trim()
  const runs=align(sentences(before),sentences(after),key),result:Tree[]=[]
  for(let i=0;i<runs.length;i++){
    const run=runs[i]
    if(run.kind==='removed'&&run.items.length===1&&runs[i+1]?.kind==='added'&&runs[i+1].items.length===1){
      const old=run.items[0],current=runs[i+1].items[0]
      const words=(values:Atom[])=>{
        const units:Atom[][]=[];let offset=0
        for(const part of new Intl.Segmenter(undefined,{granularity:'word'}).segment(values.map(v=>v.char).join(''))){
          const length=Array.from(part.segment).length
          units.push(values.slice(offset,offset+length));offset+=length
        }
        return units
      }
      const edits=align(words(old),words(current),key)
      const altered=edits.filter(edit=>edit.kind!=='equal')
      const size=(values:Atom[])=>values.filter(value=>!/^\s$/.test(value.char)).length
      const changed=altered.reduce((total,edit)=>total+size(edit.items.flat()),0)
      if(altered.length<=4&&changed<=.5*(size(old)+size(current))){
        for(const edit of edits)result.push(...(edit.kind==='equal'?renderAtoms(edit.items.flat()):inlineMark(edit.kind,edit.items.flat())))
        i++;continue
      }
    }
    const values=run.items.flat()
    result.push(...(run.kind==='equal'?renderAtoms(values):inlineMark(run.kind,values)))
  }
  return result
}

function markBlock(node:Tree,kind:'added'|'removed'):Tree {
  const marked=clone(node)
  if(marked.type==='text'&&marked.value?.trim())return element(kind==='added'?'ins':'del',[marked],`pm-review-${kind}`)
  if(marked.type==='element'){
    const classes=marked.properties?.className
    marked.properties={...marked.properties,className:[...(Array.isArray(classes)?classes:[]),`pm-revision-${kind}-block`]}
    if(kind==='removed')marked.properties['data-review-deletion']='true'
    if(isInline(marked))marked.children=inlineMark(kind,atoms(marked.children||[]))
    else if(marked.tagName!=='pre')marked.children=(marked.children||[]).map(child=>markBlock(child,kind))
  }
  return marked
}

function mergeNode(before:Tree,after:Tree):Tree[] {
  if(before.type==='element'&&after.type==='element'&&before.tagName===after.tagName&&signature({...before,children:[]})===signature({...after,children:[]})){
    const inline=isInline(before)&&isInline(after)
    if(inline)return [{...after,children:inlineChildren(before.children||[],after.children||[])}]
    if(after.tagName!=='pre'&&after.tagName!=='img')return [{...after,children:mergeChildren(before.children||[],after.children||[])}]
  }
  return [markBlock(before,'removed'),markBlock(after,'added')]
}

function mergeChildren(before:Tree[],after:Tree[]):Tree[] {
  const content=(nodes:Tree[])=>nodes.filter(node=>node.type!=='text'||!!node.value?.trim())
  const runs=align(content(before),content(after),signature),result:Tree[]=[]
  for(let i=0;i<runs.length;i++){
    const run=runs[i]
    if(run.kind==='equal')result.push(...run.items)
    else if(run.kind==='removed'&&runs[i+1]?.kind==='added'){
      const added=runs[++i].items
      for(let j=0;j<Math.max(run.items.length,added.length);j++){
        if(run.items[j]&&added[j])result.push(...mergeNode(run.items[j],added[j]))
        else result.push(markBlock(run.items[j]||added[j],run.items[j]?'removed':'added'))
      }
    }else {const kind=run.kind;result.push(...run.items.map(node=>markBlock(node,kind)))}
  }
  return result
}

/** Both sides pass through Markdown's safe AST pipeline; raw source HTML stays disabled. */
export function inlineRevision(options:{before:string}) {
  const old=parser.runSync(parser.parse(options.before)) as Tree
  return (root:unknown)=>{const tree=root as Tree;tree.children=mergeChildren(old.children||[],tree.children||[])}
}
