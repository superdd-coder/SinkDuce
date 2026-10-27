import { HelpHint } from "@/components/shared/help-hint"
import { Button } from "@/components/ui/button"
import { useT } from "@/i18n/use-t"

export function PermissionEntry({ allowed, onOpen }: { allowed: boolean; onOpen: () => void }) {
  const t = useT()
  if (!allowed) return null
  return <section className="pm-settings-section">
    <div className="pm-settings-card">
      <div className="pm-settings-card-head">
        <div className="pm-settings-card-head-text min-w-0">
          <h2 className="pm-settings-card-kicker">{t("permissions.title")}<HelpHint label={t("permissions.title")}>{t("permissions.cardDesc")}</HelpHint></h2>
        </div>
        <Button variant="default" size="sm" onClick={onOpen}>{t("permissions.manage")}</Button>
      </div>
    </div>
  </section>
}
