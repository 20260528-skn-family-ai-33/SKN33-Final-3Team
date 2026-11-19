export function monthlySeries(rows, metric) {
  const points = rows.slice(-12).map(row => ({ ...row, value: row[metric] == null ? null : Number(row[metric]) }))
  const peak = Math.max(1, ...points.map(p => p.value ?? 0))
  const step = Math.max(1, Math.ceil(peak / 4 / 10 ** Math.floor(Math.log10(peak / 4))) * 10 ** Math.floor(Math.log10(peak / 4)))
  const max = step * 4
  const segments = []
  points.forEach((point, index) => {
    if (point.value == null) return
    if (!index || points[index - 1].value == null) segments.push([])
    segments[segments.length - 1].push({ ...point, index })
  })
  return { points, segments, max, ticks: [0, step, step * 2, step * 3, max] }
}
