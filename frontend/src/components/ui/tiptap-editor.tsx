import { useState, useRef, useEffect, useCallback, type ReactNode } from "react"
import { cn } from "@/lib/utils"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useEditor, EditorContent } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import { Table, TableRow, TableCell, TableHeader } from "@tiptap/extension-table"
import TaskList from "@tiptap/extension-task-list"
import TaskItem from "@tiptap/extension-task-item"
import Placeholder from "@tiptap/extension-placeholder"
import Youtube from "@tiptap/extension-youtube"
import Highlight from "@tiptap/extension-highlight"
import { TextStyle } from "@tiptap/extension-text-style"
import Color from "@tiptap/extension-color"
import { Markdown } from "tiptap-markdown"
import { Node, mergeAttributes, Extension, type Editor } from "@tiptap/core"
import { Plugin, PluginKey } from "@tiptap/pm/state"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import {
  Bold, Italic, Strikethrough, Highlighter,
  List, ListOrdered, ListTodo, Heading1, Heading2, Heading3,
  ChevronDown,
} from "lucide-react"

/* eslint-disable @typescript-eslint/no-explicit-any */

// ──────────────────────────────────────────────
// Markdown Syntax Hover Plugin
// ──────────────────────────────────────────────
const markdownHoverKey = new PluginKey("markdownHover")

function createMarkdownHoverPlugin() {
  let tooltip: HTMLElement | null = null

  function getMarkdownSyntax(node: ProseMirrorNode): string | null {
    const marks = node.marks
    if (!marks || marks.length === 0) return null

    const text = node.text || ""
    let syntax = text

    for (const mark of marks) {
      switch (mark.type.name) {
        case "bold":
          syntax = `**${syntax}**`
          break
        case "italic":
          syntax = `*${syntax}*`
          break
        case "code":
          syntax = `\`${syntax}\``
          break
        case "strike":
          syntax = `~~${syntax}~~`
          break
        case "link":
          const href = mark.attrs.href || ""
          syntax = `[${syntax}](${href})`
          break
      }
    }

    return syntax !== text ? syntax : null
  }

  function showTooltip(syntax: string, coords: { left: number; top: number }) {
    if (!tooltip) {
      tooltip = document.createElement("div")
      tooltip.className = "md-syntax-tooltip"
      tooltip.style.cssText = `
        position: fixed;
        background: #1e1e1e;
        color: #d4d4d4;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 12px;
        font-family: 'SF Mono', Monaco, monospace;
        pointer-events: none;
        z-index: 10000;
        max-width: 400px;
        word-break: break-all;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        transition: opacity 0.15s;
      `
      document.body.appendChild(tooltip)
    }

    tooltip.textContent = syntax
    tooltip.style.left = `${coords.left}px`
    tooltip.style.top = `${coords.top - 30}px`
    tooltip.style.opacity = "1"
    tooltip.style.display = "block"
  }

  function hideTooltip() {
    if (tooltip) {
      tooltip.style.opacity = "0"
      setTimeout(() => {
        if (tooltip) tooltip.style.display = "none"
      }, 150)
    }
  }

  return new Plugin({
    key: markdownHoverKey,
    props: {
      handleDOMEvents: {
        mouseover: (view, event) => {
          const mouseEvent = event as MouseEvent
          const pos = view.posAtCoords({ left: mouseEvent.clientX, top: mouseEvent.clientY })
          if (!pos || pos.inside < 0) {
            hideTooltip()
            return false
          }

          try {
            const resolvedPos = view.state.doc.resolve(pos.inside)
            const textNode = resolvedPos.nodeAfter
            if (textNode && textNode.isText && textNode.marks.length > 0) {
              const syntax = getMarkdownSyntax(textNode)
              if (syntax) {
                showTooltip(syntax, { left: mouseEvent.clientX, top: mouseEvent.clientY })
                return false
              }
            }
          } catch {
            // Ignore resolution errors
          }

          hideTooltip()
          return false
        },
        mouseout: () => {
          hideTooltip()
          return false
        },
      },
    },
  })
}

// ──────────────────────────────────────────────
// Async Visual Translate Manager (module-level — survives React unmount)
// ──────────────────────────────────────────────

let _vtCallback: ((imageUrl: string) => Promise<string>) | null = null
const _generatingImages = new Set<string>()
const _pendingResults = new Map<string, string>() // imageId → description
/** Tracks the current DOM container for each generating image so we can
 *  remove the image-generating CSS class when generation ends, even if
 *  the editor instance was destroyed and recreated. */
const _vtContainers = new Map<string, HTMLElement>() // imageId → container
/** Flush pending auto-save to server before async generation — ensures imageId is
 *  persisted so cross-note pending-injection finds its target on reload. */
let _flushSaveBeforeGenerate: (() => Promise<void>) | null = null

export function _setFlushSaveBeforeGenerate(fn: (() => Promise<void>) | undefined) {
  _flushSaveBeforeGenerate = fn ?? null
}

function _setVTCallback(fn: ((imageUrl: string) => Promise<string>) | undefined) {
  _vtCallback = fn ?? null
}

async function _runVisualTranslate(
  imageUrl: string,
  imageId: string,
  editor: any,
  container: HTMLElement,
) {
  if (!_vtCallback) return
  _generatingImages.add(imageId)
  // Track the container so we can remove the generating class later,
  // even if the editor is recreated by a note-switch round-trip.
  _vtContainers.set(imageId, container)

  // Lock image
  container.style.pointerEvents = "none"
  container.classList.add("image-generating")

  // Flush auto-save to server BEFORE the async call, mirroring distill block's
  // pattern. This ensures the image's data-image-id is persisted on the server,
  // so if the user switches notes mid-generation, applyPendingDescriptions
  // can find and update it when they return.
  try {
    if (_flushSaveBeforeGenerate) await _flushSaveBeforeGenerate()
  } catch { /* non-critical */ }

  let _desc: string | null = null
  try {
    _desc = await _vtCallback(imageUrl)
    // Always store result — survives note switching / editor content changes
    _pendingResults.set(imageId, _desc)



    // Try to apply immediately to the current editor document.
    // Wrapped in its own try/catch: if the editor was destroyed (note switch),
    // the dispatch will fail, but we KEEP the result in _pendingResults so
    // applyPendingDescriptions can inject it when the note is reloaded.
    try {
      const { doc } = editor.state
      let foundPos: number | null = null
      doc.descendants((node: any, pos: number) => {
        if (node.type.name === "image" && node.attrs.imageId === imageId) {
          foundPos = pos
          return false
        }
        return true
      })
      if (foundPos !== null && !editor.isDestroyed) {
const nodeAt = doc.nodeAt(foundPos)
        if (nodeAt) {
          const tr = editor.state.tr
          tr.setNodeMarkup(foundPos, undefined, {
            ...nodeAt.attrs,
            visualDescription: _desc,
          })
          editor.view.dispatch(tr)
          // Result applied to editor — persisted when onChange fires.
          _pendingResults.delete(imageId)
          // Fallback: also update DOM directly.
          try {
            const descArea = container.querySelector(".image-visual-desc") as HTMLElement | null
            const descTextEl = container.querySelector(".image-visual-desc-text") as HTMLElement | null
            if (descArea && descTextEl) {
              descTextEl.textContent = _desc
              descArea.style.display = "block"
              container.classList.add("image-has-description")
            }
          } catch { /* best-effort fallback */ }
        }
      }
      // If foundPos is null, the editor is showing different content.
      // Result stays in _pendingResults — it will be applied when the
      // note's content is reloaded (see applyPendingDescriptions).
    } catch (_dispatchErr: any) {
      // Dispatch failed — editor was likely destroyed during a note switch.
      // The result is already in _pendingResults; it will be injected by
      // applyPendingDescriptions when the note is reloaded.
      console.warn("[VisualTranslate] dispatch skipped, result stays pending")
    }
  } catch (err: any) {
    // Only reached if the API call (describeImage) itself failed.
    // Clean up the pending entry — there's no result to persist.
    console.error("[VisualTranslate] generation failed:", err)
    _generatingImages.delete(imageId)
    _pendingResults.delete(imageId)
    try {
      const toast = document.createElement("div")
      toast.style.cssText = "position:fixed;bottom:20px;right:20px;background:#dc2626;color:#fff;padding:8px 16px;border-radius:6px;font-size:13px;z-index:99999;max-width:400px"
      toast.textContent = err?.message || "Visual Translate failed. Check Settings → Visual Model."
      document.body.appendChild(toast)
      setTimeout(() => toast.remove(), 5000)
    } catch { /* ignore */ }
  } finally {
    _generatingImages.delete(imageId)
    // Remove generating state from the tracked container AND force-show
    // the description on it. This guarantees the description is visible
    // even if the ProseMirror dispatch/update cycle didn't apply it.
    try {
      const c = _vtContainers.get(imageId)
      if (c) {
        c.classList.remove("image-generating")
        c.style.pointerEvents = ""
        // Force-show description on the current container
        if (_desc) {
          const da = c.querySelector(".image-visual-desc") as HTMLElement | null
          const dt = c.querySelector(".image-visual-desc-text") as HTMLElement | null
          if (da && dt) {
            dt.textContent = _desc
            da.style.display = "block"
          }
        }
      }
      _vtContainers.delete(imageId)
    } catch { /* best-effort */ }
    container.style.pointerEvents = ""
    container.classList.remove("image-generating")
  }
}

function _isImageGenerating(imageId: string): boolean {
  return _generatingImages.has(imageId)
}

/**
 * Inject pending AI descriptions into Markdown content before it is loaded
 * into the editor. Called by the React layer whenever note content is set.
 *
 * Scans for <img> tags with a known data-image-id that have a pending
 * description, and adds data-visual-desc so the editor picks it up.
 */
export function applyPendingDescriptions(markdown: string): string {
  if (_pendingResults.size === 0) return markdown

  let changed = false
  let result = markdown

  for (const [imageId, description] of _pendingResults) {
    const needle = `data-image-id="${imageId}"`
    if (!result.includes(needle)) continue

    const encodedDesc = encodeURIComponent(description)
    const descAttr = `data-visual-desc=`

    // Find the <img> tag that contains this imageId
    const needleIdx = result.indexOf(needle)
    const imgTagStart = result.lastIndexOf('<img', needleIdx)
    const imgTagEnd = result.indexOf('>', needleIdx)
    if (imgTagStart < 0 || imgTagEnd <= imgTagStart) continue

    const oldImg = result.substring(imgTagStart, imgTagEnd + 1)
    let newImg: string
    if (oldImg.includes(descAttr)) {
      // Already has a description — replace it (re-generation case)
      newImg = oldImg.replace(/data-visual-desc="[^"]*"/, `data-visual-desc="${encodedDesc}"`)
    } else {
      // No existing description — inject after needle
      newImg = oldImg.replace(needle, `${needle} data-visual-desc="${encodedDesc}"`)
    }
    result = result.replace(oldImg, newImg)
    changed = true
    _pendingResults.delete(imageId)
  }

  return changed ? result : markdown
}

