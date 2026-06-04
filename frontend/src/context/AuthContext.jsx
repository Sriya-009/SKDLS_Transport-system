import { createContext, useContext, useEffect, useState } from 'react'
import { getProfile, loginUser, logoutUser, registerUser } from '../services/authService'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let active = true

    const loadProfile = async () => {
      try {
        const response = await getProfile()
        if (active) {
          setUser(response.user || null)
        }
      } catch {
        if (active) {
          setUser(null)
        }
      } finally {
        if (active) {
          setReady(true)
        }
      }
    }

    loadProfile()

    return () => {
      active = false
    }
  }, [])

  const login = async (payload) => {
    const response = await loginUser(payload)
    setUser(response.user || null)
    setReady(true)
    return response
  }

  const register = async (payload) => {
    const response = await registerUser(payload)
    setUser(response.user || null)
    setReady(true)
    return response
  }

  const logout = async () => {
    await logoutUser()
    setUser(null)
  }

  const value = {
    user,
    ready,
    isAuthenticated: Boolean(user),
    login,
    register,
    logout,
    setUser,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)

  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }

  return context
}