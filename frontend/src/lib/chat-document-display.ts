import { isChatAttachmentImage } from './chat-attachments'

type DocumentKind = 'report' | 'pdf' | 'word' | 'sheet' | 'slides' | 'image' | 'note' | 'web' | 'meeting' | 'text' | 'file'

export function documentKind(filename: string, sourceKind?: string): DocumentKind {
  if (sourceKind === 'report' || sourceKind === 'note' || sourceKind === 'web' || sourceKind === 'meeting') return sourceKind
  if (isChatAttachmentImage(filename) || /\.svg$/i.test(filename)) return 'image'
  const extension = filename.split('.').pop()?.toLowerCase()
  if (extension === 'pdf') return 'pdf'
  if (extension === 'doc' || extension === 'docx') return 'word'
  if (['xlsx', 'xls', 'csv', 'tsv'].includes(extension || '')) return 'sheet'
  if (extension === 'ppt' || extension === 'pptx') return 'slides'
  if (['md', 'txt', 'json', 'html'].includes(extension || '')) return 'text'
  return 'file'
}

export function documentLabel(filename: string, sourceKind?: string): string {
  const kind = documentKind(filename, sourceKind)
  const labels: Record<DocumentKind, string> = {
    report: 'DOC', pdf: 'PDF', word: 'Word', sheet: 'Excel', slides: 'PPT',
    image: 'IMG', note: 'Notes', web: 'Web', meeting: 'Audio', text: 'TXT', file: 'DOC',
  }
  return ['text', 'word', 'sheet', 'slides'].includes(kind)
    ? filename.split('.').pop()?.toUpperCase() || labels[kind]
    : labels[kind]
}

export function documentDate(value: string, locale?: string): string | null {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : new Intl.DateTimeFormat(locale || undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}