// ──────────────────────────────────────────────
// Custom Resizable Image Extension
// ──────────────────────────────────────────────
function createResizableImageExtension(
  onVisualTranslate?: (imageUrl: string) => Promise<string>
) {
  _setVTCallback(onVisualTranslate)
  return Node.create({
  name: "image",
  group: "block",
  draggable: true,
  atom: true,
  inline: false,

  addAttributes() {
    return {
      src: {
        default: null,
        parseHTML: (element: HTMLElement) => {
          const src = element.getAttribute("src")
          return src ? decodeURIComponent(src) : null
        },
        renderHTML: (attrs: any) => {
          const encodedSrc = attrs.src ? encodeURI(attrs.src) : ""
          return { src: encodedSrc }
        },
      },
      alt: {
        default: "",
        parseHTML: (element: HTMLElement) => element.getAttribute("alt") || "",
        renderHTML: (attrs: any) => ({ alt: attrs.alt }),
      },
      title: {
        default: "",
        parseHTML: (element: HTMLElement) => element.getAttribute("title") || "",
        renderHTML: (attrs: any) => ({ title: attrs.title }),
      },
      width: {
        default: "55%",
        parseHTML: (element: HTMLElement) => {
          // Check data-width first (our serialized format), then style.width
          const dw = element.getAttribute("data-width")
          if (dw) return dw
          const sw = element.style.width
          if (sw && sw.endsWith("%")) return sw
          return "55%"
        },
        renderHTML: (attrs: any) => {
          const w = attrs.width
          if (w && w !== "auto") return { "data-width": w, style: `width: ${w}` }
          return {}
        },
      },
      alignment: {
        default: "center",
        parseHTML: (element: HTMLElement) => {
          const da = element.getAttribute("data-align")
          if (da) return da
          const cs = element.style.textAlign
          if (cs) return cs
          return "center"
        },
        renderHTML: (attrs: any) => ({
          "data-align": attrs.alignment || "center",
        }),
      },
      visualDescription: {
        default: null,
        parseHTML: (element: HTMLElement) => {
          const desc = element.getAttribute("data-visual-desc")
          return desc ? decodeURIComponent(desc) : null
        },
        renderHTML: (attrs: any) => {
          if (attrs.visualDescription) {
            return { "data-visual-desc": encodeURIComponent(attrs.visualDescription) }
          }
          return {}
        },
      },
      imageId: {
        default: null,
        parseHTML: (element: HTMLElement) => element.getAttribute("data-image-id") || null,
        renderHTML: (attrs: any) => {
          if (attrs.imageId) return { "data-image-id": attrs.imageId }
          return {}
        },
      },
    }
  },

  // Auto-generate imageId on creation
  addOptions() {
    return { inline: false }
  },

  parseHTML() {
    return [
      {
        tag: "img[src]",
      },
    ]
  },

  renderHTML({ HTMLAttributes }) {
    return ["img", mergeAttributes(HTMLAttributes)]
  },

  addStorage() {
    return {
      markdown: {
        serialize(state: any, node: any) {
          const alt = node.attrs.alt || ""
          const src = node.attrs.src || ""
          const title = node.attrs.title ? ` "${node.attrs.title}"` : ""
          const width = node.attrs.width || ""
          const alignment = node.attrs.alignment || ""

          // Build HTML img tag to preserve width, alignment, alt, and title.
          // Standard ![](src) loses width/alignment on round-trip — HTML <img>
          // survives tiptap-markdown's HTML parser and lets parseHTML recover them.
          const attrs: string[] = []
          attrs.push(`src="${src}"`)
          if (alt) attrs.push(`alt="${alt}"`)
          if (title) attrs.push(`title="${title}"`)
          if (width && /^\d+%$/.test(width) && width !== "55%") attrs.push(`data-width="${width}" style="width: ${width}"`)
          if (alignment && alignment !== "center") attrs.push(`data-align="${alignment}"`)
          if (node.attrs.imageId) {
            attrs.push(`data-image-id="${node.attrs.imageId}"`)
          }
          if (node.attrs.visualDescription) {
            attrs.push(`data-visual-desc="${encodeURIComponent(node.attrs.visualDescription)}"`)
          }

          state.write(`<img ${attrs.join(" ")} />`)
          state.closeBlock(node)
        },
      },
    }
  },

  addNodeView() {
    return ({ node, getPos, editor }) => {
      const container = document.createElement("div")
      container.className = "image-container"
      container.contentEditable = "false"

      // Apply alignment to the container — percentage widths are relative
      // to the container, and alignment moves the container within the
      // ProseMirror column via margin-left: auto / margin-right: auto.
      const align = node.attrs.alignment || "center"
      const rawWidth = node.attrs.width
      const hasPct = typeof rawWidth === "string" && /^\d+%$/.test(rawWidth)
      const applyLayout = () => {
        let ml = "0", mr = "0"
        if (align === "center") { ml = "auto"; mr = "auto" }
        else if (align === "right") { ml = "auto" }
        // Container always 100% — description area below fills full editor width.
        container.style.cssText = `
          position: relative;
          display: block;
          width: 100%;
          max-width: 100%;
          margin: 8px 0;
        `
        // imgWrapper constrained to image width with alignment.
        imgWrapper.style.cssText = `
          position: relative;
          display: block;
          line-height: 0;
          width: ${hasPct ? rawWidth : "auto"};
          max-width: 100%;
          margin-left: ${ml};
          margin-right: ${mr};
        `
        // captionEl matches imgWrapper width/alignment so caption stays with image.
        captionEl.style.cssText = `
          font-size: 13px;
          color: #666;
          text-align: center;
          margin-top: 8px;
          font-style: italic;
          cursor: text;
          min-height: 20px;
          width: ${hasPct ? rawWidth : "auto"};
          max-width: 100%;
          margin-left: ${ml};
          margin-right: ${mr};
        `
      }
      // ── Image wrapper — keeps resize handle pinned to image regardless of caption/description height ──
      const imgWrapper = document.createElement("div")

      const img = document.createElement("img")
      img.src = node.attrs.src
      img.alt = node.attrs.alt || ""
      img.title = node.attrs.title || ""
      img.style.cssText = `
        width: 100%;
        height: auto;
        cursor: pointer;
        border-radius: 4px;
        transition: box-shadow 0.2s;
        display: block;
      `
      // When no % width is set, let img use max-width restraint
      if (!hasPct) {
        img.style.maxWidth = "100%"
      }

      // ── Stable imageId for async generation tracking ──
      const imageId = node.attrs.imageId || crypto.randomUUID()
      if (!node.attrs.imageId) {
        setTimeout(() => {
          const pos = typeof getPos === "function" ? getPos() : undefined
          if (pos !== undefined && pos !== null) {
            const { tr } = editor.state
            const n = editor.state.doc.nodeAt(pos)
            if (n && n.type.name === "image") {
              tr.setNodeMarkup(pos, undefined, { ...n.attrs, imageId })
              editor.view.dispatch(tr)
            }
          }
        }, 0)
      }

      // Caption element — always present in the container, created once.
      // Its text is kept in sync via update() and setCaption() helper.
      const captionEl = document.createElement("div")
      captionEl.className = "image-caption"
      captionEl.style.cssText = `
        font-size: 13px;
        color: #666;
        text-align: center;
        margin-top: 8px;
        font-style: italic;
        cursor: text;
        min-height: 20px;
      `
      const setCaption = (text: string) => {
        captionEl.textContent = text || ""
        // Collapse caption when empty so there's no dead space between
        // the image and the description.
        if (text) {
          captionEl.style.display = ""
          captionEl.style.marginTop = "8px"
        } else {
          captionEl.style.display = "none"
        }
      }

      // Visual Description area — shows AI-generated description below caption.
      // Always present in DOM (hidden when no description), styled with emerald glow.
      const descArea = document.createElement("div")
      descArea.className = "image-visual-desc"
      descArea.style.cssText = `
        display: none;
        font-size: 12px;
        color: #6b7280;
        font-style: italic;
        text-align: left;
        margin-top: 6px;
        padding: 8px 12px;
        border-radius: 6px;
        background: transparent;
        border: 1px solid rgba(4, 120, 87, 0.45);
        box-shadow:
          0 0 10px rgba(4, 120, 87, 0.35),
          0 0 25px rgba(4, 120, 87, 0.15);
        line-height: 1.5;
        position: relative;
      `
      const descTextEl = document.createElement("span")
      descTextEl.className = "image-visual-desc-text"
      descArea.appendChild(descTextEl)

      // Edit / Delete buttons — appear on hover over description area
      const descActions = document.createElement("div")
      descActions.className = "image-visual-desc-actions"
      descActions.style.cssText = `
        display: none;
        position: absolute;
        top: 4px;
        right: 6px;
        gap: 4px;
      `
      // Edit button
      const editBtn = document.createElement("button")
      editBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M11.5 1.5l3 3L5 14H2v-3L11.5 1.5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`
      editBtn.title = "Edit description"
      editBtn.style.cssText = `
        padding: 2px 4px;
        border: none;
        background: rgba(0,0,0,0.06);
        border-radius: 3px;
        cursor: pointer;
        color: #666;
        display: flex;
        align-items: center;
      `
      // Delete button
      const deleteBtn = document.createElement("button")
      deleteBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M2 4h12M5 4V3a1 1 0 011-1h4a1 1 0 011 1v1M6 7v5M10 7v5M3 4l1 9a1 1 0 001 1h6a1 1 0 001-1l1-9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`
      deleteBtn.title = "Remove description"
      deleteBtn.style.cssText = `
        padding: 2px 4px;
        border: none;
        background: rgba(0,0,0,0.06);
        border-radius: 3px;
        cursor: pointer;
        color: #666;
        display: flex;
        align-items: center;
      `

      descActions.appendChild(editBtn)
      descActions.appendChild(deleteBtn)
      descArea.appendChild(descActions)

      // Show actions on hover
      descArea.addEventListener("mouseenter", () => { descActions.style.display = "flex" })
      descArea.addEventListener("mouseleave", () => { descActions.style.display = "none" })

      // Helper: update description content and visibility
      const setDescription = (desc: string | null) => {
        if (desc) {
          descTextEl.textContent = desc
          descArea.style.display = "block"
          container.classList.add("image-has-description")
        } else {
          descTextEl.textContent = ""
          descArea.style.display = "none"
          container.classList.remove("image-has-description")
        }
      }
      setDescription(node.attrs.visualDescription || null)

      // If this image is currently being generated (user switched away and
      // back mid-generation), re-apply the generating lock and animation.
      if (imageId && _isImageGenerating(imageId)) {
        container.style.pointerEvents = "none"
        container.classList.add("image-generating")
        // Update the tracked container so the finally block in
        // _runVisualTranslate can clean up the correct DOM element.
        _vtContainers.set(imageId, container)
      }

      // Persist description to node attrs
      const commitDescription = (val: string | null) => {
        setDescription(val)
        if (typeof getPos === "function") {
          const pos = getPos()
          if (pos !== undefined && pos !== null) {
            const { tr } = editor.state
            const nodeAtPos = editor.state.doc.nodeAt(pos)
            if (nodeAtPos) {
              tr.setNodeMarkup(pos, undefined, {
                ...nodeAtPos.attrs,
                visualDescription: val,
              })
              editor.view.dispatch(tr)
            }
          }
        }
      }

      // Edit: overlay a position:fixed textarea on document.body so
      // typing doesn't cause ProseMirror DOM re-layout / scroll-to-top.
      editBtn.addEventListener("click", (e) => {
        e.stopPropagation()
        const currentDesc = descTextEl.textContent || ""
        // Position the textarea exactly over the descArea
        const dr = descArea.getBoundingClientRect()
        const textarea = document.createElement("textarea")
        textarea.value = currentDesc
        textarea.style.cssText = `
          position: fixed;
          left: ${dr.left}px;
          top: ${dr.top}px;
          width: ${dr.width}px;
          height: ${dr.height}px;
          min-height: 40px;
          font-size: 12px;
          font-style: italic;
          color: #374151;
          background: rgba(255,255,255,0.95);
          border: 1px solid #3b82f6;
          border-radius: 4px;
          padding: 6px 8px;
          resize: vertical;
          outline: none;
          box-sizing: border-box;
          line-height: 1.5;
          z-index: 10002;
        `
        document.body.appendChild(textarea)
        // Track resize and scroll to reposition the textarea
        const reposition = () => {
          const r = descArea.getBoundingClientRect()
          textarea.style.left = `${r.left}px`
          textarea.style.top = `${r.top}px`
          textarea.style.width = `${r.width}px`
        }
        window.addEventListener("scroll", reposition, true)
        window.addEventListener("resize", reposition)
        textarea.focus()

        const cleanup = () => {
          textarea.remove()
          window.removeEventListener("scroll", reposition, true)
          window.removeEventListener("resize", reposition)
        }
        const save = () => {
          const val = textarea.value.trim() || null
          cleanup()
          descTextEl.style.display = ""
          commitDescription(val)
        }
        // Don't save on blur — scrolling would trigger blur and close
        // the editor. Instead, save when clicking outside the textarea.
        const descClickOutside = (me: MouseEvent) => {
          if (me.target === textarea || textarea.contains(me.target as any)) return
          save()
          document.removeEventListener("mousedown", descClickOutside, true)
        }
        setTimeout(() => document.addEventListener("mousedown", descClickOutside, true), 0)
        textarea.addEventListener("keydown", (ke: KeyboardEvent) => {
          if (ke.key === "Enter" && ke.metaKey) { ke.preventDefault(); save() }
          if (ke.key === "Escape") { textarea.value = currentDesc; cleanup(); descTextEl.style.display = "" }
        })
      })

      // Delete: remove description
      deleteBtn.addEventListener("click", (e) => {
        e.stopPropagation()
        commitDescription(null)
      })

      // Inline caption editor — mounted on document.body, positioned over
      // the caption area. Kept completely outside ProseMirror's DOM so no
      // mutations trigger nodeView destruction.
      let inlineEditor: HTMLInputElement | null = null
      const showInlineEditor = (currentAlt: string) => {
        if (inlineEditor) inlineEditor.remove()
        // Use imgWrapper for horizontal positioning and width (follows image
        // alignment), captionEl for vertical position. When caption is empty
        // and hidden, captionEl.getBoundingClientRect returns 0-height, so
        // use imgWrapper's bottom as the top position instead.
        const iw = imgWrapper.getBoundingClientRect()
        const cr = captionEl.getBoundingClientRect()
        const r = {
          left: iw.left,
          top: cr.height > 0 ? cr.top : iw.bottom + 8,
          width: iw.width,
          height: cr.height > 0 ? cr.height : 20,
        }
        inlineEditor = document.createElement("input")
        inlineEditor.type = "text"
        inlineEditor.value = currentAlt
        inlineEditor.placeholder = "Image caption..."
        inlineEditor.style.cssText = `
          position: fixed;
          left: ${r.left}px;
          top: ${r.top}px;
          width: ${r.width}px;
          height: ${r.height}px;
          font-size: 13px;
          text-align: center;
          border: 1px solid #3b82f6;
          border-radius: 3px;
          padding: 0 4px;
          outline: none;
          box-sizing: border-box;
          font-style: italic;
          color: #333;
          background: white;
          z-index: 10001;
        `
        document.body.appendChild(inlineEditor)
        inlineEditor.focus()
        inlineEditor.select()
      }

      const hideInlineEditor = () => {
        if (inlineEditor) {
          inlineEditor.remove()
          inlineEditor = null
        }
      }

      // Persist edited caption to node attrs, then update captionEl
      const commitCaption = (val: string) => {
        hideInlineEditor()
        // Update captionEl immediately — don't wait for ProseMirror
        // update() cycle. setNodeMarkup dispatches a transaction that
        // calls update(), but the inline element positioning depends
        // on captionEl being in sync.
        setCaption(val || "")
        if (typeof getPos === "function") {
          const pos = getPos()
          if (pos !== undefined && pos !== null) {
            const { tr } = editor.state
            const nodeAtPos = editor.state.doc.nodeAt(pos)
            if (nodeAtPos) {
              tr.setNodeMarkup(pos, undefined, {
                ...nodeAtPos.attrs,
                alt: val,
              })
              editor.view.dispatch(tr)
            }
          }
        }
      }

      // Reposition inline editor on scroll/resize
      const repositionEditor = () => {
        if (!inlineEditor) return
        const iw = imgWrapper.getBoundingClientRect()
        const cr = captionEl.getBoundingClientRect()
        inlineEditor.style.left = `${iw.left}px`
        inlineEditor.style.top = `${cr.height > 0 ? cr.top : iw.bottom + 8}px`
        inlineEditor.style.width = `${iw.width}px`
        inlineEditor.style.height = `${cr.height > 0 ? cr.height : 20}px`
      }
      window.addEventListener("scroll", repositionEditor, true)
      window.addEventListener("resize", repositionEditor)

      // Show resize handles on hover
      let resizeHandle: HTMLElement | null = null
      let isResizing = false
      let startX = 0
      let startWidth = 0

      const createResizeHandle = () => {
        const handle = document.createElement("div")
        handle.style.cssText = `
          position: absolute;
          right: -2px;
          bottom: 0px;
          width: 20px;
          height: 20px;
          cursor: nwse-resize;
          opacity: 0;
          transition: opacity 0.2s;
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 5;
          background: rgba(59,130,246,0.15);
          border-radius: 0 0 4px 0;
        `
        // Diagonal resize arrows SVG - two arrows pointing from corners
        handle.innerHTML = `
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M14 2L18 6M18 6H14M18 6V2" stroke="#047857" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M6 18L2 14M2 14H6M2 14V18" stroke="#047857" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="10" cy="10" r="2" fill="#047857"/>
          </svg>
        `
        return handle
      }

      resizeHandle = createResizeHandle()
      imgWrapper.appendChild(img)
      imgWrapper.appendChild(resizeHandle)
      // Apply layout now that imgWrapper and captionEl exist (referenced by applyLayout)
      applyLayout()
      setCaption(node.attrs.alt || "")
      container.appendChild(imgWrapper)
      container.appendChild(captionEl)
      container.appendChild(descArea)

      // Show/hide resize handle on hover
      container.addEventListener("mouseenter", () => {
        if (resizeHandle) resizeHandle.style.opacity = "1"
        img.style.boxShadow = "0 0 0 2px #047857"
      })

      container.addEventListener("mouseleave", () => {
        if (!isResizing && resizeHandle) {
          resizeHandle.style.opacity = "0"
          img.style.boxShadow = ""
        }
      })

      // Clean up inline editor when window unloads
      window.addEventListener("beforeunload", hideInlineEditor)

      // Listen for caption:edit custom event from the floating menu
      container.addEventListener("caption:edit", ((e: CustomEvent) => {
        const alt = e.detail?.alt ?? ""
        showInlineEditor(alt)
        if (inlineEditor) {
          let saved = false
          const save = () => {
            if (saved) return
            saved = true
            const val = inlineEditor?.value.trim() ?? ""
            commitCaption(val)
          }
          // Don't save on blur — scrolling would trigger blur and close
          // the editor. Instead, save when clicking outside the input.
          const captionClickOutside = (me: MouseEvent) => {
            if (!inlineEditor || inlineEditor.contains(me.target as any)) return
            save()
            document.removeEventListener("mousedown", captionClickOutside, true)
          }
          setTimeout(() => document.addEventListener("mousedown", captionClickOutside, true), 0)
          inlineEditor.addEventListener("keydown", (ke: KeyboardEvent) => {
            if (ke.key === "Enter") { ke.preventDefault(); save() }
            if (ke.key === "Escape") { saved = true; hideInlineEditor() }
          })
        }
      }) as EventListener)

      // Resize functionality — percentage-based width
      const getEditorContentWidth = (): number => {
        const pmEl = editor.view.dom as HTMLElement
        return pmEl?.clientWidth ?? container.parentElement?.clientWidth ?? 600
      }

      resizeHandle.addEventListener("mousedown", (e) => {
        e.preventDefault()
        isResizing = true
        startX = e.clientX

        // Always read the current visual width of the container — never
        // trust the closure `width` variable, which is stale after external
        // updates (e.g. switching notes changes `width` in attrs).
        // Read the imgWrapper width (which follows the image percentage),
        // not the container (which is always 100% for full-width description).
        const imgStyle: string = imgWrapper.style.width || String(imgWrapper.offsetWidth) + "px"
        const contentWidth = getEditorContentWidth()
        if (imgStyle && imgStyle.endsWith("%")) {
          const pct = parseFloat(imgStyle)
          startWidth = (pct / 100) * contentWidth
        } else {
          startWidth = imgWrapper.offsetWidth || img.offsetWidth
        }
        // Resize imgWrapper + captionEl during drag (not container).
        imgWrapper.style.width = `${startWidth}px`
        captionEl.style.width = `${startWidth}px`

        const onMouseMove = (e: MouseEvent) => {
          if (!isResizing) return
          const diff = e.clientX - startX
          const newWidth = Math.max(50, startWidth + diff)
          imgWrapper.style.width = `${newWidth}px`
          captionEl.style.width = `${newWidth}px`
        }

        const onMouseUp = () => {
          isResizing = false
          if (resizeHandle) resizeHandle.style.opacity = "0"
          img.style.boxShadow = ""

          // Compute percentage relative to editor content width
          const contentW = getEditorContentWidth()
          const pct = Math.round((imgWrapper.offsetWidth / contentW) * 100)
          const newPctWidth = `${pct}%`

          // Persist as percentage in node attributes
          if (typeof getPos === "function") {
            const pos = getPos()
            if (pos !== undefined && pos !== null) {
              const { tr } = editor.state
              const nodeAtPos = editor.state.doc.nodeAt(pos)
              if (nodeAtPos) {
                tr.setNodeMarkup(pos, undefined, {
                  ...nodeAtPos.attrs,
                  width: newPctWidth,
                })
                editor.view.dispatch(tr)
              }
            }
          }

          // Restore percentage-based layout on imgWrapper + captionEl,
          // NOT on container (which stays 100% for full-width description).
          imgWrapper.style.width = newPctWidth
          captionEl.style.width = newPctWidth
          container.style.width = "100%"
          captionEl.style.marginLeft = ""
          captionEl.style.marginRight = ""

          document.removeEventListener("mousemove", onMouseMove)
          document.removeEventListener("mouseup", onMouseUp)
        }

        document.addEventListener("mousemove", onMouseMove)
        document.addEventListener("mouseup", onMouseUp)
      })

      // Image click handler — always read current attrs from editor doc,
      // NOT from stale closure node.attrs (which is frozen at creation time).
      container.addEventListener("click", (e) => {
        if (!isResizing) {
          e.stopPropagation()
          // Read current attrs fresh from the document — they may have changed
          // since this node view was created (e.g. resize updated width).
          const pos = typeof getPos === "function" ? getPos() : undefined
          const currentAttrs = (pos !== undefined && pos !== null
            ? editor.state.doc.nodeAt(pos)?.attrs
            : null) ?? node.attrs

          showImageFloatingMenu(container, currentAttrs, (newAttrs: any) => {
            // Re-read position — it may have shifted
            const freshPos = typeof getPos === "function" ? getPos() : undefined
            if (freshPos === undefined || freshPos === null) return
            // Always read the LATEST attrs from the doc — never trust closure node.attrs
            const docAttrs = editor.state.doc.nodeAt(freshPos)?.attrs
            if (!docAttrs) return
            const merged = { ...docAttrs, ...newAttrs }

            // Apply layout visually immediately
            const w = merged.width
            const hasPct = typeof w === "string" && /^\d+%$/.test(w)
            const a = merged.alignment || "center"
            let ml = "0", mr = "0"
            if (a === "center") { ml = "auto"; mr = "auto" }
            else if (a === "right") { ml = "auto" }
            container.style.cssText = `
              position: relative;
              display: block;
              width: 100%;
              max-width: 100%;
              margin: 8px 0;
            `
            imgWrapper.style.cssText = `
              position: relative;
              display: block;
              line-height: 0;
              width: ${hasPct ? w : "auto"};
              max-width: 100%;
              margin-left: ${ml};
              margin-right: ${mr};
            `
            captionEl.style.cssText = `
              font-size: 13px;
              color: #666;
              text-align: center;
              margin-top: 8px;
              font-style: italic;
              cursor: text;
              min-height: 20px;
              width: ${hasPct ? w : "auto"};
              max-width: 100%;
              margin-left: ${ml};
              margin-right: ${mr};
            `

            // Persist to ProseMirror node
            const { tr } = editor.state
            tr.setNodeMarkup(freshPos, undefined, merged)
            editor.view.dispatch(tr)
          }, onVisualTranslate, editor, imageId)
        }
      })

      return {
        dom: container,
        update: (updatedNode: ProseMirrorNode) => {
          if (updatedNode.type.name !== "image") return false
          img.src = updatedNode.attrs.src
          img.alt = updatedNode.attrs.alt || ""
          img.title = updatedNode.attrs.title || ""
          const w = updatedNode.attrs.width
          const hasPctW = typeof w === "string" && /^\d+%$/.test(w)
          const a = updatedNode.attrs.alignment || "center"
          let ml = "0", mr = "0"
          if (a === "center") { ml = "auto"; mr = "auto" }
          else if (a === "right") { ml = "auto" }
          // Container always 100% — description fills full editor width.
        container.style.cssText = `
          position: relative;
          display: block;
          width: 100%;
          max-width: 100%;
          margin: 8px 0;
        `
        // imgWrapper constrained to image width with alignment.
        imgWrapper.style.cssText = `
          position: relative;
          display: block;
          line-height: 0;
          width: ${hasPctW ? w : "auto"};
          max-width: 100%;
          margin-left: ${ml};
          margin-right: ${mr};
        `
        // captionEl matches image width/alignment.
        captionEl.style.cssText = `
          font-size: 13px;
          color: #666;
          text-align: center;
          margin-top: 8px;
          font-style: italic;
          cursor: text;
          min-height: 20px;
          width: ${hasPctW ? w : "auto"};
          max-width: 100%;
          margin-left: ${ml};
          margin-right: ${mr};
        `
        // Refresh caption — ensure captionEl stays in sync even if
        // commitCaption() already updated it before ProseMirror's update() cycle.
        setCaption(updatedNode.attrs.alt || "")
          // Refresh visual description
          setDescription(updatedNode.attrs.visualDescription || null)
          // Sync generating lock state
          const genId = updatedNode.attrs.imageId
          if (genId && _isImageGenerating(genId)) {
            container.style.pointerEvents = "none"
            container.classList.add("image-generating")
          } else {
            container.style.pointerEvents = ""
            container.classList.remove("image-generating")
          }
          return true
        },
        ignoreMutation: () => {
          // Block mutations during generation to keep the node stable
          const genId = node.attrs.imageId
          if (genId && _isImageGenerating(genId)) return true
          return true
        },
      }
    }
  },
  })
}  // end createResizableImageExtension

