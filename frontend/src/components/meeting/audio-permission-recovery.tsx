import { useState } from "react"
import { toast } from "sonner"
import { getDesktopAudioPermissions, openDesktopAudioSettings, type DesktopAudioPermissions, type DesktopAudioSettingsTarget } from "@/api/client"
import { desktopShellAvailable, restartDesktopApp } from "@/lib/desktop-restart"
import { canRestartDesktopAudio } from "@/lib/desktop-audio-errors"
import { useT } from "@/i18n/use-t"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"

export function AudioPermissionRecovery({ errorCode, message, active, onRetry }: {
  errorCode: string | null
  message: string | null
  active: boolean
  onRetry: () => Promise<void>
}) {
  const t = useT()
  const [open, setOpen] = useState(true)
  const [busy, setBusy] = useState(false)
  const [permissions, setPermissions] = useState<DesktopAudioPermissions | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const check = async () => {
    setBusy(true)
    try {
      const status = await getDesktopAudioPermissions()
      setPermissions(status)
      setStatusError(null)
      return status
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : String(err))
      return null
    } finally { setBusy(false) }
  }
  const settings = async (target: DesktopAudioSettingsTarget) => {
    try { await openDesktopAudioSettings(target) }
    catch (err) { toast.error(err instanceof Error ? err.message : String(err)) }
  }
  const restart = async () => {
    const status = await check()
    // A different window may have started capture while this dialog was open.
    if (!canRestartDesktopAudio(active, status)) return
    await restartDesktopApp().catch(() => undefined)
  }

  return <Dialog open={open && !active} onOpenChange={setOpen}>
    <DialogContent className="pm-dialog pm-dialog--silk sm:max-w-[460px]">
      <DialogHeader>
        <DialogTitle>{t("meeting.audioRecoveryTitle")}</DialogTitle>
        <DialogDescription>{t(errorCode === "helper_unavailable" || errorCode === "capture_start_failed" ? "meeting.audioHelperRecoveryBody" : "meeting.audioRecoveryBody")}</DialogDescription>
      </DialogHeader>
      <p className="text-sm text-muted-foreground" role="alert">{message}</p>
      {permissions && <div className="space-y-1 text-sm" aria-live="polite">
        <p>{t("meeting.audioMicrophone")}: {t(`meeting.audioPermission_${permissions.microphone}`)}</p>
        <p>{t("meeting.audioSystemRecording")}: {t(permissions.screen_recording ? "meeting.audioPermission_authorized" : "meeting.audioPermission_denied")}</p>
        {permissions.capturing && <p>{t("meeting.recordingInProgress")}</p>}
      </div>}
      {statusError && <p className="text-sm text-muted-foreground" role="status">{statusError}</p>}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => void settings("microphone")}>{t("meeting.audioOpenMicrophone")}</Button>
        <Button variant="outline" size="sm" onClick={() => void settings("screen_recording")}>{t("meeting.audioOpenSystemRecording")}</Button>
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => void check()}>{t("meeting.audioCheckPermissions")}</Button>
      </div>
      <DialogFooter>
        {desktopShellAvailable() && <Button variant="ghost" size="sm" disabled={busy || permissions?.capturing} onClick={() => void restart()}>{t("meeting.audioRestartApp")}</Button>}
        <Button size="sm" disabled={busy || permissions?.capturing} onClick={() => { setOpen(false); void onRetry() }}>{t("meeting.audioRetryCapture")}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
