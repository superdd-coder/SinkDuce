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
import { Markdown } from "tiptap-markdown"
import { Node, mergeAttributes, Extension } from "@tiptap/core"
import { Plugin, PluginKey } from "@tiptap/pm/state"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"

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
// Custom Resizable Image Extension
// ──────────────────────────────────────────────
const ResizableImage = Node.create({
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
          // Decode URI component to get original URL
          return src ? decodeURIComponent(src) : null
        },
        renderHTML: (attrs: any) => {
          // Encode URL to handle spaces and special characters
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
        default: "auto",
        parseHTML: (element: HTMLElement) => element.style.width || "auto",
        renderHTML: (attrs: any) => attrs.width !== "auto" ? { style: `width: ${attrs.width}` } : {},
      },
      alignment: {
        default: "center",
        parseHTML: () => "center",
        renderHTML: () => ({}),
      },
    }
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
          // Always output standard markdown - postprocessDistillBlocks will handle spaces
          state.write(`![${alt}](${src})${title}`)
        },
      },
    }
  },

  addNodeView() {
    return ({ node, getPos, editor }) => {
      const container = document.createElement("div")
      container.className = "image-container"
      container.style.cssText = `
        position: relative;
        display: inline-block;
        max-width: 100%;
        margin: 8px 0;
        text-align: ${node.attrs.alignment || "center"};
      `
      container.contentEditable = "false"

      const img = document.createElement("img")
      img.src = node.attrs.src
      img.alt = node.attrs.alt || ""
      img.title = node.attrs.title || ""
      img.style.cssText = `
        max-width: 100%;
        height: auto;
        width: ${node.attrs.width || "auto"};
        cursor: pointer;
        border-radius: 4px;
        transition: box-shadow 0.2s;
      `

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
          bottom: -2px;
          width: 20px;
          height: 20px;
          cursor: nwse-resize;
          opacity: 0;
          transition: opacity 0.2s;
          display: flex;
          align-items: center;
          justify-content: center;
        `
        // Diagonal resize arrows SVG - two arrows pointing from corners
        handle.innerHTML = `
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M14 2L18 6M18 6H14M18 6V2" stroke="#3b82f6" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M6 18L2 14M2 14H6M2 14V18" stroke="#3b82f6" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="10" cy="10" r="2" fill="#3b82f6"/>
          </svg>
        `
        return handle
      }

      resizeHandle = createResizeHandle()
      container.appendChild(img)
      container.appendChild(resizeHandle)

      // Show/hide resize handle on hover
      container.addEventListener("mouseenter", () => {
        if (resizeHandle) resizeHandle.style.opacity = "1"
        img.style.boxShadow = "0 0 0 2px #3b82f6"
      })

      container.addEventListener("mouseleave", () => {
        if (!isResizing && resizeHandle) {
          resizeHandle.style.opacity = "0"
          img.style.boxShadow = ""
        }
      })

      // Resize functionality
      resizeHandle.addEventListener("mousedown", (e) => {
        e.preventDefault()
        isResizing = true
        startX = e.clientX
        startWidth = img.offsetWidth

        const onMouseMove = (e: MouseEvent) => {
          if (!isResizing) return
          const diff = e.clientX - startX
          const newWidth = Math.max(50, startWidth + diff)
          img.style.width = `${newWidth}px`
        }

        const onMouseUp = () => {
          isResizing = false
          if (resizeHandle) resizeHandle.style.opacity = "0"
          img.style.boxShadow = ""

          // Update node attributes
          if (typeof getPos === "function") {
            const pos = getPos()
            if (pos !== undefined) {
              const { tr } = editor.state
              const nodeAtPos = editor.state.doc.nodeAt(pos)
              if (nodeAtPos) {
                tr.setNodeMarkup(pos, undefined, {
                  ...nodeAtPos.attrs,
                  width: img.style.width,
                })
                editor.view.dispatch(tr)
              }
            }
          }

          document.removeEventListener("mousemove", onMouseMove)
          document.removeEventListener("mouseup", onMouseUp)
        }

        document.addEventListener("mousemove", onMouseMove)
        document.addEventListener("mouseup", onMouseUp)
      })

      // Image click handler - show floating menu
      container.addEventListener("click", (e) => {
        if (!isResizing) {
          e.stopPropagation()
          showImageFloatingMenu(container, node.attrs, (newAttrs: any) => {
            if (typeof getPos === "function") {
              const pos = getPos()
              if (pos !== undefined) {
                const { tr } = editor.state
                const nodeAtPos = editor.state.doc.nodeAt(pos)
                if (nodeAtPos) {
                  tr.setNodeMarkup(pos, undefined, { ...nodeAtPos.attrs, ...newAttrs })
                  editor.view.dispatch(tr)
                }
              }
            }
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
          img.style.width = updatedNode.attrs.width || "auto"
          container.style.textAlign = updatedNode.attrs.alignment || "center"
          return true
        },
        ignoreMutation: () => true,
      }
    }
  },
})

// ──────────────────────────────────────────────
// Image Floating Menu
// ──────────────────────────────────────────────
function showImageFloatingMenu(
  container: HTMLElement,
  attrs: any,
  onUpdate: (attrs: any) => void
) {
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
      onUpdate({ alignment: opt.value })
      container.style.textAlign = opt.value
      menu.remove()
    })
    menu.appendChild(btn)
  })

  // Divider
  const divider = document.createElement("div")
  divider.style.cssText = `width: 1px; background: rgba(255,255,255,0.2); margin: 4px 2px;`
  menu.appendChild(divider)

  // Caption button with pencil icon
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
    const caption = prompt("Enter image caption:", attrs.alt || "")
    if (caption !== null) {
      onUpdate({ alt: caption })
      // Update or create caption element
      let captionEl = container.querySelector(".image-caption") as HTMLElement
      if (!captionEl) {
        captionEl = document.createElement("div")
        captionEl.className = "image-caption"
        captionEl.style.cssText = `
          font-size: 13px;
          color: #666;
          text-align: center;
          margin-top: 8px;
          font-style: italic;
        `
        container.appendChild(captionEl)
      }
      captionEl.textContent = caption
    }
    menu.remove()
  })
  menu.appendChild(captionBtn)

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
        const dom = document.createElement("div")
        dom.setAttribute("data-type", "distill-block")
        dom.setAttribute("data-block-id", node.attrs.blockId)
        dom.setAttribute("data-loading", node.attrs.loading ? "true" : "false")
        dom.className = "distill-block"
        dom.style.cssText = `
          border: 1px solid #90caf9; border-left: 4px solid #1976d2;
          border-radius: 6px; margin: 12px 0; background: #f8fbff;
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
          background: #e3f2fd; border-bottom: 1px solid #bbdefb; font-size: 12px;
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
        link.style.cssText = `color: #1565c0; text-decoration: none; flex: 1; font-weight: 500; cursor: pointer;`
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
          background: #1976d2; color: white; border-radius: 3px;
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
                border-top: 2px solid #1976d2; border-radius: 50%;
                animation: spin 1s linear infinite;
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
          background: linear-gradient(transparent, #f8fbff);
          border: none;
          border-top: 1px solid #e3f2fd;
          color: #1976d2;
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
                    border-top: 2px solid #1976d2; border-radius: 50%;
                    animation: spin 1s linear infinite;
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
          info: { bg: "#e3f2fd", border: "#1976d2", icon: "💡" },
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

  // Position menu
  const coords = editor.view.coordsAtPos(position)
  const PADDING = 12

  menu.style.visibility = "hidden"
  menu.style.position = "fixed"
  document.body.appendChild(menu)

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

  renderCommands()
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

export function TiptapEditor({
  value, onChange, className, minHeight, placeholder, children,
  readonly = false, onImageUpload, onNoteLinkClick, onDistill, onDistillNavigate, onEditorReady,
}: Omit<MarkdownEditorProps, "variant">) {
  const lastEmitted = useRef(value)
  const externalUpdateRef = useRef(false)
  const editorRef = useRef<any>(null)

  const DistillBlock = useRef(createDistillBlockExtension(onDistillNavigate || onNoteLinkClick)).current
  const Callout = useRef(createCalloutExtension()).current
  const SlashCmd = useRef(createSlashCommandExtension(onDistill, onImageUpload)).current

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

  const editor = useEditor({
    extensions: [
      StarterKit, DistillBlock, Callout, ResizableImage, MarkdownHoverExt, TableEnhancementExt,
      Table.configure({ resizable: true }), TableRow, TableCell, TableHeader,
      TaskList, TaskItem.configure({ nested: true }),
      Placeholder.configure({ placeholder: placeholder || 'Type "/" for commands...' }),
      SlashCmd,
      Youtube.configure({ width: 640, height: 360 }),
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
    if (value === lastEmitted.current) return
    externalUpdateRef.current = true
    const { processed } = preprocessDistillBlocks(value)
    editor.commands.setContent(processed)
    lastEmitted.current = value
    requestAnimationFrame(() => { externalUpdateRef.current = false })
  }, [value, editor])

  useEffect(() => { if (editor) editor.setEditable(!readonly) }, [readonly, editor])

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      const target = e.target as HTMLElement
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
      className={cn("tiptap-editor relative", readonly && "bg-muted/50", className)}
      style={{ minHeight }}
      onClick={handleClick}
    >
      {children && !readonly && (
        <div className="absolute top-2 right-2 z-10 flex gap-1 pointer-events-auto">{children}</div>
      )}
      <style>{`
        .tiptap-editor [data-type="taskList"] {
          list-style: none;
          padding-left: 0;
        }
        .tiptap-editor [data-type="taskItem"] {
          display: flex;
          align-items: flex-start;
          gap: 8px;
        }
        .tiptap-editor [data-type="taskItem"] > label {
          flex-shrink: 0;
          margin-top: 4px;
        }
        .tiptap-editor [data-type="taskItem"] > label input[type="checkbox"] {
          width: 16px;
          height: 16px;
          cursor: pointer;
        }
        .tiptap-editor [data-type="taskItem"] > div {
          flex: 1;
          min-width: 0;
        }
        .tiptap-editor [data-type="taskItem"] > div p {
          margin: 0;
          line-height: 1.5;
        }
        /* Table styles */
        .tiptap-editor .tiptap-table {
          position: relative;
          border-collapse: collapse;
          width: 100%;
          margin: 8px 0;
        }
        .tiptap-editor .tiptap-table td,
        .tiptap-editor .tiptap-table th {
          border: 1px solid #e0e0e0;
          padding: 8px 12px;
          position: relative;
          min-width: 60px;
        }
        .tiptap-editor .tiptap-table th {
          background: #f5f5f5;
          font-weight: 600;
        }
        /* Table add row/column buttons on hover */
        .tiptap-editor .tiptap-table td:hover::after,
        .tiptap-editor .tiptap-table th:hover::after {
          content: '+';
          position: absolute;
          width: 20px;
          height: 20px;
          background: #3b82f6;
          color: white;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 14px;
          cursor: pointer;
          z-index: 10;
          opacity: 0;
          transition: opacity 0.2s;
        }
        .tiptap-editor .tiptap-table td:hover::after,
        .tiptap-editor .tiptap-table th:hover::after {
          opacity: 0.8;
        }
        /* Add column button - right side */
        .tiptap-editor .tiptap-table td:last-child::after,
        .tiptap-editor .tiptap-table th:last-child::after {
          right: -12px;
          top: 50%;
          transform: translateY(-50%);
        }
        /* Add row button - bottom */
        .tiptap-editor .tiptap-table tr:last-child td::after {
          bottom: -12px;
          left: 50%;
          transform: translateX(-50%);
        }
      `}</style>
      <EditorContent editor={editor} className="prose prose-sm dark:prose-invert max-w-none p-4" />
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
