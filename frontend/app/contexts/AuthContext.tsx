'use client'

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'
import axios from 'axios'

interface User {
  id: number
  email: string
  name: string | null
  phone?: string | null
  company?: string | null
  picture: string | null
  credits: number
  is_admin?: number
  created_at: string
}

interface AuthContextType {
  user: User | null
  token: string | null
  login: (token: string, user: User) => void
  logout: () => void
  refreshUser: () => Promise<void>
  loading: boolean
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const apiUrl = process.env.NEXT_PUBLIC_API_URL

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
    localStorage.removeItem('auth_token')
    delete axios.defaults.headers.common['Authorization']
  }, [])

  const refreshUser = useCallback(async () => {
    try {
      const storedToken = localStorage.getItem('auth_token')
      if (!storedToken) {
        setLoading(false)
        return
      }

      const response = await axios.get(`${apiUrl}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${storedToken}` }
      })
      setUser(response.data)
      setToken(storedToken)
      axios.defaults.headers.common['Authorization'] = `Bearer ${storedToken}`
    } catch (error) {
      logout()
    } finally {
      setLoading(false)
    }
  }, [apiUrl, logout])

  const login = useCallback((newToken: string, newUser: User) => {
    setToken(newToken)
    setUser(newUser)
    localStorage.setItem('auth_token', newToken)
    axios.defaults.headers.common['Authorization'] = `Bearer ${newToken}`
  }, [])

  useEffect(() => {
    // Set up axios interceptor to always include auth token
    const requestInterceptor = axios.interceptors.request.use(
      (config) => {
        const storedToken = localStorage.getItem('auth_token')
        if (storedToken && !config.headers.Authorization) {
          config.headers.Authorization = `Bearer ${storedToken}`
        }
        return config
      },
      (error) => {
        return Promise.reject(error)
      }
    )

    // Check for stored token on mount
    const storedToken = localStorage.getItem('auth_token')
    if (storedToken) {
      setToken(storedToken)
      axios.defaults.headers.common['Authorization'] = `Bearer ${storedToken}`
      refreshUser()
    } else {
      setLoading(false)
    }

    // Cleanup interceptor on unmount
    return () => {
      axios.interceptors.request.eject(requestInterceptor)
    }
  }, [refreshUser])

  return (
    <AuthContext.Provider value={{ user, token, login, logout, refreshUser, loading }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}

