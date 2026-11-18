import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AppProvider } from '../src/store'
import { CountryPicker } from '../src/components/pickers'
import Rank from '../src/pages/market/Rank'
import { registerCountries } from '../src/data/catalog'
import { PromptActions } from '../src/pages/analysis/ConversationSteps'
import ApiResults from '../src/components/ApiResults'
import 'flag-icons/css/flag-icons.min.css'
import '../src/styles/mockup.css'
import '../src/styles/app.css'

// 온보딩·관심정보·에이전트가 같이 쓰는 국가 선택(선택 칩 표시)을 가짜 국가 목록으로 띄운다.
registerCountries([['US', '미국'], ['AU', '호주'], ['NZ', '뉴질랜드'], ['JP', '일본']]
  .map(([country_code, name_ko]) => ({ country_code, name_ko, currency_code: null })))

function Demo() {
  const [value, setValue] = useState(['미국'])
  const [screen, query = 'product=330499'] = location.hash.split('?')
  if (screen === '#rank') return <Rank route={{ query: new URLSearchParams(query) }} />
  // 국기(#132): 채팅 국가 후보·관심 대상 선택 버튼과 결과 표의 국가 칸
  if (screen === '#flags') return <div style={{ width: 760 }}>
    <PromptActions busy={false} reply={() => {}} prompt={{ kind: 'COUNTRY', message: '국가 후보', choices: [{ choice_id: 'US', label: '미국' }, { choice_id: 'JP', label: '일본' }] }} />
    <PromptActions busy={false} reply={() => {}} prompt={{ kind: 'INTEREST_SELECT', message: '관심 대상', choices: [], interests: { country_codes: ['NZ'], hs_items: [] } }} />
    <ApiResults table={{ rows: [{ country: '호주', country_code: 'AU', amount_usd: 10, share_pct: 50 }] }} />
    <ApiResults tableOnly table={{ columns: [{ key: 'country_name' }, { key: 'hs_code' }], rows: [{ country_name: '미국', country_code: 'US', hs_code: '330499' }] }} />
  </div>
  return <div style={{ width: 760 }}>
    <CountryPicker showSelected value={value} onChange={setValue} />
    <output data-testid="value">{value.join(',')}</output>
  </div>
}

createRoot(document.getElementById('root')).render(<AppProvider><Demo /></AppProvider>)
