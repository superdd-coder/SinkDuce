/**
 * Meeting-summary TipTap nodes: citation chips + priority badges.
 * Used by readonly (and optionally editable) summary Tiptap so view matches edit
 * for bold/lists while keeping interactive stt / priority UI.
 */
import { Node, mergeAttributes } from "@tiptap/core"
import { parseMeetingRefGroups } from "@/lib/meeting-ref-chips"
import { trimEmphasisInteriorSpaces } from "@/lib/md-emphasis"

export type RefClickHandler = (sentenceId: string) => void

type IdNum = { id: string; num: number }

function parseIdList(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
}

/** Group sorted ids into consecutive runs (same as old SummaryMarkdownViewer). */
function consecutiveRanges(ids: string[]): { label: string; ids: string[] }[] {
  const parsed: IdNum[] = ids
    .map((id) => ({
      id,
      num: parseInt(id.replace(/^stt_0*/, "") || "0", 10),
    }))
    .sort((a, b) => a.num - b.num)
  if (parsed.length === 0) return []

  const groups: { label: string; ids: string[] }[] = []
  let ri = 0
  while (ri < parsed.length) {
    const start = parsed[ri]
    let end = start
    let rj = ri + 1
    while (rj < parsed.length && parsed[rj].num === end.num + 1) {
      end = parsed[rj]
      rj++
    }
    const sl = start.id.replace(/^stt_0*/, "") || "0"
    const el = end.id.replace(/^stt_0*/, "") || "0"
    const label = start.id === end.id ? sl : `${sl}-${el}`
    groups.push({
      label,
      ids: parsed.slice(ri, rj).map((p) => p.id),
    })
    ri = rj
  }
  return groups
}

function paintRefChips(
  host: HTMLElement,
  ids: string[],
  getOnRefClick: () => RefClickHandler,
) {
  host.replaceChildren()
  host.className = "pm-meeting-ref-group"
  const groups = consecutiveRanges(ids)
  if (groups.length === 0) {
    const btn = document.createElement("button")
    btn.type = "button"
    btn.className = "pm-meeting-ref-chip"
    btn.textContent = "?"
    host.appendChild(btn)
    return
  }
  for (const g of groups) {
    const btn = document.createElement("button")
    btn.type = "button"
    btn.className = "pm-meeting-ref-chip"
    btn.textContent = g.label
    btn.title = `Sources: ${g.ids.join(", ")}`
    btn.setAttribute("data-meeting-ref", g.ids.join(","))
    btn.addEventListener("click", (e) => {
      e.preventDefault()
      e.stopPropagation()
      const first = g.ids[0]
      if (first) getOnRefClick()(first)
    })
    host.appendChild(btn)
  }
}

/** Inline atom: [stt_0001] / [stt_0001,stt_0002] → green ref chip */
export function createMeetingRefExtension(getOnRefClick: () => RefClickHandler) {
  return Node.create({
    name: "meetingRef",
    group: "inline",
    inline: true,
    atom: true,
    selectable: false,

    addAttributes() {
      return {
        ids: {
          default: "",
          parseHTML: (el: HTMLElement) =>
            el.getAttribute("data-meeting-ref") || "",
          renderHTML: (attrs: { ids?: string }) => ({
            "data-meeting-ref": attrs.ids || "",
          }),
        },
      }
    },

    parseHTML() {
      return [{ tag: "span[data-meeting-ref]" }]
    },

    renderHTML({ HTMLAttributes }) {
      return [
        "span",
        mergeAttributes(HTMLAttributes, {
          class: "pm-meeting-ref-chip",
          "data-meeting-ref": HTMLAttributes["data-meeting-ref"] || "",
        }),
      ]
    },

    addNodeView() {
      return ({ node }) => {
        const host = document.createElement("span")
        paintRefChips(
          host,
          parseIdList(String(node.attrs.ids || "")),
          getOnRefClick,
        )
        return {
          dom: host,
          update: (updated) => {
            if (updated.type.name !== "meetingRef") return false
            paintRefChips(
              host,
              parseIdList(String(updated.attrs.ids || "")),
              getOnRefClick,
            )
            return true
          },
        }
      }
    },

    addStorage() {
      return {
        markdown: {
          serialize(
            state: { write: (s: string) => void },
            node: { attrs: { ids?: string } },
          ) {
            const ids = String(node.attrs.ids || "").trim()
            if (ids) state.write(`[${ids}]`)
          },
        },
      }
    },
  })
}

