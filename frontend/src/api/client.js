// Session cookies are HttpOnly; only the CSRF value lives in browser memory.
const BASE = import.meta.env?.VITE_API_BASE || '/api/v1'
let csrf = null
export const setCsrf = value => { csrf = value }
export class ApiError extends Error {
  constructor(response, payload) {
    super(payload.message || `요청을 처리하지 못했습니다 (${response.status}).`)
    this.status = response.status; this.code = payload.code
    this.details = payload.details; this.requestId = payload.request_id
    this.retryAfter = Number(response.headers.get('Retry-After') || 0)
  }
}
export function queryString(values = {}) {
  const q = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    for (const item of Array.isArray(value) ? value : [value]) {
      if (item !== undefined && item !== null && item !== '') q.append(key, String(item))
    }
  })
  return q.toString() ? `?${q}` : ''
}
export async function api(path, { method = 'GET', body, query, signal, idempotencyKey, blob = false } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && csrf) headers['X-CSRF-Token'] = csrf
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey
  let response
  try {
    response = await fetch(`${BASE}${path}${queryString(query)}`, {
      method, credentials: 'include', headers, signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new Error('서버에 연결하지 못했습니다. 네트워크와 서버 실행 상태를 확인하세요.')
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    if (response.status === 401 && !path.startsWith('/auth/') && path !== '/users/me') {
      setCsrf(null); window.dispatchEvent(new Event('session-expired'))
    }
    throw new ApiError(response, payload)
  }
  if (response.status === 204) return null
  if (blob) return response.blob()
  return response.json()
}
export async function download(path, filename) {
  const data = await api(path, { blob: true })
  const url = URL.createObjectURL(data)
  const a = document.createElement('a'); a.href = url; a.download = filename
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
