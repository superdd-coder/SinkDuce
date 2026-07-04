import { useState } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { createCollection } from "@/api/client"
import { toast } from "sonner"
import { ChevronDown } from "lucide-react"

interface CreateCollectionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: () => void
}

const FILE_TYPES = [
  { ext: "pdf", label: "PDF" },
  { ext: "txt", label: "TXT" },
  { ext: "md", label: "Markdown" },
  { ext: "docx", label: "Word" },
  { ext: "xlsx", label: "Excel" },
  { ext: "pptx", label: "PowerPoint" },
  { ext: "csv", label: "CSV" },
]

export function CreateCollectionDialog({ open, onOpenChange, onCreated }: CreateCollectionDialogProps) {
  const [name, setName] = useState("")
  const [dimensions, setDimensions] = useState("1536")
  const [chunkMode, setChunkMode] = useState("normal")
  const [parentStrategy, setParentStrategy] = useState("paragraph")
  const [chunkSize, setChunkSize] = useState("512")
  const [chunkOverlap, setChunkOverlap] = useState("64")
  const [parentChunkSize, setParentChunkSize] = useState("1024")
  const [parentChunkOverlap, setParentChunkOverlap] = useState("128")
  const [childChunkSize, setChildChunkSize] = useState("256")
  const [childChunkOverlap, setChildChunkOverlap] = useState("32")
  const [allowedTypes, setAllowedTypes] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const handleCreate = async () => {
    if (!name.trim()) return
    setSaving(true)
    try {
      const chunkConfig: Record<string, unknown> = {}
      if (chunkMode === "parent_child") {
        chunkConfig.chunk_mode = "parent_child"
        chunkConfig.parent_strategy = parentStrategy
        chunkConfig.parent_chunk_size = parseInt(parentChunkSize) || 1024
        chunkConfig.parent_chunk_overlap = parseInt(parentChunkOverlap) || 128
        chunkConfig.child_chunk_size = parseInt(childChunkSize) || 256
        chunkConfig.child_chunk_overlap = parseInt(childChunkOverlap) || 32
      } else {
        chunkConfig.chunk_size = parseInt(chunkSize) || 512
        chunkConfig.chunk_overlap = parseInt(chunkOverlap) || 64
      }
      if (allowedTypes.length > 0) chunkConfig.allowed_file_types = allowedTypes
      const res = await createCollection(name.trim(), parseInt(dimensions), chunkConfig)
      if (res.error) toast.error(res.error)
      else {
        toast.success(res.message || "Collection created")
        setName("")
        onOpenChange(false)
        onCreated()
      }
    } catch (err) {
      toast.error(`Failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Create Collection</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Name — always visible */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Name</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-collection"
            />
          </div>

          {/* Advance toggle */}
          <button
            type="button"
            className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setAdvancedOpen(!advancedOpen)}
          >
            <ChevronDown className={`h-3 w-3 transition-transform duration-200 ${advancedOpen ? "rotate-0" : "-rotate-90"}`} />
            ADVANCED
          </button>

          {/* Collapsible advanced section */}
          <div className={`grid transition-all duration-500 ease-[cubic-bezier(0.23,1,0.32,1)] ${
            advancedOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
          }`}>
            <div className="overflow-hidden">
              <div className="space-y-4 pt-1">
                <div className="space-y-1.5">
                  <label className="text-sm font-medium">Allowed File Types</label>
                  <p className="text-xs text-muted-foreground">Leave empty to allow all types.</p>
                  <div className="flex flex-wrap gap-2">
                    {FILE_TYPES.map((ft) => (
                      <label
                        key={ft.ext}
                        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border text-xs cursor-pointer transition-colors ${
                          allowedTypes.includes(ft.ext) ? "bg-primary text-primary-foreground border-primary" : "bg-background border-input hover:bg-accent"
                        }`}
                      >
                        <input
                          type="checkbox"
                          className="sr-only"
                          checked={allowedTypes.includes(ft.ext)}
                          onChange={() =>
                            setAllowedTypes((prev) =>
                              prev.includes(ft.ext) ? prev.filter((t) => t !== ft.ext) : [...prev, ft.ext]
                            )
                          }
                        />
                        {ft.label}
                      </label>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-sm font-medium">Dimensions</label>
                    <select
                      className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                      value={dimensions}
                      onChange={(e) => setDimensions(e.target.value)}
                    >
                      {[64, 128, 256, 512, 768, 1024, 1536, 2048, 3072].map((d) => (
                        <option key={d} value={d}>{d}</option>
                      ))}
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-sm font-medium">Chunk Mode</label>
                    <select
                      className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                      value={chunkMode}
                      onChange={(e) => setChunkMode(e.target.value)}
                    >
                      <option value="normal">Normal</option>
                      <option value="parent_child">Parent-Child</option>
                    </select>
                  </div>
                </div>

                {chunkMode === "normal" ? (
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium">Chunk Size</label>
                      <Input value={chunkSize} onChange={(e) => setChunkSize(e.target.value)} placeholder="512" />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium">Chunk Overlap</label>
                      <Input value={chunkOverlap} onChange={(e) => setChunkOverlap(e.target.value)} placeholder="64" />
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium">Parent Strategy</label>
                      <select
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                        value={parentStrategy}
                        onChange={(e) => setParentStrategy(e.target.value)}
                      >
                        <option value="paragraph">Paragraph</option>
                        <option value="fixed_token">Fixed Token</option>
                        <option value="heading">Heading</option>
                      </select>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-1.5">
                        <label className="text-sm font-medium">Parent Chunk Size</label>
                        <Input value={parentChunkSize} onChange={(e) => setParentChunkSize(e.target.value)} placeholder="1024" />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-sm font-medium">Parent Chunk Overlap</label>
                        <Input value={parentChunkOverlap} onChange={(e) => setParentChunkOverlap(e.target.value)} placeholder="128" />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-1.5">
                        <label className="text-sm font-medium">Child Chunk Size</label>
                        <Input value={childChunkSize} onChange={(e) => setChildChunkSize(e.target.value)} placeholder="256" />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-sm font-medium">Child Chunk Overlap</label>
                        <Input value={childChunkOverlap} onChange={(e) => setChildChunkOverlap(e.target.value)} placeholder="32" />
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleCreate} disabled={saving || !name.trim()}>
            {saving ? "Creating..." : "Create"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
