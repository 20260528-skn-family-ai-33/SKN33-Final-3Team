import test from 'node:test'
import assert from 'node:assert/strict'
import { articleBadges, cleanReferenceMarker } from '../src/pages/analysis/articlePresentation.js'

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
