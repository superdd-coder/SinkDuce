import { TooltipProvider } from "@/components/ui/tooltip"
import { AppLayout } from "@/components/layout/app-layout"
import { Toaster } from "sonner"

export default function App() {
  return (
    <TooltipProvider>
      <AppLayout />
      <Toaster
        position="top-right"
        toastOptions={{
          style: {
            background: "#fafaf5",
            border: "1px solid rgba(4,120,87,0.45)",
            borderRadius: "4px",
            fontSize: "10px",
            fontStyle: "italic",
            fontWeight: 300,
            fontFamily: "var(--font-sans)",
            color: "var(--color-foreground)",
            boxShadow: "0 0 10px rgba(4,120,87,0.15), 0 0 25px rgba(4,120,87,0.05)",
          },
        }}
      />
    </TooltipProvider>
  )
}
