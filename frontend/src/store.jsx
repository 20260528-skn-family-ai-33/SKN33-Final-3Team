// 앱 전역 상태. 서버 연동 전까지 localStorage에 저장한다(키 접두사 bs2.).
// API를 붙일 때는 이 파일의 동작(login, generateReport …)만 서버 호출로 바꾸면 화면 코드는 그대로 쓸 수 있다.
/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { navigate } from './router'
import {
  currentSettings, currentNotifications, initialChats, initialConsents, initialNotifications, initialProfile, initialReports, initialSettings,
  initialSubscription, seedAccounts,
} from './data/seed'
import { productName } from './data/catalog'
import { isStrongPassword } from './utils/validate'
import { canChangeAdminRole } from './auth/roles'

export function useStored(key, initial, normalize = value => value) {
  const [value, setValue] = useState(() => {
    try {
      const raw = localStorage.getItem(key)
      return normalize(raw ? JSON.parse(raw) : initial)
    } catch {
      return initial
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      window.dispatchEvent(new CustomEvent('storage-warning'))
    }
  }, [key, value])
  return [value, setValue]
}

export const REPORT_STAGES = ['데이터 조회', '분석', '기사 검색', '작성', '수치 검증', 'PDF']
const STAGE_MS = 1300

const AppContext = createContext(null)
export const useApp = () => useContext(AppContext)

