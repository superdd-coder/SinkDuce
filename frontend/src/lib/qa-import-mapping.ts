import type { QaImportPlan, QaMapping } from "@/api/client"

const singleRoles = ["question", "number", "status", "type", "group"] as const

export function columnRole(mapping: QaMapping, index: number) {
  for (const role of singleRoles) if (mapping[role] === index) return role
  return mapping.replies.includes(index) ? "reply" : mapping.ignored?.includes(index) ? "ignore" : "extra"
}

export function changeColumnRole(plan: QaImportPlan, index: number, role: string): QaMapping {
  const roles: string[] = plan.headers.map((_, i) => columnRole(plan.mapping, i))
  if (singleRoles.some(singleRole => singleRole === role)) roles.forEach((r, i) => { if (r === role) roles[i] = "extra" })
  roles[index] = role
  const single = (name: string) => roles.includes(name) ? roles.indexOf(name) : null
  const multi = (name: string) => roles.flatMap((r, i) => r === name && i >= (plan.region?.start_column || 1) - 1 ? [i] : [])
  return { question: single("question") ?? plan.mapping.question, number: single("number"), status: single("status"), type: single("type"), group: single("group"), replies: multi("reply"), extras: multi("extra"), ignored: multi("ignore") }
}

export function canConfirmQaFields(plan: Pick<QaImportPlan, "rows" | "table_rows">) {
  return plan.rows.length > 0 && plan.rows.every(row => row.text.trim()) &&
    !(plan.table_rows || []).some(row => row.kind === "review")
}

export function qaParentCandidates(plan: Pick<QaImportPlan, "rows" | "table_rows" | "parents">, key: string) {
  const source = plan.table_rows || plan.rows.map(row => ({ ...row, kind: "question", group_key: "", parent_key: "" }))
  const current = source.find(row => row.key === key)
  const excluded = new Set([key])
  let changed = true
  while (changed) {
    changed = false
    for (const row of source) {
      const parent = plan.parents?.[row.key] ?? row.parent_key ?? ""
      if (excluded.has(parent) && !excluded.has(row.key)) { excluded.add(row.key); changed = true }
    }
  }
  const eligible = new Set(source.filter(row => ["question", "child"].includes(row.kind) && row.group_key === current?.group_key && !excluded.has(row.key)).map(row => row.key))
  return plan.rows.filter(row => eligible.has(row.key))
}
