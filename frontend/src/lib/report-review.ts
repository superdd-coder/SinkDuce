import type { ReportReference, ReportBlock } from './chat-reports.ts'

/** Selection offsets count non-whitespace Unicode code points in the anchored
 * line range. Source ranges are calculated by the server, never trusted input. */
export interface CommentAnchor { kind?: "text" | "block"; start_line: number; end_line: number; quote: string; prefix: string; suffix: string; block_id?: string; block_hash?: string; end_block_id?: string; end_block_hash?: string; selection_start?: number; selection_end?: number; source_ranges?: {start:number;end:number;before:string}[] }
export interface ReportEdit { id: string; before: string; after: string; field?: 'markdown'|'title'; block_id?: string; block_hash?: string; end_block_id?: string; end_block_hash?: string; base_revision?: number; block_ids?: string[]; operation_id?: string; mode?: 'block'|'block_range'|'selection'|'replace_all'; comment_ids?: string[]; match_count?: number }
export interface ReportEditOperation { id:string; mode:'block'|'block_range'|'selection'|'replace_all'; match_count:number; edit_ids:string[] }
export interface ReportComment {
  thread_id?: string
  parent_group_id?: string
  current_text?: string
  deleted?: boolean
  discussion_history?: RevisionSuggestion[]
  batch_id?: string
  id: string; session_id: string; report_id: string; title: string; revision: number; author: string
  text: string; anchor: CommentAnchor; state: 'pending' | 'submitted' | 'resolved'
  created_at: string; updated_at: string; anchor_stale?: boolean
}
export interface RevisionPatch { field:'markdown'|'title'; start:number; end:number; before:string; after:string; rollback?:string; added?:TextMark[]; removed?:TextMark[];display_patches?:{start:number;end:number;before:string;after:string}[] }
export type TextMark = Pick<CommentAnchor,'quote'|'prefix'|'suffix'>
export interface RevisionSuggestion {
  instruction_id?:string
  location_id?:string
  location_quote?:string
  focus?:RevisionPatch
  primary_patch?:number
  id:string;thread_ids:string[];parent_ids:string[];decision:'pending'|'accepted'|'rejected'|'superseded'|'answered'
  base_revision:number;result_revision:number;comments:ReportComment[];results:ReviewOutcome[];summary:string;patches:RevisionPatch[]
  citations:import('./chat-citations').Evidence[]
}
export interface ReviewRequest { report_id: string; revision: number; comment_ids: string[] }
export interface ReviewOutcome {
  comment_id: string; status: 'applied' | 'partial' | 'unchanged' | 'needs_clarification'; summary: string; edit_ids: string[]; change_ids?: string[]
}
export interface ReviewBatch {
  id: string; report_id: string; revision: number; title: string; comments: ReportComment[]
  instruction: string; status?: string; result_revision?: number; results?: ReviewOutcome[]; edits?: ReportEdit[]; operations?: ReportEditOperation[]
}
export interface ReportChange { added?: Pick<CommentAnchor, 'quote'|'prefix'|'suffix'>[]; removed?: Pick<CommentAnchor, 'quote'|'prefix'|'suffix'>[]; id: string; field?: 'markdown'|'title'; kind: 'replace' | 'insert' | 'delete'; old_start: number; old_end: number; new_start: number; new_end: number; before: string; after: string }
export interface CommentFocus { report: ReportReference; comment_id: string; nonce: number; snapshot?: ReportComment }

