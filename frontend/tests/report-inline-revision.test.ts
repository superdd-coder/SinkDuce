import assert from 'node:assert/strict'
import {test} from 'node:test'
import {unified} from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import remarkRehype from 'remark-rehype'
import rehypeStringify from 'rehype-stringify'

async function redline(before:string,after:string){
  const {inlineRevision}=await import('../src/lib/report-inline-revision.ts')
  return String(await unified().use(remarkParse).use(remarkGfm).use(remarkRehype).use(inlineRevision,{before}).use(rehypeStringify).process(after))
}

test('a small word correction keeps the surrounding sentence once',async()=>{
  const html=await redline('The sample is queued for inspection.','The sample is ready for inspection.')
  assert.match(html,/<del[^>]*>queued<\/del><ins[^>]*>ready<\/ins>/)
  assert.equal(html.split('The sample is ').length-1,1)
  assert.equal(html.split(' for inspection.').length-1,1)
})

test('a minor edit across rich text marks only the changed phrase',async()=>{
  const html=await redline('Keep the **queued** sample for review.','Keep the **ready** sample for review.')
  assert.match(html,/<del[^>]*><strong>queued<\/strong><\/del>/)
  assert.match(html,/<ins[^>]*><strong>ready<\/strong><\/ins>/)
  assert.equal(html.split('Keep the ').length-1,1)
})

test('a numeric correction is visible in place and the untouched sentence appears once',async()=>{
  const html=await redline('Stock is **24 units**. Keep this sentence.','Stock is **36 units**. Keep this sentence.')
  assert.match(html,/<del[^>]*><strong>24<\/strong><\/del>/)
  assert.match(html,/<ins[^>]*><strong>36<\/strong><\/ins>/)
  assert.equal(html.split('Keep this sentence.').length-1,1)
  assert.equal((html.match(/<p>/g)||[]).length,1)
})

test('scattered changes in one Chinese sentence become one readable replacement',async()=>{
  const html=await redline('试验样品有18箱，合格样品有11箱，两者口径不同。下一句不变。','试验样品共有18箱；合格样品为11箱，两者口径不同。下一句不变。')
  assert.equal((html.match(/<del\b/g)||[]).length,1)
  assert.equal((html.match(/<ins\b/g)||[]).length,1)
  assert.equal(html.split('下一句不变。').length-1,1)
})

test('pure insertion and deletion are visible without opening another control',async()=>{
  assert.match(await redline('','A new paragraph.'),/pm-revision-added-block/)
  assert.match(await redline('An old paragraph.',''),/pm-revision-removed-block/)
})

test('citations keep their original links and renumbering alone is not an editorial change',async()=>{
  const html=await redline('Supported fact.[1](#evidence-a)','Supported fact.[2](#evidence-a)')
  assert.doesNotMatch(html,/<del\b|<ins\b/)
  assert.equal((html.match(/href="#evidence-a"/g)||[]).length,1)
})

test('rich text remains structured and source HTML never becomes executable markup',async()=>{
  const html=await redline('## Heading\n\n| Item | Amount |\n|---|---|\n| A | 10 |','## Heading\n\n| Item | Amount |\n|---|---|\n| A | 12 |\n\n<script>alert(1)</script>')
  assert.match(html,/<table>/)
  assert.match(html,/<del[^>]*>10<\/del>/)
  assert.match(html,/<ins[^>]*>12<\/ins>/)
  assert.doesNotMatch(html,/<script/)
})

test('deleted cell content is marked as historical text for selection mapping',async()=>{
  const html=await redline('| Item | Count |\n| --- | --- |\n| Sample | **18 crates** |','| Item | Count |\n| --- | --- |\n| Sample | **27 crates** |')
  assert.equal((html.match(/<table\b/g)||[]).length,1)
  assert.match(html,/<del[^>]*data-review-deletion="true"[^>]*><strong>18<\/strong><\/del>/)
})

test('tight list items retain their list structure and mark changed text',async()=>{
  const html=await redline('- Old item.\n- Keep item.','- New item.\n- Keep item.')
  assert.match(html,/<ul>/)
  assert.match(html,/<li><del[^>]*>Old<\/del><ins[^>]*>New<\/ins> item\.<\/li>/)
  assert.equal(html.split('Keep item.').length-1,1)
})

test('reading either side of a redline reconstructs that side without losing or duplicating content',async()=>{
  const {inlineRevision}=await import('../src/lib/report-inline-revision.ts')
  const parser=unified().use(remarkParse).use(remarkGfm).use(remarkRehype)
  const text=(node:any,side?:'before'|'after'):string=>{
    const classes=node.properties?.className||[]
    if(side==='before'&&(node.tagName==='ins'||classes.includes('pm-revision-added-block')))return ''
    if(side==='after'&&(node.tagName==='del'||classes.includes('pm-revision-removed-block')))return ''
    return node.type==='text'?node.value:(node.children||[]).map((child:any)=>text(child,side)).join('')
  }
  const plain=(value:string)=>value.replace(/\s+/g,'')
  const pairs=[
    ['# Summary\n\nKeep.\n\nOld paragraph.','# Summary\n\nNew paragraph.\n\nKeep.'],
    ['- Alpha\n- Beta\n  - Nested old.','- Alpha\n- Beta\n  - Nested new.'],
    ['> First sentence. Second **old** sentence.','> First sentence. Second **new** sentence.'],
    ['虚构样品共有18箱。保持不变。','虚构样品共有18箱；其中合格样品有11箱。保持不变。'],
    ['| Item | Count |\n| --- | --- |\n| Sample | **18 crates** |\n| Removed | 7 crates |\n| Keep | 4 crates |',
     '| Item | Count |\n| --- | --- |\n| Sample | **27 crates** |\n| Keep | 4 crates |\n| Added | 9 crates |'],
  ]
  for(const [before,after] of pairs){
    const old=parser.runSync(parser.parse(before)),current=parser.runSync(parser.parse(after)),merged=structuredClone(current)
    inlineRevision({before})(merged)
    assert.equal(plain(text(merged,'before')),plain(text(old)),before)
    assert.equal(plain(text(merged,'after')),plain(text(current)),after)
  }
})
