import { Link } from '../router'

export default function AdminTabs({ items, label = '관리자 하위 메뉴', style }) {
  return <nav className="subnav article-tabs admin-tabs" aria-label={label} style={style}>
    {items.map(t => <Link key={t.to || t.label} to={t.to} className={t.on ? 'on' : ''} aria-current={t.on ? 'page' : undefined}>{t.label}</Link>)}
  </nav>
}
