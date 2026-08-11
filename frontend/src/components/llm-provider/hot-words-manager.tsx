import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogKicker,
  DialogTitle,
} from "@/components/ui/dialog"
import { FieldLabel } from "@/components/ui/field-label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Plus, Trash2, BookOpen, Save } from "lucide-react"
import {
  getHotWordsLibraries, getHotWordsLibrary, createHotWordsLibrary,
  updateHotWordsLibrary, deleteHotWordsLibrary,
  type HotWordsLibrary, type HotWordsLibrarySummary, type HotWordItem,
} from "@/api/client"
import { toast } from "sonner"
import { cn } from "@/lib/utils"

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function HotWordsManager({ open, onOpenChange }: Props) {
  const [libraries, setLibraries] = useState<HotWordsLibrarySummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedLib, setSelectedLib] = useState<HotWordsLibrary | null>(null)
  const [isDirty, setIsDirty] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null)

  const fetchList = useCallback(async () => {
    try {
      setLibraries(await getHotWordsLibraries())
    } catch { /* ignore */ }
  }, [])

  const fetchLibrary = useCallback(async (id: string) => {
    try {
      const lib = await getHotWordsLibrary(id)
      setSelectedLib(lib)
      setIsDirty(false)
    } catch { toast.error("Failed to load library") }
  }, [])

  useEffect(() => {
    if (open) fetchList()
  }, [open, fetchList])

  useEffect(() => {
    if (selectedId) fetchLibrary(selectedId)
    else setSelectedLib(null)
  }, [selectedId, fetchLibrary])

  const handleNew = async () => {
    try {
      const lib = await createHotWordsLibrary({ name: "New Library" })
      await fetchList()
      setSelectedId(lib.id)
    } catch { toast.error("Failed to create library") }
  }

  const handleDelete = async (id: string) => {
    try {
      await deleteHotWordsLibrary(id)
      if (selectedId === id) { setSelectedId(null); setSelectedLib(null) }
      setDeleteConfirmId(null)
      await fetchList()
      toast.success("Library deleted")
    } catch { toast.error("Failed to delete") }
  }

  const handleSave = async () => {
    if (!selectedId || !selectedLib) return
    setIsSaving(true)
    try {
      const updated = await updateHotWordsLibrary(selectedId, {
        name: selectedLib.name,
        description: selectedLib.description,
        words: selectedLib.words,
      })
      setSelectedLib(updated)
      setIsDirty(false)
      toast.success("Saved")
      await fetchList()
    } catch { toast.error("Failed to save") }
    finally { setIsSaving(false) }
  }

  const updateField = (field: "name" | "description", value: string) => {
    if (!selectedLib) return
    setSelectedLib({ ...selectedLib, [field]: value })
    setIsDirty(true)
  }

  const updateWord = (index: number, field: keyof HotWordItem, value: string | number) => {
    if (!selectedLib) return
    const words = [...selectedLib.words]
    words[index] = { ...words[index], [field]: value }
    setSelectedLib({ ...selectedLib, words })
    setIsDirty(true)
  }

  const addWord = () => {
    if (!selectedLib) return
    setSelectedLib({
      ...selectedLib,
      words: [...selectedLib.words, { text: "", weight: 4, lang: "" }],
    })
    setIsDirty(true)
  }

  const removeWord = (index: number) => {
    if (!selectedLib) return
    const words = selectedLib.words.filter((_, i) => i !== index)
    setSelectedLib({ ...selectedLib, words })
    setIsDirty(true)
  }

  const handleSwitchLibrary = (id: string) => {
    if (isDirty && selectedId && selectedId !== id) {
      if (!confirm("You have unsaved changes. Discard them?")) return
    }
    setSelectedId(id)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "pm-dialog pm-dialog--silk pm-settings-hw-dialog",
          "sm:max-w-6xl h-[80vh]",
          "!animate-none data-open:!animate-none data-closed:!animate-none",
        )}
        overlayClassName="pm-dialog-overlay--silk"
      >
        <DialogHeader className="shrink-0">
          <DialogKicker>Settings</DialogKicker>
          <DialogTitle>Hot words</DialogTitle>
        </DialogHeader>

        <div className="pm-settings-hw">
          {/* Left rail */}
          <div className="pm-settings-hw-rail">
            <div className="pm-settings-hw-rail-head">
              <span className="pm-label text-[var(--pm-ink)]">Libraries</span>
              <Button variant="ghost" size="icon-sm" onClick={handleNew} title="New library">
                <Plus className="h-4 w-4" />
              </Button>
            </div>
            <ScrollArea className="flex-1 min-h-0">
              <div className="p-2 space-y-0.5">
                {libraries.map((lib) => (
                  <div
                    key={lib.id}
                    role="button"
                    tabIndex={0}
                    className={cn(
                      "pm-settings-hw-lib",
                      selectedId === lib.id && "is-active",
                    )}
                    onClick={() => handleSwitchLibrary(lib.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault()
                        handleSwitchLibrary(lib.id)
                      }
                    }}
                  >
                    <div className="truncate flex-1 min-w-0">
                      <div className="pm-title truncate">{lib.name}</div>
                      <div className="pm-meta">{lib.word_count} words</div>
                    </div>
                    {deleteConfirmId === lib.id ? (
                      <div className="flex items-center gap-0.5 shrink-0 ml-1">
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          className="text-[var(--pm-danger)]"
                          onClick={(e) => { e.stopPropagation(); handleDelete(lib.id) }}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          onClick={(e) => { e.stopPropagation(); setDeleteConfirmId(null) }}
                        >
                          ×
                        </Button>
                      </div>
                    ) : (
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="shrink-0 ml-1 opacity-50 hover:opacity-100"
                        onClick={(e) => { e.stopPropagation(); setDeleteConfirmId(lib.id) }}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                ))}
                {libraries.length === 0 && (
                  <p className="pm-meta p-2 text-center">
                    No libraries yet. Click + to create one.
                  </p>
                )}
              </div>
            </ScrollArea>
          </div>

          {/* Main pane */}
          <div className="pm-settings-hw-main">
            {selectedLib ? (
              <>
                <div className="pm-settings-hw-main-head flex-col !items-stretch space-y-3">
                  <div className="pm-config-field">
                    <FieldLabel>Name</FieldLabel>
                    <Input
                      value={selectedLib.name}
                      onChange={(e) => updateField("name", e.target.value)}
                    />
                  </div>
                  <div className="pm-config-field">
                    <FieldLabel>Description</FieldLabel>
                    <Textarea
                      value={selectedLib.description}
                      onChange={(e) => updateField("description", e.target.value)}
                      className="h-16 resize-none"
                    />
                  </div>
                </div>

                <div className="flex-1 min-h-0 flex flex-col">
                  <div className="pm-settings-hw-words-head">
                    <span className="pm-label text-[var(--pm-ink)]">
                      Words ({selectedLib.words.length})
                    </span>
                    <Button variant="ghost" size="xs" onClick={addWord}>
                      <Plus className="h-3 w-3" />
                      Add word
                    </Button>
                  </div>
                  <ScrollArea className="flex-1 min-h-0">
                    <div className="py-1">
                      {selectedLib.words.map((word, i) => (
                        <div key={i} className="pm-settings-hw-word-row">
                          <Input
                            value={word.text}
                            onChange={(e) => updateWord(i, "text", e.target.value)}
                            placeholder="Hot word"
                            className="flex-1 min-w-0"
                          />
                          <div className="flex items-center gap-1.5 shrink-0">
                            <span className="pm-meta">W</span>
                            <Input
                              type="number"
                              min={1}
                              max={10}
                              value={isNaN(word.weight) ? "" : word.weight}
                              onChange={(e) => {
                                const v = e.target.value
                                if (v === "") { updateWord(i, "weight", NaN); return }
                                const n = parseInt(v)
                                if (!isNaN(n)) updateWord(i, "weight", Math.max(1, Math.min(10, n)))
                              }}
                              onBlur={() => {
                                if (isNaN(word.weight)) updateWord(i, "weight", 4)
                              }}
                              className="w-14 text-center"
                            />
                          </div>
                          <Input
                            value={word.lang || ""}
                            onChange={(e) => updateWord(i, "lang", e.target.value)}
                            placeholder="lang"
                            className="w-16"
                          />
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className="shrink-0 text-[var(--pm-danger)] hover:text-[var(--pm-danger)]"
                            onClick={() => removeWord(i)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      ))}
                      {selectedLib.words.length === 0 && (
                        <p className="pm-meta p-4 text-center">
                          No words. Click “Add word” to add one.
                        </p>
                      )}
                    </div>
                  </ScrollArea>
                </div>

                <div className="pm-settings-hw-foot">
                  <span className="pm-meta">
                    {isDirty ? "Unsaved changes" : "Saved"}
                  </span>
                  <Button size="sm" onClick={handleSave} disabled={!isDirty || isSaving}>
                    <Save className="h-3.5 w-3.5" />
                    {isSaving ? "Saving…" : "Save"}
                  </Button>
                </div>
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center">
                <div className="pm-settings-empty">
                  <BookOpen className="h-8 w-8" />
                  <p className="pm-meta">Select a library or create one</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
