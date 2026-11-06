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


/** One shared display unit per chart. Underlying values remain unchanged. */
export function chartDisplay(values, unit) {
  const max=Math.max(0,...values.filter(v=>Number.isFinite(v)).map(Math.abs))
  const scale=unit==='USD'?(max>=1e12?1e12:max>=1e8?1e8:max>=1e4?1e4:1):1
  const prefix=({10000:'만',100000000:'억',1000000000000:'조'})[scale]||''
  return {unit:prefix+unit,format:n=>(n/scale).toLocaleString('ko-KR',{maximumFractionDigits:2})}
}