// ──────────────────────────────────────────────
// Image Floating Menu
// ──────────────────────────────────────────────
let _isPreviewMode = false
function showImageFloatingMenu(
  container: HTMLElement,
  attrs: any,
  onUpdate: (attrs: any) => void,
  onVisualTranslate?: (imageUrl: string) => Promise<string>,
  editor?: any,
  imageId?: string,
) {
  if (_isPreviewMode) return  // Don't show menus in preview
  // Remove existing menu
  const existingMenu = document.getElementById("image-floating-menu")
  if (existingMenu) existingMenu.remove()

  const menu = document.createElement("div")
  menu.id = "image-floating-menu"
  menu.style.cssText = `
    position: absolute;
    top: -44px;
    left: 50%;
    transform: translateX(-50%);
    background: #1e1e1e;
    border-radius: 8px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.3);
    padding: 6px;
    display: flex;
    gap: 4px;
    z-index: 100;
    white-space: nowrap;
  `

  // Alignment options with SVG icons
  const alignmentOptions = [
    {
      value: "left",
      svg: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3h12M2 7h8M2 11h10M2 15h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
      label: "Align left",
    },
    {
      value: "center",
      svg: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3h12M4 7h8M3 11h10M5 15h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
      label: "Align center",
    },
    {
      value: "right",
      svg: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3h12M6 7h8M4 11h10M8 15h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
      label: "Align right",
    },
  ]

  alignmentOptions.forEach(opt => {
    const btn = document.createElement("button")
    btn.innerHTML = opt.svg
    btn.title = opt.label
    btn.style.cssText = `
      padding: 6px 8px;
      border: none;
      background: ${attrs.alignment === opt.value ? "rgba(255,255,255,0.2)" : "transparent"};
      border-radius: 4px;
      cursor: pointer;
      color: white;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s;
    `
    btn.addEventListener("mouseenter", () => { btn.style.background = "rgba(255,255,255,0.15)" })
    btn.addEventListener("mouseleave", () => {
      btn.style.background = attrs.alignment === opt.value ? "rgba(255,255,255,0.2)" : "transparent"
    })
    btn.addEventListener("click", (e) => {
      e.stopPropagation()
      attrs.alignment = opt.value
      onUpdate({ alignment: opt.value })
      // Update visual highlight — re-style all buttons
      menu.querySelectorAll("button.align-btn").forEach((b, i) => {
        const el = b as HTMLElement
        el.style.background = alignmentOptions[i].value === opt.value ? "rgba(255,255,255,0.2)" : "transparent"
      })
      // Don't remove menu so user can see effect and adjust further
    })
    btn.className = "align-btn"
    menu.appendChild(btn)
  })

  // Divider
  const divider = document.createElement("div")
  divider.style.cssText = `width: 1px; background: rgba(255,255,255,0.2); margin: 4px 2px;`
  menu.appendChild(divider)

  // Caption button — inline editing instead of system prompt()
  const captionBtn = document.createElement("button")
  captionBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M11.5 1.5l3 3L5 14H2v-3L11.5 1.5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`
  captionBtn.title = "Add caption"
  captionBtn.style.cssText = `
    padding: 6px 8px;
    border: none;
    background: transparent;
    border-radius: 4px;
    cursor: pointer;
    color: white;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.15s;
  `
  captionBtn.addEventListener("mouseenter", () => { captionBtn.style.background = "rgba(255,255,255,0.15)" })
  captionBtn.addEventListener("mouseleave", () => { captionBtn.style.background = "transparent" })
  captionBtn.addEventListener("click", (e) => {
    e.stopPropagation()
    menu.remove() // close floating menu

    // Read current caption from attrs (fresh from doc, not stale closure)
    const currentAlt = attrs.alt || ""

    // Dispatch a custom event to the container so the nodeView can show
    // its inline editor. This keeps the input completely outside ProseMirror's DOM.
    const ev = new CustomEvent("caption:edit", { bubbles: false, detail: { alt: currentAlt } })
    container.dispatchEvent(ev)
  })
  menu.appendChild(captionBtn)

  // ── Visual Translate button ──
  if (onVisualTranslate) {
    const visualDivider = document.createElement("div")
    visualDivider.style.cssText = `width: 1px; background: rgba(255,255,255,0.2); margin: 4px 2px;`
    menu.appendChild(visualDivider)

    const hasDesc = !!attrs.visualDescription

    // ── AI sparkle icon (two overlapping 4-point stars) ──
    const AI_ICON = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M8 1l1.2 3.6L12.5 6l-3.3 1.4L8 11l-1.2-3.6L3.5 6l3.3-1.4L8 1z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>
      <path d="M4 10l.7 2L6.5 13l-1.8.8L4 16l-.7-2.2L1.5 13l1.8-.8L4 10z" stroke="currentColor" stroke-width="0.8" stroke-linejoin="round" opacity="0.7"/>
      <path d="M12 3l.5 1.5L14 5l-1.5.6L12 8l-.5-1.4L10 5l1.5-.6L12 3z" stroke="currentColor" stroke-width="0.8" stroke-linejoin="round" opacity="0.7"/>
    </svg>`

    const LOADING_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-dasharray="30 70" stroke-linecap="round">
        <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="1s" repeatCount="indefinite"/>
      </circle>
    </svg>`

    const visualBtn = document.createElement("button")
    visualBtn.innerHTML = AI_ICON
    visualBtn.title = hasDesc ? "Re-generate description" : "Visual Translate — generate AI description"
    visualBtn.style.cssText = `
      padding: 6px 8px;
      border: none;
      background: transparent;
      border-radius: 4px;
      cursor: pointer;
      color: ${hasDesc ? "#6ee7b7" : "white"};
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s;
    `
    visualBtn.addEventListener("mouseenter", () => { visualBtn.style.background = "rgba(255,255,255,0.15)" })
    visualBtn.addEventListener("mouseleave", () => { visualBtn.style.background = "transparent" })

    visualBtn.addEventListener("click", (e) => {
      e.stopPropagation()
      const imgId = attrs.imageId || imageId || ""
      if (!imgId || !editor) return
      if (_isImageGenerating(imgId)) return // already generating

      visualBtn.style.cursor = "wait"
      visualBtn.style.opacity = "0.6"
      visualBtn.innerHTML = LOADING_ICON

      _runVisualTranslate(attrs.src, imgId, editor, container)
      menu.remove()
    })

    menu.appendChild(visualBtn)
  }

  container.style.position = "relative"
  container.appendChild(menu)

  // Close menu when clicking outside
  setTimeout(() => {
    document.addEventListener("click", function closeMenu(e) {
      if (!menu.contains(e.target as HTMLElement) && !container.contains(e.target as HTMLElement)) {
        menu.remove()
        document.removeEventListener("click", closeMenu)
      }
    })
  }, 10)
}

// ──────────────────────────────────────────────
// DistillBlock Node Extension
// ──────────────────────────────────────────────
function createDistillBlockExtension(onNavigate?: (noteId: string) => void) {
  return Node.create({
    name: "distillBlock",
    group: "block",
    atom: true,
    draggable: false,
    selectable: false,
    defining: true,
    isolating: true,

    addAttributes() {
      return {
        blockId: {
          default: null,
          parseHTML: (element: HTMLElement) => element.getAttribute("data-block-id"),
          renderHTML: (attrs: any) => ({ "data-block-id": attrs.blockId }),
        },
        sourceNoteId: {
          default: null,
          parseHTML: (element: HTMLElement) => element.getAttribute("data-source-note-id"),
          renderHTML: (attrs: any) => ({ "data-source-note-id": attrs.sourceNoteId }),
        },
        sourceTitle: {
          default: "Untitled",
          parseHTML: (element: HTMLElement) => element.getAttribute("data-source-title"),
          renderHTML: (attrs: any) => ({ "data-source-title": attrs.sourceTitle }),
        },
        text: {
          default: "",
          parseHTML: (element: HTMLElement) => {
            const encoded = element.getAttribute("data-text")
            return encoded ? decodeURIComponent(encoded) : ""
          },
          renderHTML: (attrs: any) => ({ "data-text": encodeURIComponent(attrs.text || "") }),
        },
        loading: {
          default: false,
          parseHTML: (element: HTMLElement) => element.getAttribute("data-loading") === "true",
          renderHTML: (attrs: any) => ({ "data-loading": attrs.loading ? "true" : "false" }),
        },
      }
    },

    parseHTML() {
      return [{ tag: 'div[data-type="distill-block"]' }]
    },

    renderHTML({ HTMLAttributes }) {
      return ["div", mergeAttributes(HTMLAttributes, { "data-type": "distill-block" })]
    },

    addNodeView() {
      return ({ node, getPos, editor }) => {
        const dom = document.createElement("div")
        dom.setAttribute("data-type", "distill-block")
        dom.setAttribute("data-block-id", node.attrs.blockId)
        dom.setAttribute("data-loading", node.attrs.loading ? "true" : "false")
        dom.className = "distill-block"
        dom.style.cssText = `
          border: 1px solid rgba(26,94,61,0.2); border-left: 4px solid #1A5E3D;
          border-radius: 4px; margin: 12px 0; background: rgba(26,94,61,0.03);
          overflow: hidden; position: relative;
        `
        dom.contentEditable = "false"
        // Disable drag for loading blocks — ProseMirror sets draggable="true"
        // on this element via node spec; dom.draggable property overrides it.
        if (node.attrs.loading) {
          dom.draggable = false
          dom.style.cursor = "default"
        }

        // Header
        const header = document.createElement("div")
        header.style.cssText = `
          display: flex; align-items: center; gap: 6px; padding: 6px 10px;
          background: rgba(26,94,61,0.06); border-bottom: 1px solid rgba(26,94,61,0.12); font-size: 12px;
        `

        const handle = document.createElement("span")
        handle.textContent = "⠿"
        // Disable drag for loading blocks — dragging a loading placeholder
        // moves it to a new position, so the distill result can't find it.
        handle.style.cssText = node.attrs.loading
          ? `cursor: not-allowed; color: #bbb; font-size: 14px; user-select: none;`
          : `cursor: grab; color: #666; font-size: 14px; user-select: none;`
        if (node.attrs.loading) {
          handle.addEventListener("dragstart", (e) => { e.preventDefault(); e.stopPropagation() })
        }

        const link = document.createElement("span")
        link.textContent = `📎 ${node.attrs.sourceTitle}`
        link.style.cssText = `color: #1A5E3D; text-decoration: none; flex: 1; font-weight: 500; cursor: pointer;`
        link.addEventListener("click", (e) => {
          e.preventDefault()
          e.stopPropagation()
          // Call navigation callback directly
          if (onNavigate) {
            onNavigate(node.attrs.sourceNoteId)
          }
        })
        link.addEventListener("mouseenter", () => {
          link.style.textDecoration = "underline"
        })
        link.addEventListener("mouseleave", () => {
          link.style.textDecoration = "none"
        })

        const badge = document.createElement("span")
        badge.textContent = node.attrs.sourceNoteId?.slice(-3) || "?"
        badge.style.cssText = `
          background: #1A5E3D; color: white; border-radius: 2px;
          padding: 1px 5px; font-size: 10px; font-weight: 600;
        `

        const delBtn = document.createElement("button")
        delBtn.textContent = "✕"
        delBtn.style.cssText = `
          background: none; border: none; cursor: pointer; color: #999;
          font-size: 14px; padding: 0 2px; line-height: 1;
        `
        delBtn.addEventListener("click", () => {
          if (typeof getPos === "function") {
            const pos = getPos()
            if (pos !== undefined) {
              const blockId = node.attrs.blockId
              const sourceNoteId = node.attrs.sourceNoteId
              editor.chain().focus().deleteRange({ from: pos, to: pos + node.nodeSize }).run()
              // Dispatch on editor.view.dom (always in document) — dom is detached
              // after deleteRange, so events dispatched on it won't bubble.
              if (blockId || sourceNoteId) {
                const detail = { blockId, sourceNoteId }
                const event = new CustomEvent("distill:block-remove", { bubbles: true, detail })
                editor.view.dom.dispatchEvent(event)
              }
            }
          }
        })
        delBtn.addEventListener("mouseenter", () => { delBtn.style.color = "#f44336" })
        delBtn.addEventListener("mouseleave", () => { delBtn.style.color = "#999" })

        header.append(handle, link, badge, delBtn)

        // Content container with height limit
        const contentWrapper = document.createElement("div")
        contentWrapper.style.cssText = `
          position: relative;
          max-height: 200px;
          overflow: hidden;
          transition: max-height 0.3s ease;
        `

        const content = document.createElement("div")
        content.style.cssText = `padding: 10px 14px; font-size: 13px; line-height: 1.6; color: #333;`

        // Loading state
        if (node.attrs.loading) {
          content.innerHTML = `
            <div style="display: flex; align-items: center; gap: 8px; color: #666;">
              <div class="loading-spinner" style="
                width: 16px; height: 16px; border: 2px solid #e0e0e0;
                border-top: 2px solid #1A5E3D; border-radius: 50%;                animation: spin 1s linear infinite;
              "></div>
              <span>⏳ Distilling content from "${node.attrs.sourceTitle}"...</span>
            </div>
          `

          // Add animation style
          if (!document.getElementById("distill-loading-style")) {
            const style = document.createElement("style")
            style.id = "distill-loading-style"
            style.textContent = `
              @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
              }
            `
            document.head.appendChild(style)
          }
        } else {
          content.innerHTML = renderMarkdown(node.attrs.text)
        }

        contentWrapper.appendChild(content)

        // Expand button (only show if content is long)
        const expandBtn = document.createElement("button")
        expandBtn.textContent = "▼ Show more"
        expandBtn.style.cssText = `
          display: none;
          width: 100%;
          padding: 6px;
          background: linear-gradient(transparent, rgba(26,94,61,0.03));
          border: none;
          border-top: 1px solid rgba(26,94,61,0.12);
          color: #1A5E3D;
          font-size: 12px;
          cursor: pointer;
          text-align: center;
        `
        expandBtn.addEventListener("click", () => {
          const isExpanded = contentWrapper.style.maxHeight === "none"
          contentWrapper.style.maxHeight = isExpanded ? "200px" : "none"
          expandBtn.textContent = isExpanded ? "▼ Show more" : "▲ Show less"
        })

        dom.append(header, contentWrapper, expandBtn)

        // Check if content overflows
        requestAnimationFrame(() => {
          if (content.scrollHeight > 200) {
            expandBtn.style.display = "block"
          }
        })

        return {
          dom,
          ignoreMutation: () => true,
          update: (updatedNode: ProseMirrorNode) => {
            if (updatedNode.type.name !== "distillBlock") return false

            // Update loading state
            if (updatedNode.attrs.loading) {
              content.innerHTML = `
                <div style="display: flex; align-items: center; gap: 8px; color: #666;">
                  <div style="
                    width: 16px; height: 16px; border: 2px solid #e0e0e0;
                    border-top: 2px solid #1A5E3D; border-radius: 50%;                    animation: spin 1s linear infinite;
                  "></div>
                  <span>⏳ Distilling content from "${updatedNode.attrs.sourceTitle}"...</span>
                </div>
              `
            } else {
              content.innerHTML = renderMarkdown(updatedNode.attrs.text)
            }

            link.textContent = `📎 ${updatedNode.attrs.sourceTitle}`
            badge.textContent = updatedNode.attrs.sourceNoteId?.slice(-3) || "?"
            dom.setAttribute("data-block-id", updatedNode.attrs.blockId)
            dom.setAttribute("data-loading", updatedNode.attrs.loading ? "true" : "false")

            // Toggle handle drag state on loading transition
            if (updatedNode.attrs.loading) {
              handle.style.cursor = "not-allowed"
              handle.style.color = "#bbb"
              handle.setAttribute("draggable", "false")
              dom.draggable = false
              dom.style.cursor = "default"
            } else {
              handle.style.cursor = "grab"
              handle.style.color = "#666"
              handle.removeAttribute("draggable")
              dom.draggable = true
              dom.style.cursor = ""
            }

            // Re-check overflow
            requestAnimationFrame(() => {
              if (content.scrollHeight > 200) {
                expandBtn.style.display = "block"
              } else {
                expandBtn.style.display = "none"
              }
            })

            return true
          },
          // NOTE: No destroy() callback here. destroy() fires on every NodeView
          // teardown — including when switching notes (Tiptap replaces content,
          // old NodeViews are destroyed). At that point activeNoteId has already
          // changed but latestContentRef may still hold old content, so saving
          // would overwrite the target note's content. Backspace/Delete removal
          // of distill blocks is detected in handleContentChange instead.
        }
      }
    },

    addStorage() {
      return {
        markdown: {
          serialize: (state: { write: (text: string) => void; ensureNewLine: () => void }, node: ProseMirrorNode) => {
            const { blockId, sourceNoteId, sourceTitle, text, loading } = node.attrs
            const loadingExtra = loading ? ',"loading":true' : ''
            state.write(`:::distill-block{"id":"${blockId}","source":"${sourceNoteId}","source-title":"${sourceTitle}"${loadingExtra}}\n`)
            state.write(text + "\n")
            state.write(":::\n\n")  // double newline — terminates HTML block for next parse cycle
          },
        },
      }
    },
  })
}

