import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { uploadMeetingImage } from "@/api/client"
import { useT } from "@/i18n/use-t"
import type { Editor } from "@tiptap/react"
import type { MeetingNotesStatus } from "@/hooks/use-meeting-notes"

export function MeetingNotesCard({
  meetingId,
  value,
  onChange,
  status,
  placeholder,
  label,
  idleMeta,
  /** Hide the in-card save-status meta (e.g. the rail shows it in the tab bar). */
  hideMeta,
  minHeight = "200px",
  showToolbar,
  onEditorReady,
}: {
  meetingId: string
  value: string
  onChange: (value: string) => void
  status: MeetingNotesStatus
  placeholder: string
  /** Card title. Pass null to hide it entirely (e.g. a tab already names
   * the card) — the save-status meta stays. */
  label?: string | null
  idleMeta?: string
  hideMeta?: boolean
  minHeight?: string
  showToolbar?: boolean
  onEditorReady?: (editor: Editor) => void
}) {
  const t = useT()
  const meta = status === "saved"
    ? (idleMeta ?? t("meeting.notesSaved"))
    : t("meeting.notesSaving")

  return (
    <div className="pm-meeting-f-card">
      <div className="pm-meeting-f-card-h">
        {label === null ? (
          <span />
        ) : (
          <span className="pm-meeting-f-card-label">{label ?? t("common.notes")}</span>
        )}
        {!hideMeta && <span className="pm-meeting-f-card-meta">{meta}</span>}
      </div>
      <div className="pm-meeting-f-card-body pm-meeting-f-notes">
        <MarkdownEditor
          value={value}
          onChange={onChange}
          minHeight={minHeight}
          placeholder={placeholder}
          showToolbar={showToolbar}
          onEditorReady={onEditorReady}
          onImageUpload={async (file) => {
            const result = await uploadMeetingImage(meetingId, file)
            return result.url
          }}
        />
      </div>
    </div>
  )
}
