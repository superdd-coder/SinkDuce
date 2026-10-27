import type { Evidence } from './chat-citations.ts'

type RetrievedSource = { text: string; metadata?: Record<string, unknown> }
const locationKeys = ['page_number', 'slide_number', 'paragraph_index', 'sheet_name', 'char_offset', 'heading_path', 'line', 'sentence_id', 'start_time', 'end_time', 'image_index']

function canonicalSource(meta: Record<string, unknown>): string {
  const source = String(meta.source || '').trim()
  if (source.startsWith('file:') && !source.startsWith('file://')) return `__file__:${source.slice(5)}`
  return source || (meta.file_id ? `__file__:${meta.file_id}` : '')
}

/** Retrieval rows and sentence citations share the same chunk identity, never just a filename. */
export function sourceEvidence(row: RetrievedSource, index: number, records: Evidence[] = []): Evidence {
  const meta = row.metadata || {}
  const source = canonicalSource(meta)
  const fileId = String(meta.file_id || (source.startsWith('__file__:') ? source.slice(9) : ''))
  const chunkId = String(meta.chunk_id || meta.id || '')
  const saved = records.find(record => {
    const origin = record.source
    if (meta.collection && origin.collection && meta.collection !== origin.collection) return false
    if (meta.version_id && origin.version_id !== meta.version_id) return false
    if (fileId && origin.file_id && fileId !== origin.file_id) return false
    if (chunkId && origin.chunk_id && chunkId !== origin.chunk_id) return false
    if (meta.meeting_id && origin.meeting_id && meta.meeting_id !== origin.meeting_id) return false
    if (['char_offset', 'page_number', 'slide_number', 'sentence_id'].some(key =>
      meta[key] != null && record.location[key] != null && meta[key] !== record.location[key])) return false
    const sameChunk = chunkId && origin.chunk_id === chunkId
    const sameSentence = meta.meeting_id && origin.meeting_id === meta.meeting_id && meta.sentence_id && record.location.sentence_id === meta.sentence_id
    const samePassage = row.text && record.quote === row.text && (fileId && origin.file_id === fileId || source && canonicalSource(origin) === source)
    return !!(sameChunk || sameSentence || samePassage)
  })
  if (saved) return saved
  const kind = meta.attachment_id ? 'attachment' : meta.meeting_id || meta.source_type === 'meeting' ? 'meeting'
    : meta.url || meta.source_type === 'web' ? 'web' : source.startsWith('__note__:') ? 'note' : 'document'
  const rawTitle = String(meta.source_label || meta.display_name || meta.filename || meta.title || meta.url || source || '')
  const title = /^(?:__file__:|file:)/.test(rawTitle) || /^[a-f0-9]{32,64}$/i.test(rawTitle) ? 'Source' : rawTitle.split('/').pop() || 'Source'
  const location = Object.fromEntries(locationKeys.filter(key => meta[key] != null).map(key => [key, meta[key]]))
  return {
    id: `retrieved:${JSON.stringify([meta.collection || meta.collection_name || '', fileId || meta.meeting_id || meta.url || source, meta.version_id || '', chunkId || index, location])}`,
    kind, title, quote: row.text,
    source: { ...meta, ...(source && {source}), ...(fileId && {file_id:fileId}), ...(chunkId && {chunk_id:chunkId}) },
    location,
  }
}

/** Only collection-backed sources have a managed file-detail dialog. */
export function citationFileTarget(evidence: Evidence, collections: {id:string;name:string}[]) {
  const meta = evidence.source
  const collection = String(meta.collection || meta.collection_name || '').trim()
  if (!collection || meta.attachment_id || evidence.kind === 'attachment' || evidence.kind === 'web') return null
  const source = canonicalSource(meta)
  const fileId = String(meta.file_id || (source.startsWith('__file__:') ? source.slice(9) : '')) || undefined
  if (!source && !fileId) return null
  return {
    collectionId: collections.find(item => item.id === collection || item.name === collection)?.id || collection,
    fileId, source,
    versionId: typeof meta.version_id === 'string' ? meta.version_id : undefined,
  }
}