// ──────────────────────────────────────────────
// Callout Node Extension
// ──────────────────────────────────────────────
function createCalloutExtension() {
  return Node.create({
    name: "callout",
    group: "block",
    content: "block+",
    defining: true,

    addAttributes() {
      return {
        type: {
          default: "info",
          parseHTML: (element: HTMLElement) => element.getAttribute("data-callout-type") || "info",
          renderHTML: (attrs: any) => ({ "data-callout-type": attrs.type }),
        },
      }
    },

    parseHTML() {
      return [{ tag: 'div[data-type="callout"]' }]
    },

    renderHTML({ HTMLAttributes }) {
      return ["div", mergeAttributes(HTMLAttributes, { "data-type": "callout" })]
    },

    addNodeView() {
      return ({ node }) => {
        const dom = document.createElement("div")
        dom.setAttribute("data-type", "callout")
        dom.setAttribute("data-callout-type", node.attrs.type)

        const colors: Record<string, { bg: string; border: string; icon: string }> = {
          info: { bg: "rgba(26,94,61,0.06)", border: "#1A5E3D", icon: "💡" },
          warning: { bg: "#fff3e0", border: "#f57c00", icon: "⚠️" },
          success: { bg: "#e8f5e9", border: "#388e3c", icon: "✅" },
          error: { bg: "#ffebee", border: "#d32f2f", icon: "❌" },
        }

        const color = colors[node.attrs.type] || colors.info
        dom.style.cssText = `
          border-left: 4px solid ${color.border}; background: ${color.bg};
          border-radius: 4px; padding: 12px 16px; margin: 8px 0;
        `

        const icon = document.createElement("span")
        icon.textContent = color.icon
        icon.style.cssText = `margin-right: 8px;`

        const content = document.createElement("div")
        content.style.cssText = `display: inline;`

        dom.append(icon, content)

        return {
          dom,
          contentDOM: content,
        }
      }
    },
  })
}

// ──────────────────────────────────────────────
// Slash Command Extension
// ──────────────────────────────────────────────
function createSlashCommandExtension(
  onDistill?: () => void,
  onImageUpload?: (file: File) => Promise<string>
) {
  return Extension.create({
    name: "slashCommand",
    addKeyboardShortcuts() {
      return {
        "/": ({ editor }) => {
          const { from } = editor.state.selection
          const textBefore = editor.state.doc.textBetween(Math.max(0, from - 1), from, "")
          if (from === 1 || textBefore === "\n" || textBefore === "") {
            showSlashMenu(editor, from, onDistill, onImageUpload)
            return true
          }
          return false
        },
      }
    },
  })
}

