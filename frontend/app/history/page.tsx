'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import { useAuth } from '../contexts/AuthContext'
import UserMenu from '../components/UserMenu'

interface Report {
  id: number
  filename: string
  report_system: string | null
  building_year: number | null
  uploaded_at: string
  overall_score: number | null
  quality_score: number | null
  completeness_score: number | null
  compliance_score: number | null
}

export default function HistoryPage() {
  const router = useRouter()
  const { user, loading: authLoading } = useAuth()
  const [reports, setReports] = useState<Report[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Redirect to login if not authenticated
  useEffect(() => {
    if (!authLoading && !user) {
      router.push('/login')
    }
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user) return

    const fetchReports = async () => {
      try {
        const apiUrl = process.env.NEXT_PUBLIC_API_URL
        const response = await axios.get(`${apiUrl}/api/v1/reports/`)
        setReports(response.data)
      } catch (err: any) {
        if (err.response?.status === 401) {
          router.push('/login')
        } else {
          setError(err.response?.data?.detail || 'Failed to load reports')
        }
      } finally {
        setLoading(false)
      }
    }

    fetchReports()
  }, [user, router])

  // Show loading while checking auth
  if (authLoading || loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading...</p>
        </div>
      </div>
    )
  }

  if (!user) {
    return null
  }

  const getScoreColor = (score: number | null) => {
    if (score === null) return 'text-gray-400'
    if (score >= 80) return 'text-green-600'
    if (score >= 60) return 'text-yellow-600'
    return 'text-red-600'
  }

  const getScoreBadge = (score: number | null) => {
    if (score === null) return 'bg-gray-100 text-gray-600'
    if (score >= 80) return 'bg-green-100 text-green-700'
    if (score >= 60) return 'bg-yellow-100 text-yellow-700'
    return 'bg-red-100 text-red-700'
  }

  const formatDate = (dateString: string) => {
    const date = new Date(dateString)
    return date.toLocaleDateString('nb-NO', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50">
      <header className="border-b border-gray-200 bg-white/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="container mx-auto px-4 py-4">
          <div className="flex justify-between items-center">
            <div className="flex items-center space-x-4">
              <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 text-transparent bg-clip-text">
                Verifisert
              </h1>
              <nav className="hidden md:flex space-x-4">
                <button
                  onClick={() => router.push('/')}
                  className="text-gray-600 hover:text-blue-600 transition-colors"
                >
                  Upload
                </button>
                <span className="text-blue-600 font-medium border-b-2 border-blue-600">
                  History
                </span>
              </nav>
            </div>
            <UserMenu />
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-8">
        <div className="max-w-6xl mx-auto">
          <div className="mb-8">
            <h2 className="text-3xl font-bold text-gray-900 mb-2">Report History</h2>
            <p className="text-gray-600">View all your previous report analyses</p>
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-6">
              {error}
            </div>
          )}

          {reports.length === 0 ? (
            <div className="bg-white rounded-2xl shadow-xl border border-gray-100 p-12 text-center">
              <svg
                className="w-16 h-16 text-gray-300 mx-auto mb-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
              <h3 className="text-xl font-semibold text-gray-700 mb-2">No reports yet</h3>
              <p className="text-gray-500 mb-6">Upload your first building condition report to get started</p>
              <button
                onClick={() => router.push('/')}
                className="px-6 py-3 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-lg font-medium hover:from-blue-700 hover:to-indigo-700 transition-all shadow-md hover:shadow-lg"
              >
                Upload Report
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              {reports.map((report) => (
                <div
                  key={report.id}
                  className="bg-white rounded-xl shadow-lg border border-gray-100 p-6 hover:shadow-xl transition-shadow cursor-pointer"
                  onClick={() => router.push(`/results/${report.id}`)}
                >
                  <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                    <div className="flex-1">
                      <h3 className="text-lg font-bold text-gray-900 mb-2">{report.filename}</h3>
                      <div className="flex flex-wrap gap-4 text-sm text-gray-600">
                        <div className="flex items-center space-x-2">
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                          </svg>
                          <span>{formatDate(report.uploaded_at)}</span>
                        </div>
                        {report.building_year && (
                          <div className="flex items-center space-x-2">
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                            </svg>
                            <span>Byggeår: {report.building_year}</span>
                          </div>
                        )}
                        {report.report_system && (
                          <div className="flex items-center space-x-2">
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                            </svg>
                            <span>{report.report_system}</span>
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-4">
                      {/* Scores */}
                      <div className="flex gap-3">
                        <div className="text-center">
                          <div className={`text-2xl font-bold ${getScoreColor(report.overall_score)}`}>
                            {report.overall_score !== null ? Math.round(report.overall_score) : '-'}
                          </div>
                          <div className="text-xs text-gray-500">Overall</div>
                        </div>
                        <div className="text-center">
                          <div className={`text-sm font-semibold ${getScoreColor(report.quality_score)}`}>
                            {report.quality_score !== null ? Math.round(report.quality_score) : '-'}
                          </div>
                          <div className="text-xs text-gray-500">Quality</div>
                        </div>
                        <div className="text-center">
                          <div className={`text-sm font-semibold ${getScoreColor(report.completeness_score)}`}>
                            {report.completeness_score !== null ? Math.round(report.completeness_score) : '-'}
                          </div>
                          <div className="text-xs text-gray-500">Complete</div>
                        </div>
                        <div className="text-center">
                          <div className={`text-sm font-semibold ${getScoreColor(report.compliance_score)}`}>
                            {report.compliance_score !== null ? Math.round(report.compliance_score) : '-'}
                          </div>
                          <div className="text-xs text-gray-500">Compliant</div>
                        </div>
                      </div>

                      {/* View button */}
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          router.push(`/results/${report.id}`)
                        }}
                        className="px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-lg font-medium hover:from-blue-700 hover:to-indigo-700 transition-all shadow-md hover:shadow-lg"
                      >
                        View Report
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Upload new report button */}
          {reports.length > 0 && (
            <div className="mt-8 text-center">
              <button
                onClick={() => router.push('/')}
                className="px-6 py-3 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-lg font-medium hover:from-blue-700 hover:to-indigo-700 transition-all shadow-md hover:shadow-lg"
              >
                Upload New Report
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

