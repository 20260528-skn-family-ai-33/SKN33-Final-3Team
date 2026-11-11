import { createRoot } from 'react-dom/client'
import { AppProvider, useApp } from '../src/store'
import { useRoute } from '../src/router'
import Analysis from '../src/pages/analysis/Analysis'
import Articles from '../src/pages/market/Articles'
import AccountPage from '../src/pages/account/AccountPage'
import '../src/styles/mockup.css'
import '../src/styles/app.css'

export function Screen() {
  const app = useApp(), route = useRoute()
  if (app.loading) return null
  const Page = route.path === '/articles' ? Articles : route.path.startsWith('/account') ? AccountPage : Analysis
  return <Page route={route} />
}
createRoot(document.getElementById('root')).render(<AppProvider><Screen /></AppProvider>)
