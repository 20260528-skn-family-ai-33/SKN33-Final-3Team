import { monthlySeries } from './monthlySeries'

export default function MonthlyLine({ rows, metric, label, unit, color }) {
  const { points, segments, max, ticks } = monthlySeries(rows, metric)
  const basis = point => point.is_current ? '현재' : metric === 'total_members' && point.member_source === 'REGISTRATION' ? '가입일 기준 누적' : metric === 'active_subscriptions' && point.subscription_source === 'START_DATE' ? '현재 구독 시작일 기준 누적' : '시작일 기준 누적'
  const w = 600, h = 250, left = 54, right = 20, top = 26, bottom = 38
  const x = index => left + index * (w - left - right) / Math.max(1, points.length - 1)
  const y = value => h - bottom - value / max * (h - top - bottom)
  return <div className="admin-monthly-chart">
    <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${label}, 최근 최대 12개월`}>
      <title>{label}. {points.map(p => `${p.month}: ${p.value == null ? '기록 없음' : `${p.value}${unit}`}${p.value == null ? '' : ` (${basis(p)})`}`).join(', ')}</title>
      {ticks.map(tick => <g key={tick}>
        <line x1={left} x2={w - right} y1={y(tick)} y2={y(tick)} stroke="#E3E6EA" />
        <text x={left - 10} y={y(tick) + 4} textAnchor="end" fontSize="12" fill="#8A919C">{tick.toLocaleString()}</text>
      </g>)}
      {segments.filter(segment => segment.length > 1).map((segment, index) => <polyline key={index} points={segment.map(p => `${x(p.index)},${y(p.value)}`).join(' ')} fill="none" stroke={color} strokeWidth="2.5" strokeLinejoin="round" />)}
      {points.map((point, index) => <g key={point.month}>
        <text x={x(index)} y={h - 14} textAnchor="middle" fontSize="11" fill={point.is_current ? color : '#8A919C'}>{point.month.slice(2).replace('-', '.')}</text>
        {point.value != null && <g>
          <circle cx={x(index)} cy={y(point.value)} r="4" fill={point.is_current ? '#fff' : color} stroke={color} strokeWidth="2" tabIndex="0"><title>{point.month} · {point.value.toLocaleString()}{unit}{` (${basis(point)})`}</title></circle>
          {(point.is_current || point.value > 0) && <text x={x(index)} y={y(point.value) - 12} textAnchor="end" fontSize="12" fontWeight="700" fill={color}>{point.value.toLocaleString()}</text>}
        </g>}
      </g>)}
    </svg>
  </div>
}