/** Inline atom: [priority: high|medium|low] → badge */
export function createMeetingPriorityExtension() {
  return Node.create({
    name: "meetingPriority",
    group: "inline",
    inline: true,
    atom: true,
    selectable: false,

    addAttributes() {
      return {
        level: {
          default: "medium",
          parseHTML: (el: HTMLElement) =>
            (el.getAttribute("data-meeting-pri") || "medium").toLowerCase(),
          renderHTML: (attrs: { level?: string }) => ({
            "data-meeting-pri": attrs.level || "medium",
          }),
        },
      }
    },

    parseHTML() {
      return [{ tag: "span[data-meeting-pri]" }]
    },

    renderHTML({ HTMLAttributes }) {
      const level = String(
        HTMLAttributes["data-meeting-pri"] || "medium",
      ).toLowerCase()
      const priClass =
        level === "high" ? "is-high" : level === "low" ? "is-low" : "is-medium"
      return [
        "span",
        mergeAttributes(HTMLAttributes, {
          class: `pm-meeting-pri ${priClass}`,
        }),
        level.toUpperCase(),
      ]
    },

    addNodeView() {
      return ({ node }) => {
        const level = String(node.attrs.level || "medium").toLowerCase()
        const span = document.createElement("span")
        const priClass =
          level === "high" ? "is-high" : level === "low" ? "is-low" : "is-medium"
        span.className = `pm-meeting-pri ${priClass}`
        span.textContent = level.toUpperCase()
        span.setAttribute("data-meeting-pri", level)
        return {
          dom: span,
          update: (updated) => {
            if (updated.type.name !== "meetingPriority") return false
            const next = String(updated.attrs.level || "medium").toLowerCase()
            const cls =
              next === "high"
                ? "is-high"
                : next === "low"
                  ? "is-low"
                  : "is-medium"
            span.className = `pm-meeting-pri ${cls}`
            span.textContent = next.toUpperCase()
            span.setAttribute("data-meeting-pri", next)
            return true
          },
        }
      }
    },

    addStorage() {
      return {
        markdown: {
          serialize(
            state: { write: (s: string) => void },
            node: { attrs: { level?: string } },
          ) {
            const level = String(node.attrs.level || "medium").toLowerCase()
            state.write(`[priority: ${level}]`)
          },
        },
      }
    },
  })
}

/**
 * Display-only prep before TipTap setContent:
 * - undo Tiptap escapes
 * - [spk:N] → display name
 * - [ref:N] / [stt_…] / [priority:…] → HTML spans parsed by our nodes
 * Never write this string back to disk (display only).
 */
export function prepareMeetingSummaryForTiptapView(
  md: string,
  speakerNames?: Record<string, string> | null,
): string {
  let s = (md || "")
    .replace(/\\\[/g, "[")
    .replace(/\\\]/g, "]")
    .replace(/\\~/g, "~")
    .replace(/\\_/g, "_")
    .replace(/\\\*/g, "*")
    .replace(/【/g, "[")
    .replace(/】/g, "]")
    .replace(/〔/g, "[")
    .replace(/〕/g, "]")
    .replace(/［/g, "[")
    .replace(/］/g, "]")

  // Interior spaces in **bold** / *italic* only — never eat gaps between adjacent emphasis
  s = trimEmphasisInteriorSpaces(s)

  const names = speakerNames || {}
  s = s.replace(/\[spk:(\d+)\]/g, (_, id: string) => names[id] ?? `Speaker ${id}`)
  for (const [id, name] of Object.entries(names)) {
    if (!name) continue
    const esc = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    s = s.replace(new RegExp(`\\bSpeaker\\s+${esc}\\b`, "g"), name)
  }

  // Citations → HTML for meetingRef node (before markdown-it treats [] oddly)
  // Streaming LLM writes [ref:67]; persist rewrites to [stt_0067].
  s = s.replace(
    /\[ref:\s*((?:stt_)?\d+(?:\s*[-–—]\s*(?:stt_)?\d+)?(?:\s*,\s*(?:stt_)?\d+(?:\s*[-–—]\s*(?:stt_)?\d+)?)*)\s*\]/gi,
    (_m, inner: string) => {
      const ids = parseMeetingRefGroups(inner).flatMap((g) => g.ids)
      if (!ids.length) return _m
      return `<span data-meeting-ref="${ids.join(",").replace(/"/g, "")}"></span>`
    },
  )
  s = s.replace(
    /\[(?:ref:)?\s*(stt_\d+(?:\s*,\s*stt_\d+)*)\s*\]/gi,
    (_m, ids: string) =>
      `<span data-meeting-ref="${String(ids).replace(/"/g, "")}"></span>`,
  )

  s = s.replace(
    /\[\s*priority\s*:\s*(high|medium|low)\s*\]/gi,
    (_m, level: string) =>
      `<span data-meeting-pri="${String(level).toLowerCase()}"></span>`,
  )

  return s
}
