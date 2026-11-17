import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { AppProvider, useApp } from '../src/store'
import { useRoute } from '../src/router'
import Dashboard from '../src/pages/home/Dashboard'
import '../src/styles/mockup.css'
import '../src/styles/app.css'

export function Screen() {
  const app = useApp(), route = useRoute()
  if (app.loading || !app.account) return null
  return <>
    <button onClick={() => app.setProfile({ ...app.profile, countries: ['중국'] })}>관심 변경 테스트</button>
    <button onClick={app.refresh}>계정 갱신 테스트</button>
    <Dashboard route={route} />
  </>
}
createRoot(document.getElementById('root')).render(<StrictMode><AppProvider><Screen /></AppProvider></StrictMode>)
