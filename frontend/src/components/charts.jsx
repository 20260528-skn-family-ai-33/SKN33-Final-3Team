// 목업과 같은 모양의 SVG 차트(선 그래프·도넛·스파크라인)

/** 월별 꺾은선. series: [{ color, values }] — 점 위에 값 표시 */
export function LineChart({ labels, series, yTicks, width = 760, height = 230, label = '선 그래프' }) {
  const x0 = 40, x1 = width - 20, yTop = 16, yBottom = height - 28
  const min = yTicks[0], max = yTicks[yTicks.length - 1]
  const x = i => (labels.length === 1 ? (x0 + x1) / 2 : x0 + (i * (x1 - x0)) / (labels.length - 1))
  const y = v => yBottom - ((v - min) / (max - min)) * (yBottom - yTop)
  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} style={{ display: 'block' }}>
      {yTicks.map(t => (
        <g key={t}>
          <line x1={x0} x2={x1} y1={y(t)} y2={y(t)} stroke="#ECEEF1" />
          <text x={x0 - 8} y={y(t) + 4} fontSize="11" fill="#8A919C" textAnchor="end">{t}</text>
        </g>
      ))}
      {labels.map((l, i) => <text key={l} x={x(i)} y={height - 8} fontSize="11" fill="#8A919C" textAnchor="middle">{l}</text>)}
      {series.map(s => (
        <g key={s.color}>
          <path d={s.values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')} fill="none" stroke={s.color} strokeWidth="2.5" strokeLinejoin="round" />
          {s.values.map((v, i) => (
            <g key={i}>
              <circle cx={x(i)} cy={y(v)} r="4.5" fill="#fff" stroke={s.color} strokeWidth="2.5" />
              <text x={x(i)} y={y(v) - 11} fontSize="12" fontWeight="700" fill={s.color} textAnchor="middle">{v}</text>
            </g>
          ))}
        </g>
      ))}
    </svg>
  )
}

/** 비율 도넛(목업의 원그래프). items: [[label, percent, color]] */
export function Donut({ items, size = 132 }) {
  const C = 2 * Math.PI * 10.5
  const lens = items.map(([, pct]) => (pct / 100) * C)
  const starts = lens.map((_, i) => lens.slice(0, i).reduce((s, v) => s + v, 0))
  return (
    <svg width={size} height={size} viewBox="0 0 42 42" role="img" aria-label="원그래프" style={{ flex: 'none', transform: 'rotate(-90deg)' }}>
      {items.map(([label, , color], i) => (
        <circle key={label} cx="21" cy="21" r="10.5" fill="none" stroke={color} strokeWidth="21" strokeDasharray={`${lens[i].toFixed(2)} ${(C - lens[i]).toFixed(2)}`} strokeDashoffset={(-starts[i]).toFixed(2)} />
      ))}
    </svg>
  )
}

/** 카드 안 작은 추세선 */
export function Sparkline({ d, values, color = '#C8102E' }) {
  let path = d
  if (!path && values) {
    const max = Math.max(...values), min = Math.min(...values)
    path = values.map((v, i) => `${i ? 'L' : 'M'}${(i * 120 / (values.length - 1)).toFixed(1)} ${(32 - ((v - min) / (max - min || 1)) * 28).toFixed(1)}`).join(' ')
  }
  return (
    <svg width="80" height="38" viewBox="0 0 120 36" preserveAspectRatio="none" style={{ flex: 'none' }} aria-hidden="true">
      <path d={path} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  )
}