export function reviewRequests(comments: ReportComment[]): ReviewRequest[] {
  const groups = new Map<string, ReviewRequest>()
  for (const c of comments) if (c.state === 'pending') {
    const key = `${c.report_id}/${c.revision}`
    const group = groups.get(key) || {report_id:c.report_id, revision:c.revision, comment_ids:[]}
    group.comment_ids.push(c.id); groups.set(key, group)
  }
  return [...groups.values()]
}
export function canSendReview(text: string, drafts: { status: string }[], hasComments: boolean) {
  return !drafts.some(d => d.status !== 'ready') && (!!text.trim() || !!drafts.length || hasComments)
}
// eslint-disable-next-line no-control-regex -- Python whitespace also includes the four legacy record separators.
export const normalizeReviewText = (text: string) => text.replace(/[\s\p{White_Space}\u001c-\u001f]+/gu, '')
export function commentAnchor(text: string, quote: string, offset: number, start_line: number, end_line: number): CommentAnchor {
  const selection_start=Array.from(normalizeReviewText(text.slice(0,offset))).length
  return {start_line,end_line,quote,prefix:Array.from(text.slice(0,offset)).slice(-60).join(''),suffix:Array.from(text.slice(offset+quote.length)).slice(0,60).join(''),
    selection_start,selection_end:selection_start+Array.from(normalizeReviewText(quote)).length}
}
export function locateComment(text: string, anchor: Pick<CommentAnchor, 'quote' | 'prefix' | 'suffix' | 'selection_start' | 'selection_end'>) {
  const indices: number[] = [], characters: string[] = []
  let offset=0
  for (const char of text) {if (normalizeReviewText(char)) {indices.push(offset);characters.push(char)};offset+=char.length}
  if(anchor.selection_start!==undefined||anchor.selection_end!==undefined){
    const start=anchor.selection_start,end=anchor.selection_end
    if(typeof start!=='number'||typeof end!=='number'||!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<=start||end>characters.length||characters.slice(start,end).join('')!==normalizeReviewText(anchor.quote))return null
    return {start:indices[start],end:indices[end-1]+characters[end-1].length}
  }
  const normalized=characters.join(''), quote=normalizeReviewText(anchor.quote)
  const prefix=normalizeReviewText(anchor.prefix), suffix=normalizeReviewText(anchor.suffix)
  const needle=prefix+quote+suffix
  if (!needle) return null
  const found=normalized.indexOf(needle)
  if (found<0 || normalized.indexOf(needle,found+1)>=0) return null
  const start=Array.from(normalized.slice(0,found+prefix.length)).length,end=start+Array.from(quote).length
  return {start:indices[start],end:indices[end-1]+characters[end-1].length}
}
export function commentReference(c: ReportComment): ReportReference {
  return {id:c.report_id,session_id:c.session_id,title:c.title,revision:c.revision,updated_at:c.updated_at}
}
export function reviewSnapshots(comments: ReportComment[], instruction: string): ReviewBatch[] {
  return reviewRequests(comments).map(r=>({id:`draft-${r.report_id}`,report_id:r.report_id,revision:r.revision,
    title:comments.find(c=>c.report_id===r.report_id)!.title,instruction,
    comments:comments.filter(c=>r.comment_ids.includes(c.id)).map(c=>({...c,anchor:{...c.anchor}}))}))
}

export interface ReportCommentDraft { anchor: CommentAnchor; text: string; anchor_stale?: boolean }
export function withReportBlocks(anchor: CommentAnchor, blocks?: ReportBlock[]): CommentAnchor {
  const first=blocks?.find(block=>block.start_line<=anchor.start_line&&block.end_line>=anchor.start_line)
  const last=blocks?.find(block=>block.start_line<=anchor.end_line&&block.end_line>=anchor.end_line)
  return first&&last?{...anchor,block_id:first.id,block_hash:first.hash,end_block_id:last.id,end_block_hash:last.hash}:anchor
}
export function relocateReviewDraft(draft: ReportCommentDraft, oldSource: string, newSource: string, oldBlocks?: ReportBlock[], newBlocks?: ReportBlock[]): ReportCommentDraft {
  const fragment=oldSource.split('\n').slice(draft.anchor.start_line-1,draft.anchor.end_line).join('\n')
  if(draft.anchor.block_id){
    const before=oldBlocks?.find(block=>block.id===draft.anchor.block_id)
    const first=newBlocks?.find(block=>block.id===draft.anchor.block_id)
    const last=newBlocks?.find(block=>block.id===(draft.anchor.end_block_id||draft.anchor.block_id))
    if(draft.anchor_stale||!before||!first||!last||first.hash!==draft.anchor.block_hash||last.hash!==(draft.anchor.end_block_hash||draft.anchor.block_hash))return {...draft,anchor_stale:true}
    const shift=first.start_line-before.start_line
    const anchor={...draft.anchor,start_line:draft.anchor.start_line+shift,end_line:draft.anchor.end_line+shift}
    const relocated=newSource.split('\n').slice(anchor.start_line-1,anchor.end_line).join('\n')
    return relocated===fragment?{...draft,anchor_stale:false,anchor}:{...draft,anchor_stale:true}
  }
  const unique=(text:string)=>fragment.length>0&&text.indexOf(fragment)===text.lastIndexOf(fragment)&&text.includes(fragment)
  if(draft.anchor_stale||!unique(oldSource)||!unique(newSource))return {...draft,anchor_stale:true}
  const first=newSource.slice(0,newSource.indexOf(fragment)).split('\n').length
  return {...draft,anchor_stale:false,anchor:{...draft.anchor,start_line:first,end_line:first+draft.anchor.end_line-draft.anchor.start_line}}
}
