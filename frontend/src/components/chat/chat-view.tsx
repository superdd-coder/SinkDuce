import { useEffect, useRef, useState } from "react"
import { useAppStore } from "@/stores/app-store"
import { MessageBubble } from "./message-bubble"
import { ChatInput } from "./chat-input"
import { SourceDetailPanel } from "./source-detail-panel"
import { PanelRightClose } from "lucide-react"
import { Button } from "@/components/ui/button"
import { getLLMProviders } from "@/api/client"
import type { Source } from "@/stores/app-store"

export function ChatView() {
  const { messages, setProviders, setActiveProvider, setActiveModel, activeProvider, activeModel } = useAppStore()
  const bottomRef = useRef<HTMLDivElement>(null)
  const [selectedSource, setSelectedSource] = useState<Source | null>(null)

  useEffect(() => {
    const loadProviders = async () => {
      try {
        const list = await getLLMProviders()
        setProviders(list)
        if (!activeProvider) {
          const defaultP = list.find((p) => p.is_default) || list[0]
          if (defaultP) {
            setActiveProvider(defaultP.id)
            if (!activeModel) {
              setActiveModel(defaultP.default_model || defaultP.selected_models?.[0] || defaultP.model || null)
            }
          }
        }
      } catch {
        // ignore
      }
    }
    loadProviders()
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const handleSelectSource = (source: Source) => {
    setSelectedSource(source)
  }

  const handleClosePanel = () => {
    setSelectedSource(null)
  }

  const selectedSourceId = (selectedSource?.metadata?.id as string) || null

  return (
    <div className="flex flex-col h-full overflow-hidden relative">
      <div className="flex-1 flex min-h-0">
        {/* Main chat area */}
        <div className={`flex flex-col flex-1 min-w-0 ${selectedSource ? "hidden sm:flex" : ""}`}>
          <div className="flex-1 overflow-y-auto pb-44">
            {messages.length === 0 ? (
              <div
                className="flex flex-col items-center justify-center h-full gap-2 py-20"
                style={{ color: "var(--ze-muted)" }}
              >
                <p
                  className="text-sm font-medium"
                  style={{ color: "var(--ze-ink)", fontFamily: "var(--font-serif)" }}
                >
                  Ask a question about your documents
                </p>
                <p className="text-xs">Upload documents first, then start chatting</p>
              </div>
            ) : (
              <div className="max-w-3xl mx-auto py-4 px-12">
                {messages.map((msg) => (
                  <MessageBubble
                    key={msg.id}
                    message={msg}
                    onSelectSource={handleSelectSource}
                    selectedSourceId={selectedSourceId}
                  />
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>
        </div>

        {/* Right-side source detail panel */}
        <div className={`shrink-0 overflow-hidden transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${selectedSource ? "w-full sm:w-[42vw]" : "w-0"}`}>
          <div className={`w-full sm:w-[42vw] h-full transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${selectedSource ? "translate-x-0 opacity-100" : "translate-x-8 opacity-0"}`}>
            <div className="sm:hidden absolute top-0 right-0 z-10 p-2">
              <Button variant="ghost" size="sm" onClick={handleClosePanel}>
                <PanelRightClose className="h-4 w-4 mr-1" /> Back to chat
              </Button>
            </div>
            <SourceDetailPanel source={selectedSource} onClose={handleClosePanel} />
          </div>
        </div>
      </div>

      {/* Floating chat input — overlays the message area */}
      <div className={`absolute bottom-4 left-0 z-10 pointer-events-none transition-all duration-700 ease-[cubic-bezier(0.23,1,0.32,1)] ${selectedSource ? "right-[42vw] max-sm:hidden" : "right-0"}`}>
        <div className="pointer-events-auto">
          <ChatInput />
        </div>
      </div>
    </div>
  )
}
