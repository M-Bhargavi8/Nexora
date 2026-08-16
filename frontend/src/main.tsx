import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('Root element #root not found in index.html')
}

// StrictMode intentionally double-invokes effects in development to surface
// bugs caused by missing cleanup. It has no effect on the production build.
createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
