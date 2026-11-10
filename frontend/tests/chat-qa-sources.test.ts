import assert from 'node:assert/strict'
import { test } from 'node:test'
import { citationFileTarget, sourceEvidence } from '../src/lib/source-evidence.ts'

const row = {
  text: 'Before rehearsal.',
  metadata: {
    collection: 'example-project', version_id: 'working-version', qa_snapshot_id: 'example-snapshot',
    qa_list_id: 'example-list', qa_question_id: 'example-question', qa_column_id: 'second-round',
    qa_evidence_id: 'e_exact', source_label: 'Example checklist · Gate check',
  },
}

test('a Q&A source row opens the pinned thread and round without an inline citation', () => {
  const result = sourceEvidence(row, 0)
  assert.equal(result.kind, 'qa')
  assert.equal(result.id, 'e_exact')
  assert.equal(result.source.qa_snapshot_id, 'example-snapshot')
  assert.equal(result.source.qa_column_id, 'second-round')
  assert.equal(citationFileTarget(result, []), null)
})

test('Q&A evidence uses the exact ID even when another source repeats the quote', () => {
  const exact = sourceEvidence(row, 0)
  const unrelated = {...exact, id: 'e_other', source: {...exact.source, qa_snapshot_id: 'other-thread'}}
  assert.deepEqual(sourceEvidence(row, 0, [unrelated, exact]), exact)
})
