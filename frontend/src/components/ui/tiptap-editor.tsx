import { useState, useRef, useEffect, useCallback, type ReactNode } from "react"
import { cn } from "@/lib/utils"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"
import { unified } from "unified"
import remarkParse from "remark-parse"
import remarkRehype from "remark-rehype"
import rehypeStringify from "rehype-stringify"
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
import { Plugin, PluginKey, TextSelection } from "@tiptap/pm/state"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import type { EditorView } from "@tiptap/pm/view"
import {
  Bold, Italic, Strikethrough,
  List, ListOrdered, ListTodo,
} from "lucide-react"
import { SoftMenu, MenuItem } from "@/components/ui/menu"

/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * Click *below* the last content block (tall min-height ProseMirror pad) →
 * place caret at end of document (Typora / Notes style).
 * Left/right of text (same vertical band as a line) must NOT jump to end.
 * Also: posAtCoords null in empty pad → end (common when min-height expands PM).
 */
function placeCaretAtEndIfClickBelowContent(
  view: EditorView,
  event: MouseEvent
): boolean {
  if (event.button !== 0) return false

  const goEnd = () => {
    event.preventDefault()
    const sel = TextSelection.atEnd(view.state.doc)
    view.dispatch(view.state.tr.setSelection(sel).scrollIntoView())
    if (!view.hasFocus()) view.focus()
  }

  const last = view.dom.lastElementChild as HTMLElement | null
  if (!last) {
    goEnd()
    return true
  }

  // Click clearly below the last block’s ink box
  const bottom = last.getBoundingClientRect().bottom
  if (event.clientY > bottom + 2) {
    goEnd()
    return true
  }

  /*
   * Empty vertical pad: last block’s content box is short but ProseMirror is tall.
   * posAtCoords may still resolve into the last node — prefer end when the hit
   * is in the trailing empty fraction of the editor (below content mid-line).
   */
  const coords = view.posAtCoords({
    left: event.clientX,
    top: event.clientY,
  })
  if (!coords) {
    goEnd()
    return true
  }

  return false
}

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

const _generatingImages = new Set<string>()
const _pendingResults = new Map<string, string>() // imageId → description
const _vtContainers = new Map<string, HTMLElement>() // imageId → container
let _flushSaveBeforeGenerate: (() => Promise<void>) | null = null

