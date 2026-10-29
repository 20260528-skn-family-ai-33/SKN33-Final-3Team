import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { mapLimited } from '../../api/integration'
import { countryCode, countryName, productName } from '../../data/catalog'
export default function useUnData(profile) {
  const key = JSON.stringify([profile.products, profile.countries])
  const [state, setState] = useState({ key: null, items: [], error: null })
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
              cards.get(name).items.push({ hs, name: productName(hs), rank: r.korea_rank ? `${r.korea_rank}위` : '순위 미제공', share: r.korea_share_pct == null ? '—' : `${Number(r.korea_share_pct).toFixed(1)}%`, change: r.share_change_pp == null ? '비교 자료 없음' : `${Number(r.share_change_pp).toFixed(1)}%p`, dir: r.share_change_pp > 0 ? 'up' : r.share_change_pp < 0 ? 'down' : 'flat', importShare: '미제공', period })
            }
        }
        if (!controller.signal.aborted) setState({ key, items: [...cards.values()], error: null })
      } catch (error) { if (!controller.signal.aborted) setState({ key, items: [], error }) }
    }
    load(); return () => controller.abort()
  }, [key])
  return state.key === key ? state : { items: [], error: null }
}
