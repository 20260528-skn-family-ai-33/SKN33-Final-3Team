// 스트리밍은 문자열만 정리하고, 완료된 문장은 원래 표기 위치에 참고 태그를 표시한다.
const REFERENCE_MARKER = /참고\s*\(직접 답변 아님\)\s*[:：]?[ \t]*/g
export const cleanReferenceMarker = text => typeof text === 'string' ? text.replace(REFERENCE_MARKER, '') : text

export function referenceParts(text) {
  if (typeof text !== 'string') return [{ text, reference: false }]
  const parts = []
  let offset = 0
  for (const match of text.matchAll(REFERENCE_MARKER)) {
    if (match.index > offset) parts.push({ text: text.slice(offset, match.index), reference: false })
    parts.push({ text: '', reference: true })
    offset = match.index + match[0].length
  }
  if (offset < text.length) parts.push({ text: text.slice(offset), reference: false })
  return parts
}

export function articleBadges(article) {
  if (article.unavailable) return ['참고']
  if (article.evidence_role === 'context_reference') return ['참고']
  if (article.evidence_role === 'mixed') return ['근거', '참고']
  if (article.evidence_role === 'direct_evidence') return ['근거']
  // Older answers without a role must not be promoted to direct evidence.
  return ['참고']
}
