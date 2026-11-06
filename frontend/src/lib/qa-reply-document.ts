import StarterKit from "@tiptap/starter-kit"
import { Markdown } from "tiptap-markdown"
import { Extension } from "@tiptap/core"
import { Plugin } from "@tiptap/pm/state"

// Retain the reply field's existing size limit, including markup, for paste and typing.
const ReplyLimit = Extension.create({
  name: "replyLimit",
  addProseMirrorPlugins() {
    return [new Plugin({ filterTransaction: transaction => {
      if (!transaction.docChanged) return true
      const storage = this.editor.storage as unknown as { markdown: { serializer: { serialize: (doc: typeof transaction.doc) => string } } }
      return storage.markdown.serializer.serialize(transaction.doc).length <= 32767
    } })]
  },
})

const replyStarterKit = () => StarterKit.configure({
  blockquote: false, bulletList: false, code: false, codeBlock: false,
  heading: false, horizontalRule: false, link: false, listItem: false,
  listKeymap: false, orderedList: false, strike: false, underline: false,
  dropcursor: false, gapcursor: false, trailingNode: false,
})

export const replyExtensions = () => [replyStarterKit(), Markdown.configure({ html: false }), ReplyLimit]
