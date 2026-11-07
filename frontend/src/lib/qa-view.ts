export interface QaPanel { mode: "overview" | "reading" | "preview"; questionId: string | null }
type PanelAction = { type: "select"; questionId: string } | { type: "preview" | "list" | "overview" }

export function qaPanelTransition(state: QaPanel, action: PanelAction): QaPanel {
  if (action.type === "select") return { mode: "reading", questionId: action.questionId }
  if (action.type === "overview") return { ...state, mode: "overview" }
  if (!state.questionId) return state
  return { ...state, mode: action.type === "preview" ? "preview" : "reading" }
}

interface SearchableQuestion { included: boolean; status: string; type?: string; text: string; replies: { body: string }[] }
export function filterQaQuestions<T extends SearchableQuestion>(questions: T[], query: string, status: string, includeOmitted: boolean, type = "all"): T[] {
  const search = query.trim().toLocaleLowerCase()
  return questions.filter(q => (includeOmitted || q.included) && (status === "all" || q.status === status)
    && (type === "all" || q.type === type)
    && (!search || [q.text, ...q.replies.map(r => r.body)].some(text => text.toLocaleLowerCase().includes(search))))
}
