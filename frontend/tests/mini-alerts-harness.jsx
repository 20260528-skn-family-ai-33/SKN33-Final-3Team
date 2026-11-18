import { createRoot } from 'react-dom/client'
import { useState } from 'react'
import { AppProvider, useApp, useNotifications } from '../src/store'
import { useRoute } from '../src/router'
import AlertPopover from '../src/components/AlertPopover'
import Inbox from '../src/pages/account/Inbox'
import { SiteHeader } from '../src/components/layout'
import '../src/styles/mockup.css'
import '../src/styles/app.css'

export function Screen() {
  const app = useApp(), notifications = useNotifications(), route = useRoute()
  const [open, setOpen] = useState(false)
  if (app.loading) return null
  return <>
    <SiteHeader unread={notifications.unread} bellOpen={open} onBell={() => setOpen(!open)} userName="테스트">
      {open && <AlertPopover onClose={() => setOpen(false)} />}
    </SiteHeader>
    <button onClick={() => app.login('other@example.test', 'synthetic')}>계정 변경</button>
    <button onClick={() => notifications.refreshNotifications(true)}>목록 갱신</button>
    <output data-testid="original">{JSON.stringify(notifications.notifications.map(n => ({ id: n.id, read: n.read })))}</output>
    <output data-testid="unread">{notifications.unread}</output>
    {route.path === '/account/notifications' && <Inbox route={route} />}
  </>
}
createRoot(document.getElementById('root')).render(<AppProvider><Screen /></AppProvider>)