export function _setFlushSaveBeforeGenerate(fn: (() => Promise<void>) | undefined) {
  _flushSaveBeforeGenerate = fn ?? null
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
function createResizableImageExtension() {
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
        // Description tracks image width + alignment (not full editor width).
        descArea.style.width = hasPct ? rawWidth : "auto"
        descArea.style.maxWidth = "100%"
        descArea.style.marginLeft = ml
        descArea.style.marginRight = mr
        descArea.style.boxSizing = "border-box"
        descArea.style.textAlign = "center"
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

      // Visual Description area — under image, same width/alignment as caption.
      const descArea = document.createElement("div")
      descArea.className = "image-visual-desc"
      descArea.style.cssText = `
        display: none;
        font-size: 12px;
        color: #6b7280;
        font-style: italic;
        text-align: center;
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
        box-sizing: border-box;
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

          // Restore percentage layout + keep image/caption/desc centered.
          // Do NOT clear marginLeft/Right after setNodeMarkup — that raced
          // update() and left caption/description left-aligned.
          const a = node.attrs.alignment || "center"
          let ml = "0",
            mr = "0"
          if (a === "center") {
            ml = "auto"
            mr = "auto"
          } else if (a === "right") {
            ml = "auto"
          }
          container.style.width = "100%"
          imgWrapper.style.cssText = `
            position: relative;
            display: block;
            line-height: 0;
            width: ${newPctWidth};
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
            width: ${newPctWidth};
            max-width: 100%;
            margin-left: ${ml};
            margin-right: ${mr};
          `
          // Keep description under the image (same width + center), not full-bleed left
          descArea.style.width = newPctWidth
          descArea.style.maxWidth = "100%"
          descArea.style.marginLeft = ml
          descArea.style.marginRight = mr
          descArea.style.boxSizing = "border-box"
          descArea.style.textAlign = "center"

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
            descArea.style.width = hasPct ? w : "auto"
            descArea.style.maxWidth = "100%"
            descArea.style.marginLeft = ml
            descArea.style.marginRight = mr
            descArea.style.boxSizing = "border-box"
            descArea.style.textAlign = "center"

            // Persist to ProseMirror node
            const { tr } = editor.state
            tr.setNodeMarkup(freshPos, undefined, merged)
            editor.view.dispatch(tr)
          })
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
        descArea.style.width = hasPctW ? w : "auto"
        descArea.style.maxWidth = "100%"
        descArea.style.marginLeft = ml
        descArea.style.marginRight = mr
        descArea.style.boxSizing = "border-box"
        descArea.style.textAlign = "center"
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
// Premium float bars (image / table) — fixed portal, SoftMenu surface
// Avoid absolute top:-44px inside overflow:hidden pane cards (clipping).
// ──────────────────────────────────────────────
let _isPreviewMode = false
let _floatBarCleanup: (() => void) | null = null
let _floatBarAnchor: HTMLElement | null = null

function dismissPremiumFloatBars() {
  document.getElementById("image-floating-menu")?.remove()
  document.getElementById("table-floating-menu")?.remove()
  if (_floatBarCleanup) {
    _floatBarCleanup()
    _floatBarCleanup = null
  }
  _floatBarAnchor = null
}

/** Mount a Premium float bar on document.body (fixed) above `anchor`. */
function mountPremiumFloatBar(
  menu: HTMLElement,
  anchor: HTMLElement,
  opts?: { align?: "center" | "start" },
) {
  dismissPremiumFloatBars()
  _floatBarAnchor = anchor
  menu.classList.add("pm-float-bar")
  menu.setAttribute("role", "toolbar")
  document.body.appendChild(menu)

  const place = () => {
    if (!menu.isConnected || !anchor.isConnected) return
    const r = anchor.getBoundingClientRect()
    const mh = menu.offsetHeight || 36
    const mw = menu.offsetWidth || 160
    const gap = 8
    // Clear sticky format strip + viewport chrome so bar isn't covered/clipped
    let minTop = 8
    const fmt = document.querySelector(
      ".pm-fmt-toolbar.is-editing",
    ) as HTMLElement | null
    if (fmt) {
      const fb = fmt.getBoundingClientRect().bottom
      if (fb > 0) minTop = Math.max(minTop, fb + 6)
    }
    let top = r.top - mh - gap
    // Flip below when not enough room under sticky format strip / viewport top
    if (top < minTop) {
      top = Math.min(r.bottom + gap, window.innerHeight - mh - 8)
    }
    let left =
      opts?.align === "start"
        ? r.left
        : r.left + r.width / 2 - mw / 2
    left = Math.max(8, Math.min(left, window.innerWidth - mw - 8))
    menu.style.top = `${Math.round(top)}px`
    menu.style.left = `${Math.round(left)}px`
  }

  // Enter animation after first layout
  requestAnimationFrame(() => {
    place()
    menu.classList.add("is-open")
    requestAnimationFrame(place)
  })

  const onReposition = () => place()
  window.addEventListener("scroll", onReposition, true)
  window.addEventListener("resize", onReposition)

  const onOutside = (e: MouseEvent) => {
    const t = e.target
    if (
      t instanceof globalThis.Node &&
      (menu.contains(t) || anchor.contains(t))
    ) {
      return
    }
    dismissPremiumFloatBars()
    document.removeEventListener("mousedown", onOutside, true)
  }
  // mousedown so we dismiss before other click handlers re-open
  setTimeout(() => {
    document.addEventListener("mousedown", onOutside, true)
  }, 0)

  _floatBarCleanup = () => {
    window.removeEventListener("scroll", onReposition, true)
    window.removeEventListener("resize", onReposition)
    document.removeEventListener("mousedown", onOutside, true)
  }
}

function makeFloatBarBtn(
  title: string,
  svg: string,
  opts?: { active?: boolean; danger?: boolean; className?: string },
): HTMLButtonElement {
  const b = document.createElement("button")
  b.type = "button"
  b.className = [
    "pm-float-bar-btn",
    opts?.active ? "is-on" : "",
    opts?.danger ? "is-danger" : "",
    opts?.className || "",
  ]
    .filter(Boolean)
    .join(" ")
  b.title = title
  b.setAttribute("aria-label", title)
  b.innerHTML = svg
  return b
}

function makeFloatBarSep(): HTMLDivElement {
  const d = document.createElement("div")
  d.className = "pm-float-bar-sep"
  d.setAttribute("aria-hidden", "true")
  return d
}

function showImageFloatingMenu(
  container: HTMLElement,
  attrs: any,
  onUpdate: (attrs: any) => void,
) {
  if (_isPreviewMode) return

  // Anchor to the image frame (not full-width container) so the bar sits over the photo
  const anchor =
    (container.querySelector("img")?.parentElement as HTMLElement | null) ||
    container

  // Same image already open → keep
  if (
    document.getElementById("image-floating-menu") &&
    _floatBarAnchor === anchor
  ) {
    return
  }

  const menu = document.createElement("div")
  menu.id = "image-floating-menu"
  menu.setAttribute("aria-label", "Image")

  // Prevent mousedown from stealing editor focus
  menu.addEventListener("mousedown", (e) => {
    if ((e.target as HTMLElement).tagName !== "INPUT") e.preventDefault()
  })
  menu.addEventListener("click", (e) => e.stopPropagation())

  const alignmentOptions = [
    {
      value: "left",
      svg: `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M2 3h12M2 7h8M2 11h10M2 15h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
      label: "Align left",
    },
    {
      value: "center",
      svg: `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M2 3h12M4 7h8M3 11h10M5 15h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
      label: "Align center",
    },
    {
      value: "right",
      svg: `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M2 3h12M6 7h8M4 11h10M8 15h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
      label: "Align right",
    },
  ]

  const currentAlign = attrs.alignment || "center"
  alignmentOptions.forEach((opt) => {
    const btn = makeFloatBarBtn(opt.label, opt.svg, {
      active: currentAlign === opt.value,
      className: "align-btn",
    })
    btn.dataset.align = opt.value
    btn.addEventListener("click", (e) => {
      e.stopPropagation()
      attrs.alignment = opt.value
      onUpdate({ alignment: opt.value })
      menu.querySelectorAll("button.align-btn").forEach((b) => {
        const el = b as HTMLButtonElement
        el.classList.toggle("is-on", el.dataset.align === opt.value)
      })
    })
    menu.appendChild(btn)
  })

  menu.appendChild(makeFloatBarSep())

  const captionBtn = makeFloatBarBtn(
    "Add caption",
    `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M11.5 1.5l3 3L5 14H2v-3L11.5 1.5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
  )
  captionBtn.addEventListener("click", (e) => {
    e.stopPropagation()
    const currentAlt = attrs.alt || ""
    dismissPremiumFloatBars()
    const ev = new CustomEvent("caption:edit", {
      bubbles: false,
      detail: { alt: currentAlt },
    })
    container.dispatchEvent(ev)
  })
  menu.appendChild(captionBtn)

  mountPremiumFloatBar(menu, anchor, { align: "center" })
}

// ──────────────────────────────────────────────
// DistillBlock Node Extension
// ──────────────────────────────────────────────

/**
 * Speaker maps for live [spk:ID] rendering. Always re-fetched (in-flight
 * deduped) so rename in Meeting UI shows up in open distill blocks.
 */
const _speakerInflight = new Map<string, Promise<Record<string, string>>>()

/** Drop cache so next paint pulls latest names (call when note dialog opens). */
export function invalidateMeetingSpeakerCache(meetingId?: string) {
  if (meetingId) {
    _speakerInflight.delete(meetingId)
  } else {
    _speakerInflight.clear()
  }
  // Notify open distill NodeViews to re-paint
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("meeting-speakers-changed", {
        detail: meetingId ? { meetingId } : {},
      })
    )
  }
}

function parseMeetingSourceId(
  sourceId: string | null | undefined,
): { meetingId: string; tabId: string } | null {
  if (!sourceId || !sourceId.startsWith("meeting:")) return null
  const rest = sourceId.slice("meeting:".length).trim()
  if (!rest) return null
  const colon = rest.indexOf(":")
  if (colon === -1) return { meetingId: rest, tabId: "tab_general" }
  const meetingId = rest.slice(0, colon).trim()
  const tabId = rest.slice(colon + 1).trim() || "tab_general"
  return meetingId ? { meetingId, tabId } : null
}

/** Apply latest speaker names to [spk:ID] / Speaker N tokens (display only). */
function applySpeakerDisplay(
  text: string,
  names: Record<string, string>,
): string {
  let t = text || ""
  t = t.replace(/\\\[/g, "[").replace(/\\\]/g, "]")
  t = t.replace(/\[spk:([^\]]+)\]/g, (_, id: string) => {
    const name = names[id]?.trim()
    return name || `Speaker ${id}`
  })
  for (const [id, name] of Object.entries(names)) {
    if (!name) continue
    const esc = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    t = t.replace(new RegExp(`\\bSpeaker\\s+${esc}\\b`, "gi"), name)
  }
  return t
}

async function fetchMeetingSpeakerNames(
  meetingId: string,
): Promise<Record<string, string>> {
  let p = _speakerInflight.get(meetingId)
  if (!p) {
    p = (async () => {
      try {
        const { getMeeting } = await import("@/api/client")
        const m = await getMeeting(meetingId)
        return (m.speaker_names ?? {}) as Record<string, string>
      } catch {
        return {}
      } finally {
        // Allow a fresh fetch next time (after this promise settles)
        _speakerInflight.delete(meetingId)
      }
    })()
    _speakerInflight.set(meetingId, p)
  }
  return p
}

async function resolveDistillTextForDisplay(
  text: string,
  sourceNoteId: string | null | undefined,
): Promise<string> {
  const parsed = parseMeetingSourceId(sourceNoteId)
  if (!parsed || !text) return text || ""
  const names = await fetchMeetingSpeakerNames(parsed.meetingId)
  return applySpeakerDisplay(text, names)
}

function createDistillBlockExtension(onNavigate?: (noteId: string) => void) {
  return Node.create({
    name: "distillBlock",
    group: "block",
    atom: true,
    // Must be true so PM enables block drag (grip / card reorder)
    draggable: true,
    selectable: true,
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
        /** Collapsed max height (px) — keep in sync with CSS --distill-collapsed-h */
        const COLLAPSED_MAX = 200

        const dom = document.createElement("div")
        dom.setAttribute("data-type", "distill-block")
        dom.setAttribute("data-block-id", node.attrs.blockId)
        dom.setAttribute("data-loading", node.attrs.loading ? "true" : "false")
        dom.className = "distill-block"
        if (node.attrs.loading) dom.classList.add("is-loading")
        dom.contentEditable = "false"

        // Node is draggable:true — PM sets dom.draggable. Loading blocks must not move
        // (result replace looks up by position / temp id).
        if (node.attrs.loading) {
          dom.draggable = false
        }

        // ── Header: grip · source · delete (no ID badge) ──
        const header = document.createElement("div")
        header.className = "distill-block__header"

        const handle = document.createElement("span")
        handle.className = "distill-block__grip"
        handle.textContent = "⠿"
        handle.setAttribute("aria-hidden", "true")
        handle.title = "Drag to reorder"
        if (node.attrs.loading) {
          handle.classList.add("is-disabled")
          handle.addEventListener("dragstart", (e) => {
            e.preventDefault()
            e.stopPropagation()
          })
        }

        // Use <span role="link"> not <button> — buttons suppress HTML5/PM drag from the card
        const link = document.createElement("span")
        link.className = "distill-block__source"
        link.setAttribute("role", "link")
        link.tabIndex = 0
        const sourceTitle = (node.attrs.sourceTitle as string) || "source"
        link.textContent = sourceTitle
        link.title = sourceTitle
        const goSource = (e: Event) => {
          e.preventDefault()
          e.stopPropagation()
          if (onNavigate) onNavigate(node.attrs.sourceNoteId)
        }
        link.addEventListener("click", goSource)
        link.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") goSource(e)
        })

        // Two-step delete: × → expand "DELETE" → second click removes (avoids mis-tap)
        const delBtn = document.createElement("button")
        delBtn.type = "button"
        delBtn.className = "distill-block__delete"
        delBtn.innerHTML =
          '<span class="distill-block__delete-x" aria-hidden="true">×</span><span class="distill-block__delete-label">Delete</span>'
        delBtn.title = "Remove distill block"
        delBtn.setAttribute("aria-label", "Remove distill block")
        delBtn.setAttribute("aria-expanded", "false")

        let deleteArmed = false
        let deleteArmTimer: number | null = null
        let deleteOutsideCleanup: (() => void) | null = null

        const disarmDelete = () => {
          deleteArmed = false
          delBtn.classList.remove("is-confirm")
          delBtn.setAttribute("aria-expanded", "false")
          delBtn.setAttribute("aria-label", "Remove distill block")
          delBtn.title = "Remove distill block"
          if (deleteArmTimer != null) {
            window.clearTimeout(deleteArmTimer)
            deleteArmTimer = null
          }
          deleteOutsideCleanup?.()
          deleteOutsideCleanup = null
        }

        const armDelete = () => {
          deleteArmed = true
          delBtn.classList.add("is-confirm")
          delBtn.setAttribute("aria-expanded", "true")
          delBtn.setAttribute("aria-label", "Confirm delete distill block")
          delBtn.title = "Click again to delete"
          // Auto-collapse if user abandons
          if (deleteArmTimer != null) window.clearTimeout(deleteArmTimer)
          deleteArmTimer = window.setTimeout(() => disarmDelete(), 4000)
          // Outside click / Escape cancels
          deleteOutsideCleanup?.()
          const onPointerDown = (ev: Event) => {
            const t = ev.target
            if (t instanceof globalThis.Node && delBtn.contains(t)) return
            disarmDelete()
          }
          const onKey = (ev: KeyboardEvent) => {
            if (ev.key === "Escape") disarmDelete()
          }
          // next tick so this click doesn't immediately disarm
          window.setTimeout(() => {
            document.addEventListener("pointerdown", onPointerDown, true)
            document.addEventListener("keydown", onKey, true)
          }, 0)
          deleteOutsideCleanup = () => {
            document.removeEventListener("pointerdown", onPointerDown, true)
            document.removeEventListener("keydown", onKey, true)
          }
        }

        const performDelete = () => {
          if (typeof getPos !== "function") return
          const pos = getPos()
          if (pos === undefined) return
          const blockId = node.attrs.blockId
          const sourceNoteId = node.attrs.sourceNoteId
          disarmDelete()
          editor
            .chain()
            .focus()
            .deleteRange({ from: pos, to: pos + node.nodeSize })
            .run()
          // Dispatch on editor.view.dom (always in document) — dom is detached
          // after deleteRange, so events dispatched on it won't bubble.
          if (blockId || sourceNoteId) {
            const detail = { blockId, sourceNoteId }
            const event = new CustomEvent("distill:block-remove", {
              bubbles: true,
              detail,
            })
            editor.view.dom.dispatchEvent(event)
          }
        }

        delBtn.addEventListener("click", (e) => {
          e.preventDefault()
          e.stopPropagation()
          if (!deleteArmed) {
            armDelete()
            return
          }
          performDelete()
        })

        header.append(handle, link, delBtn)

        // ── Body scroll region + D fade/pill ──
        const contentWrapper = document.createElement("div")
        contentWrapper.className = "distill-block__scroll"

        const content = document.createElement("div")
        // prose / prose-sm: match app markdown heading·list·code styles
        content.className = "distill-block__body prose prose-sm max-w-none"

        const fade = document.createElement("div")
        fade.className = "distill-block__fade"
        fade.setAttribute("aria-hidden", "true")

        const pill = document.createElement("button")
        pill.type = "button"
        pill.className = "distill-block__pill"
        pill.textContent = "Show more"
        pill.setAttribute("data-action", "expand")
        pill.setAttribute("aria-expanded", "false")

        fade.appendChild(pill)
        contentWrapper.append(content, fade)

        // Latest attrs for speaker re-paint (meeting names change while note stays open)
        let latestText = node.attrs.text as string
        let latestSourceId = node.attrs.sourceNoteId as string | null
        let latestLoading = !!node.attrs.loading
        let latestTitle = (node.attrs.sourceTitle as string) || "source"
        let expandAnimating = false
        let expandAnimCleanup: (() => void) | null = null

        /** Cap expanded height: min(50vh, 28rem, content) — matches CSS --distill-expanded-h */
        const expandedTargetPx = () => {
          const rem =
            parseFloat(getComputedStyle(document.documentElement).fontSize) ||
            16
          const cap = Math.min(window.innerHeight * 0.5, 28 * rem)
          // Include fade strip so sticky less still fits when content is long
          return Math.min(cap, Math.max(content.scrollHeight, COLLAPSED_MAX))
        }

        const prefersReducedMotion = () =>
          typeof window !== "undefined" &&
          window.matchMedia("(prefers-reduced-motion: reduce)").matches

        const setExpandedChrome = (expanded: boolean) => {
          dom.classList.toggle("is-expanded", expanded)
          pill.textContent = expanded ? "Show less" : "Show more"
          pill.setAttribute("aria-expanded", expanded ? "true" : "false")
        }

        /**
         * Soft height tween between collapsed (200px) and expanded cap.
         * Uses explicit px max-height so CSS transition always has concrete endpoints.
         */
        const animateExpand = (expand: boolean) => {
          if (expandAnimating) return
          if (latestLoading || !dom.classList.contains("is-overflow")) return

          // Instant path
          if (prefersReducedMotion()) {
            setExpandedChrome(expand)
            contentWrapper.style.maxHeight = ""
            contentWrapper.style.overflow = ""
            if (!expand) contentWrapper.scrollTop = 0
            return
          }

          expandAnimCleanup?.()
          expandAnimating = true
          dom.classList.add("is-animating")
          // Lock overflow while tweening
          contentWrapper.style.overflow = "hidden"

          const from = contentWrapper.getBoundingClientRect().height
          const to = expand ? expandedTargetPx() : COLLAPSED_MAX

          // Pin start height, then flip class + end height on next frame
          contentWrapper.style.maxHeight = `${Math.round(from)}px`
          // Force layout so the browser registers the start value
          void contentWrapper.offsetHeight

          if (expand) {
            setExpandedChrome(true)
          } else {
            // Keep is-expanded until end so we still measure from open layout;
            // label updates immediately for feedback
            pill.textContent = "Show more"
            pill.setAttribute("aria-expanded", "false")
            contentWrapper.scrollTop = 0
          }

          contentWrapper.style.maxHeight = `${Math.round(to)}px`

          const finish = () => {
            contentWrapper.removeEventListener("transitionend", onEnd)
            window.clearTimeout(fallbackTimer)
            if (!expand) {
              setExpandedChrome(false)
              contentWrapper.scrollTop = 0
            } else {
              setExpandedChrome(true)
            }
            // Hand control back to CSS vars after settle
            contentWrapper.style.maxHeight = ""
            contentWrapper.style.overflow = ""
            dom.classList.remove("is-animating")
            expandAnimating = false
            expandAnimCleanup = null
          }

          const onEnd = (ev: TransitionEvent) => {
            if (ev.target !== contentWrapper) return
            if (ev.propertyName !== "max-height") return
            finish()
          }

          // Fallback if transitionend is skipped (display:none mid-flight, etc.)
          const fallbackTimer = window.setTimeout(finish, 450)
          contentWrapper.addEventListener("transitionend", onEnd)
          expandAnimCleanup = () => {
            contentWrapper.removeEventListener("transitionend", onEnd)
            window.clearTimeout(fallbackTimer)
            expandAnimating = false
            expandAnimCleanup = null
          }
        }

        pill.addEventListener("click", (e) => {
          e.preventDefault()
          e.stopPropagation()
          if (latestLoading || !dom.classList.contains("is-overflow")) return
          if (expandAnimating) return
          const next = !dom.classList.contains("is-expanded")
          animateExpand(next)
        })

        /**
         * Wheel ownership:
         * - Collapsed: never scroll inside the card — drive the outer note
         *   scroller (.pm-ws-editor) so the document moves (not the browser
         *   window / dialog, and not the clipped distill body).
         * - Expanded: keep wheel inside the block; contain at ends.
         */
        const wheelDeltaY = (e: WheelEvent, linePx: number) => {
          if (e.deltaMode === 1) return e.deltaY * 16
          if (e.deltaMode === 2) return e.deltaY * linePx
          return e.deltaY
        }

        const onWheel = (e: WheelEvent) => {
          if (latestLoading || expandAnimating) return
          if (e.deltaY === 0 && e.deltaX === 0) return

          // Collapsed (or short content): scroll the note pane, not the block
          if (
            !dom.classList.contains("is-expanded") ||
            !dom.classList.contains("is-overflow")
          ) {
            const outer =
              (dom.closest(".pm-ws-editor") as HTMLElement | null) ||
              (dom.closest(".ProseMirror")?.parentElement as HTMLElement | null)
            if (!outer) return
            e.preventDefault()
            e.stopPropagation()
            outer.scrollTop += wheelDeltaY(e, outer.clientHeight || 40)
            return
          }

          // Expanded + long: internal scroll only
          const el = contentWrapper
          const maxScroll = Math.max(0, el.scrollHeight - el.clientHeight)
          if (maxScroll <= 0) return

          const dy = wheelDeltaY(e, el.clientHeight || 40)
          const top = el.scrollTop
          const atTop = top <= 0
          const atBottom = top >= maxScroll - 1
          const canAbsorb =
            (dy > 0 && !atBottom) || (dy < 0 && !atTop)

          e.preventDefault()
          e.stopPropagation()
          if (canAbsorb) el.scrollTop = top + dy
        }
        dom.addEventListener("wheel", onWheel, { passive: false, capture: true })

        const setLoadingChrome = (loading: boolean) => {
          dom.classList.toggle("is-loading", loading)
          dom.setAttribute("data-loading", loading ? "true" : "false")
          if (loading) {
            dom.draggable = false
            handle.classList.add("is-disabled")
          } else {
            dom.draggable = true
            handle.classList.remove("is-disabled")
          }
        }

        const remeasureOverflow = () => {
          if (latestLoading) {
            expandAnimCleanup?.()
            dom.classList.remove("is-overflow", "is-expanded", "is-animating")
            contentWrapper.style.maxHeight = ""
            contentWrapper.style.overflow = ""
            fade.setAttribute("aria-hidden", "true")
            pill.textContent = "Show more"
            pill.setAttribute("aria-expanded", "false")
            return
          }
          // content.scrollHeight is full content height regardless of max-height clip
          const overflow = content.scrollHeight > COLLAPSED_MAX + 1
          dom.classList.toggle("is-overflow", overflow)
          if (!overflow) {
            expandAnimCleanup?.()
            dom.classList.remove("is-expanded", "is-animating")
            contentWrapper.style.maxHeight = ""
            contentWrapper.style.overflow = ""
            pill.textContent = "Show more"
            pill.setAttribute("aria-expanded", "false")
          }
          fade.setAttribute("aria-hidden", overflow ? "false" : "true")
          if (dom.classList.contains("is-expanded") && !expandAnimating) {
            pill.textContent = "Show less"
            pill.setAttribute("aria-expanded", "true")
          }
        }

        const paintContent = (
          text: string,
          sourceId: string | null,
          loading: boolean,
          title: string,
        ) => {
          latestText = text
          latestSourceId = sourceId
          latestLoading = loading
          latestTitle = title
          setLoadingChrome(loading)

          if (loading) {
            content.replaceChildren()
            const row = document.createElement("div")
            row.className = "distill-block__loading"
            const spin = document.createElement("span")
            spin.className = "distill-block__spinner"
            spin.setAttribute("aria-hidden", "true")
            const label = document.createElement("span")
            label.textContent = `Distilling from “${title}”…`
            row.append(spin, label)
            content.appendChild(row)
            remeasureOverflow()
            return
          }

          // Sync first paint (IDs as Speaker N until names load)
          content.innerHTML = renderMarkdown(
            applySpeakerDisplay(text || "", {}),
          )
          requestAnimationFrame(remeasureOverflow)

          // Always re-fetch latest speaker names for meeting distill sources
          void resolveDistillTextForDisplay(text || "", sourceId).then(
            (resolved) => {
              if (content.isConnected) {
                content.innerHTML = renderMarkdown(resolved)
                requestAnimationFrame(remeasureOverflow)
              }
            },
          )
        }

        // Loading / body
        paintContent(
          node.attrs.text,
          node.attrs.sourceNoteId,
          !!node.attrs.loading,
          node.attrs.sourceTitle || "source",
        )

        dom.append(header, contentWrapper)

        // Re-resolve speakers when names change (Meeting page) or tab becomes visible
        const onSpeakersChanged = (ev: Event) => {
          if (latestLoading || !content.isConnected) return
          const detail = (ev as CustomEvent).detail as
            | { meetingId?: string }
            | undefined
          const parsed = parseMeetingSourceId(latestSourceId)
          if (
            detail?.meetingId &&
            parsed &&
            detail.meetingId !== parsed.meetingId
          ) {
            return
          }
          paintContent(
            latestText,
            latestSourceId,
            latestLoading,
            latestTitle,
          )
        }
        const onVisible = () => {
          if (document.visibilityState === "visible") {
            onSpeakersChanged(new Event("visibilitychange"))
          }
        }
        window.addEventListener("meeting-speakers-changed", onSpeakersChanged)
        document.addEventListener("visibilitychange", onVisible)

        // Initial overflow check after layout
        requestAnimationFrame(remeasureOverflow)

        return {
          dom,
          ignoreMutation: () => true,
          update: (updatedNode: ProseMirrorNode) => {
            if (updatedNode.type.name !== "distillBlock") return false

            paintContent(
              updatedNode.attrs.text,
              updatedNode.attrs.sourceNoteId,
              !!updatedNode.attrs.loading,
              updatedNode.attrs.sourceTitle || "source",
            )

            const t = (updatedNode.attrs.sourceTitle as string) || "source"
            link.textContent = t
            link.title = t
            dom.setAttribute("data-block-id", updatedNode.attrs.blockId)

            requestAnimationFrame(remeasureOverflow)
            return true
          },
          // Only clean listeners — do not flush note content here
          destroy: () => {
            expandAnimCleanup?.()
            disarmDelete()
            dom.removeEventListener("wheel", onWheel, true)
            window.removeEventListener(
              "meeting-speakers-changed",
              onSpeakersChanged,
            )
            document.removeEventListener("visibilitychange", onVisible)
          },
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
// Callout Node Extension — Premium soft rail card
// ──────────────────────────────────────────────
const CALLOUT_ICONS: Record<string, string> = {
  // Lucide-weight monoline (currentColor)
  info: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/></svg>`,
  warning: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5L2.5 19.5h19L12 3.5z"/><path d="M12 10v4"/><path d="M12 17h.01"/></svg>`,
  success: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/></svg>`,
  error: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/></svg>`,
}

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
          parseHTML: (element: HTMLElement) =>
            element.getAttribute("data-callout-type") || "info",
          renderHTML: (attrs: any) => ({ "data-callout-type": attrs.type }),
        },
      }
    },

    parseHTML() {
      return [{ tag: 'div[data-type="callout"]' }]
    },

    renderHTML({ HTMLAttributes, node }) {
      const t = node?.attrs?.type || "info"
      return [
        "div",
        mergeAttributes(HTMLAttributes, {
          "data-type": "callout",
          "data-callout-type": t,
          class: `pm-callout pm-callout--${t}`,
        }),
        0,
      ]
    },

    addNodeView() {
      return ({ node }) => {
        const dom = document.createElement("div")
        const iconEl = document.createElement("span")
        iconEl.className = "pm-callout__icon"
        iconEl.setAttribute("aria-hidden", "true")
        const content = document.createElement("div")
        content.className = "pm-callout__body"

        const applyType = (type: string) => {
          const t = CALLOUT_ICONS[type] ? type : "info"
          dom.setAttribute("data-type", "callout")
          dom.setAttribute("data-callout-type", t)
          dom.className = `pm-callout pm-callout--${t}`
          iconEl.innerHTML = CALLOUT_ICONS[t]
        }
        applyType(node.attrs.type || "info")
        dom.append(iconEl, content)

        return {
          dom,
          contentDOM: content,
          update: (updated: ProseMirrorNode) => {
            if (updated.type.name !== "callout") return false
            applyType(updated.attrs.type || "info")
            return true
          },
        }
      }
    },
  })
}

