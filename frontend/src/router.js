// 해시 라우터. 라이브러리 없이 `#/경로?쿼리` 형식만 다룬다.
// 서버 연동 시 react-router 등으로 바꾸더라도 Link / navigate / useRoute 인터페이스만 맞추면 된다.
import { createElement, useEffect, useState } from 'react'

function readHash() {
  const raw = decodeURI(window.location.hash.slice(1)) || '/'
  const [path, search = ''] = raw.split('?')
  return { path: path || '/', search, query: new URLSearchParams(search), href: raw }
}

export function useRoute() {
  const [route, setRoute] = useState(readHash)
  useEffect(() => {
    const onChange = () => {
      const next = readHash()
      setRoute(prev => {
        // 같은 화면 안에서 쿼리만 바뀌는 경우(탭·모달)는 스크롤을 유지한다.
        if (prev.path !== next.path) window.scrollTo(0, 0)
        return next
      })
    }
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function navigate(to, { replace = false } = {}) {
  const hash = '#' + to
  if (replace) {
    window.history.replaceState(null, '', hash)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  } else {
    window.location.hash = to
  }
}

/** 현재 쿼리에 값을 덮어쓴 경로를 만든다. value가 null이면 키를 지운다. */
export function withQuery(route, patch) {
  const q = new URLSearchParams(route.search)
  Object.entries(patch).forEach(([k, v]) => (v === null || v === undefined || v === '' ? q.delete(k) : q.set(k, v)))
  const s = q.toString()
  return route.path + (s ? '?' + s : '')
}

export function Link({ to, children, ...rest }) {
  return createElement('a', { href: '#' + to, ...rest }, children)
}
