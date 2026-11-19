import test from 'node:test'
import assert from 'node:assert/strict'
import { articleBadges, cleanReferenceMarker, referenceParts } from '../src/pages/analysis/articlePresentation.js'

test('article roles remain distinct; mixed shows both and legacy is not promoted', () => {
  assert.deepEqual(articleBadges({evidence_role:'context_reference'}), ['참고'])
  assert.deepEqual(articleBadges({evidence_role:'direct_evidence'}), ['근거'])
  assert.deepEqual(articleBadges({evidence_role:'mixed'}), ['근거','참고'])
  assert.deepEqual(articleBadges({}), ['참고'])
  assert.deepEqual(articleBadges({evidence_role:'direct_evidence',unavailable:true}), ['참고'])
})
test('only the redundant role marker is removed, retaining factual caveats', () => {
  assert.equal(cleanReferenceMarker('참고(직접 답변 아님): 2025년 규제 개혁. 수출 원인은 확인 불가.'), '2025년 규제 개혁. 수출 원인은 확인 불가.')
  assert.equal(cleanReferenceMarker('시장 규모는 1조 위안. [1]'), '시장 규모는 1조 위안. [1]')
})

test('mixed articles retain evidence badges and tag only the original reference markers', () => {
  const article = { evidence_role: 'mixed', sentences: [
    {text: '중국 수출은 증가했다. [1]'},
    {text: '참고(직접 답변 아님): 시장 규모는 전년도 기준이다. [1]'},
  ] }
  assert.deepEqual(articleBadges(article), ['근거', '참고'])
  assert.deepEqual(referenceParts(article.sentences[0].text), [{text: article.sentences[0].text, reference: false}])
  assert.deepEqual(referenceParts(article.sentences[1].text), [
    {text: '', reference: true}, {text: '시장 규모는 전년도 기준이다. [1]', reference: false},
  ])
  const answer = article.sentences.map(s => s.text).join('\n') + '\n표기 없는 다음 문장.'
  const parts = referenceParts(answer)
  assert.equal(parts.filter(p => p.reference).length, 1)
  assert.equal(parts[0].text, '중국 수출은 증가했다. [1]\n')
  assert.equal(parts.map(p => p.text).join(''), cleanReferenceMarker(answer))
})
