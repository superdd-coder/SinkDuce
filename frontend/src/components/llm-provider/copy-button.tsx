/** 复制按钮（配对卡片/弹窗共用）。 */
import { useState } from "react"
import { Check, Copy } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { copyText } from "@/lib/clipboard"
import { useT } from "@/i18n/use-t"

export function CopyBtn({ text, label }: { text: string; label: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    try {
      await copyText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      toast.error(t("settings.couldNotCopy", { label }))
    }
  }
  return (
    <Button type="button" variant="ghost" size="sm" onClick={() => { void handleCopy() }}>
      {copied ? (
        <>
          <Check className="h-3.5 w-3.5" strokeWidth={2} />
          {t("common.copied")}
        </>
      ) : (
        <>
          <Copy className="h-3.5 w-3.5" strokeWidth={1.75} />
          {t("common.copy")}
        </>
      )}
    </Button>
  )
}
