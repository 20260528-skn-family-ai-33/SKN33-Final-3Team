import React from 'react'
import { createRoot } from 'react-dom/client'
import { AppProvider } from '../src/store'
import ReportView from '../src/pages/analysis/ReportView'

createRoot(document.getElementById('root')).render(
  <AppProvider><ReportView route={{ query: new URLSearchParams('id=1') }} /></AppProvider>,
)
