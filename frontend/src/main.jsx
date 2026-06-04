import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import { UiProvider } from './context/UiContext.jsx'
import { AppErrorBoundary } from './components/AppChrome.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthProvider>
      <UiProvider>
        <AppErrorBoundary>
          <App />
        </AppErrorBoundary>
      </UiProvider>
    </AuthProvider>
  </StrictMode>,
)
