import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { mapLimited } from '../../api/integration'
import { countryCode, countryName, productName } from '../../data/catalog'
// 조회 전·조건이 바뀐 직후에 돌려주는 값. 렌더마다 새 배열을 만들면 이 값에 기대는 측정 효과가 매번 다시 돈다.
const EMPTY = { items: [], error: null }
// 점유율 변화(%p)를 화면에 보이는 소수 첫째 자리로 반올림한다. 화살표 방향도 이 값으로 정해 '▲ 0.0%p'가 나오지 않게 한다(-0은 0).
const roundPp = value => value == null ? null : Number(Number(value).toFixed(1)) || 0
// reload: 오류 안내의 '다시 불러오기'가 같은 조건으로 다시 조회한다.
export default function useUnData(profile) {
  const key = JSON.stringify([profile.products, profile.countries])
  const [state, setState] = useState({ key: null, items: [], error: null })
  const [round, setRound] = useState(0)
  const reload = useCallback(() => setRound(n => n + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    const [products, countries] = JSON.parse(key)
    const load = async () => {
      const cards = new Map()
      try {
        const batches = await mapLimited(products, 3, async hs => {
          const rows = []
          const query = { hs6_code: hs, hs_ver: 'HS2022', freq_code: 'M', country_codes: countries.map(countryCode) }
          const data = await api('/un/availability', { query, signal: controller.signal })
          const periods = new Map()
          for (const observation of data.items) {
            const period = observation.periods.at(-1)
            if (!period) continue
            if (!periods.has(period)) periods.set(period, [])
            periods.get(period).push(observation.country_code)
          }
          for (const [period, country_codes] of periods) {
            const result = await api('/analysis/import-market-shares', { method: 'POST', body: { ...query, country_codes, period_start: period, period_end: period }, signal: controller.signal })
            rows.push(...result.items.map(r => ({ ...r, hs, period })))
          }
          return rows
        }, controller.signal)
        for (const rows of batches) {
            for (const r of rows) {
              const { hs, period } = r
              const name = countryName(r.country_code)
              if (!cards.has(name)) cards.set(name, { country: name, meta: 'UN Comtrade · 월간', items: [] })
              const pp = roundPp(r.share_change_pp)
              cards.get(name).items.push({ hs, name: productName(hs), rank: r.korea_rank ? `${r.korea_rank}위` : null, share: r.korea_share_pct == null ? '—' : `${Number(r.korea_share_pct).toFixed(1)}%`, change: pp == null ? '비교 자료 없음' : `${pp.toFixed(1)}%p`, dir: pp == null ? 'none' : pp > 0 ? 'up' : pp < 0 ? 'down' : 'flat', period })
            }
        }
        if (!controller.signal.aborted) setState({ key, items: [...cards.values()], error: null })
      } catch (error) { if (!controller.signal.aborted) setState({ key, items: [], error }) }
    }
    load(); return () => controller.abort()
  }, [key, round])
  return { ...(state.key === key ? state : EMPTY), reload }
}
