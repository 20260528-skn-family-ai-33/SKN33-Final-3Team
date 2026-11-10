import React from 'react'
import { createRoot } from 'react-dom/client'
import { AppProvider, useApp, useToast } from '../src/store'
import { useRoute } from '../src/router'
import Onboarding from '../src/pages/auth/Onboarding'
import AccountPage from '../src/pages/account/AccountPage'
import Login from '../src/pages/auth/Login'
import { Signup } from '../src/pages/auth/Signup'
import '../src/styles/mockup.css'
import '../src/styles/app.css'

export function Screen() {
  const app = useApp()
  const { toast } = useToast()
  const route = useRoute()
  if (app.loading) return <p>Loading</p>
  const Page = route.path === '/onboarding' ? Onboarding
    : route.path.startsWith('/account') ? AccountPage
    : route.path === '/login' ? Login : route.path === '/signup' ? Signup : null
  return <>{Page ? <Page route={route} /> : <p>메인 화면</p>}<p role="status">{toast}</p></>
}

createRoot(document.getElementById('root')).render(<AppProvider><Screen /></AppProvider>)
