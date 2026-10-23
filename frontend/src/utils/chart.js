// 차트 눈금 계산
/** 0부터 max까지 보기 좋은 눈금 */
export function niceTicks(values, count = 4, fromZero = false) {
  const max = Math.max(...values), minV = Math.min(...values)
  const lo = fromZero ? 0 : Math.max(0, Math.floor((minV * 0.85) / 5) * 5)
  const span = Math.max(1, max * 1.05 - lo)
  const raw = span / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || raw
  return Array.from({ length: count + 1 }, (_, i) => +(lo + i * step).toFixed(2))
}
