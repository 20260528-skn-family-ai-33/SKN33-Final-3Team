// Display-only cleanup: retain the stored evidence text and source quotes.
export const cleanReferenceMarker = text => typeof text === 'string'
  ? text.replace(/참고\s*\(직접 답변 아님\)\s*[:：]?\s*/g, '') : text

export function articleBadges(article) {
  if (article.unavailable) return ['참고']
  if (article.evidence_role === 'context_reference') return ['참고']
  if (article.evidence_role === 'mixed') return ['근거', '참고']
  if (article.evidence_role === 'direct_evidence') return ['근거']
  // Older answers without a role must not be promoted to direct evidence.
  return ['참고']
}