// ──────────────────────────────────────────────
// Show Slash Menu
// ──────────────────────────────────────────────
function showSlashMenu(
  editor: any,
  position: number,
  onDistill?: () => void,
  onImageUpload?: (file: File) => Promise<string>
) {
  const existingMenu = document.getElementById("slash-menu")
  if (existingMenu) existingMenu.remove()

  const commandGroups = [
    {
      label: "Basic Blocks",
      commands: [
        { label: "Heading 1", icon: "H1", desc: "Large heading", action: () => editor.chain().focus().toggleHeading({ level: 1 }).run() },
        { label: "Heading 2", icon: "H2", desc: "Medium heading", action: () => editor.chain().focus().toggleHeading({ level: 2 }).run() },
        { label: "Heading 3", icon: "H3", desc: "Small heading", action: () => editor.chain().focus().toggleHeading({ level: 3 }).run() },
        { label: "Bullet List", icon: "•", desc: "Unordered list", action: () => editor.chain().focus().toggleBulletList().run() },
        { label: "Numbered List", icon: "1.", desc: "Ordered list", action: () => editor.chain().focus().toggleOrderedList().run() },
        { label: "Task List", icon: "☑️", desc: "Track tasks", action: () => editor.chain().focus().insertContent('<ul data-type="taskList"><li data-type="taskItem" data-checked="false">Task</li></ul>').run() },
        { label: "Quote", icon: "❝", desc: "Blockquote", action: () => editor.chain().focus().toggleBlockquote().run() },
        { label: "Divider", icon: "—", desc: "Horizontal line", action: () => editor.chain().focus().setHorizontalRule().run() },
      ],
    },
    {
      label: "Media",
      commands: [
        {
          label: "Image",
          icon: "🖼️",
          desc: "Upload image",
          action: () => {
            const input = document.createElement("input")
            input.type = "file"
            input.accept = "image/*"
            input.onchange = async () => {
              const file = input.files?.[0]
              if (file && onImageUpload) {
                try {
                  const url = await onImageUpload(file)
                  editor.chain().focus().insertContent({ type: "image", attrs: { src: url } }).run()
                } catch (err) {
                  console.error("Upload failed:", err)
                }
              }
            }
            input.click()
          },
        },
        {
          label: "Video",
          icon: "🎬",
          desc: "YouTube embed",
          action: () => {
            const url = prompt("YouTube URL:")
            if (url) {
              const match = url.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\s]+)/)
              if (match) editor.chain().focus().setYoutubeVideo({ src: `https://www.youtube.com/watch?v=${match[1]}` }).run()
            }
          },
        },
      ],
    },
    {
      label: "Advanced",
      commands: [
        { label: "Table", icon: "📊", desc: "Insert table", action: () => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
        { label: "Code Block", icon: "💻", desc: "Code block", action: () => editor.chain().focus().toggleCodeBlock().run() },
        { label: "Callout", icon: "💡", desc: "Info callout", action: () => editor.chain().focus().insertContent({ type: "callout", attrs: { type: "info" }, content: [{ type: "paragraph", content: [{ type: "text", text: "Callout" }] }] }).run() },
      ],
    },
    {
      label: "AI & Integration",
      commands: [
        { label: "Distill Block", icon: "🔗", desc: "Extract from note", action: () => onDistill ? onDistill() : alert("Drag a note to distill") },
      ],
    },
  ]

  const menu = document.createElement("div")
  menu.id = "slash-menu"
  menu.style.cssText = `
    position: fixed;
    background: white;
    border: 1px solid #e0e0e0;
    border-radius: 12px;
    box-shadow: 0 8px 30px rgba(0,0,0,0.12);
    padding: 8px 0;
    z-index: 1000;
    min-width: 280px;
    max-height: 400px;
    overflow-y: auto;
  `

  const searchContainer = document.createElement("div")
  searchContainer.style.cssText = `padding: 8px 14px; border-bottom: 1px solid #e0e0e0;`
  const searchInput = document.createElement("input")
  searchInput.placeholder = "Filter..."
  searchInput.style.cssText = `width: 100%; border: none; outline: none; font-size: 14px;`
  searchContainer.appendChild(searchInput)

  const commandList = document.createElement("div")
  menu.append(searchContainer, commandList)

  let allCommands: any[] = []
  let filteredCommands: any[] = []
  let selectedIndex = 0

  commandGroups.forEach((group) => {
    group.commands.forEach((cmd) => {
      allCommands.push({ ...cmd, group: group.label })
    })
  })
  filteredCommands = [...allCommands]

  function renderCommands(filter = "") {
    commandList.innerHTML = ""
    selectedIndex = 0

    filteredCommands = allCommands.filter((cmd) => {
      const searchStr = `${cmd.label} ${cmd.desc} ${cmd.group}`.toLowerCase()
      return searchStr.includes(filter.toLowerCase())
    })

    if (filteredCommands.length === 0) {
      commandList.innerHTML = '<div style="padding: 16px; text-align: center; color: #999;">No commands</div>'
      return
    }

    const grouped: any = {}
    filteredCommands.forEach((cmd) => {
      if (!grouped[cmd.group]) grouped[cmd.group] = []
      grouped[cmd.group].push(cmd)
    })

    let itemIndex = 0
    Object.entries(grouped).forEach(([, commands]) => {
      ;(commands as any[]).forEach((cmd) => {
        const item = document.createElement("div")
        item.style.cssText = `display: flex; align-items: center; padding: 8px 14px; cursor: pointer;`
        item.dataset.index = String(itemIndex++)

        item.innerHTML = `
          <div style="width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; background: #f5f5f5; border-radius: 6px; margin-right: 10px; font-size: 16px;">${cmd.icon}</div>
          <div style="flex: 1;">
            <div style="font-size: 13px; font-weight: 500;">${cmd.label}</div>
            <div style="font-size: 11px; color: #666;">${cmd.desc}</div>
          </div>
        `

        item.addEventListener("mouseenter", () => { item.style.background = "#f0f7ff" })
        item.addEventListener("mouseleave", () => { item.style.background = "white" })
        item.addEventListener("click", () => { menu.remove(); cmd.action() })

        commandList.appendChild(item)
      })
    })

    updateSelection()
  }

  function updateSelection() {
    const items = commandList.querySelectorAll("div[data-index]")
    items.forEach((item, i) => {
      ;(item as HTMLElement).style.background = i === selectedIndex ? "#f0f7ff" : "white"
    })
    const selectedItem = items[selectedIndex] as HTMLElement
    if (selectedItem) selectedItem.scrollIntoView({ block: "nearest" })
  }

  searchInput.addEventListener("keydown", (e) => {
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault()
        selectedIndex = Math.min(selectedIndex + 1, filteredCommands.length - 1)
        updateSelection()
        break
      case "ArrowUp":
        e.preventDefault()
        selectedIndex = Math.max(selectedIndex - 1, 0)
        updateSelection()
        break
      case "Enter":
        e.preventDefault()
        const cmd = filteredCommands[selectedIndex]
        if (cmd) { menu.remove(); cmd.action() }
        break
      case "Escape":
        e.preventDefault()
        menu.remove()
        break
    }
  })

  searchInput.addEventListener("input", (e) => {
    renderCommands((e.target as HTMLInputElement).value)
  })

  // Render commands FIRST so we measure actual menu dimensions
  menu.style.visibility = "hidden"
  menu.style.position = "fixed"
  document.body.appendChild(menu)
  renderCommands()

  // Position menu with actual content dimensions
  const coords = editor.view.coordsAtPos(position)
  const PADDING = 12

  const menuRect = menu.getBoundingClientRect()
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight

  let top = coords.bottom + 8
  let left = coords.left

  if (left + menuRect.width > viewportWidth - PADDING) left = viewportWidth - menuRect.width - PADDING
  if (left < PADDING) left = PADDING
  if (top + menuRect.height > viewportHeight - PADDING) top = coords.top - menuRect.height - 8
  if (top < PADDING) { top = PADDING; menu.style.maxHeight = `${viewportHeight - PADDING * 2}px` }

  menu.style.top = `${top}px`
  menu.style.left = `${left}px`
  menu.style.visibility = "visible"

  searchInput.focus()

  setTimeout(() => {
    document.addEventListener("click", function closeMenu(e) {
      if (!menu.contains(e.target as HTMLElement)) {
        menu.remove()
        document.removeEventListener("click", closeMenu)
      }
    })
  }, 10)
}

// ──────────────────────────────────────────────
// Table Context Menu
// ──────────────────────────────────────────────
function showTableContextMenu(event: MouseEvent, editor: any) {
  if (_isPreviewMode) return
  const existingMenu = document.getElementById("table-context-menu")
  if (existingMenu) existingMenu.remove()

  const menu = document.createElement("div")
  menu.id = "table-context-menu"
  menu.style.cssText = `
    position: fixed; background: white; border: 1px solid #e0e0e0;
    border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    padding: 4px 0; z-index: 1000; min-width: 180px;
  `

  const commands = [
    { label: "➕ Add row above", action: () => editor.chain().focus().addRowBefore().run() },
    { label: "➕ Add row below", action: () => editor.chain().focus().addRowAfter().run() },
    { label: "➕ Add column left", action: () => editor.chain().focus().addColumnBefore().run() },
    { label: "➕ Add column right", action: () => editor.chain().focus().addColumnAfter().run() },
    { divider: true },
    { label: "🗑️ Delete row", action: () => editor.chain().focus().deleteRow().run() },
    { label: "🗑️ Delete column", action: () => editor.chain().focus().deleteColumn().run() },
    { divider: true },
    { label: "❌ Delete table", action: () => editor.chain().focus().deleteTable().run() },
  ]

  commands.forEach((cmd) => {
    if ((cmd as any).divider) {
      const divider = document.createElement("div")
      divider.style.cssText = `height: 1px; background: #e0e0e0; margin: 4px 0;`
      menu.appendChild(divider)
      return
    }
    const item = document.createElement("div")
    item.style.cssText = `padding: 8px 14px; cursor: pointer; font-size: 13px;`
    item.textContent = (cmd as any).label
    item.addEventListener("mouseenter", () => { item.style.background = "#f0f7ff" })
    item.addEventListener("mouseleave", () => { item.style.background = "white" })
    item.addEventListener("click", () => { menu.remove(); (cmd as any).action() })
    menu.appendChild(item)
  })

  menu.style.top = `${event.clientY}px`
  menu.style.left = `${event.clientX}px`
  document.body.appendChild(menu)

  const rect = menu.getBoundingClientRect()
  if (rect.right > window.innerWidth) menu.style.left = `${window.innerWidth - rect.width - 8}px`
  if (rect.bottom > window.innerHeight) menu.style.top = `${window.innerHeight - rect.height - 8}px`

  setTimeout(() => {
    document.addEventListener("click", function closeMenu(e) {
      if (!menu.contains(e.target as HTMLElement)) {
        menu.remove()
        document.removeEventListener("click", closeMenu)
      }
    })
  }, 10)
}

