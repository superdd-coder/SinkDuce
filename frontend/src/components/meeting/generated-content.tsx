import { useState, useEffect, useMemo, useRef, useLayoutEffect } from "react"
import { Button } from "@/components/ui/button"
import { MarkdownEditor } from "@/components/ui/markdown-editor"
import { Pencil, X, Save, Loader2, Search } from "lucide-react"
import type { TodoItem } from "@/api/client"

/** Normalize Milkdown-style markdown to strict CommonMark before saving to API */
export function normalizeMd(md: string): string {
  return md
    .replace(/\*\*\s+([^*]+?)\s*\*\*/g, "**$1**")
    .replace(/(?<!\*)\*(?!\*)\s+([^*]+?)\s*(?<!\*)\*(?!\*)/g, "*$1*")
    .replace(/`([^`]+?)`/g, (_, inner: string) => "`" + inner.trim() + "`")
    .replace(/\[([^\]]+)\]\(\s+([^)]+?)\s*\)/g, "[$1]($2)")
    .replace(/^(#{1,6})\s{2,}/gm, "$1 ")
}

function highlightTextNodes(el: HTMLElement, query: string) {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  while (walker.nextNode()) nodes.push(walker.currentNode as Text)
  for (const node of nodes) {
    const text = node.textContent || ""
    if (!text.toLowerCase().includes(query)) continue
    const fragment = document.createDocumentFragment()
    let last = 0
    const re = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi")
    let match: RegExpExecArray | null
    while ((match = re.exec(text)) !== null) {
      if (match.index > last) fragment.appendChild(document.createTextNode(text.slice(last, match.index)))
      const mark = document.createElement("mark")
      mark.className = "bg-yellow-200 dark:bg-yellow-800 rounded px-0.5"
      mark.textContent = match[0]
      fragment.appendChild(mark)
      last = match.index + match[0].length
    }
    if (last < text.length) fragment.appendChild(document.createTextNode(text.slice(last)))
    node.parentNode?.replaceChild(fragment, node)
  }
}

interface GeneratedContentProps {
  tab: "detail" | "summary" | "todo"
  content: string | null
  todos?: TodoItem[] | null
  loading: boolean
  onSave: (data: { summary?: string; detail?: string; todos?: TodoItem[] }) => void
}

export function GeneratedContent({ tab, content, todos, loading, onSave }: GeneratedContentProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState("")
  const [todoDraft, setTodoDraft] = useState<TodoItem[]>([])

  useEffect(() => {
    if (tab === "detail" || tab === "summary") {
      setDraft(content ?? "")
    }
    if (tab === "todo") {
      setTodoDraft(todos ? [...todos] : [])
    }
    setEditing(false)
  }, [content, todos, tab])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin mr-2" />
        Generating...
      </div>
    )
  }

  if (tab === "detail") {
    return <DetailWithSearch content={content} onSave={onSave} />
  }

  if (tab === "summary") {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-end">
          {!editing && content && (
            <Button variant="ghost" size="sm" onClick={() => { setDraft(content); setEditing(true) }}>
              <Pencil className="h-3 w-3 mr-1" /> Edit
            </Button>
          )}
        </div>
        {editing ? (
          <MarkdownEditor
            value={draft}
            onChange={setDraft}
            minHeight="250px"
            placeholder="Write summary in Markdown..."
          >
            <Button size="sm" onClick={() => { onSave({ summary: draft }); setEditing(false) }}>
              <Save className="h-3 w-3 mr-1" /> Save
            </Button>
            <Button variant="outline" size="sm" onClick={() => setEditing(false)}>
              <X className="h-3 w-3 mr-1" /> Cancel
            </Button>
          </MarkdownEditor>
        ) : (
          content ? (
            <MarkdownEditor value={content} readonly />
          ) : (
            <span className="text-muted-foreground">No summary generated yet.</span>
          )
        )}
      </div>
    )
  }

  // TODO tab
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end">
        {!editing && todos && todos.length > 0 && (
          <Button variant="ghost" size="sm" onClick={() => { setTodoDraft([...todos]); setEditing(true) }}>
            <Pencil className="h-3 w-3 mr-1" /> Edit
          </Button>
        )}
      </div>
      {editing ? (
        <>
          {todoDraft.map((item, i) => (
            <div key={i} className="flex gap-2 items-start">
              <input
                type="checkbox"
                className="mt-1.5 rounded"
                checked={false}
                readOnly
              />
              <div className="flex-1 space-y-1">
                <input
                  className="w-full text-sm border rounded px-2 py-1 bg-background"
                  value={item.text}
                  onChange={(e) => {
                    const next = [...todoDraft]
                    next[i] = { ...next[i], text: e.target.value }
                    setTodoDraft(next)
                  }}
                  placeholder="Task description"
                />
                <div className="flex gap-2">
                  <input
                    className="text-xs border rounded px-2 py-0.5 bg-background w-32"
                    value={item.assignee ?? ""}
                    onChange={(e) => {
                      const next = [...todoDraft]
                      next[i] = { ...next[i], assignee: e.target.value || undefined }
                      setTodoDraft(next)
                    }}
                    placeholder="Assignee"
                  />
                  <select
                    className="text-xs border rounded px-2 py-0.5 bg-background"
                    value={item.priority ?? "medium"}
                    onChange={(e) => {
                      const next = [...todoDraft]
                      next[i] = { ...next[i], priority: e.target.value }
                      setTodoDraft(next)
                    }}
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-destructive"
                    onClick={() => setTodoDraft(todoDraft.filter((_, j) => j !== i))}
                  >
                    <X className="h-3 w-3" />
                  </Button>
                </div>
              </div>
            </div>
          ))}
          <Button variant="outline" size="sm" onClick={() => setTodoDraft([...todoDraft, { text: "", priority: "medium" }])}>
            + Add item
          </Button>
          <div className="flex gap-2 pt-2">
            <Button size="sm" onClick={() => { onSave({ todos: todoDraft }); setEditing(false) }}>
              <Save className="h-3 w-3 mr-1" /> Save
            </Button>
            <Button variant="outline" size="sm" onClick={() => setEditing(false)}>
              <X className="h-3 w-3 mr-1" /> Cancel
            </Button>
          </div>
        </>
      ) : (
        <div className="space-y-1">
          {todos && todos.length > 0 ? (
            todos.map((item, i) => (
              <div key={i} className="flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-1 rounded" checked={false} readOnly />
                <div>
                  <span>{item.text}</span>
                  {item.assignee && <span className="text-xs text-muted-foreground ml-2">@{item.assignee}</span>}
                  {item.priority && item.priority !== "medium" && (
                    <span className="text-xs text-muted-foreground ml-1">[{item.priority}]</span>
                  )}
                </div>
              </div>
            ))
          ) : (
            <span className="text-muted-foreground text-sm">No TODO items generated yet.</span>
          )}
        </div>
      )}
    </div>
  )
}

function DetailWithSearch({ content, onSave }: { content: string | null; onSave?: (data: { summary?: string; detail?: string; todos?: TodoItem[] }) => void }) {
  const [search, setSearch] = useState("")
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState("")
  const editorWrapRef = useRef<HTMLDivElement>(null)
  const query = search.toLowerCase().trim()

  useEffect(() => {
    if (!editing) setDraft(content ?? "")
  }, [content, editing])

  // DOM-based search highlighting on Milkdown readonly output
  useLayoutEffect(() => {
    const el = editorWrapRef.current
    if (!el || !query || editing || !content) return
    highlightTextNodes(el, query)
  }, [content, query, editing])

  const matchCount = useMemo(() => {
    if (!query || !content) return 0
    const lower = content.toLowerCase()
    let count = 0, pos = 0
    while ((pos = lower.indexOf(query, pos)) !== -1) { count++; pos += query.length }
    return count
  }, [content, query])

  if (!content && !editing) {
    return <span className="text-muted-foreground">No detail generated yet.</span>
  }

  if (editing) {
    return (
      <MarkdownEditor
        value={draft}
        onChange={setDraft}
        minHeight="250px"
        placeholder="Write detail in Markdown..."
        className="flex-1 min-h-0"
      >
        <Button variant="outline" size="sm" onClick={() => { setEditing(false); setDraft(content ?? "") }}>Discard</Button>
        <Button size="sm" onClick={() => { onSave?.({ detail: draft }); setEditing(false) }}>Save</Button>
      </MarkdownEditor>
    )
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-1 pb-2">
        <div className="relative flex-1">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search detail..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-7 pl-7 pr-2 text-xs rounded-md border border-input bg-background"
          />
        </div>
        <Button variant="ghost" size="sm" onClick={() => { setDraft(content ?? ""); setEditing(true) }}>
          <Pencil className="h-3.5 w-3.5" />
        </Button>
      </div>
      {query && <p className="text-[10px] text-muted-foreground px-1 pb-1">{matchCount} match{matchCount !== 1 ? "es" : ""}</p>}
      <div ref={editorWrapRef} className="flex-1 overflow-auto">
        {content && <MarkdownEditor value={content} readonly />}
      </div>
    </div>
  )
}
