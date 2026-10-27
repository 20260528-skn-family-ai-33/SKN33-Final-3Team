// 비밀번호 조건 실시간 표시(회원가입·비밀번호 변경 공통)
import { passwordChecks } from '../utils/validate'

export default function PasswordRules({ value, showErrors }) {
  const checks = passwordChecks(value)
  return (
    <ul aria-label="비밀번호 조건" style={{ listStyle: 'none', margin: '8px 0 0', padding: 0, display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: 12, fontWeight: 700 }}>
      {checks.map(c => {
        const color = c.ok ? 'var(--ok)' : showErrors || value ? 'var(--red-d)' : 'var(--sub2)'
        return (
          <li key={c.key} style={{ color, display: 'inline-flex', alignItems: 'center', gap: 4 }} aria-label={`${c.label} ${c.ok ? '충족' : '미충족'}`}>
            <span aria-hidden="true">{c.ok ? '✓' : value || showErrors ? '✕' : '·'}</span>{c.label}
          </li>
        )
      })}
    </ul>
  )
}
