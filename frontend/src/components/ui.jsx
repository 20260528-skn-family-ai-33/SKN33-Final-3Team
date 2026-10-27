// 목업 클래스(.btn, .input, .cb, .rd, .tg, .modal …)를 실제로 동작하는 요소로 감싼 공통 컴포넌트.
import { useEffect, useId, useRef } from 'react'
import { Link } from '../router'
import Icon from './Icon'

const cx = (...a) => a.filter(Boolean).join(' ')

/** 버튼. to가 있으면 링크, 없으면 button으로 그린다. kind: red | ink | plum | line | lav | danger | dis */
export function Btn({ kind = 'line', size = 'sm', block, to, icon, iconRight, disabled, className, children, type = 'button', ...rest }) {
  const cls = cx('btn', `btn-${disabled ? 'dis' : kind}`, size === 'sm' && 'btn-sm', size === 'lg' && 'btn-lg', block && 'btn-block', className)
  const body = <>{icon && <Icon name={icon} size={size === 'lg' ? 16 : 14} />}{children}{iconRight && <Icon name={iconRight} size={size === 'lg' ? 18 : 15} />}</>
  if (to && !disabled) return <Link to={to} className={cls} {...rest}>{body}</Link>
  return <button type={type} className={cls} disabled={disabled} aria-disabled={disabled || undefined} {...rest}>{body}</button>
}

/** 칩 형태 탭. to가 있으면 링크. */
export function Chip({ on, sm, to, className, children, ...rest }) {
  const cls = cx('chip', sm && 'sm', on && 'on', className)
  if (to) return <Link to={to} className={cls} aria-current={on ? 'page' : undefined} {...rest}>{children}</Link>
  return <button type="button" className={cls} aria-pressed={!!on} {...rest}>{children}</button>
}

export function Tag({ tone, className, children, ...rest }) {
  return <span className={cx('tag', tone, className)} {...rest}>{children}</span>
}

/** .input 상자 안에 실제 input을 넣는다. 값이 있으면 .val, 오류면 .err */
export function TextField({ value, onChange, placeholder, type = 'text', icon, iconColor = '#9AA0AA', sm, invalid, className, style, inputRef, onEnter, ...rest }) {
  return (
    <div className={cx('input', sm && 'sm', value && 'val', invalid && 'err', className)} style={style}>
      <input
        ref={inputRef} type={type} value={value ?? ''} placeholder={placeholder} aria-invalid={invalid || undefined}
        onChange={e => onChange?.(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && onEnter) { e.preventDefault(); onEnter() } }}
        {...rest}
      />
      {icon && <Icon name={icon} size={sm ? 16 : 18} color={iconColor} stroke={icon === 'lock' ? 1.8 : 1.9} />}
    </div>
  )
}

export function TextArea({ value, onChange, placeholder, rows = 4, invalid, style, ...rest }) {
  return (
    <div className={cx('input', 'area', value && 'val', invalid && 'err')} style={style}>
      <textarea rows={rows} value={value ?? ''} placeholder={placeholder} onChange={e => onChange?.(e.target.value)} {...rest} />
    </div>
  )
}

/** 목업의 드롭다운 모양(.input + chevron)을 유지한 select */
export function Select({ value, onChange, options, sm, prefix, style, className, ...rest }) {
  return (
    <div className={cx('input', 'select', sm && 'sm', 'val', className)} style={style}>
      {prefix && <span className="select-prefix">{prefix}</span>}
      <select value={value} onChange={e => onChange?.(e.target.value)} {...rest}>
        {options.map(o => {
          const [v, label] = Array.isArray(o) ? o : [o, o]
          return <option key={v} value={v}>{label}</option>
        })}
      </select>
      <Icon name="chevronDown" color="#5C6370" stroke={2} />
    </div>
  )
}

