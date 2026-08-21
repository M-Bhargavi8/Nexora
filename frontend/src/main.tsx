import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import './index.css'
import App from './App'
import { AuthProvider } from './contexts/AuthProvider'

const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('Root element #root not found in index.html')
}

// Nesting order matters, and getting it wrong fails at runtime rather than at
// compile time:
//
//   BrowserRouter  outermost. Everything below may call useNavigate or
//                  useLocation, and those throw outside a router.
//   AuthProvider   wraps App so every route reads the same session, and so
//                  the "am I signed in?" request fires once for the whole
//                  application rather than once per page.
//
// StrictMode intentionally double-invokes effects in development to surface
// bugs caused by missing cleanup. It has no effect on the production build.
createRoot(rootElement).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
