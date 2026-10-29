/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api, setCsrf, download } from './api/client'
import { navigate } from './router'
import { registerCountries, registerProducts, countryCode, countryName } from './data/catalog'
import { mapNotification, mapReport } from './api/adapters'
const AppContext = createContext(null)
export const useApp = () => useContext(AppContext)
export const REPORT_STAGES = ['생성 대기', '서버에서 생성 중', '완료']
// Presentation state only. Server records and credentials never persist in localStorage.
export function useStored(_key, initial) { return useState(initial) }
export async function allPages(path, query = {}) {
  const items = []
  for (let page = 1; ; page++) {
    const data = await api(path, { query: { ...query, page, page_size: 100 } })
    items.push(...data.items)
    if (!data.page?.has_next) return items
  }
}
const emptyProfile = { name: '', email: '', products: [], countries: [], home: 'all' }
const accountView = user => ({ ...user, id: user.user_id, serverRole: user.role, role: user.role === 'ADMIN' ? 'admin' : 'user', provider: 'email' })
export function AppProvider({ children }) {
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [profile, updateProfile] = useState(emptyProfile)
  const [settings, updateSettings] = useState({ all: false, surge: false, major: false, report: false })
  const [subscription, updateSubscription] = useState({ cycles: [], formats: ['PDF'], items: [] })
  const [notifications, updateNotifications] = useState([])
  const [unread, setUnread] = useState(0)
  const [reports, setReports] = useState([])
  const [chats, setChats] = useState([])
  const [accounts, setAccounts] = useState([])
  const [options, setOptions] = useState({ report_periods: [] })
  const [countries, setCountries] = useState([])
  const [mainView, setMainView] = useState(null)
  const [toast, setToast] = useState('')
  const epoch = useRef(0)
  const notificationRequest = useRef(null)
  const notificationRevision = useRef(0)
  const notify = useCallback(message => setToast(message), [])
  const fail = useCallback(error => {
    notify(error.message + (error.status === 409 ? ' 새로고침한 뒤 변경 내용을 확인하고 다시 저장해 주세요.' : error.status === 429 ? ` ${error.retryAfter || 60}초 후 다시 시도해 주세요.` : ''))
    return false
  }, [notify])
  const run = useCallback(async action => { try { return await action() } catch (error) { return fail(error) } }, [fail])
  const clear = useCallback(() => {
    epoch.current++; setSession(null); setCsrf(null); updateProfile(emptyProfile); setChats([]); setReports([]); updateNotifications([]); setUnread(0); setAccounts([]); setMainView(null)
  }, [])
  const refreshNotifications = useCallback(async (force = false) => {
    const current = epoch.current
    if (!force && notificationRequest.current?.epoch === current) return notificationRequest.current.promise
    const revision = ++notificationRevision.current
    const promise = Promise.all([allPages('/notifications'), api('/notifications/unread-count')]).then(([items, count]) => {
      if (current === epoch.current && revision === notificationRevision.current) { updateNotifications(items.map(mapNotification)); setUnread(count.count) }
    }).finally(() => { if (notificationRequest.current?.promise === promise) notificationRequest.current = null })
    notificationRequest.current = { epoch: current, promise }
    return promise
  }, [])
  const refreshReports = useCallback(async () => {
    const current = epoch.current; const items = await allPages('/reports')
    if (current === epoch.current) setReports(items.map(mapReport))
  }, [])
  const refreshChats = useCallback(async () => {
    const current = epoch.current; const items = await allPages('/chat/sessions')
    if (current === epoch.current) setChats(items.map(c => ({ ...c, id: c.session_id, date: c.updated_at, messages: [] })))
  }, [])
  const loadUser = useCallback(async (user, current) => {
    const [interest, config, refs, pref, subs] = await Promise.all([
      api('/users/me/interests'), api('/config/options'), api('/reference/countries'), api('/users/me/notification-settings'), api('/users/me/subscriptions'),
    ])
    if (current !== epoch.current) return
    registerCountries(refs.items); setCountries(refs.items); setOptions(config)
    const scope = interest.interests
    const groups = await Promise.all([...new Set(scope.hs_items.map(h => h.hs_code.slice(0, 4)))].map(code => api(`/reference/hs-codes/${code}/children`, { query: { hs_ver: 'HS2022' } })))
    if (current !== epoch.current) return
    groups.forEach(g => registerProducts(g.items))
    updateProfile({ ...emptyProfile, name: user.name, email: user.email, products: scope.hs_items.map(h => h.hs_code), countries: scope.country_codes.map(countryName), home: scope.landing_page === 'INTERESTED' ? 'interest' : 'all', version: scope.version })
    const s = pref.settings
    updateSettings({ all: s.notifications_enabled, surge: s.spike_popup, major: s.major_issue_popup, report: s.report_popup, termsAgreed: s.consent_current, termsVersion: s.terms_version })
    updateSubscription({ ...subs, cycles: subs.items.filter(s => s.enabled).map(s => s.period_code.toLowerCase()), formats: subs.items.find(s => s.enabled)?.formats || ['PDF'] })
    await Promise.all([refreshNotifications(), refreshReports(), refreshChats()])
  }, [refreshNotifications, refreshReports, refreshChats])
  const refresh = useCallback(async () => {
    const current = ++epoch.current; setLoading(true); setLoadError(null)
    try {
      const data = await api('/users/me')
      if (current !== epoch.current) return
      setCsrf(data.csrf_token); setSession(accountView(data.user)); await loadUser(data.user, current)
    } catch (error) {
      if (current !== epoch.current) return
      if (error.status === 401) { clear(); setLoading(false) } else setLoadError(error)
    } finally { if (current === epoch.current) setLoading(false) }
  }, [loadUser, clear])
  useEffect(() => {
    const generation = epoch
    void Promise.resolve().then(refresh)
    const expired = () => { clear(); setLoading(false); notify('로그인이 만료되었습니다. 다시 로그인해 주세요.') }
    window.addEventListener('session-expired', expired)
    return () => { generation.current++; window.removeEventListener('session-expired', expired) }
  }, [refresh, clear, notify])
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(''), 5000); return () => clearTimeout(timer) }, [toast])
  useEffect(() => {
    if (!session || loading || loadError) return
    const poll = () => { if (document.visibilityState !== 'hidden') void refreshNotifications().catch(() => {}) }
    const timer = setInterval(poll, 30000)
    window.addEventListener('focus', poll)
    document.addEventListener('visibilitychange', poll)
    return () => { clearInterval(timer); window.removeEventListener('focus', poll); document.removeEventListener('visibilitychange', poll) }
  }, [session, loading, loadError, refreshNotifications])
  const authenticate = async (path, body) => {
    try {
      const data = await api(path, { method: 'POST', body }); const current = ++epoch.current
      setCsrf(data.csrf_token); setSession(accountView(data.user)); setLoading(true); setLoadError(null)
      try { await loadUser(data.user, current) } catch (error) { setLoadError(error) } finally { setLoading(false) }
      return { ok: true, account: accountView(data.user), path: data.user.post_login_path }
    } catch (error) { return { ok: false, message: error.message } }
  }
  const setProfile = async next => run(async () => {
    const p = typeof next === 'function' ? next(profile) : next
    const data = await api('/users/me/interests', { method: 'PUT', body: { hs_items: p.products.map(hs_code => ({ hs_code, hs_ver: 'HS2022' })), country_codes: p.countries.map(countryCode), landing_page: p.home === 'interest' ? 'INTERESTED' : 'GLOBAL', expected_version: profile.version } })
    updateProfile({ ...p, version: data.interests.version, home: data.interests.landing_page === 'INTERESTED' ? 'interest' : 'all' }); return true
  })
  const setSettings = async next => run(async () => {
    await api('/users/me/notification-settings', { method: 'PATCH', body: { notifications_enabled: next.all, spike_popup: next.surge, major_issue_popup: next.major, report_popup: next.report } })
    updateSettings(next); return true
  })
  const setSubscription = async next => run(async () => {
    const s = typeof next === 'function' ? next(subscription) : next
    if (s.cycles.length) await api('/users/me/subscriptions', { method: 'PUT', body: { period_codes: s.cycles.map(c => c.toUpperCase()), formats: s.formats, use_saved_interests: true, expected_version: subscription.version } })
    else for (const item of subscription.items.filter(s => s.enabled)) await api(`/users/me/subscriptions/${item.subscription_id}`, { method: 'DELETE' })
    const fresh = await api('/users/me/subscriptions'); updateSubscription({ ...fresh, cycles: fresh.items.filter(s => s.enabled).map(s => s.period_code.toLowerCase()), formats: fresh.items.find(s => s.enabled)?.formats || s.formats }); return true
  })
  const deleteSubscription = async cycle => run(async () => {
    const target = subscription.items.find(s => s.enabled && s.period_code === cycle.toUpperCase())
    if (!target) throw new Error('삭제할 구독을 찾을 수 없습니다. 새로고침해 주세요.')
    await api(`/users/me/subscriptions/${target.subscription_id}`, { method: 'DELETE' })
    const fresh = await api('/users/me/subscriptions')
    updateSubscription({ ...fresh, cycles: fresh.items.filter(s => s.enabled).map(s => s.period_code.toLowerCase()), formats: fresh.items.find(s => s.enabled)?.formats || subscription.formats })
    return true
  })
  const setNotifications = useCallback(async next => run(async () => {
    const values = typeof next === 'function' ? next(notifications) : next
    const changed = values.filter(n => n.read && !notifications.find(old => old.id === n.id)?.read)
    if (!changed.length) return true
    for (const item of changed) await api(`/notifications/${item.id}/read`, { method: 'PATCH', body: {} })
    await refreshNotifications(true); return true
  }), [notifications, refreshNotifications, run])
  const logout = () => run(async () => { await api('/auth/logout', { method: 'POST', body: {} }); clear(); navigate('/login'); return true })
  const changePassword = (current, next) => run(async () => {
    await api('/users/me/password', { method: 'POST', body: { email: session.email, current_password: current, new_password: next, new_password_confirm: next } }); clear(); navigate('/login'); return true
  })
  const downloadReport = (id, format) => run(() => download(`/reports/${id}/files/${format.toLowerCase()}`, `리포트-${id}.${format.toLowerCase()}`))
  const value = {
    session, account: session, accounts, setAccounts, loading, loadError, refresh, options, countries,
    login: (email, password) => authenticate('/auth/login', { email, password }),
    signup: form => authenticate('/auth/signup', { name: form.name, email: form.email, password: form.password, password_confirm: form.confirm || form.password }),
    socialSignIn: () => ({ ok: false, message: '소셜 로그인은 서버 연동 준비 중입니다.' }), logout, changePassword,
    withdraw: () => notify('회원 탈퇴는 운영 정책 확정 후 제공됩니다.'),
    consents: {}, setConsents: () => { notify('일반 약관 동의 관리는 서버 연동 준비 중입니다.'); return false },
    profile, setProfile, settings, setSettings, subscription, setSubscription, deleteSubscription,
    notifications, setNotifications, unread, refreshNotifications, chats, setChats, refreshChats, reports, refreshReports, downloadReport,
    mainView, setMainView, notify, toast, setToast, run, fail,
  }
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}
