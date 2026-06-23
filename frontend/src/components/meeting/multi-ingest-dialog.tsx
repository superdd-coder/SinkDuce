import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Progress } from "@/components/ui/progress"
import { Loader2, CheckCircle, SkipForward } from "lucide-react"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { useAppStore } from "@/stores/app-store"
import {
  splitMeetingByProject,
  recommendCollectionsForText,
  allocateMulti,
  deleteAllAllocations,
  type ProjectSplit,
  type CollectionRecommendation,
} from "@/api/client"
import { toast } from "sonner"

interface SelectionState {
  collection: string
  confirmed: boolean
}

interface MultiIngestDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  meetingId: string
  isReingest?: boolean
  allocatedCollections?: string[]
  allocatedFileIds?: string[]
  onComplete: () => void
}

export function MultiIngestDialog({ open, onOpenChange, meetingId, isReingest, allocatedCollections, allocatedFileIds, onComplete }: MultiIngestDialogProps) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [projects, setProjects] = useState<ProjectSplit[]>([])
  const [projectRecs, setProjectRecs] = useState<Record<number, CollectionRecommendation[]>>({})
  const [selections, setSelections] = useState<Record<number, SelectionState>>({})
  const [currentAllocations, setCurrentAllocations] = useState<Record<number, string>>({})
  const [activeTab, setActiveTab] = useState("0")

  // Use store for ingest progress (persists across dialog open/close)
  const { ingestMeetingId, ingestProgress, ingestProjectNames, setIngestState, collections, fetchCollections } = useAppStore()
  const isIngesting = ingestMeetingId === meetingId && Object.values(ingestProgress).some((s) => s === "pending")
  const isDone = ingestMeetingId === meetingId && Object.keys(ingestProgress).length > 0 && Object.values(ingestProgress).every((s) => s === "done")

  const loadData = useCallback(async () => {
    if (!open || !meetingId) return

    // If there's an ongoing ingest for this meeting, show progress instead of reloading
    if (ingestMeetingId === meetingId && Object.keys(ingestProgress).length > 0 && Object.values(ingestProgress).some((s) => s === "pending")) {
      setLoading(false)
      return
    }

    // Clear stale ingest state from previous ingest
    if (ingestMeetingId) {
      setIngestState(null, {}, [])
    }

    setLoading(true)
    setError(null)
    try {
      // Step 1: Split and load collections in parallel
      const [splitRes] = await Promise.all([
        splitMeetingByProject(meetingId),
        fetchCollections(),
      ])
      setProjects(splitRes.projects)

      // Step 2: Recommend per project in parallel (use detail only for matching)
      const projectTexts = splitRes.projects.map((p) => p.detail || "")
      const recResults = await Promise.all(
        projectTexts.map((text) => text ? recommendCollectionsForText(text) : Promise.resolve({ recommendations: [] }))
      )

      // Store per-project recommendations
      const recsMap: Record<number, CollectionRecommendation[]> = {}
      splitRes.projects.forEach((_, i) => { recsMap[i] = recResults[i]?.recommendations ?? [] })
      setProjectRecs(recsMap)

      // Step 3: Pre-select best collection per project (threshold = 0.3)
      // For re-ingest, use current allocations as pre-selection
      const MATCH_THRESHOLD = 0.3
      const initial: Record<number, SelectionState> = {}
      const curAlloc: Record<number, string> = {}
      splitRes.projects.forEach((_, i) => {
        // Check if this project is already allocated to a collection
        let currentCol = ""
        if (isReingest && allocatedCollections && allocatedFileIds) {
          const projName = splitRes.projects[i].name
          const idx = allocatedFileIds.findIndex((fid) => fid.includes(projName))
          if (idx !== -1) {
            currentCol = allocatedCollections[idx] || ""
            curAlloc[i] = currentCol
          }
        }
        // Fall back to recommendation-based pre-selection
        if (!currentCol) {
          const recs = recsMap[i]
          currentCol = recs.length > 0 && recs[0].score >= MATCH_THRESHOLD ? recs[0].collection : ""
        }
        initial[i] = { collection: currentCol, confirmed: false }
      })
      setCurrentAllocations(curAlloc)
      setSelections(initial)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [open, meetingId, isReingest, allocatedCollections, allocatedFileIds])

  useEffect(() => { loadData() }, [loadData])

  const confirmedCount = Object.values(selections).filter((s) => s.confirmed).length

  const setCollection = (index: number, collection: string) => {
    setSelections((prev) => ({
      ...prev,
      [index]: { ...prev[index], collection },
    }))
  }

  const buildContent = (project: ProjectSplit): string => {
    const parts: string[] = []
    if (project.summary) {
      parts.push(project.summary)
    }
    if (project.detail) {
      parts.push(project.detail)
    }
    return parts.join("\n\n")
  }

  const jumpToNextTab = (currentIndex: number, sel?: Record<number, SelectionState>) => {
    const s = sel ?? selections
    const next = projects.findIndex((_, i) => i > currentIndex && !s[i]?.confirmed)
    if (next !== -1) setActiveTab(String(next))
  }

  const handleIngestAll = async () => {
    const confirmed = projects
      .map((p, i) => ({ project: p, index: i, sel: selections[i] }))
      .filter(({ sel }) => sel?.confirmed)

    if (confirmed.length === 0) return

    const progress: Record<number, "pending" | "done" | "error"> = {}
    const names: string[] = []
    confirmed.forEach(({ index, project }) => { progress[index] = "pending"; names[index] = project.name })
    setIngestState(meetingId, progress, names)

    try {
      // If re-ingest, delete old allocations first
      if (isReingest) {
        toast.info("Clearing previous allocations...")
        await deleteAllAllocations(meetingId)
      }

      const allocations = confirmed.map(({ project, sel }) => ({
        collection: sel.collection,
        content: buildContent(project),
        project_name: project.name,
      }))

      await allocateMulti(meetingId, allocations)

      // Mark all as done
      const doneProgress: Record<number, "pending" | "done" | "error"> = {}
      confirmed.forEach(({ index }) => { doneProgress[index] = "done" })
      setIngestState(meetingId, doneProgress, names)

      const collNames = [...new Set(allocations.map((a) => collections.find(c => c.id === a.collection)?.name || a.collection))].join(", ")
      toast.success(`Ingested ${confirmed.length} project(s) to ${collNames}`)

      // Short delay so user sees the success state
      setTimeout(async () => {
        await onComplete()
        setIngestState(null, {}, [])
        onOpenChange(false)
      }, 800)
    } catch (err) {
      toast.error(`Ingest failed: ${err instanceof Error ? err.message : String(err)}`)
      setIngestState(null, {}, [])
    }
  }

  const handleClose = (nextOpen: boolean) => {
    if (!nextOpen) {
      if (isIngesting) {
        toast.info("Ingestion continues in the background...")
      }
      onOpenChange(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="!max-w-[85vw] !w-[85vw] h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Ingest to Projects</DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="flex flex-col items-center justify-center py-12 gap-3">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Analyzing meeting content...</p>
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-12 gap-3">
            <p className="text-sm text-destructive">{error}</p>
            <Button variant="outline" size="sm" onClick={loadData}>Retry</Button>
          </div>
        ) : projects.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2">
            <p className="text-sm text-muted-foreground">No projects detected in meeting content.</p>
          </div>
        ) : (isIngesting || isDone) ? (
          <div className="space-y-3 py-4">
            <p className="text-sm font-medium">{isDone ? "Ingestion complete" : "Ingesting projects..."}</p>
            {Object.entries(ingestProgress).map(([idx, status]) => {
              const i = Number(idx)
              const name = ingestProjectNames[i] || `Project ${i + 1}`
              return (
                <div key={i} className="flex items-center gap-2">
                  {status === "done" ? (
                    <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />
                  ) : status === "error" ? (
                    <span className="h-4 w-4 text-destructive shrink-0">✕</span>
                  ) : (
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground shrink-0" />
                  )}
                  <span className="text-sm truncate flex-1">{name}</span>
                </div>
              )
            })}
            <Progress value={Object.values(ingestProgress).filter((s) => s === "done").length} max={Object.keys(ingestProgress).length} />
          </div>
        ) : (
          <>
            <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 min-h-0 flex flex-col">
              <TabsList variant="line" className="overflow-x-auto shrink-0">
                {projects.map((p, i) => (
                  <TabsTrigger key={i} value={String(i)} className="gap-1.5">
                    {selections[i]?.confirmed && <CheckCircle className="h-3 w-3 text-green-500" />}
                    {p.name}
                  </TabsTrigger>
                ))}
              </TabsList>

              {projects.map((p, i) => (
                <TabsContent key={i} value={String(i)} className="flex-1 min-h-0 flex flex-col gap-3 mt-2">
                  {/* Content preview */}
                  <ScrollArea className="flex-1 min-h-0">
                    <div className="space-y-4 pr-3">
                      {p.summary && (
                        <div>
                          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Summary</p>
                          <MarkdownEditor value={p.summary} readonly />
                        </div>
                      )}
                      {p.detail && (
                        <div>
                          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Detail</p>
                          <MarkdownEditor value={p.detail} readonly />
                        </div>
                      )}
                    </div>
                  </ScrollArea>

                  {/* Project selection */}
                  <div className="flex items-center gap-2">
                    <label className="text-xs font-medium shrink-0">Project:</label>
                    <select
                      className="flex-1 h-8 rounded-md border border-input bg-background px-2 text-sm"
                      value={selections[i]?.collection ?? ""}
                      onChange={(e) => {
                        if (e.target.value === "__CREATE_NEW__") {
                          const { setSidebarView, setPendingCreateCollection } = useAppStore.getState()
                          setPendingCreateCollection(true)
                          setSidebarView("database")
                          return
                        }
                        setCollection(i, e.target.value)
                      }}
                    >
                      <option value="" disabled>Select project...</option>
                      {collections.map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                      <option value="__CREATE_NEW__">+ Create New Project</option>
                    </select>
                    {currentAllocations[i] && (
                      <span className="text-xs text-blue-500 shrink-0">Current: {currentAllocations[i]}</span>
                    )}
                    {!selections[i]?.collection && !currentAllocations[i] && (
                      <span className="text-xs text-orange-500 shrink-0">No match found</span>
                    )}
                    {selections[i]?.collection && projectRecs[i]?.find((r) => r.collection === selections[i]?.collection) && (
                      <span className="text-xs text-muted-foreground shrink-0">
                        Score: {(projectRecs[i].find((r) => r.collection === selections[i]?.collection)!.score * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>

                  {/* Confirm / Skip */}
                  <div className="flex items-center gap-2">
                    <Button
                      variant={selections[i]?.confirmed ? "default" : "outline"}
                      size="sm"
                      onClick={() => {
                        if (!selections[i]?.collection) return
                        setSelections((prev) => {
                          const next = { ...prev, [i]: { ...prev[i], confirmed: true } }
                          // Jump to next unconfirmed tab
                          const nextIdx = projects.findIndex((_, j) => j > i && !next[j]?.confirmed)
                          if (nextIdx !== -1) setActiveTab(String(nextIdx))
                          return next
                        })
                      }}
                      className="flex-1"
                      disabled={!selections[i]?.collection}
                    >
                      <CheckCircle className="h-4 w-4 mr-1.5" />
                      {selections[i]?.confirmed ? "Confirmed" : "Confirm & Ingest"}
                    </Button>
                    <Button
                      variant={selections[i]?.confirmed === false && selections[i]?.collection ? "secondary" : "ghost"}
                      size="sm"
                      onClick={() => {
                        setSelections((prev) => {
                          const next = { ...prev, [i]: { ...prev[i], confirmed: false } }
                          jumpToNextTab(i, next)
                          return next
                        })
                      }}
                    >
                      <SkipForward className="h-4 w-4 mr-1.5" />
                      Skip
                    </Button>
                  </div>
                </TabsContent>
              ))}
            </Tabs>

            {/* Bottom bar */}
            <div className="flex items-center justify-between pt-2 border-t">
              <span className="text-xs text-muted-foreground">
                {confirmedCount} of {projects.length} project{projects.length !== 1 ? "s" : ""} confirmed
              </span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  disabled={confirmedCount === 0}
                  onClick={handleIngestAll}
                >
                  Ingest All Confirmed
                </Button>
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