const today = () => {
  const d = new Date()
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`
}

export function AppProvider({ children }) {
  const [accounts, setAccounts] = useStored('bs2.accounts', seedAccounts)
  const [storedSession, setSession] = useStored('bs2.session', null)
  const sessionAccount = accounts.find(a => a.email === storedSession?.email)
  const session = useMemo(() => storedSession && sessionAccount ? { ...storedSession, role: sessionAccount.role } : null, [storedSession, sessionAccount])
  const [profile, setProfile] = useStored('bs2.profile', initialProfile)
  const [settings, setSettings] = useStored('bs2.settings', initialSettings, currentSettings)
  const [consents, setConsents] = useStored('bs2.consents', initialConsents)
  const [subscription, setSubscription] = useStored('bs2.subscription', initialSubscription)
  const [notifications, setNotifications] = useStored('bs2.notifications', initialNotifications, currentNotifications)
  const [chats, setChats] = useStored('bs2.chats', initialChats)
  const [reports, setReports] = useStored('bs2.reports', initialReports)
  const [mainView, setMainView] = useStored('bs2.mainView', null)
  const [toast, setToast] = useState('')
  const [tick, setTick] = useState(0)

  const notify = useCallback(message => setToast(message), [])

  useEffect(() => {
    if (!toast) return undefined
    const t = setTimeout(() => setToast(''), 5000)
    return () => clearTimeout(t)
  }, [toast])

  useEffect(() => {
    const warn = () => setToast('브라우저 저장 공간을 쓸 수 없습니다. 새로고침하면 변경 내용이 사라질 수 있습니다.')
    window.addEventListener('storage-warning', warn)
    return () => window.removeEventListener('storage-warning', warn)
  }, [])

  // 리포트 생성 진행: 단계별로 시간이 지나면 완료 처리한다. 화면을 옮겨도 계속 진행된다.
  const generating = reports.some(r => r.status === 'generating')
  useEffect(() => {
    if (!generating) return undefined
    const timer = setInterval(() => {
      setTick(t => t + 1)
      const now = Date.now()
      const finished = reports.filter(r => r.status === 'generating' && now - r.startedAt >= STAGE_MS * REPORT_STAGES.length)
      if (!finished.length) return
      setReports(rs => rs.map(r => (finished.some(f => f.id === r.id) ? { ...r, status: 'ready', meta: `생성일 ${r.created} · PDF 생성 완료` } : r)))
      setChats(cs => cs.map(c => {
        const done = finished.find(f => f.chatId === c.id)
        if (!done) return c
        return {
          ...c, status: 'done', reportId: done.id,
          messages: c.messages.map(m => (m.kind === 'progress' && m.reportId === done.id ? { ...m, kind: 'done' } : m)),
        }
      }))
      setToast('리포트 생성이 완료되었습니다. 요청형 리포트 목록에 등록했습니다.')
    }, 400)
    return () => clearInterval(timer)
  }, [generating, reports, setReports, setChats])

  const reportStage = useCallback(report => {
    if (!report || report.status !== 'generating') return REPORT_STAGES.length
    return Math.min(REPORT_STAGES.length - 1, Math.floor((Date.now() - report.startedAt) / STAGE_MS))
  }, [])

  const login = useCallback((email, password) => {
    const account = accounts.find(a => a.email.toLowerCase() === email.trim().toLowerCase())
    if (!account || !account.password || account.password !== password) return { ok: false }
    if (account.status === 'withdrawn') return { ok: false, message: '탈퇴 처리된 계정입니다. 탈퇴일로부터 1개월 동안은 다시 활성화할 수 없습니다.' }
    setSession({ email: account.email, role: account.role, provider: account.provider })
    setProfile(p => (p.email === account.email ? p : { ...initialProfile, name: account.name, email: account.email, countries: [], products: [], home: 'all' }))
    setMainView(null)
    return { ok: true, account }
  }, [accounts, setSession, setProfile, setMainView])

  /** 소셜 인증 결과로 로그인한다. 연결된 계정(같은 제공자 ID 또는 같은 이메일)이 없으면 existing: false */
  const socialSignIn = useCallback(user => {
    const linked = accounts.find(a => a.socials?.some(x => x.provider === user.provider && x.providerId === user.providerId))
    const byEmail = user.email && accounts.find(a => a.email.toLowerCase() === user.email.toLowerCase())
    const match = linked || byEmail
    if (!match) return { ok: true, existing: false }
    if (match.status === 'withdrawn') return { ok: false, message: '탈퇴 처리된 계정입니다. 탈퇴일로부터 1개월 동안은 다시 가입하거나 로그인할 수 없습니다.' }
    setAccounts(list => list.map(a => (a.email !== match.email ? a : {
      ...a, socials: [...(a.socials || []).filter(x => x.provider !== user.provider), { provider: user.provider, providerId: user.providerId }],
    })))
    setSession({ email: match.email, role: match.role, provider: user.provider })
    setProfile(p => (p.email === match.email ? p : { ...initialProfile, name: match.name, email: match.email, countries: [], products: [], home: 'all' }))
    setMainView(null)
    return { ok: true, existing: true, account: match }
  }, [accounts, setAccounts, setSession, setProfile, setMainView])

  const signup = useCallback(({ name, email, password, provider = 'email', providerId }) => {
    if (provider === 'email' && !isStrongPassword(password || '')) return false
    const account = {
      email: email.trim(), password: password || null, name: name.trim(), provider, joined: today(), role: 'user',
      socials: providerId ? [{ provider, providerId }] : [],
    }
    setAccounts(list => [...list.filter(a => a.email !== account.email), account])
    setSession({ email: account.email, role: 'user', provider })
    setProfile({ ...initialProfile, name: account.name, email: account.email, countries: [], products: [], home: 'all' })
    setMainView(null)
  }, [setAccounts, setSession, setProfile, setMainView])

  const changeAdminRole = useCallback((email, role) => {
    const target = accounts.find(a => a.email === email)
    if (!canChangeAdminRole(session, target, role)) return false
    setAccounts(list => list.map(a => a.email === email ? { ...a, role } : a))
    setToast(role === 'admin' ? '관리자 권한을 부여했습니다.' : '관리자 권한을 회수했습니다.')
    return true
  }, [accounts, session, setAccounts])

  const logout = useCallback(() => {
    setSession(null)
    setMainView(null)
    try {
      sessionStorage.removeItem('bs2.pwVerified')
      sessionStorage.removeItem('bs2.socialPending')
    } catch { /* 저장소 접근 불가 */ }
    navigate('/login')
    setToast('로그아웃했습니다.')
  }, [setSession, setMainView])

  const changePassword = useCallback((current, next) => {
    const account = accounts.find(a => a.email === session?.email)
    // 서버에서도 같은 규칙으로 막아야 한다(화면 검사만 믿지 않기).
    if (!account || account.password !== current || !isStrongPassword(next)) return false
    setAccounts(list => list.map(a => (a.email === account.email ? { ...a, password: next } : a)))
    return true
  }, [accounts, session, setAccounts])

  const withdraw = useCallback(reasons => {
    setAccounts(list => list.map(a => (a.email === session?.email ? { ...a, status: 'withdrawn', withdrawnAt: today(), withdrawReasons: reasons } : a)))
    setSession(null)
    navigate('/login')
    setToast('회원탈퇴가 완료되었습니다. 탈퇴일로부터 1개월 보존 후 완전히 삭제됩니다.')
  }, [session, setAccounts, setSession])

  const resetDemo = useCallback(() => {
    ;['accounts', 'session', 'profile', 'settings', 'consents', 'subscription', 'notifications', 'chats', 'reports', 'mainView', 'keywords']
      .forEach(k => { try { localStorage.removeItem('bs2.' + k) } catch { /* 저장소 접근 불가 */ } })
    window.location.hash = '/login'
    window.location.reload()
  }, [])

  /** 채팅 조건으로 요청형 리포트를 만든다. */
  const generateReport = useCallback((chatId, options) => {
    const id = 'r-' + Date.now().toString(36)
    const title = `${options.country || '전체 국가'} ${productName(options.product)} 수출입 분석`
    setReports(rs => [{
      id, chatId, kind: 'chat', title, status: 'generating', startedAt: Date.now(), created: today(), pages: 12,
      meta: `생성일 ${today()} · 생성 중`, note: '생성 완료 시 요청형 목록에 등록된 리포트', options,
    }, ...rs])
    setChats(cs => cs.map(c => (c.id !== chatId ? c : {
      ...c, status: 'generating',
      messages: c.messages.map(m => (m.kind === 'condition' && !m.locked ? { ...m, locked: true } : m))
        .concat({ role: 'ai', kind: 'progress', options, reportId: id }),
    })))
    setToast('리포트 생성을 시작했습니다. 다른 화면으로 이동해도 작업은 유지됩니다.')
    return id
  }, [setReports, setChats])

  const unread = notifications.filter(n => !n.read).length
  const account = accounts.find(a => a.email === session?.email)

  const value = useMemo(() => ({
    accounts, setAccounts, account, session, changeAdminRole, login, socialSignIn, signup, logout, changePassword, withdraw, resetDemo,
    profile, setProfile, settings, setSettings, consents, setConsents, subscription, setSubscription,
    notifications, setNotifications, unread, chats, setChats, reports, setReports, generateReport, reportStage,
    mainView, setMainView, notify, toast, setToast, tick,
  }), [tick, accounts, setAccounts, account, session, changeAdminRole, login, socialSignIn, signup, logout, changePassword, withdraw, resetDemo,
    profile, setProfile, settings, setSettings, consents, setConsents, subscription, setSubscription,
    notifications, setNotifications, unread, chats, setChats, reports, setReports, generateReport, reportStage,
    mainView, setMainView, notify, toast])

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}
