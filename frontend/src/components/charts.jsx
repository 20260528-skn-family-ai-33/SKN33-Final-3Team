// 목업과 같은 모양의 SVG 차트(선 그래프·도넛·스파크라인)

/** 월별 꺾은선. series: [{ color, values }] — 점 위에 값 표시, null 칸은 선을 끊는다 */
export function LineChart({ labels, series, yTicks, width = 760, height = 230, label = '선 그래프', formatValue = String, fullLabels, showValues = true }) {
  const x0 = Math.max(40,Math.min(100,...yTicks.map(t=>String(formatValue(t)).length*7+12))), x1 = width - 20, yTop = 16, yBottom = height - 28
  const min = yTicks[0], max = yTicks[yTicks.length - 1]
  const x = i => (labels.length === 1 ? (x0 + x1) / 2 : x0 + (i * (x1 - x0)) / (labels.length - 1))
  const y = v => yBottom - ((v - min) / (max - min || 1)) * (yBottom - yTop)
  const every = Math.ceil(labels.length / 12)
  const path = values => values.map((v, i) => v == null ? '' : `${i && values[i - 1] != null ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} style={{ display: 'block' }}>
      {yTicks.map(t => (
        <g key={t}>
          <line x1={x0} x2={x1} y1={y(t)} y2={y(t)} stroke="#ECEEF1" />
          <text x={x0 - 8} y={y(t) + 4} fontSize="11" fill="#8A919C" textAnchor="end">{formatValue(t)}</text>
        </g>
      ))}
      {labels.map((l, i) => i % every === 0 && <text key={i} x={x(i)} y={height - 8} fontSize="11" fill="#8A919C" textAnchor="middle">{fullLabels?.[i] && <title>{fullLabels[i]}</title>}{l}</text>)}
      {series.map((s, si) => (
        <g key={si}>
          <path d={path(s.values)} fill="none" stroke={s.color} strokeWidth="2.5" strokeLinejoin="round" />
          {s.values.map((v, i) => v != null && (
            <g key={i}>
              <circle cx={x(i)} cy={y(v)} r="4.5" fill="#fff" stroke={s.color} strokeWidth="2.5"><title>{s.name} · {labels[i]}: {formatValue(v)}</title></circle>
              {/* 비교(여러 선)도 값을 작은 글자로 쓴다. 겹치지 않게 짝수 선은 점 위, 홀수 선은 점 아래에 둔다(21번) */}
              {showValues && labels.length<=12 && <text x={x(i)} y={series.length===1 || si%2===0 ? y(v) - (series.length===1?11:8) : y(v) + 16}
                fontSize={series.length===1 ? (String(formatValue(v)).length>8?10:12) : 9} fontWeight="700" fill={s.color}
                textAnchor={i === 0 ? 'start' : i === s.values.length - 1 ? 'end' : 'middle'}>{formatValue(v)}</text>}
            </g>
          ))}
        </g>
      ))}
    </svg>
  )
}

/** 가로 막대. series가 둘 이상이면 묶음 막대, 음수는 0선 왼쪽으로 그린다 */
export function BarChart({ labels, series, width = 760, label = '막대 그래프', formatValue = String, fullLabels }) {
  const values = series.flatMap(s => s.values).filter(v => v != null)
  const lo = Math.min(0, ...values), hi = Math.max(0, ...values)
  const x0 = 150, x1 = width - Math.max(80,Math.min(160,...values.map(v=>String(formatValue(v)).length*7+15))), bar = 14, gap = 4, group = series.length * (bar + gap) + 10
  const x = v => x0 + ((v - lo) / (hi - lo || 1)) * (x1 - x0)
  const height = labels.length * group + 8
  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} style={{ display: 'block' }}>
      <line x1={x(0)} x2={x(0)} y1={0} y2={height} stroke="#D5D9E0" />
      {labels.map((l, i) => (
        <g key={i}>
          <text x={x0 - 10} y={i * group + group / 2 + 4} fontSize="12" fill="#444B57" textAnchor="end">{fullLabels?.[i] && <title>{fullLabels[i]}</title>}{l}</text>
          {series.map((s, si) => {
            const v = s.values[i], top = i * group + 5 + si * (bar + gap)
            return v != null && <g key={si}>
              <rect x={Math.min(x(0), x(v))} y={top} width={Math.max(1, Math.abs(x(v) - x(0)))} height={bar} rx="3" fill={s.color}><title>{s.name} · {l}: {formatValue(v)}</title></rect>
              <text x={Math.max(x(0), x(v)) + 6} y={top + bar - 3} fontSize="11" fontWeight="700" fill={s.color}>{formatValue(v)}</text>
            </g>
          })}
        </g>
      ))}
    </svg>
  )
}
/** 두 시점 비교 막대(조회·비교 기간) */
export const GroupedBarChart = BarChart

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
    path = values.map((v, i) => `${i ? 'L' : 'M'}${(values.length === 1 ? 60 : i * 120 / (values.length - 1)).toFixed(1)} ${(32 - ((v - min) / (max - min || 1)) * 28).toFixed(1)}`).join(' ')
  }
  return (
    <svg width="80" height="38" viewBox="0 0 120 36" preserveAspectRatio="none" style={{ flex: 'none' }} aria-hidden="true">
      <path d={path} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  )
}
