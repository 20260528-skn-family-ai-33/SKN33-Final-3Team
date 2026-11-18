import { createRoot } from 'react-dom/client'
import { useEffect } from 'react'
import { AppProvider, useApp, useNotifications } from '../src/store'
import App from '../src/App'
import '../src/styles/mockup.css'
import '../src/styles/app.css'

export function Screen() {
  const app = useApp(), notifications = useNotifications()
  useEffect(() => {
    window.miniAlertTest = { login: app.login, refresh: notifications.refreshNotifications }
  }, [app.login, notifications.refreshNotifications])
  return <>
    <App />
    <output hidden data-testid="original">{JSON.stringify(notifications.notifications.map(n => ({ id: n.id, read: n.read })))}</output>
    <output hidden data-testid="unread">{notifications.unread}</output>
  </>
}
createRoot(document.getElementById('root')).render(<AppProvider><Screen /></AppProvider>)
