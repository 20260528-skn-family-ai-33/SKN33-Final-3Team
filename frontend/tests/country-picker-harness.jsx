import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AppProvider } from '../src/store'
import { CountryPicker } from '../src/components/pickers'
import Rank from '../src/pages/market/Rank'
import { registerCountries } from '../src/data/catalog'
import '../src/styles/mockup.css'
import '../src/styles/app.css'

// 온보딩·관심정보·에이전트가 같이 쓰는 국가 선택(선택 칩 표시)을 가짜 국가 목록으로 띄운다.
registerCountries([['US', '미국'], ['AU', '호주'], ['NZ', '뉴질랜드'], ['JP', '일본']]
  .map(([country_code, name_ko]) => ({ country_code, name_ko, currency_code: null })))

function Demo() {
  const [value, setValue] = useState(['미국'])
  if (location.hash === '#rank') return <Rank route={{ query: new URLSearchParams('product=330499') }} />
  return <div style={{ width: 760 }}>
    <CountryPicker showSelected value={value} onChange={setValue} />
    <output data-testid="value">{value.join(',')}</output>
  </div>
}

createRoot(document.getElementById('root')).render(<AppProvider><Demo /></AppProvider>)
