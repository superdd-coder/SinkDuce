import { AudioLines, FileText, Globe, Image, NotebookPen, Presentation, Table2 } from 'lucide-react'
import { documentKind } from '@/lib/chat-document-display'
import './document-mark.css'

/** Quiet file identity shared by the output card and conversation inventory. */
export function DocumentMark({filename='',kind,large=false}: {filename?:string;kind?:string;large?:boolean}) {
  const resolved = documentKind(filename, kind)
  const Icon = resolved === 'image' ? Image : resolved === 'note' ? NotebookPen : resolved === 'sheet' ? Table2
    : resolved === 'slides' ? Presentation : resolved === 'web' ? Globe : resolved === 'meeting' ? AudioLines : FileText
  return <span className={`pm-document-mark${large?' is-large':''}`} data-kind={resolved} aria-hidden="true">
    <Icon className="pm-document-mark-symbol" strokeWidth={1.5}/>
  </span>
}
