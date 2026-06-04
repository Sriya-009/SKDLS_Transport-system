import { createContext, useContext, useEffect, useMemo, useState } from 'react'

const UiContext = createContext(null)
const TOAST_TTL = 4200
const ONBOARDING_KEY = 'skdls_onboarding_seen'

function readStoredFlag(key, fallback = false) {
  if (typeof window === 'undefined') {
    return fallback
  }

  return window.localStorage.getItem(key) === '1'
}

export function UiProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const [isOnline, setIsOnline] = useState(typeof navigator === 'undefined' ? true : navigator.onLine)
  const [onboardingVisible, setOnboardingVisible] = useState(() => {
    if (typeof window === 'undefined') {
      return false
    }

    return window.localStorage.getItem(ONBOARDING_KEY) !== '1'
  })

  const pushToast = (type, title, message) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setToasts((previous) => [...previous, { id, type, title, message }])
    window.setTimeout(() => {
      setToasts((previous) => previous.filter((toast) => toast.id !== id))
    }, TOAST_TTL)
  }

  const dismissToast = (toastId) => {
    setToasts((previous) => previous.filter((toast) => toast.id !== toastId))
  }

  const completeOnboarding = () => {
    setOnboardingVisible(false)
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(ONBOARDING_KEY, '1')
    }
  }

  useEffect(() => {
    const handleOnline = () => {
      setIsOnline(true)
      pushToast('success', 'Back online', 'The logistics dashboard has reconnected to the backend.')
    }

    const handleOffline = () => {
      setIsOnline(false)
      pushToast('warning', 'Offline mode', 'Cached data will stay visible until the connection returns.')
    }

    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)

    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [])

  const value = useMemo(
    () => ({
      toasts,
      pushToast,
      dismissToast,
      isOnline,
      onboardingVisible,
      completeOnboarding,
    }),
    [toasts, isOnline, onboardingVisible],
  )

  return <UiContext.Provider value={value}>{children}</UiContext.Provider>
}

export function useUi() {
  const context = useContext(UiContext)

  if (!context) {
    throw new Error('useUi must be used within a UiProvider')
  }

  return context
}