// ──────────────────────────────────────────────
// Table Floating Menu (bubble menu)
// ──────────────────────────────────────────────
function showTableFloatingMenu(table: HTMLElement, editor: any) {
  if (_isPreviewMode) return
  const existing = document.getElementById("table-floating-menu")
  if (existing) existing.remove()

  const menu = document.createElement("div")
  menu.id = "table-floating-menu"
  menu.style.cssText = `
    position: absolute; top: -44px; left: 0; transform: none;
    background: #1e1e1e; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.3);
    padding: 6px; display: flex; gap: 4px; z-index: 100; white-space: nowrap;
    align-items: center;
  `

  // ★ KEY FIX: prevent mousedown from stealing focus out of ProseMirror.
  // Without this, clicking a button causes the browser to blur the editor,
  // so editor.chain().focus() cannot restore a valid table selection.
  // Exception: <input> elements need focus for typing.
  menu.addEventListener("mousedown", (e) => {
    if ((e.target as HTMLElement).tagName !== "INPUT") e.preventDefault()
  })

  const btnStyle = `
    padding: 5px 7px; border: none; background: transparent;
    border-radius: 4px; cursor: pointer; color: white;
    display: flex; align-items: center; justify-content: center;
    transition: background 0.15s; font-size: 14px;
  `
  const hover = (btn: HTMLButtonElement) => {
    btn.addEventListener("mouseenter", () => { btn.style.background = "rgba(255,255,255,0.15)" })
    btn.addEventListener("mouseleave", () => { btn.style.background = "transparent" })
  }

  const makeBtn = (title: string, svg: string, action: () => void) => {
    const b = document.createElement("button")
    b.innerHTML = svg
    b.title = title
    b.style.cssText = btnStyle
    hover(b)
    b.addEventListener("click", (e) => { e.stopPropagation(); action() })
    return b
  }

  const makeDivider = () => {
    const d = document.createElement("div")
    d.style.cssText = `width: 1px; background: rgba(255,255,255,0.2); margin: 2px; height: 22px;`
    return d
  }

  // Row buttons
  menu.appendChild(makeBtn("Insert row above",
    `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 4h12M8 1v3M5 1l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><rect x="2" y="6" width="12" height="3" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/><rect x="2" y="11" width="12" height="3" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/></svg>`,
    () => editor.chain().focus().addRowBefore().run()))
  menu.appendChild(makeBtn("Insert row below",
    `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 12h12M8 15v-3M5 15l3-3 3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><rect x="2" y="2" width="12" height="3" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/><rect x="2" y="7" width="12" height="3" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/></svg>`,
    () => editor.chain().focus().addRowAfter().run()))

  menu.appendChild(makeDivider())

  // Column buttons
  menu.appendChild(makeBtn("Insert column left",
    `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 2v12M1 8h3M1 5l3 3-3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><rect x="6" y="2" width="3" height="12" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/><rect x="11" y="2" width="3" height="12" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/></svg>`,
    () => editor.chain().focus().addColumnBefore().run()))
  menu.appendChild(makeBtn("Insert column right",
    `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M12 2v12M15 8h-3M15 5l-3 3 3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><rect x="2" y="2" width="3" height="12" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/><rect x="7" y="2" width="3" height="12" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/></svg>`,
    () => editor.chain().focus().addColumnAfter().run()))

  menu.appendChild(makeDivider())

  // Delete row/column buttons
  menu.appendChild(makeBtn("Delete row",
    `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 6h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M5 3l8 10M13 3L5 13" stroke="currentColor" stroke-width="1" stroke-linecap="round" opacity="0.5"/></svg>`,
    () => editor.chain().focus().deleteRow().run()))
  menu.appendChild(makeBtn("Delete column",
    `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 2v12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M3 5l10 8M3 13L13 5" stroke="currentColor" stroke-width="1" stroke-linecap="round" opacity="0.5"/></svg>`,
    () => editor.chain().focus().deleteColumn().run()))

  menu.appendChild(makeDivider())

  // Delete table — red icon
  const delTableBtn = document.createElement("button")
  delTableBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="12" height="12" stroke="#ef4444" stroke-width="1.3" rx="1"/><path d="M5 5l6 6M11 5l-6 6" stroke="#ef4444" stroke-width="1.5" stroke-linecap="round"/></svg>`
  delTableBtn.title = "Delete table"
  delTableBtn.style.cssText = btnStyle
  hover(delTableBtn)
  delTableBtn.addEventListener("click", (e) => { e.stopPropagation(); editor.chain().focus().deleteTable().run(); menu.remove() })
  menu.appendChild(delTableBtn)

  menu.appendChild(makeDivider())

  // Resize grid button (9 rows × 5 columns grid + custom inputs)
  const resizeWrap = document.createElement("div")
  resizeWrap.style.cssText = `position: relative;`
  const resizeBtn = document.createElement("button")
  resizeBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="1" y="1" width="6" height="6" stroke="currentColor" stroke-width="1.3" rx="0.5"/><rect x="9" y="1" width="6" height="6" stroke="currentColor" stroke-width="1.3" rx="0.5"/><rect x="1" y="9" width="6" height="6" stroke="currentColor" stroke-width="1.3" rx="0.5"/><rect x="9" y="9" width="6" height="6" stroke="currentColor" stroke-width="1.3" rx="0.5"/></svg>`
  resizeBtn.title = "Resize table"
  resizeBtn.style.cssText = btnStyle
  hover(resizeBtn)
  resizeWrap.appendChild(resizeBtn)

  let dropdownEl: HTMLDivElement | null = null
  let closeGridHandler: ((ev: MouseEvent) => void) | null = null

  const closeDropdown = () => {
    if (dropdownEl) { dropdownEl.remove(); dropdownEl = null }
    if (closeGridHandler) { document.removeEventListener("click", closeGridHandler); closeGridHandler = null }
  }

  // Find the position of a cell at (targetRow, targetCol) inside a SPECIFIC table node
  // (not just the first table in the document)
  const findCellPos = (tablePos: number, targetRow: number, targetCol: number): number => {
    let result = -1
    let currentRow = -1
    let currentCol = 0

    editor.view.state.doc.nodesBetween(
      tablePos,
      tablePos + editor.view.state.doc.nodeAt(tablePos)!.nodeSize,
      (node: ProseMirrorNode, pos: number) => {
        if (result >= 0) return false
        if (pos === tablePos) return true // skip table itself, descend
        if (node.type.name === "tableRow") {
          currentRow++
          currentCol = 0
          return true
        }
        if (node.type.name === "tableCell" || node.type.name === "tableHeader") {
          if (currentRow === targetRow && currentCol === targetCol) {
            result = pos + 1 // inside the cell
            return false
          }
          currentCol++
          return false
        }
        return false
      }
    )
    return result
  }

  // Find the DOM table element's position in the ProseMirror document
  const findTablePos = (tableEl: HTMLElement): number => {
    let result = -1
    editor.view.state.doc.descendants((node: ProseMirrorNode, pos: number) => {
      if (result >= 0) return false
      if (node.type.name === "table") {
        // Check if this table node's DOM matches the clicked element
        const dom = editor.view.nodeDOM(pos) as HTMLElement | null
        if (dom === tableEl || dom?.contains(tableEl) || tableEl.contains(dom)) {
          result = pos
          return false
        }
      }
      return result < 0
    })
    return result
  }

  // Resize table — operates on the clicked table only, from outer boundaries
  const resizeTable = (targetRows: number, targetCols: number) => {
    try {
      const tablePos = findTablePos(table)
      if (tablePos < 0) return

      const tableNode = editor.view.state.doc.nodeAt(tablePos)
      if (!tableNode) return

      const curRows = tableNode.childCount
      const curCols = curRows > 0 ? tableNode.firstChild!.childCount : 0

      // Helper: set cursor in target cell via editor.chain(), return true if succeeded
      const goTo = (row: number, col: number): boolean => {
        const cellPos = findCellPos(tablePos, row, col)
        if (cellPos < 0) return false
        editor.chain().setTextSelection(cellPos).run()
        return true
      }

      // Expand rows — add after last row
      for (let i = curRows; i < targetRows; i++) {
        const lastRow = editor.view.state.doc.nodeAt(tablePos)!.childCount - 1
        const lastCol = editor.view.state.doc.nodeAt(tablePos)!.child(lastRow).childCount - 1
        if (!goTo(lastRow, lastCol)) break
        editor.commands.addRowAfter()
      }

      // Shrink rows — delete last row
      for (let i = curRows; i > targetRows; i--) {
        const t = editor.view.state.doc.nodeAt(tablePos)
        if (!t || t.childCount <= 1) break
        const lastRow = t.childCount - 1
        const lastCol = t.child(lastRow).childCount - 1
        if (!goTo(lastRow, lastCol)) break
        editor.commands.deleteRow()
      }

      // Expand columns — add after last column
      for (let i = curCols; i < targetCols; i++) {
        const t = editor.view.state.doc.nodeAt(tablePos)
        if (!t || t.childCount === 0) break
        const lastCol = t.firstChild!.childCount - 1
        if (!goTo(0, lastCol)) break
        editor.commands.addColumnAfter()
      }

      // Shrink columns — delete last column
      for (let i = curCols; i > targetCols; i--) {
        const t = editor.view.state.doc.nodeAt(tablePos)
        if (!t || t.childCount === 0 || t.firstChild!.childCount <= 1) break
        const lastCol = t.firstChild!.childCount - 1
        if (!goTo(0, lastCol)) break
        editor.commands.deleteColumn()
      }
    } catch { /* table may become invalid during resize */ }
  }

  resizeBtn.addEventListener("click", (e) => {
    e.stopPropagation()
    if (dropdownEl) { closeDropdown(); return }

    const dropdown = document.createElement("div")
    dropdown.className = "table-resize-dropdown"
    dropdown.style.cssText = `
      position: absolute; top: 100%; left: 0; margin-top: 4px;
      background: #2c2c2c; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.3);
      padding: 12px; z-index: 101;
    `
    dropdownEl = dropdown

    // 9 rows × 5 columns grid
    const GRID_ROWS = 9
    const GRID_COLS = 5
    const grid = document.createElement("div")
    grid.style.cssText = `display: grid; grid-template-columns: repeat(${GRID_COLS}, 18px); gap: 2px; justify-content: center;`
    dropdown.appendChild(grid)

    // Preview: target dimensions on hover
    const preview = document.createElement("div")
    preview.style.cssText = `color: rgba(255,255,255,0.6); font-size: 11px; margin-top: 8px; text-align: center; min-height: 16px;`
    preview.textContent = "hover to select"
    dropdown.appendChild(preview)

    const cells: HTMLDivElement[] = []
    for (let r = 1; r <= GRID_ROWS; r++) {
      for (let c = 1; c <= GRID_COLS; c++) {
        const cell = document.createElement("div")
        cell.dataset.row = String(r)
        cell.dataset.col = String(c)
        cell.style.cssText = `width: 18px; height: 18px;
          border: 1px solid rgba(255,255,255,0.12); background: transparent;
          border-radius: 2px; cursor: pointer; transition: background 0.06s, border-color 0.06s;`
        cell.addEventListener("mouseenter", () => {
          // Highlight all cells from (1,1) to (r,c)
          cells.forEach((d) => {
            const cr = Number(d.dataset.row)
            const cc = Number(d.dataset.col)
            if (cr <= r && cc <= c) {
              d.style.background = "rgba(59,130,246,0.6)"
              d.style.borderColor = "rgba(59,130,246,0.9)"
            } else {
              d.style.background = "transparent"
              d.style.borderColor = "rgba(255,255,255,0.12)"
            }
          })
          preview.textContent = `${r} × ${c}`
        })
        cell.addEventListener("click", (ev) => {
          ev.stopPropagation()
          resizeTable(r, c)
          closeDropdown()
          menu.remove()
        })
        cells.push(cell)
        grid.appendChild(cell)
      }
    }

    // Reset grid highlight when mouse leaves grid area
    grid.addEventListener("mouseleave", () => {
      cells.forEach((d) => {
        d.style.background = "transparent"
        d.style.borderColor = "rgba(255,255,255,0.12)"
      })
      preview.textContent = "hover to select"
    })

    // Divider between grid and custom inputs
    const sep = document.createElement("div")
    sep.style.cssText = `height: 1px; background: rgba(255,255,255,0.15); margin: 10px 0 8px;`
    dropdown.appendChild(sep)

    // Custom row/col inputs
    const inputRow = document.createElement("div")
    inputRow.style.cssText = `display: flex; gap: 8px; align-items: center; justify-content: center;`

    const inputStyle = `width: 48px; height: 26px; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.2);
      border-radius: 4px; color: white; text-align: center; font-size: 12px; outline: none;`
    const labelStyle = `color: rgba(255,255,255,0.5); font-size: 11px;`

    const rowsLabel = document.createElement("span")
    rowsLabel.style.cssText = labelStyle
    rowsLabel.textContent = "rows"
    const rowsInput = document.createElement("input")
    rowsInput.type = "number"
    rowsInput.min = "1"
    rowsInput.value = String(table.querySelectorAll("tr").length)
    rowsInput.style.cssText = inputStyle

    const colsLabel = document.createElement("span")
    colsLabel.style.cssText = labelStyle
    colsLabel.textContent = "cols"
    const colsInput = document.createElement("input")
    colsInput.type = "number"
    colsInput.min = "1"
    colsInput.value = String(table.querySelector("tr")?.querySelectorAll("th,td").length || 3)
    colsInput.style.cssText = inputStyle

    const applyBtn = document.createElement("button")
    applyBtn.textContent = "✓"
    applyBtn.style.cssText = `width: 26px; height: 26px; background: rgba(59,130,246,0.7); border: none;
      border-radius: 4px; color: white; cursor: pointer; font-size: 14px; display: flex; align-items: center; justify-content: center;`
    applyBtn.addEventListener("mouseenter", () => { applyBtn.style.background = "rgba(59,130,246,0.9)" })
    applyBtn.addEventListener("mouseleave", () => { applyBtn.style.background = "rgba(59,130,246,0.7)" })

    const applyCustom = () => {
      const r = Math.max(1, parseInt(rowsInput.value) || 1)
      const c = Math.max(1, parseInt(colsInput.value) || 1)
      resizeTable(r, c)
      closeDropdown()
      menu.remove()
    }
    applyBtn.addEventListener("click", (ev) => { ev.stopPropagation(); applyCustom() })
    rowsInput.addEventListener("keydown", (ev) => { ev.stopPropagation(); if (ev.key === "Enter") applyCustom() })
    colsInput.addEventListener("keydown", (ev) => { ev.stopPropagation(); if (ev.key === "Enter") applyCustom() })

    inputRow.appendChild(rowsLabel)
    inputRow.appendChild(rowsInput)
    inputRow.appendChild(colsLabel)
    inputRow.appendChild(colsInput)
    inputRow.appendChild(applyBtn)
    dropdown.appendChild(inputRow)

    resizeWrap.appendChild(dropdown)

    // Close dropdown when clicking outside it
    setTimeout(() => {
      closeGridHandler = (ev: MouseEvent) => {
        if (dropdownEl && !dropdownEl.contains(ev.target as HTMLElement) && ev.target !== resizeBtn) {
          closeDropdown()
        }
      }
      document.addEventListener("click", closeGridHandler)
    }, 10)
  })

  // Insert resize button as FIRST element in the menu
  menu.insertBefore(resizeWrap, menu.firstChild)
  const resizeDivider = makeDivider()
  menu.insertBefore(resizeDivider, resizeWrap.nextSibling)

  // Prevent clicks inside menu from bubbling to the table/ProseMirror
  menu.addEventListener("click", (e) => e.stopPropagation())

  table.style.position = "relative"
  table.appendChild(menu)

  // Close menu when clicking outside the table
  setTimeout(() => {
    const close = (e: MouseEvent) => {
      if (!table.contains(e.target as HTMLElement)) {
        menu.remove()
        document.removeEventListener("click", close)
      }
    }
    document.addEventListener("click", close)
  }, 10)
}

// ──────────────────────────────────────────────
// Utility: Simple Markdown Renderer
// ──────────────────────────────────────────────
function renderMarkdown(md: string): string {
  return md
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\/li>)/s, "<ul>$1</ul>")
    .replace(/\n/g, "<br>")
}

// ──────────────────────────────────────────────
// Preprocessor / Postprocessor
// ──────────────────────────────────────────────
export function preprocessDistillBlocks(markdown: string): {
  processed: string
  blocks: Array<{ id: string; text: string }>
} {
  const blocks: Array<{ id: string; text: string }> = []

  // First, handle angle-bracket wrapped URLs in images
  // Convert ![alt](<url>) to ![alt](url) for Tiptap
  let decodedMarkdown = markdown.replace(
    /!\[([^\]]*)\]\(<([^>]+)>\)/g,
    (_match, alt, url) => {
      return `![${alt}](${url})`
    }
  )

  // Ensure blank line after self-closing <img /> tags.
  // Without it, markdown-it treats <img> as an HTML block and consumes the
  // following line (e.g. ## Heading) as raw text instead of parsing it.
  decodedMarkdown = decodedMarkdown.replace(
    /(<img [^>]*\/>)(\n?)(?!\n)/g,
    '$1\n\n'
  )

  const processed = decodedMarkdown.replace(
    /:::distill-block(\{[^}]+\})\n([\s\S]*?)\n:::\n?/g,
    (match, jsonAttrs, body) => {
      try {
        const attrs = JSON.parse(jsonAttrs)
        blocks.push({ id: attrs.id, text: body.trim() })
        const loadingAttr = attrs.loading ? ' data-loading="true"' : ''
        // Two newlines after </div> — terminates markdown-it's HTML block mode
        // so following markdown (## headings, **bold**, lists etc.) is parsed correctly.
        // Without the blank line, markdown-it slurps the next line into the HTML block.
        return `<div data-type="distill-block" data-block-id="${attrs.id}" data-source-note-id="${attrs.source}" data-source-title="${attrs["source-title"]}" data-text="${encodeURIComponent(body.trim())}"${loadingAttr}></div>\n\n`
      } catch { return match }
    }
  )
  return { processed, blocks }
}

