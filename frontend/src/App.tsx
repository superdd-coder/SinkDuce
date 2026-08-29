import { TooltipProvider } from "@/components/ui/tooltip"
import { AppLayout } from "@/components/layout/app-layout"
import { WebSearchConfirmDialog } from "@/components/chat/web-search-confirm-dialog"
import { TodoDeleteConfirmDialog } from "@/components/chat/todo-delete-confirm-dialog"
import { useDesktopExternalLinks } from "@/hooks/use-desktop-external-links"
import { Toaster } from "sonner"

export default function App() {
  useDesktopExternalLinks()
  return (
    <TooltipProvider>
      <AppLayout />
      {/* Global HITL bar (portaled to body) — must stay mounted for SSE confirm */}
      <WebSearchConfirmDialog />
      <TodoDeleteConfirmDialog />
      <Toaster
        position="top-right"
        offset={18}
        gap={8}
        visibleToasts={4}
        duration={3600}
        toastOptions={{
          className: "pm-toast",
          classNames: {
            toast: "pm-toast",
            title: "pm-toast-title",
            description: "pm-toast-desc",
            actionButton: "pm-toast-action",
            cancelButton: "pm-toast-cancel",
            closeButton: "pm-toast-close",
            success: "pm-toast--success",
            error: "pm-toast--error",
            warning: "pm-toast--warning",
            info: "pm-toast--info",
            loading: "pm-toast--loading",
          },
        }}
      />
    </TooltipProvider>
  )
}
