'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import { useAuth } from './contexts/AuthContext'
import UserMenu from './components/UserMenu'

export default function Home() {
  const [file, setFile] = useState<File | null>(null)
  const [reportSystem, setReportSystem] = useState('')
  const [buildingYear, setBuildingYear] = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const router = useRouter()
  const { user, token, loading, refreshUser } = useAuth()

  // Check if we're on admin subdomain and redirect to admin panel (FIRST CHECK)
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const hostname = window.location.hostname
      if (hostname === 'admin.verifisert.no' || hostname === 'admin.validert.no') {
        // We're on admin subdomain, redirect to admin panel immediately
        window.location.href = '/admin'
        return
      }
    }
  }, [])

  // Early return if on admin subdomain (prevent rendering user dashboard)
  if (typeof window !== 'undefined') {
    const hostname = window.location.hostname
    if (hostname === 'admin.verifisert.no' || hostname === 'admin.validert.no') {
      return (
        <div className="min-h-screen bg-gray-50 flex items-center justify-center">
          <div className="text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
            <p className="mt-4 text-gray-600">Redirecting to admin panel...</p>
          </div>
        </div>
      )
    }
  }

  // Redirect to login if not authenticated (after loading completes)
  useEffect(() => {
    if (!loading && !user) {
      router.push('/login')
      return
    }
    // Check if profile is complete (name, phone, company)
    if (!loading && user && (!user.name || !user.phone || !user.company)) {
      router.push('/onboarding')
    }
  }, [user, loading, router])

  // Show loading while checking auth
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

  // Show loading while redirecting (don't return null to avoid hooks error)
  if (!user) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Redirecting...</p>
        </div>
      </div>
    )
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0])
      setError('')
    }
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const droppedFile = e.dataTransfer.files[0]
      if (droppedFile.type === 'application/pdf') {
        setFile(droppedFile)
        setError('')
      } else {
        setError('Please upload a PDF file')
      }
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (!user) {
      setError('Please sign in to upload reports')
      return
    }

    if (!file) {
      setError('Please select a PDF file')
      return
    }

    setUploading(true)
    setError('')

    try {
      const formData = new FormData()
      formData.append('file', file)
      if (reportSystem) {
        formData.append('report_system', reportSystem)
      }
      if (buildingYear) {
        formData.append('building_year', buildingYear)
      }

      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      // Get token from context or localStorage as fallback
      const authToken = token || localStorage.getItem('auth_token')
      if (!authToken) {
        setError('Please sign in to upload reports')
        setUploading(false)
        return
      }
      
      const response = await axios.post(
        `${apiUrl}/api/v1/reports/upload`,
        formData,
        {
          headers: {
            Authorization: `Bearer ${authToken}`,
            // Don't set Content-Type - axios will set it automatically with boundary for FormData
          },
        }
      )

      // Redirect to results page
      router.push(`/results/${response.data.id}`)
    } catch (err: any) {
      if (err.response?.status === 401) {
        setError('Please sign in to upload reports. If you are signed in, please try signing out and signing in again.')
      } else {
        setError(err.response?.data?.detail || 'Failed to upload report. Please try again.')
      }
      setUploading(false)
    }
  }

  // Removed unnecessary refreshUser call - AuthContext already handles this on mount

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 Bytes'
    const k = 1024
    const sizes = ['Bytes', 'KB', 'MB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50">
      {/* Header */}
      <header className="bg-white/80 backdrop-blur-sm border-b border-gray-200 sticky top-0 z-50">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-6">
            <div className="flex items-center space-x-3">
              <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-lg flex items-center justify-center">
                <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <div>
                <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
                  Verifisert
                </h1>
                <p className="text-xs text-gray-500">AI-powered quality assessment</p>
              </div>
              </div>
              <nav className="hidden md:flex space-x-4">
                <span className="text-blue-600 font-medium border-b-2 border-blue-600 pb-1">
                  Upload
                </span>
                <button
                  onClick={() => router.push('/history')}
                  className="text-gray-600 hover:text-blue-600 transition-colors pb-1"
                >
                  History
                </button>
              </nav>
            </div>
            <UserMenu />
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-12">
        <div className="max-w-3xl mx-auto">
          {/* Hero Section */}
          <div className="text-center mb-12">
            <h2 className="text-4xl md:text-5xl font-bold text-gray-900 mb-4">
              Evaluate Your Building Condition Reports
            </h2>
            <p className="text-xl text-gray-600 max-w-2xl mx-auto">
              Upload your tilstandsrapport and receive automated quality evaluation based on Norwegian building standards
            </p>
          </div>

          {/* Upload Card */}
          <div className="bg-white rounded-2xl shadow-xl border border-gray-100 overflow-hidden">
            <div className="bg-gradient-to-r from-blue-600 to-indigo-600 px-8 py-6">
              <h3 className="text-2xl font-semibold text-white">Upload Report</h3>
              <p className="text-blue-100 mt-1">Get instant quality analysis</p>
            </div>

            <form onSubmit={handleSubmit} className="p-8 space-y-6">
              {/* File Upload */}
              <div>
                <label htmlFor="file" className="block text-sm font-semibold text-gray-700 mb-3">
                  PDF Report <span className="text-red-500">*</span>
                </label>
                <div
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                  className={`relative border-2 border-dashed rounded-xl p-8 transition-all duration-200 ${
                    isDragging
                      ? 'border-blue-500 bg-blue-50 scale-[1.02]'
                      : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'
                  }`}
                >
                  <input
                    id="file"
                    name="file"
                    type="file"
                    accept=".pdf"
                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                    onChange={handleFileChange}
                    disabled={uploading}
                  />
                  <div className="text-center">
                    {file ? (
                      <div className="space-y-3">
                        <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto">
                          <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                          </svg>
                        </div>
                        <div>
                          <p className="text-sm font-medium text-gray-900">{file.name}</p>
                          <p className="text-xs text-gray-500 mt-1">{formatFileSize(file.size)}</p>
                        </div>
                        <button
                          type="button"
                          onClick={() => setFile(null)}
                          className="text-xs text-red-600 hover:text-red-700 font-medium"
                        >
                          Remove file
                        </button>
                      </div>
                    ) : (
                      <div className="space-y-4">
                        <div className="w-16 h-16 bg-blue-100 rounded-full flex items-center justify-center mx-auto">
                          <svg className="w-8 h-8 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                          </svg>
                        </div>
                        <div>
                          <p className="text-sm font-medium text-gray-900">
                            <span className="text-blue-600 hover:text-blue-700 cursor-pointer">Click to upload</span> or drag and drop
                          </p>
                          <p className="text-xs text-gray-500 mt-1">PDF files only (max 10MB)</p>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Optional Fields */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <label htmlFor="report_system" className="block text-sm font-semibold text-gray-700 mb-2">
                    Report System <span className="text-gray-400 font-normal">(Optional)</span>
                  </label>
                  <input
                    type="text"
                    id="report_system"
                    value={reportSystem}
                    onChange={(e) => setReportSystem(e.target.value)}
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all"
                    placeholder="e.g., Byggtjeneste, NITO"
                    disabled={uploading}
                  />
                </div>

                <div>
                  <label htmlFor="building_year" className="block text-sm font-semibold text-gray-700 mb-2">
                    Building Year <span className="text-gray-400 font-normal">(Optional)</span>
                  </label>
                  <input
                    type="number"
                    id="building_year"
                    value={buildingYear}
                    onChange={(e) => setBuildingYear(e.target.value)}
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all"
                    placeholder="e.g., 1985"
                    min="1800"
                    max="2030"
                    disabled={uploading}
                  />
                </div>
              </div>

              {/* Error Message */}
              {error && (
                <div className="bg-red-50 border-l-4 border-red-500 text-red-700 px-4 py-3 rounded-lg flex items-start space-x-3">
                  <svg className="w-5 h-5 text-red-500 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                  </svg>
                  <p className="text-sm">{error}</p>
                </div>
              )}

              {/* Submit Button */}
              <button
                type="submit"
                disabled={uploading || !file || !user}
                className="w-full bg-gradient-to-r from-blue-600 to-indigo-600 text-white py-4 px-6 rounded-lg font-semibold text-lg hover:from-blue-700 hover:to-indigo-700 focus:outline-none focus:ring-4 focus:ring-blue-300 disabled:from-gray-400 disabled:to-gray-500 disabled:cursor-not-allowed transition-all duration-200 transform hover:scale-[1.02] disabled:scale-100 shadow-lg disabled:shadow-none"
              >
                {uploading ? (
                  <span className="flex items-center justify-center space-x-2">
                    <svg className="animate-spin h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    <span>Analyzing Report...</span>
                  </span>
                ) : (
                  <span className="flex items-center justify-center space-x-2">
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span>Upload and Analyze</span>
                  </span>
                )}
              </button>
            </form>
          </div>

          {/* Info Section */}
          <div className="mt-8 bg-white rounded-2xl shadow-lg border border-gray-100 p-8">
            <h2 className="text-2xl font-bold text-gray-900 mb-6 flex items-center">
              <span className="w-1 h-8 bg-gradient-to-b from-blue-600 to-indigo-600 rounded-full mr-4"></span>
              How It Works
            </h2>
            <div className="grid md:grid-cols-3 gap-6">
              {[
                { num: '1', title: 'Upload PDF', desc: 'Upload your building condition report (tilstandsrapport)' },
                { num: '2', title: 'AI Analysis', desc: 'Our AI analyzes against Norwegian building standards (NS 3600, NS 3940, TEK)' },
                { num: '3', title: 'Get Results', desc: 'Receive detailed quality scores, findings, and recommendations' },
              ].map((step, idx) => (
                <div key={idx}>
                  <div className="flex items-start space-x-4">
                    <div className="flex-shrink-0 w-12 h-12 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-xl flex items-center justify-center text-white font-bold text-lg shadow-lg">
                      {step.num}
                    </div>
                    <div className="flex-1 pt-1">
                      <h3 className="font-semibold text-gray-900 mb-1">{step.title}</h3>
                      <p className="text-sm text-gray-600">{step.desc}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Standards Info */}
          <div className="mt-8 bg-gradient-to-r from-blue-50 to-indigo-50 rounded-2xl border border-blue-100 p-6">
            <p className="text-sm text-gray-700 text-center">
              <span className="font-semibold">Standards evaluated:</span> Forskrift til avhendingslova, NS 3600:2018, NS 3940:2023, TEK, Prop. 44 L
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