export function postprocessDistillBlocks(markdown: string): string {
  // Convert distill block divs back to markdown.
  // Preserve all known attributes (id, source, source-title, loading) so the
  // round-trip is idempotent — otherwise "loading" is lost and the loading
  // placeholder can't be found/replaced.
  let processed = markdown.replace(
    /<div[^>]*data-type="distill-block"[^>]*data-block-id="([^"]*)"[^>]*data-source-note-id="([^"]*)"[^>]*data-source-title="([^"]*)"[^>]*data-text="([^"]*)"[^>]*><\/div>/g,
    (_match, blockId, sourceNoteId, sourceTitle, encodedText) => {
      const text = decodeURIComponent(encodedText)
      // Preserve data-loading if present in the original HTML
      const hasLoading = _match.includes('data-loading="true"')
      const extra = hasLoading ? ',"loading":true' : ''
      return `:::distill-block{"id":"${blockId}","source":"${sourceNoteId}","source-title":"${sourceTitle}"${extra}}\n${text}\n:::`
    }
  )

  return processed
}

// ──────────────────────────────────────────────
// Component Props
// ──────────────────────────────────────────────
interface MarkdownEditorProps {
  value: string
  onChange?: (value: string) => void
  className?: string
  minHeight?: string
  placeholder?: string
  children?: ReactNode
  readonly?: boolean
  variant?: "block" | "plain"
  onImageUpload?: (file: File) => Promise<string>
  onNoteLinkClick?: (noteId: string) => void
  onDistill?: () => void
  onDistillNavigate?: (noteId: string) => void // Add this for distill block navigation
  /** Called when the editor instance is ready. Passes back the Tiptap editor. */
  onEditorReady?: (editor: any) => void
  /** Whether to show the built-in formatting toolbar. Default true. */
  showToolbar?: boolean
  /** Called when user clicks Visual Translate on an image. Receives image URL, returns description string. */
  onVisualTranslate?: (imageUrl: string) => Promise<string>
}

// ──────────────────────────────────────────────
// Tiptap Editor Component
// ──────────────────────────────────────────────

