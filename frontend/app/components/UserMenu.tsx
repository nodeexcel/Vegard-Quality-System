'use client'

import { useAuth } from '../contexts/AuthContext'
import GoogleLogin from './GoogleLogin'
import { useState, useEffect, useRef } from 'react'

export default function UserMenu() {
  const { user, logout, loading } = useAuth()
  const [showMenu, setShowMenu] = useState(false)
  const [imageError, setImageError] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setShowMenu(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Reset image error when user changes
  useEffect(() => {
    setImageError(false)
  }, [user?.picture])

  if (loading) {
    return <div className="w-8 h-8 bg-gray-200 rounded-full animate-pulse"></div>
  }

  if (!user) {
    return <GoogleLogin />
  } 

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setShowMenu(!showMenu)}
        className="flex items-center space-x-3 hover:bg-gray-100 rounded-lg px-3 py-2 transition-colors"
      >
        {user.picture && !imageError ? (
          <img
            src={user.picture}
            alt={user.name || user.email}
            className="w-8 h-8 rounded-full object-cover"
            onError={() => setImageError(true)}
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="w-8 h-8 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-full flex items-center justify-center text-white font-bold text-sm">
            {user.name?.[0] || user.email[0].toUpperCase()}
          </div>
        )}
        <div className="hidden md:block text-left">
          <div className="text-sm font-medium text-gray-900">{user.name || user.email}</div>
          <div className="text-xs text-gray-500">
            {user.credits || 0} kreditter
          </div>
        </div>
        <svg className="w-4 h-4 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {showMenu && (
        <div className="absolute right-0 mt-2 w-64 bg-white rounded-lg shadow-xl border border-gray-200 py-2 z-50">
          <div className="px-4 py-3 border-b border-gray-200">
            <p className="text-sm font-medium text-gray-900">{user.name || 'Bruker'}</p>
            <p className="text-xs text-gray-500">{user.email}</p>
            <div className="mt-2 flex items-center justify-between">
              <span className="text-xs text-gray-500">Kreditter:</span>
              <span className={`text-sm font-bold ${(user.credits || 0) < 10 ? 'text-red-600' : 'text-green-600'}`}>
                {user.credits || 0}
              </span>
            </div>
          </div>
          <a
            href="/buy-credits"
            className="block w-full text-left px-4 py-2 text-sm text-blue-600 hover:bg-blue-50 transition-colors font-medium"
            onClick={() => setShowMenu(false)}
          >
            💳 Kjøp kreditter
          </a>
          <a
            href="/payment-history"
            className="block w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
            onClick={() => setShowMenu(false)}
          >
            Betalingshistorikk
          </a>
          <a
            href="/profile"
            className="block w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
            onClick={() => setShowMenu(false)}
          >
            Rediger profil
          </a>
          <button
            onClick={() => {
              logout()
              setShowMenu(false)
            }}
            className="w-full text-left px-4 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors"
          >
            Logg ut
          </button>
        </div>
      )}
    </div>
  )
}