// ──────────────────────────────────────────────
// Slash Command Extension
// ──────────────────────────────────────────────
function createSlashCommandExtension(
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
            showSlashMenu(editor, from, onImageUpload)
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
  onImageUpload?: (file: File) => Promise<string>
) {
  const existingMenu = document.getElementById("slash-menu") as HTMLElement & {
    __close?: () => void
  } | null
  if (existingMenu) {
    existingMenu.__close?.()
    existingMenu.remove()
  }

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
      ],
    },
    {
      label: "Advanced",
      commands: [
        { label: "Table", icon: "📊", desc: "Insert table", action: () => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
        { label: "Code Block", icon: "💻", desc: "Code block", action: () => editor.chain().focus().toggleCodeBlock().run() },
        {
          label: "Callout",
          icon: "💡",
          desc: "Info callout",
          action: () =>
            editor
              .chain()
              .focus()
              .insertContent({
                type: "callout",
                attrs: { type: "info" },
                content: [
                  {
                    type: "paragraph",
                    content: [{ type: "text", text: "Note" }],
                  },
                ],
              })
              .run(),
        },
      ],
    },
  ]

  const menu = document.createElement("div")
  menu.id = "slash-menu"
  menu.className = "pm-slash-menu"
  menu.setAttribute("role", "listbox")
  menu.setAttribute("aria-label", "Insert block")

  const searchContainer = document.createElement("div")
  searchContainer.className = "pm-slash-menu__search"
  const searchInput = document.createElement("input")
  searchInput.className = "pm-slash-menu__input"
  searchInput.type = "search"
  searchInput.placeholder = "Filter commands…"
  searchInput.setAttribute("aria-label", "Filter commands")
  searchInput.autocomplete = "off"
  searchInput.spellcheck = false
  searchContainer.appendChild(searchInput)

  const commandList = document.createElement("div")
  commandList.className = "pm-slash-menu__list"
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
      const empty = document.createElement("div")
      empty.className = "pm-slash-menu__empty"
      empty.textContent = "No commands"
      commandList.appendChild(empty)
      return
    }

    const grouped: Record<string, any[]> = {}
    filteredCommands.forEach((cmd) => {
      if (!grouped[cmd.group]) grouped[cmd.group] = []
      grouped[cmd.group].push(cmd)
    })

    let itemIndex = 0
    Object.entries(grouped).forEach(([groupLabel, commands]) => {
      const groupEl = document.createElement("div")
      groupEl.className = "pm-slash-menu__group"
      groupEl.textContent = groupLabel
      commandList.appendChild(groupEl)

      commands.forEach((cmd) => {
        const item = document.createElement("button")
        item.type = "button"
        item.className = "pm-slash-menu__item"
        item.dataset.index = String(itemIndex++)
        item.setAttribute("role", "option")

        const icon = document.createElement("span")
        icon.className = "pm-slash-menu__icon"
        icon.setAttribute("aria-hidden", "true")
        icon.textContent = cmd.icon

        const label = document.createElement("span")
        label.className = "pm-slash-menu__label"
        label.textContent = cmd.label

        const desc = document.createElement("span")
        desc.className = "pm-slash-menu__desc"
        desc.textContent = cmd.desc || ""

        item.append(icon, label, desc)

        item.addEventListener("mouseenter", () => {
          selectedIndex = Number(item.dataset.index) || 0
          updateSelection()
        })
        item.addEventListener("mousedown", (ev) => {
          // Keep focus in filter; prevent editor blur before click
          ev.preventDefault()
        })
        item.addEventListener("click", (ev) => {
          ev.preventDefault()
          ev.stopPropagation()
          closeMenu()
          cmd.action()
        })

        commandList.appendChild(item)
      })
    })

    updateSelection()
  }

  function updateSelection() {
    const items = commandList.querySelectorAll(".pm-slash-menu__item[data-index]")
    items.forEach((item, i) => {
      const el = item as HTMLElement
      const on = i === selectedIndex
      el.classList.toggle("is-on", on)
      el.setAttribute("aria-selected", on ? "true" : "false")
    })
    const selectedItem = items[selectedIndex] as HTMLElement | undefined
    if (selectedItem) selectedItem.scrollIntoView({ block: "nearest" })
  }

  const onPointerDownOutside = (e: Event) => {
    // Use DOM Node (globalThis) — TipTap/ProseMirror also exports `Node`
    const t = e.target
    if (t instanceof globalThis.Node && menu.contains(t)) return
    closeMenu()
  }

  const onDocKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault()
      e.stopPropagation()
      closeMenu()
      try {
        editor.chain().focus().run()
      } catch {
        /* editor may be gone */
      }
    }
  }

  const onEditorDestroy = () => {
    closeMenu()
  }

  const closeMenu = () => {
    try {
      editor.off?.("destroy", onEditorDestroy)
    } catch {
      /* ignore */
    }
    if (!menu.isConnected) {
      document.removeEventListener("pointerdown", onPointerDownOutside, true)
      document.removeEventListener("keydown", onDocKeyDown, true)
      return
    }
    menu.remove()
    document.removeEventListener("pointerdown", onPointerDownOutside, true)
    document.removeEventListener("keydown", onDocKeyDown, true)
  }
  ;(menu as HTMLElement & { __close?: () => void }).__close = closeMenu
  try {
    editor.on?.("destroy", onEditorDestroy)
  } catch {
    /* ignore */
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
        if (cmd) {
          closeMenu()
          cmd.action()
        }
        break
      case "Escape":
      case "Delete":
      case "Backspace":
        // Empty filter + Delete/Backspace (or always Escape) → dismiss slash menu
        if (e.key === "Escape" || !(e.target as HTMLInputElement).value) {
          e.preventDefault()
          closeMenu()
          try {
            editor.chain().focus().run()
          } catch {
            /* ignore */
          }
        }
        break
    }
  })

  searchInput.addEventListener("input", (e) => {
    renderCommands((e.target as HTMLInputElement).value)
  })

  // Mount hidden → measure → place → soft open
  menu.style.visibility = "hidden"
  document.body.appendChild(menu)
  renderCommands()

  const coords = editor.view.coordsAtPos(position)
  const PADDING = 12
  const menuRect = menu.getBoundingClientRect()
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight

  let top = coords.bottom + 8
  let left = coords.left

  if (left + menuRect.width > viewportWidth - PADDING) {
    left = viewportWidth - menuRect.width - PADDING
  }
  if (left < PADDING) left = PADDING
  if (top + menuRect.height > viewportHeight - PADDING) {
    top = coords.top - menuRect.height - 8
  }
  if (top < PADDING) {
    top = PADDING
    menu.style.maxHeight = `${viewportHeight - PADDING * 2}px`
  }

  menu.style.top = `${Math.round(top)}px`
  menu.style.left = `${Math.round(left)}px`
  menu.style.visibility = "visible"
  requestAnimationFrame(() => {
    menu.classList.add("is-open")
  })

  searchInput.focus()

  // Capture-phase pointerdown so clicks on editor / sidebar still dismiss.
  // Defer one frame so the opening keystroke doesn't immediately close.
  requestAnimationFrame(() => {
    document.addEventListener("pointerdown", onPointerDownOutside, true)
    document.addEventListener("keydown", onDocKeyDown, true)
  })
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

  // Same table already open → keep
  if (
    document.getElementById("table-floating-menu") &&
    _floatBarAnchor === table
  ) {
    return
  }

  const menu = document.createElement("div")
  menu.id = "table-floating-menu"
  menu.setAttribute("aria-label", "Table")

  // ★ KEY FIX: prevent mousedown from stealing focus out of ProseMirror.
  // Without this, clicking a button causes the browser to blur the editor,
  // so editor.chain().focus() cannot restore a valid table selection.
  // Exception: <input> elements need focus for typing.
  menu.addEventListener("mousedown", (e) => {
    if ((e.target as HTMLElement).tagName !== "INPUT") e.preventDefault()
  })
  menu.addEventListener("click", (e) => e.stopPropagation())

  const makeBtn = (
    title: string,
    svg: string,
    action: () => void,
    /** Close the floating bar after the action (delete ops). */
    closeAfter = false,
    danger = false,
  ) => {
    const b = makeFloatBarBtn(title, svg, { danger })
    b.addEventListener("click", (e) => {
      e.stopPropagation()
      action()
      if (closeAfter) dismissPremiumFloatBars()
    })
    return b
  }

  // Find the position of a cell at (targetRow, targetCol) inside a SPECIFIC table node
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

  // Resize grid button (9 rows × 5 columns grid + custom inputs)
  const resizeWrap = document.createElement("div")
  resizeWrap.style.cssText = `position: relative; display: inline-flex;`
  const resizeBtn = makeFloatBarBtn(
    "Resize table",
    `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><rect x="1" y="1" width="6" height="6" stroke="currentColor" stroke-width="1.3" rx="0.5"/><rect x="9" y="1" width="6" height="6" stroke="currentColor" stroke-width="1.3" rx="0.5"/><rect x="1" y="9" width="6" height="6" stroke="currentColor" stroke-width="1.3" rx="0.5"/><rect x="9" y="9" width="6" height="6" stroke="currentColor" stroke-width="1.3" rx="0.5"/></svg>`,
  )
  resizeWrap.appendChild(resizeBtn)

  let dropdownEl: HTMLDivElement | null = null
  let closeGridHandler: ((ev: MouseEvent) => void) | null = null

  const closeDropdown = () => {
    if (dropdownEl) { dropdownEl.remove(); dropdownEl = null }
    if (closeGridHandler) {
      document.removeEventListener("mousedown", closeGridHandler, true)
      closeGridHandler = null
    }
    resizeBtn.classList.remove("is-on")
  }

  resizeBtn.addEventListener("click", (e) => {
    e.stopPropagation()
    if (dropdownEl) { closeDropdown(); return }

    const dropdown = document.createElement("div")
    dropdown.className = "pm-float-bar-panel table-resize-dropdown"
    dropdownEl = dropdown
    resizeBtn.classList.add("is-on")

    const GRID_ROWS = 9
    const GRID_COLS = 5
    const grid = document.createElement("div")
    grid.className = "pm-float-bar-grid"
    grid.style.gridTemplateColumns = `repeat(${GRID_COLS}, 16px)`
    dropdown.appendChild(grid)

    const preview = document.createElement("div")
    preview.className = "pm-float-bar-panel-meta"
    preview.textContent = "hover to select"
    dropdown.appendChild(preview)

    const cells: HTMLDivElement[] = []
    for (let r = 1; r <= GRID_ROWS; r++) {
      for (let c = 1; c <= GRID_COLS; c++) {
        const cell = document.createElement("div")
        cell.className = "pm-float-bar-grid-cell"
        cell.dataset.row = String(r)
        cell.dataset.col = String(c)
        cell.addEventListener("mouseenter", () => {
          cells.forEach((d) => {
            const cr = Number(d.dataset.row)
            const cc = Number(d.dataset.col)
            d.classList.toggle("is-hot", cr <= r && cc <= c)
          })
          preview.textContent = `${r} × ${c}`
        })
        cell.addEventListener("click", (ev) => {
          ev.stopPropagation()
          resizeTable(r, c)
          closeDropdown()
          dismissPremiumFloatBars()
        })
        cells.push(cell)
        grid.appendChild(cell)
      }
    }

    grid.addEventListener("mouseleave", () => {
      cells.forEach((d) => d.classList.remove("is-hot"))
      preview.textContent = "hover to select"
    })

    const sep = document.createElement("div")
    sep.className = "pm-float-bar-panel-rule"
    dropdown.appendChild(sep)

    const inputRow = document.createElement("div")
    inputRow.className = "pm-float-bar-panel-row"

    const rowsLabel = document.createElement("span")
    rowsLabel.className = "pm-float-bar-panel-label"
    rowsLabel.textContent = "rows"
    const rowsInput = document.createElement("input")
    rowsInput.type = "number"
    rowsInput.min = "1"
    rowsInput.value = String(table.querySelectorAll("tr").length)
    rowsInput.className = "pm-float-bar-panel-input"

    const colsLabel = document.createElement("span")
    colsLabel.className = "pm-float-bar-panel-label"
    colsLabel.textContent = "cols"
    const colsInput = document.createElement("input")
    colsInput.type = "number"
    colsInput.min = "1"
    colsInput.value = String(table.querySelector("tr")?.querySelectorAll("th,td").length || 3)
    colsInput.className = "pm-float-bar-panel-input"

    const applyBtn = document.createElement("button")
    applyBtn.type = "button"
    applyBtn.className = "pm-float-bar-panel-apply"
    applyBtn.title = "Apply"
    applyBtn.setAttribute("aria-label", "Apply")
    applyBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3.5 3.5L13 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`

    const applyCustom = () => {
      const r = Math.max(1, parseInt(rowsInput.value) || 1)
      const c = Math.max(1, parseInt(colsInput.value) || 1)
      resizeTable(r, c)
      closeDropdown()
      dismissPremiumFloatBars()
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

    setTimeout(() => {
      closeGridHandler = (ev: MouseEvent) => {
        if (
          dropdownEl &&
          !dropdownEl.contains(ev.target as HTMLElement) &&
          ev.target !== resizeBtn &&
          !(
            ev.target instanceof globalThis.Node &&
            resizeBtn.contains(ev.target)
          )
        ) {
          closeDropdown()
        }
      }
      document.addEventListener("mousedown", closeGridHandler, true)
    }, 0)
  })

  menu.appendChild(resizeWrap)
  menu.appendChild(makeFloatBarSep())

  // Row buttons
  menu.appendChild(makeBtn(
    "Insert row above",
    `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M2 4h12M8 1v3M5 1l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><rect x="2" y="6" width="12" height="3" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/><rect x="2" y="11" width="12" height="3" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/></svg>`,
    () => editor.chain().focus().addRowBefore().run(),
  ))
  menu.appendChild(makeBtn(
    "Insert row below",
    `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M2 12h12M8 15v-3M5 15l3-3 3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><rect x="2" y="2" width="12" height="3" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/><rect x="2" y="7" width="12" height="3" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/></svg>`,
    () => editor.chain().focus().addRowAfter().run(),
  ))

  menu.appendChild(makeFloatBarSep())

  // Column buttons
  menu.appendChild(makeBtn(
    "Insert column left",
    `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M4 2v12M1 8h3M1 5l3 3-3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><rect x="6" y="2" width="3" height="12" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/><rect x="11" y="2" width="3" height="12" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/></svg>`,
    () => editor.chain().focus().addColumnBefore().run(),
  ))
  menu.appendChild(makeBtn(
    "Insert column right",
    `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><path d="M12 2v12M15 8h-3M15 5l-3 3 3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><rect x="2" y="2" width="3" height="12" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/><rect x="7" y="2" width="3" height="12" stroke="currentColor" stroke-width="1" rx="0.5" opacity="0.5"/></svg>`,
    () => editor.chain().focus().addColumnAfter().run(),
  ))

  menu.appendChild(makeFloatBarSep())

  // Delete row/column — Lucide-weight icons (row/col bands + small X), close after
  menu.appendChild(makeBtn(
    "Delete row",
    `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><rect x="1.5" y="2" width="13" height="2.4" rx="0.7" stroke="currentColor" stroke-width="1.25" opacity="0.32"/><rect x="1.5" y="6.8" width="8.2" height="2.4" rx="0.7" stroke="currentColor" stroke-width="1.25"/><rect x="1.5" y="11.6" width="13" height="2.4" rx="0.7" stroke="currentColor" stroke-width="1.25" opacity="0.32"/><path d="M11.2 6.4l3.2 3.2M14.4 6.4l-3.2 3.2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>`,
    () => editor.chain().focus().deleteRow().run(),
    true,
  ))
  menu.appendChild(makeBtn(
    "Delete column",
    `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><rect x="2" y="1.5" width="2.4" height="13" rx="0.7" stroke="currentColor" stroke-width="1.25" opacity="0.32"/><rect x="6.8" y="1.5" width="2.4" height="8.2" rx="0.7" stroke="currentColor" stroke-width="1.25"/><rect x="11.6" y="1.5" width="2.4" height="13" rx="0.7" stroke="currentColor" stroke-width="1.25" opacity="0.32"/><path d="M6.4 11.2l3.2 3.2M9.6 11.2l-3.2 3.2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>`,
    () => editor.chain().focus().deleteColumn().run(),
    true,
  ))

  menu.appendChild(makeFloatBarSep())

  // Delete table
  menu.appendChild(makeBtn(
    "Delete table",
    `<svg width="15" height="15" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="12" height="12" stroke="currentColor" stroke-width="1.3" rx="1"/><path d="M5 5l6 6M11 5l-6 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
    () => editor.chain().focus().deleteTable().run(),
    true,
    true,
  ))

  mountPremiumFloatBar(menu, table, { align: "start" })
}

// ──────────────────────────────────────────────
// Utility: Markdown → HTML for distill NodeView body
// (naive regex missed headings / GFM lists; use remark pipeline)
// ──────────────────────────────────────────────
function renderMarkdown(md: string): string {
  if (!md) return ""
  try {
    return String(
      unified()
        .use(remarkParse)
        .use(remarkGfm)
        .use(remarkBreaks)
        .use(remarkRehype)
        .use(rehypeStringify)
        .processSync(md),
    )
  } catch {
    // Safe fallback: escape only
    return md
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\n/g, "<br>")
  }
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
  /**
   * Fired after ProseMirror has taken focus (and click selection is applied).
   * Prefer this over parent mousedown for dual-pane focus — avoids select-all thrash.
   */
  onEditorFocus?: () => void
  /** Whether to show the built-in formatting toolbar. Default true. */
  showToolbar?: boolean
  /** Top offset for sticky toolbar (px). */
  stickyToolbarOffset?: number
  /** Extra toolbar actions on the right side. */
  toolbarActions?: ReactNode
  /**
   * Remove default EditorContent padding (p-4).
   * Use for flush reading surfaces (e.g. Collection Overview summary)
   * so body width matches plain text siblings.
   */
  flush?: boolean
  /**
   * Enable `/` slash command menu. Default true (message / note surfaces).
   * Compact fields (e.g. todo description) pass false — toolbar still available.
   */
  enableSlash?: boolean
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
// Editor Toolbar — Premium R2 strip (Master locked)
// ──────────────────────────────────────────────

/** Solid hex for TipTap data-color + lower-half marker CSS via --pm-hl */
const HIGHLIGHT_SWATCHES = [
  { color: "#e8d48b", label: "Yellow", swatch: "#e8d48b" },
  { color: "#c5dccf", label: "Green", swatch: "#c5dccf" },
  { color: "#f0c9c4", label: "Rose", swatch: "#f0c9c4" },
  { color: "#ddd6c8", label: "Warm gray", swatch: "#ddd6c8" },
  { color: "#d4e0f0", label: "Cool mist", swatch: "#d4e0f0" },
] as const

/** Premium text colors — --pm-* family (no deep-green twin) */
const TEXT_COLOR_PRESETS = [
  { color: "#121410", label: "Ink" },
  { color: "#1a5e3d", label: "Green" },
  { color: "#b42318", label: "Danger" },
  { color: "#6a706a", label: "Muted" },
  { color: "#8a7355", label: "Warm" },
] as const

const TEXT_COLOR_QUICK = TEXT_COLOR_PRESETS.slice(0, 3)
const HIGHLIGHT_QUICK = HIGHLIGHT_SWATCHES.slice(0, 3)

function FmtSep() {
  return <span className="pm-fmt-sep" aria-hidden />
}

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
      data-active={active || undefined}
      disabled={disabled}
      onClick={onClick}
      className={cn("pm-fmt-btn", active && "is-on", className)}
    >
      {children}
    </button>
  )
}

/** Highlight swatch — half-fill hints marker style */
function HighlightSwatch({
  color, active, tooltip, onClick,
}: {
  color: string; active: boolean; tooltip: string; onClick: () => void
}) {
  return (
    <button
      type="button"
      title={tooltip}
      onClick={onClick}
      className={cn("pm-fmt-hl", active && "is-on")}
      style={{
        background: `linear-gradient(to bottom, transparent 50%, ${color} 50%)`,
      }}
    />
  )
}

/** Text color — light A + thin bar */
function TextColorSwatch({
  color, active, tooltip, onClick,
}: {
  color: string; active: boolean; tooltip: string; onClick: () => void
}) {
  return (
    <button
      type="button"
      title={tooltip}
      onClick={onClick}
      className={cn("pm-fmt-tc", active && "is-on")}
    >
      <span className="pm-fmt-tc-a" style={{ color }}>A</span>
      <span className="pm-fmt-tc-bar" style={{ backgroundColor: color }} />
    </button>
  )
}

function ColorPaletteMenu({
  kind,
  presets,
  activeColor,
  onSelect,
  /** Highlight only: empty chip clears mark (no “Clear” text row) */
  onClearHighlight,
  /** Narrow bar: one trigger opens full palette (no inline swatches) */
  compact = false,
}: {
  kind: "highlight" | "text"
  presets: readonly { color: string; label: string; swatch?: string }[]
  activeColor: string | null
  onSelect: (color: string) => void
  onClearHighlight?: () => void
  compact?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const anchorRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const t = e.target
      if (t instanceof globalThis.Node && ref.current?.contains(t)) return
      // Portaled SoftMenu lives under body — still treat as "inside"
      if (
        t instanceof Element &&
        t.closest('[data-slot="menu"][data-menu-portal="true"]')
      ) {
        return
      }
      setOpen(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  const preview = presets.find((p) => p.color === activeColor)
  const previewSw =
    preview && "swatch" in preview && preview.swatch
      ? preview.swatch
      : preview?.color ?? (kind === "highlight" ? "#e8d48b" : "#121410")
  const noHighlight = kind === "highlight" && !activeColor

  return (
    <div ref={ref} className="relative">
      {compact ? (
        <button
          ref={anchorRef}
          type="button"
          title={kind === "highlight" ? "Highlight" : "Text color"}
          className={cn("pm-fmt-compact", open && "is-on")}
          onClick={() => setOpen((o) => !o)}
        >
          {kind === "highlight" ? (
            <span
              className={cn("pm-fmt-hl", noHighlight && "is-none")}
              style={
                noHighlight
                  ? undefined
                  : {
                      background: `linear-gradient(to bottom, transparent 50%, ${previewSw} 50%)`,
                    }
              }
            />
          ) : (
            <span className="pm-fmt-tc">
              <span
                className="pm-fmt-tc-a"
                style={{ color: activeColor || "#121410" }}
              >
                A
              </span>
              <span
                className="pm-fmt-tc-bar"
                style={{ backgroundColor: activeColor || "#121410" }}
              />
            </span>
          )}
          <span className="pm-fmt-compact-chev" aria-hidden>
            ▾
          </span>
        </button>
      ) : (
        <button
          ref={anchorRef}
          type="button"
          title={kind === "highlight" ? "More highlights" : "More text colors"}
          className={cn("pm-fmt-more", open && "is-on")}
          onClick={() => setOpen((o) => !o)}
        >
          +
        </button>
      )}
      <SoftMenu
        open={open}
        portal
        anchorRef={anchorRef}
        align="center"
        className="pm-fmt-palette"
      >
        {/* Single compact row of chips — none (hl only) + presets */}
        <div className="pm-fmt-palette-row">
          {kind === "highlight" && (
            <button
              type="button"
              title="No highlight"
              className={cn("pm-fmt-hl is-none", !activeColor && "is-on")}
              onClick={() => {
                onClearHighlight?.()
                setOpen(false)
              }}
            />
          )}
          {presets.map((p) => {
            const sw = "swatch" in p && p.swatch ? p.swatch : p.color
            const isActive = activeColor === p.color
            return (
              <button
                key={p.color}
                type="button"
                title={p.label}
                className={cn(
                  kind === "highlight" ? "pm-fmt-hl" : "pm-fmt-palette-dot",
                  isActive && "is-on",
                )}
                style={
                  kind === "highlight"
                    ? {
                        background: `linear-gradient(to bottom, transparent 48%, ${sw} 48%)`,
                      }
                    : { backgroundColor: sw }
                }
                onClick={() => {
                  onSelect(p.color)
                  setOpen(false)
                }}
              />
            )
          })}
        </div>
      </SoftMenu>
    </div>
  )
}

function HeadingDropdown({ editor }: { editor: Editor }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const anchorRef = useRef<HTMLButtonElement>(null)

  const level = ([1, 2, 3] as const).find((l) =>
    editor.isActive("heading", { level: l }),
  )
  // No Paragraph row — default trigger is “H” when body text
  const triggerLabel =
    level === 1 ? "H1" : level === 2 ? "H2" : level === 3 ? "H3" : "H"

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const t = e.target
      if (t instanceof globalThis.Node && ref.current?.contains(t)) return
      if (
        t instanceof Element &&
        t.closest('[data-slot="menu"][data-menu-portal="true"]')
      ) {
        return
      }
      setOpen(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        ref={anchorRef}
        type="button"
        title="Heading"
        className={cn("pm-fmt-hd", level && "is-on")}
        onClick={() => setOpen((o) => !o)}
      >
        {triggerLabel}
        <span className="pm-fmt-hd-chev" aria-hidden>
          ▾
        </span>
      </button>
      <SoftMenu
        open={open}
        portal
        anchorRef={anchorRef}
        align="start"
        className="pm-fmt-hd-menu min-w-[11rem]"
      >
        {([1, 2, 3] as const).map((l) => (
          <MenuItem
            key={l}
            active={level === l}
            className="pm-fmt-hd-item"
            onClick={() => {
              // Same level again → toggle off to paragraph (TipTap toggleHeading)
              editor.chain().focus().toggleHeading({ level: l }).run()
              setOpen(false)
            }}
          >
            <span className={cn("pm-fmt-hd-item-label", `is-h${l}`)}>
              Heading {l}
            </span>
            <span className="pm-fmt-hd-item-tag">H{l}</span>
          </MenuItem>
        ))}
      </SoftMenu>
    </div>
  )
}

/** Collapse highlight + text-color swatches into ▾ menus below this width */
const FMT_COMPACT_PX = 420

export function EditorToolbar({
  editor,
  stickyOffset = 0,
  actions,
}: {
  editor: Editor
  stickyOffset?: number
  actions?: ReactNode
}) {
  const [, setTick] = useState(0)
  const barRef = useRef<HTMLDivElement>(null)
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [compactColors, setCompactColors] = useState(false)
  const [editing, setEditing] = useState(false)

  const showToolbar = useCallback(() => {
    if (hideTimerRef.current) {
      clearTimeout(hideTimerRef.current)
      hideTimerRef.current = null
    }
    setEditing(true)
  }, [])

  /** Debounced hide — avoids flash when PM blurs for a frame on toolbar click */
  const scheduleHideToolbar = useCallback(() => {
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current)
    hideTimerRef.current = setTimeout(() => {
      hideTimerRef.current = null
      // Note switch / unmount: editor may already be destroyed
      if (editor.isDestroyed) {
        setEditing(false)
        return
      }
      try {
        if (editor.view.hasFocus()) return
      } catch {
        setEditing(false)
        return
      }
      const bar = barRef.current
      const ae = document.activeElement
      if (bar && ae && bar.contains(ae)) return
      // Open SoftMenu (incl. body-portaled submenus) keeps toolbar "editing"
      if (
        bar?.querySelector(
          '[data-slot="menu"].is-open, .pm-menu--soft.is-open'
        ) ||
        document.querySelector(
          '[data-slot="menu"][data-menu-portal="true"].is-open, [data-slot="menu"][data-menu-portal="true"].pm-menu--soft.is-open'
        )
      ) {
        return
      }
      setEditing(false)
    }, 160)
  }, [editor])

  useEffect(() => {
    let alive = true
    const cb = () => {
      if (!alive || editor.isDestroyed) return
      setTick((t) => t + 1)
    }
    const onFocus = () => {
      if (!alive || editor.isDestroyed) return
      showToolbar()
    }
    const onBlur = () => {
      if (!alive || editor.isDestroyed) return
      scheduleHideToolbar()
    }
    editor.on("selectionUpdate", cb)
    editor.on("transaction", cb)
    editor.on("focus", onFocus)
    editor.on("blur", onBlur)
    try {
      if (editor.view.hasFocus()) showToolbar()
    } catch {
      /* destroyed */
    }
    return () => {
      alive = false
      editor.off("selectionUpdate", cb)
      editor.off("transaction", cb)
      editor.off("focus", onFocus)
      editor.off("blur", onBlur)
      if (hideTimerRef.current) {
        clearTimeout(hideTimerRef.current)
        hideTimerRef.current = null
      }
    }
  }, [editor, showToolbar, scheduleHideToolbar])

  useEffect(() => {
    const el = barRef.current
    if (!el || typeof ResizeObserver === "undefined") return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0
      setCompactColors(w > 0 && w < FMT_COMPACT_PX)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const activeHighlight: string | null = (() => {
    if (editor.isDestroyed) return null
    try {
      const attrs = editor.getAttributes("highlight")
      return (
        attrs.color ??
        (editor.isActive("highlight") ? HIGHLIGHT_QUICK[0].color : null)
      )
    } catch {
      return null
    }
  })()

  const activeTextColor: string | null = (() => {
    if (editor.isDestroyed) return null
    try {
      const attrs = editor.getAttributes("textStyle")
      return attrs.color ?? null
    } catch {
      return null
    }
  })()

  const applyTextColor = (color: string) => {
    if (color === "#121410" || color === "#000000") {
      // Ink / legacy black → clear mark (default body color)
      editor.chain().focus().unsetColor().run()
    } else {
      editor.chain().focus().setColor(color).run()
    }
  }

  const toggleHighlight = (color: string) => {
    if (activeHighlight === color) {
      editor.chain().focus().unsetHighlight().run()
    } else {
      editor.chain().focus().toggleHighlight({ color }).run()
    }
  }

  return (
    <div
      ref={barRef}
      className={cn(
        "pm-fmt-toolbar shrink-0 sticky z-10",
        editing && "is-editing",
      )}
      style={{ top: stickyOffset ?? 0 }}
      onMouseDown={(e) => {
        // Keep visible + keep PM selection (table-menu pattern)
        showToolbar()
        if ((e.target as HTMLElement).closest("button, [role='menu']")) {
          e.preventDefault()
        }
      }}
      onMouseLeave={() => {
        try {
          if (!editor.isDestroyed && !editor.view.hasFocus()) {
            scheduleHideToolbar()
          }
        } catch {
          scheduleHideToolbar()
        }
      }}
    >
      <div className="pm-fmt-inner">
        <div className="pm-fmt-grp">
          <ToolbarBtn
            active={editor.isActive("bold")}
            tooltip="Bold (Ctrl+B)"
            onClick={() => editor.chain().focus().toggleBold().run()}
          >
            <Bold className="h-3.5 w-3.5" strokeWidth={1.5} />
          </ToolbarBtn>
          <ToolbarBtn
            active={editor.isActive("italic")}
            tooltip="Italic (Ctrl+I)"
            onClick={() => editor.chain().focus().toggleItalic().run()}
          >
            <Italic className="h-3.5 w-3.5" strokeWidth={1.5} />
          </ToolbarBtn>
          <ToolbarBtn
            active={editor.isActive("strike")}
            tooltip="Strikethrough"
            onClick={() => editor.chain().focus().toggleStrike().run()}
          >
            <Strikethrough className="h-3.5 w-3.5" strokeWidth={1.5} />
          </ToolbarBtn>
        </div>

        <FmtSep />

        <div className="pm-fmt-grp">
          {compactColors ? (
            <ColorPaletteMenu
              kind="highlight"
              compact
              presets={HIGHLIGHT_SWATCHES}
              activeColor={activeHighlight}
              onSelect={(c) => toggleHighlight(c)}
              onClearHighlight={() =>
                editor.chain().focus().unsetHighlight().run()
              }
            />
          ) : (
            <>
              {HIGHLIGHT_QUICK.map((p) => (
                <HighlightSwatch
                  key={p.color}
                  color={p.swatch}
                  active={activeHighlight === p.color}
                  tooltip={`Highlight ${p.label}`}
                  onClick={() => toggleHighlight(p.color)}
                />
              ))}
              <ColorPaletteMenu
                kind="highlight"
                presets={HIGHLIGHT_SWATCHES}
                activeColor={activeHighlight}
                onSelect={(c) => toggleHighlight(c)}
                onClearHighlight={() =>
                  editor.chain().focus().unsetHighlight().run()
                }
              />
            </>
          )}
        </div>

        <FmtSep />

        <div className="pm-fmt-grp">
          {compactColors ? (
            <ColorPaletteMenu
              kind="text"
              compact
              presets={TEXT_COLOR_PRESETS}
              activeColor={activeTextColor}
              onSelect={applyTextColor}
            />
          ) : (
            <>
              {TEXT_COLOR_QUICK.map((p) => (
                <TextColorSwatch
                  key={p.color}
                  color={p.color}
                  active={
                    p.color === "#121410"
                      ? !activeTextColor ||
                        activeTextColor === "#121410" ||
                        activeTextColor === "#000000"
                      : activeTextColor === p.color
                  }
                  tooltip={`Text ${p.label}`}
                  onClick={() => applyTextColor(p.color)}
                />
              ))}
              <ColorPaletteMenu
                kind="text"
                presets={TEXT_COLOR_PRESETS}
                activeColor={activeTextColor}
                onSelect={applyTextColor}
              />
            </>
          )}
        </div>

        <FmtSep />

        <HeadingDropdown editor={editor} />

        <FmtSep />

        <div className="pm-fmt-grp">
          <ToolbarBtn
            active={editor.isActive("bulletList")}
            tooltip="Bullet list"
            onClick={() => editor.chain().focus().toggleBulletList().run()}
          >
            <List className="h-4 w-4" strokeWidth={1.75} />
          </ToolbarBtn>
          <ToolbarBtn
            active={editor.isActive("orderedList")}
            tooltip="Numbered list"
            onClick={() => editor.chain().focus().toggleOrderedList().run()}
          >
            <ListOrdered className="h-4 w-4" strokeWidth={1.75} />
          </ToolbarBtn>
          <ToolbarBtn
            active={editor.isActive("taskList")}
            tooltip="Task list"
            onClick={() => editor.chain().focus().toggleTaskList().run()}
          >
            <ListTodo className="h-4 w-4" strokeWidth={1.75} />
          </ToolbarBtn>
        </div>

        {actions && (
          <>
            <FmtSep />
            <div className="pm-fmt-grp ml-auto">{actions}</div>
          </>
        )}
      </div>
    </div>
  )
}

/**
 * Highlight mark: store color as --pm-hl for lower-half marker CSS.
 * Merge parent attrs so TipTap Highlight schema stays compatible.
 */
const PremiumHighlight = Highlight.extend({
  addAttributes() {
    const parent = this.parent?.() ?? {}
    return {
      ...parent,
      color: {
        default: null,
        parseHTML: (element: HTMLElement) => {
          const raw =
            element.getAttribute("data-color") ||
            element.style.getPropertyValue("--pm-hl")?.trim() ||
            element.style.backgroundColor ||
            null
          if (!raw) return null
          // Normalize rgb(...) from older inline styles if needed — keep as-is for presets
          return raw
        },
        renderHTML: (attributes: { color?: string | null }) => {
          if (!attributes.color) {
            return { class: "pm-mark-hl" }
          }
          return {
            class: "pm-mark-hl",
            "data-color": attributes.color,
            style: `--pm-hl: ${attributes.color}`,
          }
        },
      },
    }
  },
}).configure({ multicolor: true })

/** Shared empty-editor hint for message-style MarkdownEditors. */
export const MESSAGE_EDITOR_PLACEHOLDER =
  "Write a message… type / for commands"

export function TiptapEditor({
  value, onChange, className, placeholder, children,
  readonly = false, onImageUpload, onNoteLinkClick, onDistillNavigate, onEditorReady,
  onEditorFocus,
  showToolbar = true,
  stickyToolbarOffset, toolbarActions,
  flush = false,
  enableSlash = true,
}: Omit<MarkdownEditorProps, "variant" | "minHeight">) {
  const lastEmitted = useRef(value)
  const externalUpdateRef = useRef(false)
  const editorRef = useRef<any>(null)
  const _readonlyRef = useRef(readonly)
  _readonlyRef.current = readonly
  // Stable focus callback — avoid re-creating useEditor on parent re-renders
  const onEditorFocusRef = useRef(onEditorFocus)
  onEditorFocusRef.current = onEditorFocus
  // Placeholder extension is configured once; read latest text via ref so
  // prop updates / HMR are not stuck on the first mount string.
  const defaultPlaceholder = enableSlash
    ? MESSAGE_EDITOR_PLACEHOLDER
    : "Write…"
  const placeholderRef = useRef(placeholder || defaultPlaceholder)
  placeholderRef.current = placeholder || defaultPlaceholder

  const DistillBlock = useRef(createDistillBlockExtension(onDistillNavigate || onNoteLinkClick)).current
  const Callout = useRef(createCalloutExtension()).current
  const SlashCmd = useRef(
    enableSlash ? createSlashCommandExtension(onImageUpload) : null
  ).current
  const ResizableImage = useRef(createResizableImageExtension()).current

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

  // Prevent deletion of distill blocks and images in readonly mode.
  // Must tolerate setContent / full-doc replace (collection switch, prop updates)
  // — naive nodesBetween(from,to) on intermediate docs throws nodeSize on undefined.
  const ReadonlyProtectExt = useRef(Extension.create({
    name: "readonlyProtect",
    addProseMirrorPlugins() {
      return [new Plugin({
        key: new PluginKey("readonlyProtect"),
        filterTransaction: (tr) => {
          if (!_readonlyRef.current) return true
          if (!tr.docChanged) return true
          try {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const before = (tr as any).before as { content?: { size: number }; descendants?: (fn: (n: any) => void) => void } | undefined
            const after = tr.doc
            if (!before?.content || !after?.content) return true

            // Full document replace (TipTap setContent) — always allow
            const beforeSize = before.content.size
            for (const step of tr.steps) {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              const s = step as any
              if (typeof s.from === "number" && typeof s.to === "number" && s.from === 0 && s.to >= beforeSize) {
                return true
              }
            }

            const beforeProtected = countProtectedNodes(before)
            const afterProtected = countProtectedNodes(after)
            // Block only when protected nodes are removed (user delete)
            if (afterProtected < beforeProtected) return false
          } catch {
            return true
          }
          return true
        },
      })]
    },
  })).current

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function countProtectedNodes(doc: any): number {
    let count = 0
    try {
      if (!doc?.descendants) return 0
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      doc.descendants((node: any) => {
        const n = node?.type?.name
        if (n === "distillBlock" || n === "resizableImage") count++
      })
    } catch {
      /* ignore corrupt/partial docs during apply */
    }
    return count
  }

  const editor = useEditor({
    extensions: [
      StarterKit, DistillBlock, Callout, ResizableImage, ReadonlyProtectExt, MarkdownHoverExt, TableEnhancementExt,
      Table.configure({ resizable: true }), TableRow, TableCell, TableHeader,
      TaskList, TaskItem.configure({ nested: true }),
      Placeholder.configure({
        placeholder: () =>
          placeholderRef.current ||
          (enableSlash ? MESSAGE_EDITOR_PLACEHOLDER : "Write…"),
        showOnlyWhenEditable: true,
        showOnlyCurrent: false,
        includeChildren: false,
      }),
      ...(SlashCmd ? [SlashCmd] : []),
      Youtube.configure({ width: 640, height: 360 }),
      PremiumHighlight,
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
    onFocus: () => {
      // After PM applied the click caret/selection — notify pane chrome.
      // Double-rAF so we don't re-render (distill rail, etc.) mid-gesture.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          onEditorFocusRef.current?.()
        })
      })
    },
    editorProps: {
      attributes: { class: "focus:outline-none" },
      handleDOMEvents: {
        // Empty pad under last line → caret at end (not stuck mid-void)
        mousedown: (view, event) => {
          if (_readonlyRef.current) return false
          return placeCaretAtEndIfClickBelowContent(view, event as MouseEvent)
        },
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
    // Preserve cursor only for small in-place edits — full note switch resets caret.
    // Restoring old note positions into a new doc (esp. with atom distill nodes) can throw.
    const prevSel = editor.state.selection
    const hadFocus = editor.isFocused
    const prevSize = editor.state.doc.content.size
    const isLikelyNoteSwitch =
      Math.abs((enriched?.length ?? 0) - (lastEmitted.current?.length ?? 0)) > 80 ||
      (lastEmitted.current === "" && (enriched?.length ?? 0) > 0) ||
      ((lastEmitted.current?.length ?? 0) > 0 && enriched === "")
    try {
      // emitUpdate: false avoids cascading onUpdate during external reload
      editor.commands.setContent(processed, { emitUpdate: false })
      try {
        const maxPos = editor.state.doc.content.size
        if (maxPos <= 0) {
          /* empty doc */
        } else if (isLikelyNoteSwitch || !hadFocus) {
          // Note switch / unfocused: safe start, never transplant foreign selection
          editor.commands.setTextSelection(Math.min(1, maxPos))
          if (!hadFocus) editor.commands.blur()
        } else {
          // Same note external update: try keep caret, collapse accidental all-select
          let from = Math.min(Math.max(prevSel.from, 1), maxPos)
          let to = Math.min(Math.max(prevSel.to, 1), maxPos)
          if (maxPos > 2 && to - from >= maxPos - 2) {
            from = Math.min(Math.max(from, 1), maxPos)
            to = from
          }
          // If doc shrank a lot, clamp more aggressively
          if (prevSize > 0 && maxPos < prevSize * 0.5) {
            from = to = Math.min(1, maxPos)
          }
          try {
            if (from !== to) {
              editor.commands.setTextSelection({ from, to })
            } else {
              editor.commands.setTextSelection(from)
            }
          } catch {
            editor.commands.setTextSelection(Math.min(1, maxPos))
          }
        }
      } catch {
        try {
          const maxPos = editor.state.doc.content.size
          if (maxPos > 0) editor.commands.setTextSelection(Math.min(1, maxPos))
          if (!hadFocus) editor.commands.blur()
        } catch {
          /* ignore selection restore */
        }
      }
    } catch (err) {
      // Fallback: destroy-range replace can throw on corrupt intermediate state
      console.warn("[TiptapEditor] setContent failed, retry empty then content", err)
      try {
        editor.commands.clearContent(false)
        editor.commands.setContent(processed, { emitUpdate: false })
      } catch (err2) {
        console.error("[TiptapEditor] setContent recovery failed", err2)
      }
    }
    // Use enriched as lastEmitted so the next render cycle sees that the
    // injected content is already applied and doesn't re-trigger setContent.
    lastEmitted.current = enriched
    // setContent with emitUpdate:false skips onUpdate — call onChange when
    // AI description injection changed the markdown so state + save stay in sync.
    if (enriched !== value) {
      onChange?.(enriched)
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

      // Table click → Premium float bar (portal; same-table guard inside show)
      const tableEl = target.closest("table") as HTMLElement | null
      if (tableEl && editorRef.current) {
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
      // Caret placement on chrome / empty pad:
      // - Below last line only → end of document
      // - Left/right margins → do nothing (no forced position)
      const editor = editorRef.current
      if (!editor || editor.isDestroyed || readonly) return
      const pmEl = editor.view.dom as HTMLElement
      const last = pmEl.lastElementChild as HTMLElement | null
      const belowContent =
        !last || e.clientY > last.getBoundingClientRect().bottom + 2
      const outsidePm = !pmEl.contains(target)
      const onPmSurface = target === pmEl || pmEl.contains(target)

      if (belowContent && (outsidePm || onPmSurface)) {
        e.preventDefault()
        editor.chain().focus("end").run()
      }
      // Left/right gutters: leave selection alone (no focus("end"), no Y snap)
    },
    [onNoteLinkClick, readonly]
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
        /* TipTap Placeholder extension — without this, data-placeholder is invisible */
        .tiptap-editor .ProseMirror p.is-editor-empty:first-child::before,
        .tiptap-editor .ProseMirror p.is-empty:first-child::before {
          content: attr(data-placeholder);
          float: left;
          height: 0;
          pointer-events: none;
          color: var(--muted-foreground, #737373);
          opacity: 0.7;
          font-style: italic;
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
        /* Table / quote / callout chrome → index.css (.pm-prose-*) */
      `}</style>
      {!readonly && showToolbar && <EditorToolbar editor={editor} stickyOffset={stickyToolbarOffset} actions={toolbarActions} />}
      <EditorContent
        editor={editor}
        className={cn(
          "prose prose-sm dark:prose-invert max-w-none min-h-full flex-1 w-full",
          flush ? "p-0 !max-w-none" : "p-4"
        )}
      />
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
          <span className="text-muted-foreground italic text-sm">{placeholder || MESSAGE_EDITOR_PLACEHOLDER}</span>
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
