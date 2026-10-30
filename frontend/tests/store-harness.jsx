import { createRoot } from 'react-dom/client'
import { useLayoutEffect } from 'react'
import { AppProvider, useApp, useNotifications, useToast } from '../src/store'
import Analysis from '../src/pages/analysis/Analysis'

const counts = { app: 0, notifications: 0, toast: 0 }
export function AppProbe() {
  const value = useApp()
  useLayoutEffect(() => { counts.app++; window.reviewApp = value })
  return null
}
export function NotificationProbe() {
  const value = useNotifications()
  useLayoutEffect(() => { counts.notifications++; window.reviewNotifications = value })
  return null
}
export function ToastProbe() {
  const value = useToast()
  useLayoutEffect(() => { counts.toast++; window.reviewToast = value })
  return null
}
window.reviewCounts = counts
const root = createRoot(document.getElementById('root'))
window.reviewShowAnalysis = () => root.render(
  <AppProvider><Analysis route={{ query: new URLSearchParams('chat=1') }} /></AppProvider>,
)
root.render(
  <AppProvider><AppProbe /><NotificationProbe /><ToastProbe /></AppProvider>,
)
