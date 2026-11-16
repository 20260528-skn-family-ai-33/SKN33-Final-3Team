// 요약 화면만 줄인다. 관심정보 편집 화면에는 전체 선택 항목을 유지한다.
export function summarizeItems(items, empty, limit = 3) {
  if (!items.length) return empty
  const remaining = Math.max(0, items.length - limit)
  return items.slice(0, limit).join(' · ') + (remaining ? ` 외 ${remaining}개` : '')
}
