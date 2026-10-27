import {Table, TableRow, TableHeader, TableCell} from '@tiptap/extension-table'
import {Markdown} from 'tiptap-markdown'
import {TiptapStarterKit} from '@/components/ui/svg-code-block-extension'
import {createMeetingPriorityExtension,createMeetingRefExtension,type RefClickHandler} from './meeting-summary-marks'

/** One reading schema for rich minutes, retaining source IDs and SVG code. */
export function meetingSummaryExtensions(getOnRefClick:()=>RefClickHandler) {
  return [
    TiptapStarterKit.configure({heading:{levels:[1,2,3]}}),
    Table.configure({resizable:false,renderWrapper:true}),TableRow,TableHeader,TableCell,
    createMeetingRefExtension(getOnRefClick),createMeetingPriorityExtension(),
    Markdown.configure({html:true,tightLists:true,bulletListMarker:'-',linkify:false,transformPastedText:false,transformCopiedText:false}),
  ]
}
