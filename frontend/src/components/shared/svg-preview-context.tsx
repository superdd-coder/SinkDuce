import { createContext, useCallback, useContext, useMemo, useState, type ReactNode, type RefObject } from "react"
import { SvgPreviewDialog } from "@/components/chat/svg-preview-dialog"

type Preview = { source: string; triggerRef: RefObject<HTMLButtonElement | null> }
type PreviewHost = {
  preview: Preview | null
  open: (source: string, triggerRef: Preview["triggerRef"]) => void
}

const SvgPreviewContext = createContext<PreviewHost | null>(null)
export const useSvgPreviewHost = () => useContext(SvgPreviewContext)

/** Own the popup outside editor node views and their mutable DOM lifecycle. */
export function SvgPreviewProvider({ children }: { children: ReactNode }) {
  const [preview, setPreview] = useState<Preview | null>(null)
  const open = useCallback((source: string, triggerRef: Preview["triggerRef"]) => {
    setPreview({ source, triggerRef })
  }, [])
  const value = useMemo(() => ({ preview, open }), [preview, open])
  return (
    <SvgPreviewContext.Provider value={value}>
      {children}
      {preview && <SvgPreviewDialog {...preview} onClose={() => setPreview(null)} />}
    </SvgPreviewContext.Provider>
  )
}
