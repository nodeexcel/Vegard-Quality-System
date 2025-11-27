'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '../contexts/AuthContext'
import GoogleLogin from '../components/GoogleLogin'

export default function LoginPage() {
  const { user, loading } = useAuth()
  const router = useRouter()

  useEffect(() => {
    // Redirect to home if already logged in
    if (!loading && user) {
      router.push('/')
    }
  }, [user, loading, router])

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading...</p>
        </div>
      </div>
    )
  }

  if (user) {
    return null // Will redirect
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 flex items-center justify-center p-4">
      <div className="max-w-md w-full">
        {/* Logo and Title */}
        <div className="text-center mb-8">
          <div className="flex items-center justify-center space-x-3 mb-4">
            <div className="w-16 h-16 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-2xl flex items-center justify-center shadow-xl">
              <svg className="w-10 h-10 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
          </div>
          <h1 className="text-4xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent mb-2">
            KvalitetTakst
          </h1>
          <p className="text-xl text-gray-600">AI-powered quality assessment</p>
        </div>

        {/* Login Card */}
        <div className="bg-white rounded-2xl shadow-xl border border-gray-100 p-8">
          <div className="text-center mb-6">
            <h2 className="text-2xl font-bold text-gray-900 mb-2">Welcome</h2>
            <p className="text-gray-600">
              Sign in with your Google account to access the report analysis platform
            </p>
          </div>

          <div className="flex justify-center">
            <GoogleLogin />
          </div>

          <div className="mt-6 pt-6 border-t border-gray-200">
            <p className="text-xs text-gray-500 text-center">
              By signing in, you agree to use this service for analyzing building condition reports.
              <br />
              Your data is secure and private.
            </p>
          </div>
        </div>

        {/* Info Section */}
        <div className="mt-6 bg-white rounded-xl shadow-lg border border-gray-100 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-3">What you can do:</h3>
          <ul className="space-y-2 text-sm text-gray-600">
            <li className="flex items-start">
              <span className="text-blue-600 mr-2">✓</span>
              <span>Upload and analyze building condition reports</span>
            </li>
            <li className="flex items-start">
              <span className="text-blue-600 mr-2">✓</span>
              <span>Get detailed quality scores and findings</span>
            </li>
            <li className="flex items-start">
              <span className="text-blue-600 mr-2">✓</span>
              <span>Receive improvement recommendations</span>
            </li>
            <li className="flex items-start">
              <span className="text-blue-600 mr-2">✓</span>
              <span>Access your report history</span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  )
}