export function Check({ checked, onChange, children, className, style, disabled, ...rest }) {
  return (
    <label className={cx('opt', disabled && 'off', className)} style={style}>
      <input type="checkbox" className="sr-only" checked={!!checked} disabled={disabled} onChange={e => onChange?.(e.target.checked)} {...rest} />
      <span className={cx('cb', checked && 'on')} aria-hidden="true" />
      {children}
    </label>
  )
}

export function Radio({ checked, onChange, name, children, className, style, ...rest }) {
  return (
    <label className={cx('opt', className)} style={style}>
      <input type="radio" className="sr-only" name={name} checked={!!checked} onChange={() => onChange?.()} {...rest} />
      <span className={cx('rd', checked && 'on')} aria-hidden="true" />
      {children}
    </label>
  )
}

export function Toggle({ on, onChange, label, disabled }) {
  return (
    <button
      type="button" role="switch" aria-checked={!!on} aria-label={label} disabled={disabled}
      className={cx('tg', on && 'on')} onClick={() => onChange?.(!on)}
    />
  )
}

/** 화면 위에 뜨는 확인 창. Esc·배경 클릭으로 닫는다. */
export function Modal({ open = true, onClose, width = 600, top, label, children, className }) {
  const ref = useRef(null)
  const id = useId()
  useEffect(() => {
    if (!open) return undefined
    const prev = document.activeElement
    ref.current?.focus()
    const onKey = e => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', onKey)
    document.body.classList.add('modal-open')
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.classList.remove('modal-open')
      prev?.focus?.()
    }
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="modal-layer" style={top ? { paddingTop: top } : undefined}>
      <div className="dim" onClick={onClose} />
      <div ref={ref} className={cx('modal', className)} style={{ width }} role="dialog" aria-modal="true" aria-label={label} aria-labelledby={label ? undefined : id} tabIndex={-1}>
        <span id={id} hidden>{label}</span>
        {children}
      </div>
    </div>
  )
}

/** 남색 머리글 + 격자 행 표 (.th / .tr) */
export function GridTable({ cols, head, rows, renderCell, rowKey, selected, onRowClick, style }) {
  return (
    <div style={style}>
      <div className="th" style={{ gridTemplateColumns: cols }}>{head.map((h, i) => <span key={i}>{h}</span>)}</div>
      {rows.map((r, i) => (
        <div
          key={rowKey ? rowKey(r, i) : i} className={cx('tr', selected?.(r, i) && 'sel', onRowClick && 'clickable')}
          style={{ gridTemplateColumns: cols }} onClick={onRowClick ? () => onRowClick(r, i) : undefined}
        >
          {(Array.isArray(r) ? r : head).map((c, j) => <span key={j}>{renderCell ? renderCell(r, j, i) : c}</span>)}
        </div>
      ))}
    </div>
  )
}

/** 단계 표시(관심사 등록 등) */
export function Steps({ items, current }) {
  return (
    <div className="steps">
      {items.map((label, i) => (
        <div key={label} style={{ display: 'contents' }}>
          {i > 0 && <span className={cx('bar', i <= current && 'on')} />}
          <div className={cx('step', i === current && 'on', i < current && 'done')}>
            <i>{i < current ? <Icon name="check" size={16} color="#fff" stroke={2.4} /> : i + 1}</i>
            {String(i + 1).padStart(2, '0')} {label}
          </div>
        </div>
      ))}
    </div>
  )
}

export function Pager({ page, pages, onChange }) {
  return (
    <nav aria-label="페이지" style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 22 }}>
      <Chip sm aria-label="이전 페이지" disabled={page <= 1} onClick={() => onChange(page - 1)}><Icon name="arrowLeft" size={14} /></Chip>
      {Array.from({ length: pages }, (_, i) => i + 1).map(n => (
        <Chip sm key={n} on={n === page} onClick={() => onChange(n)}>{n}</Chip>
      ))}
      <Chip sm aria-label="다음 페이지" disabled={page >= pages} onClick={() => onChange(page + 1)}><Icon name="arrowRight" size={14} /></Chip>
    </nav>
  )
}
