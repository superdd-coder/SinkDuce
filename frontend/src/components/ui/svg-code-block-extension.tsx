import StarterKit from "@tiptap/starter-kit"
import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer, type NodeViewProps } from "@tiptap/react"
import { SvgCodeBlock } from "@/components/chat/svg-code-block"

/** Preview controls, including their nested icons, belong to the widget. */
export function stopSvgPreviewControlEvent({ event }: { event: Event }): boolean {
  return !!(event.target as Element | null)?.closest?.("[data-svg-preview-controls]")
}

/** A view of the standard codeBlock node; Markdown always retains its source. */
export function EditorCodeBlock({ node }: Pick<NodeViewProps, "node">) {
  const language = String(node.attrs.language || "")
  if (language.toLowerCase() !== "svg") {
    return (
      <NodeViewWrapper as="pre">
        <NodeViewContent<"code"> as="code" className={language ? `language-${language}` : undefined} />
      </NodeViewWrapper>
    )
  }
  return (
    <NodeViewWrapper className="pm-editor-svg">
      <SvgCodeBlock
        source={node.textContent}
        complete
        sourceEditor={<NodeViewContent<"code"> as="code" className="language-svg" style={{ whiteSpace: "pre" }} />}
      />
    </NodeViewWrapper>
  )
}

// Extend the codeBlock already supplied by StarterKit, retaining its schema,
// shortcuts and Markdown serializer without another editor dependency.
export const TiptapStarterKit = StarterKit.extend({
  addExtensions() {
    return (this.parent?.() || []).map((extension) => extension.name === "codeBlock"
      ? extension.extend({
        addNodeView() {
          return ReactNodeViewRenderer(EditorCodeBlock, {
            contentDOMElementTag: "span",
            stopEvent: stopSvgPreviewControlEvent,
          })
        },
      })
      : extension)
  },
})