/** Lightweight markdown → HTML for paste interception (no deps). */
function markdownToHtml(md: string): string {
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  const inline = (s: string) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/__(.+?)__/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/_(.+?)_/g, "<em>$1</em>")
      .replace(/`([^`]+?)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>')

  const lines = md.split("\n")
  const blocks: string[] = []
  let i = 0
  while (i < lines.length) {
    const l = lines[i]
    // blank line
    if (!l.trim()) { i++; continue }
    // distill block — pass through unchanged (preprocessed separately)
    if (l.trimStart().startsWith(":::distill-block")) {
      const blockLines: string[] = [l]; i++
      while (i < lines.length && !lines[i].trimStart().startsWith(":::")) { blockLines.push(lines[i]); i++ }
      if (i < lines.length) { blockLines.push(lines[i]); i++ }
      blocks.push(blockLines.join("\n")); continue
    }
    // heading
    const m = l.match(/^(#{1,6})\s+(.*)$/)
    if (m) { blocks.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); i++; continue }
    // code block
    if (l.trimStart().startsWith("```")) {
      const code: string[] = []; i++
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) { code.push(esc(lines[i])); i++ }
      if (i < lines.length) i++
      blocks.push(`<pre><code>${code.join("\n")}</code></pre>`); continue
    }
    // hr
    if (/^[-*_]{3,}\s*$/.test(l.trim())) { blocks.push("<hr>"); i++; continue }
    // blockquote
    if (/^>\s?/.test(l)) {
      const qLines: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i])) { qLines.push(lines[i].replace(/^>\s?/, "")); i++ }
      blocks.push(`<blockquote>${inline(qLines.join(" "))}</blockquote>`); continue
    }
    // unordered list
    if (/^\s*[-*+]\s/.test(l)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*+]\s/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*+]\s/, "")); i++ }
      blocks.push(`<ul>${items.map(it => `<li>${inline(it)}</li>`).join("")}</ul>`); continue
    }
    // ordered list
    if (/^\s*\d+\.\s/.test(l)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+\.\s/.test(lines[i])) { items.push(lines[i].replace(/^\s*\d+\.\s/, "")); i++ }
      blocks.push(`<ol>${items.map(it => `<li>${inline(it)}</li>`).join("")}</ol>`); continue
    }
    // paragraph (collect consecutive non-blank lines)
    const pLines: string[] = []
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|>\s?|\s*[-*+]\s|\s*\d+\.\s|[-*_]{3,}\s*$|```|:::)/.test(lines[i])) {
      pLines.push(lines[i]); i++
    }
    if (pLines.length) blocks.push(`<p>${inline(pLines.join(" "))}</p>`)
  }
  return blocks.join("")
}

// ──────────────────────────────────────────────
// Editor Toolbar
// ──────────────────────────────────────────────

const HIGHLIGHT_PRESETS = [
  { color: "#fef08a", label: "Yellow" },
  { color: "#bbf7d0", label: "Green" },
  { color: "#fca5a5", label: "Red" },
  { color: "#c4b5fd", label: "Purple" },
  { color: "#fdba74", label: "Orange" },
]

const TEXT_COLOR_PRESETS = [
  { color: "#000000", label: "Black" },
  { color: "#ef4444", label: "Red" },
  { color: "#3b82f6", label: "Blue" },
  { color: "#22c55e", label: "Green" },
  { color: "#a855f7", label: "Purple" },
  { color: "#f97316", label: "Orange" },
]

function ToolbarBtn({
  active, disabled, tooltip, onClick, children, className,
}: {
  active?: boolean; disabled?: boolean; tooltip: string; onClick: () => void
  children: ReactNode; className?: string
}) {
  return (
    <button
      type="button"
      title={tooltip}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "h-7 w-7 p-0 flex items-center justify-center rounded-sm text-muted-foreground",
        "hover:bg-accent hover:text-foreground transition-colors cursor-pointer",
        "disabled:opacity-30 disabled:pointer-events-none",
        active && "bg-accent text-foreground",
        className,
      )}
    >
      {children}
    </button>
  )
}

function ColorSwatch({
  color, active, tooltip, onClick,
}: {
  color: string; active: boolean; tooltip: string; onClick: () => void
}) {
  return (
    <button
      type="button"
      title={tooltip}
      onClick={onClick}
      className={cn(
        "h-5 w-5 rounded-full border border-border cursor-pointer transition-transform hover:scale-110",
        active && "ring-2 ring-foreground ring-offset-1 ring-offset-background",
      )}
      style={{ backgroundColor: color }}
    />
  )
}

function ColorDropdown({
  trigger, presets, activeColor, onSelect,
}: {
  trigger: ReactNode; presets: typeof HIGHLIGHT_PRESETS; activeColor: string | null; onSelect: (color: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as globalThis.Node)) setOpen(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  return (
    <div ref={ref} className="relative">
      <div onClick={() => setOpen(o => !o)} className="cursor-pointer">
        {trigger}
      </div>
      {open && (
        <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 z-50 bg-popover border border-border rounded-md shadow-md p-2 flex gap-1.5">
          {presets.map(p => (
            <ColorSwatch
              key={p.color}
              color={p.color}
              tooltip={p.label}
              active={activeColor === p.color}
              onClick={() => { onSelect(p.color); setOpen(false) }}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function HeadingDropdown({ editor }: { editor: Editor }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const levels: { level: 1 | 2 | 3; Icon: typeof Heading1; label: string }[] = [
    { level: 1, Icon: Heading1, label: "Heading 1" },
    { level: 2, Icon: Heading2, label: "Heading 2" },
    { level: 3, Icon: Heading3, label: "Heading 3" },
  ]

  const activeLevel = levels.find(l => editor.isActive("heading", { level: l.level }))

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as globalThis.Node)) setOpen(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        title="Headings"
        onClick={() => setOpen(o => !o)}
        className={cn(
          "h-7 px-1.5 flex items-center gap-0.5 rounded-sm text-muted-foreground text-xs font-medium cursor-pointer",
          "hover:bg-accent hover:text-foreground transition-colors",
          activeLevel && "bg-accent text-foreground",
        )}
      >
        {activeLevel ? <activeLevel.Icon className="h-4 w-4" /> : <Heading1 className="h-4 w-4" />}
        <ChevronDown className="h-3 w-3" />
      </button>
      {open && (
        <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 z-50 bg-popover border border-border rounded-md shadow-md p-1 flex gap-0.5">
          {levels.map(({ level, Icon, label }) => (
            <ToolbarBtn
              key={level}
              active={editor.isActive("heading", { level })}
              tooltip={label}
              onClick={() => { editor.chain().focus().toggleHeading({ level }).run(); setOpen(false) }}
            >
              <Icon className="h-4 w-4" />
            </ToolbarBtn>
          ))}
        </div>
      )}
    </div>
  )
}

export function EditorToolbar({ editor }: { editor: Editor }) {
  // Force re-render on selection/content changes so active states stay in sync
  const [, setTick] = useState(0)
  useEffect(() => {
    const cb = () => setTick(t => t + 1)
    editor.on("selectionUpdate", cb)
    editor.on("transaction", cb)
    return () => { editor.off("selectionUpdate", cb); editor.off("transaction", cb) }
  }, [editor])

  // Find active highlight color
  const activeHighlight: string | null = (() => {
    const attrs = editor.getAttributes("highlight")
    return attrs.color ?? (editor.isActive("highlight") ? "#fef08a" : null)
  })()

  // Find active text color
  const activeTextColor: string | null = (() => {
    const attrs = editor.getAttributes("textStyle")
    return attrs.color ?? null
  })()

  return (
    <div className="flex items-center gap-0.5 px-3 h-9 border-b border-border bg-muted/30 shrink-0">
      {/* Text style */}
      <ToolbarBtn active={editor.isActive("bold")} tooltip="Bold (Ctrl+B)"
        onClick={() => editor.chain().focus().toggleBold().run()}>
        <Bold className="h-4 w-4" />
      </ToolbarBtn>
      <ToolbarBtn active={editor.isActive("italic")} tooltip="Italic (Ctrl+I)"
        onClick={() => editor.chain().focus().toggleItalic().run()}>
        <Italic className="h-4 w-4" />
      </ToolbarBtn>
      <ToolbarBtn active={editor.isActive("strike")} tooltip="Strikethrough"
        onClick={() => editor.chain().focus().toggleStrike().run()}>
        <Strikethrough className="h-4 w-4" />
      </ToolbarBtn>

      <div className="w-px h-5 bg-border mx-1" />

      {/* Highlight — 2 primary + more dropdown */}
      <ToolbarBtn
        active={activeHighlight === "#fef08a"}
        tooltip="Highlight Yellow"
        onClick={() => editor.chain().focus().toggleHighlight({ color: "#fef08a" }).run()}
      >
        <div className="relative">
          <Highlighter className="h-4 w-4" />
          <div className="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-yellow-300 border border-border" />
        </div>
      </ToolbarBtn>
      <ToolbarBtn
        active={activeHighlight === "#bbf7d0"}
        tooltip="Highlight Green"
        onClick={() => editor.chain().focus().toggleHighlight({ color: "#bbf7d0" }).run()}
      >
        <div className="relative">
          <Highlighter className="h-4 w-4" />
          <div className="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-green-300 border border-border" />
        </div>
      </ToolbarBtn>
      <ColorDropdown
        trigger={
          <div className="h-7 w-5 flex items-center justify-center text-muted-foreground hover:text-foreground cursor-pointer">
            <ChevronDown className="h-3 w-3" />
          </div>
        }
        presets={HIGHLIGHT_PRESETS}
        activeColor={activeHighlight}
        onSelect={(color) => editor.chain().focus().toggleHighlight({ color }).run()}
      />

      <div className="w-px h-5 bg-border mx-1" />

      {/* Text color — 3 primary + more dropdown */}
      {TEXT_COLOR_PRESETS.slice(0, 3).map(p => (
        <ToolbarBtn
          key={p.color}
          active={activeTextColor === p.color}
          tooltip={`Text ${p.label}`}
          onClick={() => {
            if (p.color === "#000000") {
              editor.chain().focus().unsetMark("textStyle").run()
            } else {
              editor.chain().focus().setColor(p.color).run()
            }
          }}
        >
          <div className="flex flex-col items-center">
            <span className="text-xs font-bold leading-none" style={{ color: p.color }}>A</span>
            <div className="w-3 h-0.5 rounded-full mt-0.5" style={{ backgroundColor: p.color }} />
          </div>
        </ToolbarBtn>
      ))}
      <ColorDropdown
        trigger={
          <div className="h-7 w-5 flex items-center justify-center text-muted-foreground hover:text-foreground cursor-pointer">
            <ChevronDown className="h-3 w-3" />
          </div>
        }
        presets={TEXT_COLOR_PRESETS}
        activeColor={activeTextColor}
        onSelect={(color) => {
          if (color === "#000000") {
            editor.chain().focus().unsetMark("textStyle").run()
          } else {
            editor.chain().focus().setColor(color).run()
          }
        }}
      />

      <div className="w-px h-5 bg-border mx-1" />

      {/* Heading dropdown */}
      <HeadingDropdown editor={editor} />

      <div className="w-px h-5 bg-border mx-1" />

      {/* Lists */}
      <ToolbarBtn active={editor.isActive("bulletList")} tooltip="Bullet List"
        onClick={() => editor.chain().focus().toggleBulletList().run()}>
        <List className="h-4 w-4" />
      </ToolbarBtn>
      <ToolbarBtn active={editor.isActive("orderedList")} tooltip="Numbered List"
        onClick={() => editor.chain().focus().toggleOrderedList().run()}>
        <ListOrdered className="h-4 w-4" />
      </ToolbarBtn>
      <ToolbarBtn active={editor.isActive("taskList")} tooltip="Task List"
        onClick={() => editor.chain().focus().toggleTaskList().run()}>
        <ListTodo className="h-4 w-4" />
      </ToolbarBtn>
    </div>
  )
}

export function TiptapEditor({
  value, onChange, className, placeholder, children,
  readonly = false, onImageUpload, onNoteLinkClick, onDistill, onDistillNavigate, onEditorReady,
  showToolbar = true, onVisualTranslate,
}: Omit<MarkdownEditorProps, "variant" | "minHeight">) {
  const lastEmitted = useRef(value)
  const externalUpdateRef = useRef(false)
  const editorRef = useRef<any>(null)
  const _readonlyRef = useRef(readonly)
  _readonlyRef.current = readonly

  const DistillBlock = useRef(createDistillBlockExtension(onDistillNavigate || onNoteLinkClick)).current
  const Callout = useRef(createCalloutExtension()).current
  const SlashCmd = useRef(createSlashCommandExtension(onDistill, onImageUpload)).current
  const ResizableImage = useRef(createResizableImageExtension(onVisualTranslate)).current

  // Markdown Hover Extension
  const MarkdownHoverExt = useRef(Extension.create({
    name: "markdownHover",
    addProseMirrorPlugins() {
      return [createMarkdownHoverPlugin()]
    },
  })).current

  // Table Enhancement Extension (using CSS)
  const TableEnhancementExt = useRef(Extension.create({
    name: "tableEnhancement",
  })).current

  // Prevent deletion of distill blocks and images in readonly mode
  const ReadonlyProtectExt = useRef(Extension.create({
    name: "readonlyProtect",
    addProseMirrorPlugins() {
      return [new Plugin({
        key: new PluginKey("readonlyProtect"),
        filterTransaction: (tr) => {
          if (!_readonlyRef.current) return true
          for (const step of tr.steps) {
            const map = (step as any).getMap?.()
            if (!map) continue
            // Check if any deleted range contains a distill-block or image
            const from = (step as any).from
            const to = (step as any).to
            if (from == null || to == null) continue
            tr.doc.nodesBetween(from, Math.min(to, tr.doc.nodeSize - 1), (node) => {
              if (node.type.name === "distillBlock" || node.type.name === "resizableImage") {
                throw new Error("BLOCKED")
              }
            })
            try {
              tr.before.nodesBetween(from, Math.min(to, (tr as any).before.nodeSize - 1), (node: any) => {
                if (node.type.name === "distillBlock" || node.type.name === "resizableImage") {
                  throw new Error("CHECK_DELETED")
                }
              })
            } catch (e: any) {
              if (e.message === "CHECK_DELETED") {
                // Node existed before but exists now too — check if it's being deleted
                const beforeCount = countNodes(tr.before, "distillBlock") + countNodes(tr.before, "resizableImage")
                const afterCount = countNodes(tr.doc, "distillBlock") + countNodes(tr.doc, "resizableImage")
                if (afterCount < beforeCount) return false
              }
            }
          }
          return true
        },
      })]
    },
  })).current

  function countNodes(doc: any, typeName: string): number {
    let count = 0
    doc.nodesBetween(0, doc.nodeSize, (node: any) => {
      if (node.type.name === typeName) count++
    })
    return count
  }

  const editor = useEditor({
    extensions: [
      StarterKit, DistillBlock, Callout, ResizableImage, ReadonlyProtectExt, MarkdownHoverExt, TableEnhancementExt,
      Table.configure({ resizable: true }), TableRow, TableCell, TableHeader,
      TaskList, TaskItem.configure({ nested: true }),
      Placeholder.configure({ placeholder: placeholder || 'Type "/" for commands...' }),
      SlashCmd,
      Youtube.configure({ width: 640, height: 360 }),
      Highlight.configure({ multicolor: true }),
      TextStyle,
      Color,
      Markdown.configure({
        html: true,
        tightLists: true,
        bulletListMarker: "-",
        linkify: true,
        transformPastedText: true,
        transformCopiedText: false,
      }),
    ],
    content: preprocessDistillBlocks(value).processed,
    editable: !readonly,
    onUpdate: ({ editor }) => {
      const storage = editor.storage as any
      const md = storage?.markdown?.getMarkdown?.() ?? ""
      const processed = postprocessDistillBlocks(md)
      lastEmitted.current = processed
      if (!externalUpdateRef.current) onChange?.(processed)
    },
    editorProps: {
      attributes: { class: "focus:outline-none" },
      handleDOMEvents: {
        contextmenu: (_view, event) => {
          const target = event.target as HTMLElement
          const table = target.closest("table")
          if (table && editorRef.current) {
            event.preventDefault()
            showTableContextMenu(event, editorRef.current)
            return true
          }
          return false
        },
        keydown: (_view, event) => {
          if (!_readonlyRef.current) return false
          if (event.key === "Backspace" || event.key === "Delete") {
            const target = event.target as HTMLElement
            if (target.closest("[data-type='distill-block']") ||
                target.closest(".image-visual-desc") ||
                target.closest("img[data-visual]")) {
              event.preventDefault()
              event.stopPropagation()
              return true
            }
          }
          return false
        },
      },
      handlePaste: (_view, event) => {
        // Intercept plain-text clipboard and convert markdown to HTML.
        // Without this, ProseMirror prefers text/html from the clipboard,
        // so patterns like "### heading" or "**bold**" are inserted as-is.
        const text = event.clipboardData?.getData("text/plain")
        if (!text) return false
        const hasMarkdown = /^#{1,6}\s|^\s*[-*+]\s|^\s*\d+\.\s|^>\s|```|\*\*.+?\*\*|__.+?__|^[-*_]{3,}\s*$|:::/m.test(text)
        if (!hasMarkdown) return false
        try {
          // Preprocess distill blocks first (converts :::distill-block{...} to HTML divs)
          const { processed } = preprocessDistillBlocks(text)
          const html = markdownToHtml(processed)
          if (html) {
            editorRef.current?.commands.insertContent(html)
            return true  // prevent default (raw text) insertion
          }
        } catch { /* fall through to default paste */ }
        return false
      },
    },
  })

  useEffect(() => { editorRef.current = editor }, [editor])
  useEffect(() => { if (editor && onEditorReady) onEditorReady(editor) }, [editor, onEditorReady])

  useEffect(() => {
    if (!editor) return
    // Apply any pending AI descriptions to the content before loading.
    // This catches descriptions that completed while viewing a different note.
    const enriched = applyPendingDescriptions(value)
    const shouldReload = enriched !== value || value !== lastEmitted.current
    if (!shouldReload) return
    externalUpdateRef.current = true
    const { processed } = preprocessDistillBlocks(enriched)
    editor.commands.setContent(processed)
    // Use enriched as lastEmitted so the next render cycle sees that the
    // injected content is already applied and doesn't re-trigger setContent.
    lastEmitted.current = enriched
    // setContent triggers onUpdate, but onUpdate skips onChange while
    // externalUpdateRef is true. We must call onChange manually so that:
    // 1. React state (content) is updated with the enriched markdown
    // 2. handleContentChange schedules auto-save to persist to server
    // Without this, the injected description only lives in the editor DOM
    // and is lost on the next note switch.
    if (enriched !== value) {
      onChange?.(enriched)
      // Also immediately flush the save to server — don't wait for the
      // 800ms auto-save timer, because the user might switch notes before
      // it fires. This mirrors how distill blocks flush-save before async ops.
      try { _flushSaveBeforeGenerate?.() } catch { /* best-effort */ }
    }
    requestAnimationFrame(() => { externalUpdateRef.current = false })
  }, [value, editor])

  useEffect(() => {
    if (!editor) return
    editor.setEditable(!readonly)
    _isPreviewMode = readonly
    const el = editor.view.dom as HTMLElement
    if (readonly) {
      el.classList.add("tiptap-readonly")
    } else {
      el.classList.remove("tiptap-readonly")
    }
  }, [readonly, editor])

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      const target = e.target as HTMLElement

      // Table click → show floating menu (same pattern as image floating menu)
      const tableEl = target.closest("table") as HTMLElement | null
      if (tableEl && editorRef.current) {
        const existing = document.getElementById("table-floating-menu")
        if (existing && tableEl.contains(existing)) return // already showing for this table
        if (existing) existing.remove()
        showTableFloatingMenu(tableEl, editorRef.current)
      }

      const anchor = target.closest('a[href^="note-id://"]') as HTMLAnchorElement | null
      if (anchor) {
        e.preventDefault()
        e.stopPropagation()
        const noteId = anchor.getAttribute("href")?.replace("note-id://", "")
        if (noteId) onNoteLinkClick?.(noteId)
      }
      const distillBlock = target.closest("[data-type='distill-block']")
      if (distillBlock) {
        const noteId = distillBlock.getAttribute("data-source-note-id")
        if (noteId) onNoteLinkClick?.(noteId)
      }
      // If clicked outside ProseMirror content area (empty editor space),
      // focus the editor and place cursor at the nearest content position.
      const pmEl = editorRef.current?.view?.dom as HTMLElement | undefined
      if (pmEl && !pmEl.contains(target)) {
        const editor = editorRef.current
        if (editor && !editor.isDestroyed) {
          // Use posAtCoords to find the nearest valid document position
          // for the click coordinates, then place the cursor there.
          const pos = editor.view.posAtCoords({
            left: e.clientX,
            top: e.clientY,
          })
          if (pos) {
            editor.commands.setTextSelection(pos.pos)
          }
          editor.commands.focus()
        }
      }
    },
    [onNoteLinkClick]
  )

  useEffect(() => {
    if (!editor || !onImageUpload) return
    const handlePaste = async (e: ClipboardEvent) => {
      const items = e.clipboardData?.items
      if (!items) return
      for (const item of items) {
        if (item.type.startsWith("image/")) {
          e.preventDefault()
          const file = item.getAsFile()
          if (file) {
            try { const url = await onImageUpload(file); editor.chain().focus().insertContent({ type: "image", attrs: { src: url } }).run() }
            catch (err) { console.error("Upload failed:", err) }
          }
        }
      }
    }
    const handleDrop = async (e: DragEvent) => {
      const files = e.dataTransfer?.files
      if (!files) return
      for (const file of files) {
        if (file.type.startsWith("image/")) {
          e.preventDefault()
          try { const url = await onImageUpload(file); editor.chain().focus().insertContent({ type: "image", attrs: { src: url } }).run() }
          catch (err) { console.error("Upload failed:", err) }
        }
      }
    }
    const editorEl = editor.view.dom
    editorEl.addEventListener("paste", handlePaste as any)
    editorEl.addEventListener("drop", handleDrop as any)
    return () => {
      editorEl.removeEventListener("paste", handlePaste as any)
      editorEl.removeEventListener("drop", handleDrop as any)
    }
  }, [editor, onImageUpload])

  if (!editor) return null

  return (
    <div
      className={cn("tiptap-editor relative min-h-full flex flex-col", className)}
      onClick={handleClick}
    >
      {children && !readonly && (
        <div className="absolute top-2 right-2 z-10 flex gap-1 pointer-events-auto">{children}</div>
      )}
      <style>{`
        .tiptap-editor .ProseMirror {
          min-height: 100%;
        }
        .tiptap-editor ul[data-type="taskList"] {
          list-style: none !important;
          padding-left: 0 !important;
        }
        .tiptap-editor ul[data-type="taskList"] > li[data-checked] {
          display: flex !important;
          align-items: center !important;
          gap: 8px !important;
          margin-top: 0.5em !important;
          margin-bottom: 0.5em !important;
        }
        .tiptap-editor ul[data-type="taskList"] > li[data-checked] > label {
          flex-shrink: 0 !important;
          margin: 0 !important;
          padding: 0 !important;
          line-height: 1 !important;
        }
        .tiptap-editor ul[data-type="taskList"] > li[data-checked] > label input[type="checkbox"] {
          width: 16px;
          height: 16px;
          margin: 0 !important;
          cursor: pointer;
        }
        .tiptap-editor ul[data-type="taskList"] > li[data-checked] > div {
          flex: 1 !important;
          min-width: 0 !important;
        }
        .tiptap-editor ul[data-type="taskList"] > li[data-checked] > div p {
          margin: 0 !important;
          line-height: 1.5 !important;
        }
        /* Table styles — Typora-like */
        .tiptap-editor table {
          position: relative;
          border-collapse: collapse;
          width: 100%;
          margin: 8px 0;
          font-size: 0.875rem;
        }
        .tiptap-editor table td,
        .tiptap-editor table th {
          border: 1px solid var(--border, #c0c0c0);
          padding: 6px 12px;
          position: relative;
          min-width: 60px;
          text-align: left;
        }
        /* Zebra striping — odd rows gray, even rows white */
        .tiptap-editor table tr:nth-child(odd) td,
        .tiptap-editor table tr:nth-child(odd) th {
          background: var(--muted, #f0f0f0) !important;
        }
        .tiptap-editor table tr:nth-child(even) td,
        .tiptap-editor table tr:nth-child(even) th {
          background: transparent !important;
        }
      `}</style>
      {!readonly && showToolbar && <EditorToolbar editor={editor} />}
      <EditorContent editor={editor} className="prose prose-sm dark:prose-invert max-w-none p-4 min-h-full flex-1" />
    </div>
  )
}

// ──────────────────────────────────────────────
// Plain Editor
// ──────────────────────────────────────────────
function PlainEditor({ value, onChange, className, minHeight, placeholder }: MarkdownEditorProps) {
  const [focused, setFocused] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isEmpty = !value.trim()

  return (
    <div className={cn("md-editor", className)} style={{ minHeight }}>
      <textarea ref={textareaRef} value={value} onChange={(e) => onChange?.(e.target.value)}
        onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
        className={cn("md-editor-textarea", focused && "md-editor-textarea-focused")}
        placeholder={placeholder} />
      {!focused && !isEmpty && (
        <div className="md-editor-overlay" onClick={() => textareaRef.current?.focus()}>
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
          </div>
        </div>
      )}
      {!focused && isEmpty && (
        <div className="md-editor-overlay" onClick={() => textareaRef.current?.focus()}>
          <span className="text-muted-foreground italic text-sm">{placeholder || "Nothing to preview"}</span>
        </div>
      )}
    </div>
  )
}

// ──────────────────────────────────────────────
// Public Component
// ──────────────────────────────────────────────
export function MarkdownEditor(props: MarkdownEditorProps) {
  const { variant = "block" } = props
  if (variant === "plain") return <PlainEditor {...props} />
  return <TiptapEditor {...props} />
}
