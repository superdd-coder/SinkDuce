import { locateComment, type CommentAnchor } from './report-review.ts'

export type TextPassage = Pick<CommentAnchor, 'quote' | 'prefix' | 'suffix' | 'selection_start' | 'selection_end'> & Partial<Pick<CommentAnchor,'start_line'|'end_line'>>
/** Preserve React-owned text nodes and keep citation controls outside the anchor. */
export function passageRange(block: HTMLElement, anchor: TextPassage): Range | null {
  const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT,{acceptNode:node=>{
    if(node.parentElement?.closest('button,svg,[aria-hidden="true"],[hidden],[data-review-deletion]'))return NodeFilter.FILTER_REJECT
    if(anchor.start_line!==undefined&&anchor.end_line!==undefined){
      const owner=node.parentElement?.closest<HTMLElement>('[data-report-line]')
      if(!owner||Number(owner.dataset.reportLine)<anchor.start_line||Number(owner.dataset.reportEnd)>anchor.end_line)return NodeFilter.FILTER_REJECT
    }
    return NodeFilter.FILTER_ACCEPT
  }})
  const nodes:Text[]=[]
  let node:Node|null
  while((node=walker.nextNode()))nodes.push(node as Text)
  const hit=locateComment(nodes.map(node=>node.data).join(''),anchor)
  if(!hit)return null
  const range=document.createRange()
  let offset=0
  for(const node of nodes){
    if(hit.start>=offset&&hit.start<offset+node.length)range.setStart(node,hit.start-offset)
    if(hit.end>offset&&hit.end<=offset+node.length)range.setEnd(node,hit.end-offset)
    offset+=node.length
  }
  return range
}
/** A separate name per report prevents two open report surfaces from sharing marks. */
export function installHighlights(name:string,ranges:Range[],kind:'comment'|'added'|'removed'|'focus') {
  const api=(globalThis.CSS as unknown as {highlights?:Map<string,unknown>})?.highlights
  const HighlightClass=(globalThis as unknown as {Highlight?:new(...ranges:Range[])=>unknown}).Highlight
  if(!api||!HighlightClass)return ()=>{}
  api.set(name,new HighlightClass(...ranges))
  const style=document.createElement('style')
  const paint=kind==='focus'?'background:#1a5e3d36;color:inherit':kind==='removed'?'background:#a0685b18;text-decoration:line-through;text-decoration-color:#a0685b':
    kind==='added'?'background:#1a5e3d20;text-decoration:underline;text-decoration-color:#1a5e3d70':
      'background:#1a5e3d18;text-decoration:underline;text-decoration-color:#1a5e3d55'
  style.textContent=`::highlight(${name}){${paint}}`
  document.head.append(style)
  return ()=>{api.delete(name);style.remove()}
}

export interface ReviewPassageTarget {anchor:CommentAnchor;groupId?:string;targetLine?:number;stale?:boolean}

/** Use the same source anchor for navigation, rail placement and focused paint. */
export function reviewPassageTarget(root:HTMLElement,target:ReviewPassageTarget):HTMLElement|null {
  if(target.groupId){
    const escaped=target.groupId.replace(/["\\]/g,'\\$&')
    const region=root.querySelector<HTMLElement>(`[data-review-primary~="${escaped}"]`)||root.querySelector<HTMLElement>(`[data-review-group~="${escaped}"]`)
    if(region){
      if(target.targetLine!==undefined){
        const local=[...region.querySelectorAll<HTMLElement>('[data-report-line]')].filter(el=>Number(el.dataset.reportLine)<=target.targetLine!&&Number(el.dataset.reportEnd)>=target.targetLine!)
        if(local.length)return local.at(-1)!
      }
      return region
    }
  }
  if(target.stale&&target.targetLine===undefined)return null
  const line=target.targetLine??target.anchor.start_line
  const blocks=[...root.querySelectorAll<HTMLElement>('[data-report-line]')]
  return blocks.find(el=>Number(el.dataset.reportLine)<=line&&Number(el.dataset.reportEnd)>=line)
    ||(target.targetLine!==undefined?(blocks.find(el=>Number(el.dataset.reportLine)>=line)||blocks.at(-1)):null)||null
}

/** Paint only; React-owned text and the user's new selection remain untouched. */
export function focusReviewPassage(root:HTMLElement,target:ReviewPassageTarget,name='report-focus') {
  const block=reviewPassageTarget(root,target)
  if(!block)return ()=>{}
  block.setAttribute('data-review-focused',target.groupId?'change':'passage')
  const range=!target.groupId&&!target.stale&&target.anchor.kind!=='block'?passageRange(root,target.anchor):null
  const clear=range?installHighlights(name,[range],'focus'):()=>{}
  return ()=>{block.removeAttribute('data-review-focused');clear()}
}
