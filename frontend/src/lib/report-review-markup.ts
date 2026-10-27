import {locateComment,type TextMark} from './report-review.ts'
interface Tree {type:string;value?:string;tagName?:string;properties?:Record<string,unknown>;children?:Tree[]}
/** Only generated HAST nodes are inserted; source HTML remains disabled. */
export function reviewMarkup(options:{kind:'added'|'removed';passages:TextMark[];all?:boolean}) {
  return (root:unknown)=>{
    const leaves:{node:Tree;parent:Tree;start:number;end:number}[]=[]
    let text=''
    const walk=(node:Tree)=>{
      if(node.type==='element'&&(['pre','code','svg','button'].includes(node.tagName||'')||(node.tagName==='a'&&String(node.properties?.href||'').startsWith('#evidence-'))))return
      for(const child of node.children||[]){
        if(child.type==='text'){const start=text.length;text+=child.value||'';leaves.push({node:child,parent:node,start,end:text.length})}
        else walk(child)
      }
    }
    walk(root as Tree)
    const ranges=options.all?[{start:0,end:text.length}]:options.passages.flatMap(p=>{const found=locateComment(text,p);return found?[found]:[]}).sort((a,b)=>a.start-b.start)
    const merged:{start:number;end:number}[]=[]
    for(const r of ranges){const last=merged.at(-1);if(last&&r.start<=last.end)last.end=Math.max(last.end,r.end);else merged.push({...r})}
    for(const leaf of leaves){
      const hits=merged.filter(r=>r.start<leaf.end&&r.end>leaf.start)
      if(!hits.length)continue
      const value=leaf.node.value||'',replacement:Tree[]=[]
      let at=0
      for(const r of hits){
        const start=Math.max(0,r.start-leaf.start),end=Math.min(value.length,r.end-leaf.start)
        if(start>at)replacement.push({type:'text',value:value.slice(at,start)})
        replacement.push({type:'element',tagName:options.kind==='removed'?'del':'ins',properties:{className:[`pm-review-${options.kind}`]},children:[{type:'text',value:value.slice(start,end)}]});at=end
      }
      if(at<value.length)replacement.push({type:'text',value:value.slice(at)})
      const siblings=leaf.parent.children!;siblings.splice(siblings.indexOf(leaf.node),1,...replacement)
    }
  }
}
