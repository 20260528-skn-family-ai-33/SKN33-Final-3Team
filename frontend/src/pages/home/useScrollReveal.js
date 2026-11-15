// 스크롤해서 화면에 들어온 요소에 등장 효과를 건다. 요소에 data-reveal 속성을 붙이면 화면에 15% 이상 보일 때 한 번만 data-in이 붙고,
// 실제 움직임은 styles/app.css의 [data-reveal] 규칙이 맡는다. 값이 'wait'로 끝나면(자료를 아직 못 받은 상태) 건너뛴다.
import { useEffect } from 'react'

// 상승 화살표는 큰 화면에서 처음부터 보이는 위치에 있다. 열자마자 끝나 버리지 않게, 스크롤을 조금 내린 뒤에 시작한다.
const NEEDS_SCROLL = 'arrow'
const SCROLL_START = 24

export default function useScrollReveal(key) {
  useEffect(() => {
    const targets = [...document.querySelectorAll('[data-reveal]:not([data-reveal$="wait"]):not([data-in])')]
    const show = el => el.setAttribute('data-in', '')
    if (typeof IntersectionObserver === 'undefined') { targets.forEach(show); return undefined }
    let waiting = []
    const onScroll = () => {
      if (window.scrollY < SCROLL_START) return
      waiting.forEach(show); waiting = []
      window.removeEventListener('scroll', onScroll)
    }
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return
      observer.unobserve(entry.target)
      if (entry.target.dataset.reveal === NEEDS_SCROLL && window.scrollY < SCROLL_START) {
        if (!waiting.length) window.addEventListener('scroll', onScroll, { passive: true })
        waiting.push(entry.target)
      } else show(entry.target)
    }), { threshold: 0.15, rootMargin: '0px 0px -6% 0px' })
    targets.forEach(el => observer.observe(el))
    return () => { observer.disconnect(); window.removeEventListener('scroll', onScroll) }
  }, [key])
}
