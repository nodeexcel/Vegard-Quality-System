'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import axios from 'axios'
import { useAuth } from '../../contexts/AuthContext'

interface Component {
  component_type: string
  name: string
  condition: string | null
  description: string | null
  score: number | null
}

interface Finding {
  finding_type: string
  severity: string
  title: string
  description: string
  suggestion: string | null
  standard_reference: string | null
}

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
  components: Component[]
  findings: Finding[]
  ai_analysis: {
    summary?: string
    recommendations?: string[]
    overall_assessment?: any
    legal_risk?: any
    courtroom_assessment?: any
    improvement_suggestions?: {
      for_takstmann?: (string | { issue?: string; recommended_text?: string })[]
      for_report_text?: (string | { issue?: string; recommended_text?: string })[]
    }
  } | null
  extracted_text: string | null
}

export default function ResultsPage() {
  const params = useParams()
  const router = useRouter()
  const { user, loading: authLoading } = useAuth()
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showVerification, setShowVerification] = useState(false)
  const [showFullText, setShowFullText] = useState(false)

  // Redirect to login if not authenticated
  useEffect(() => {
    if (!authLoading && !user) {
      router.push('/login')
    }
  }, [user, authLoading, router])

  useEffect(() => {
    if (!user) return // Don't fetch if not authenticated

    const fetchReport = async () => {
      try {
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
        const response = await axios.get(`${apiUrl}/api/v1/reports/${params.id}`)
        setReport(response.data)
      } catch (err: any) {
        if (err.response?.status === 401) {
          router.push('/login')
        } else {
          setError(err.response?.data?.detail || 'Failed to load report')
        }
      } finally {
        setLoading(false)
      }
    }

    if (params.id) {
      fetchReport()
    }
  }, [params.id, user, router])

  // Show loading while checking auth
  if (authLoading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading...</p>
        </div>
      </div>
    )
  }

  // Don't render if not authenticated (will redirect)
  if (!user) {
    return null
  }

  const getScoreColor = (score: number | null) => {
    if (score === null) return { bg: 'bg-gray-400', text: 'text-gray-700', ring: 'ring-gray-200' }
    if (score >= 80) return { bg: 'bg-green-500', text: 'text-green-700', ring: 'ring-green-200' }
    if (score >= 60) return { bg: 'bg-yellow-500', text: 'text-yellow-700', ring: 'ring-yellow-200' }
    if (score >= 40) return { bg: 'bg-orange-500', text: 'text-orange-700', ring: 'ring-orange-200' }
    return { bg: 'bg-red-500', text: 'text-red-700', ring: 'ring-red-200' }
  }

  const getScoreGradient = (score: number | null) => {
    if (score === null) return 'from-gray-400 to-gray-500'
    if (score >= 80) return 'from-green-500 to-emerald-600'
    if (score >= 60) return 'from-yellow-400 to-yellow-600'
    if (score >= 40) return 'from-orange-500 to-orange-600'
    return 'from-red-500 to-red-600'
  }

  const getSeverityConfig = (severity: string) => {
    switch (severity.toLowerCase()) {
      case 'critical':
        return {
          bg: 'bg-red-50',
          border: 'border-red-200',
          text: 'text-red-800',
          badge: 'bg-red-100 text-red-800',
          icon: '🔴'
        }
      case 'high':
        return {
          bg: 'bg-orange-50',
          border: 'border-orange-200',
          text: 'text-orange-800',
          badge: 'bg-orange-100 text-orange-800',
          icon: '🟠'
        }
      case 'medium':
        return {
          bg: 'bg-yellow-50',
          border: 'border-yellow-200',
          text: 'text-yellow-800',
          badge: 'bg-yellow-100 text-yellow-800',
          icon: '🟡'
        }
      case 'low':
        return {
          bg: 'bg-blue-50',
          border: 'border-blue-200',
          text: 'text-blue-800',
          badge: 'bg-blue-100 text-blue-800',
          icon: '🔵'
        }
      default:
        return {
          bg: 'bg-gray-50',
          border: 'border-gray-200',
          text: 'text-gray-800',
          badge: 'bg-gray-100 text-gray-800',
          icon: '⚪'
        }
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 flex items-center justify-center">
        <div className="text-center">
          <div className="relative">
            <div className="animate-spin rounded-full h-16 w-16 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="w-8 h-8 bg-blue-600 rounded-full animate-pulse"></div>
            </div>
          </div>
          <p className="mt-6 text-lg font-medium text-gray-700">Loading analysis results...</p>
          <p className="mt-2 text-sm text-gray-500">This may take a few seconds</p>
        </div>
      </div>
    )
  }

  if (error || !report) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl shadow-xl p-8 max-w-md w-full border border-gray-100">
          <div className="text-center">
            <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h3 className="text-xl font-bold text-gray-900 mb-2">Error</h3>
            <p className="text-red-600 mb-6">{error || 'Report not found'}</p>
            <button
              onClick={() => router.push('/')}
              className="w-full bg-gradient-to-r from-blue-600 to-indigo-600 text-white py-3 px-6 rounded-lg font-semibold hover:from-blue-700 hover:to-indigo-700 transition-all shadow-lg hover:shadow-xl"
            >
              Back to Upload
            </button>
          </div>
        </div>
      </div>
    )
  }

  const overallScoreColor = getScoreColor(report.overall_score)
  const overallGradient = getScoreGradient(report.overall_score)

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50">
      {/* Header */}
      <header className="bg-white/80 backdrop-blur-sm border-b border-gray-200 sticky top-0 z-50">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-6">
              <button
                onClick={() => router.push('/')}
                className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
                </svg>
              </button>
              <div className="flex items-center space-x-3">
                <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <div>
                  <h1 className="text-xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
                    Analysis Results
                  </h1>
                  <p className="text-xs text-gray-500">Report #{report.id}</p>
                </div>
              </div>
              <nav className="hidden md:flex space-x-4">
                <button
                  onClick={() => router.push('/')}
                  className="text-gray-600 hover:text-blue-600 transition-colors"
                >
                  Upload
                </button>
                <button
                  onClick={() => router.push('/history')}
                  className="text-gray-600 hover:text-blue-600 transition-colors"
                >
                  History
                </button>
              </nav>
            </div>
            <button
              onClick={() => router.push('/')}
              className="px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-lg font-medium hover:from-blue-700 hover:to-indigo-700 transition-all shadow-md hover:shadow-lg"
            >
              Upload New Report
            </button>
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-8">
        <div className="max-w-7xl mx-auto">
          {/* Report Header Card */}
          <div className="bg-white rounded-2xl shadow-xl border border-gray-100 p-6 mb-6">
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
              <div className="flex-1">
                <div className="flex items-center justify-between mb-2">
                  <h2 className="text-2xl font-bold text-gray-900">{report.filename}</h2>
                  {/* Trygghetsscore in top right corner - score in middle of image */}
                  {report.overall_score !== null && (
                    <div className="relative inline-block">
                      <img
                        src="/Trygghetsscore_Topp_s.png"
                        alt="Trygghetsscore"
                        className="h-32 w-auto"
                      />
                      {/* Score number in the middle of the image */}
                      <div className="absolute inset-0 flex items-center justify-center">
                        <div className="text-5xl font-bold text-red-600 drop-shadow-[0_2px_4px_rgba(255,255,255,0.8)]">
                          {Math.min(Math.round(report.overall_score * 0.99), 99).toFixed(0)}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap gap-4 text-sm text-gray-600">
                  {report.report_system && (
                    <div className="flex items-center space-x-2">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      <span>{report.report_system}</span>
                    </div>
                  )}
                  {report.building_year && (
                    <div className="flex items-center space-x-2">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                      </svg>
                      <span>{report.building_year}</span>
                    </div>
                  )}
                  <div className="flex items-center space-x-2">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span>{new Date(report.uploaded_at).toLocaleDateString('no-NO')}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Summary Section */}
          {report.ai_analysis?.summary && (
            <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
              <div className="flex items-center space-x-3 mb-4">
                <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold text-gray-900">Executive Summary</h2>
              </div>
              <p className="text-gray-700 leading-relaxed text-lg">{report.ai_analysis.summary}</p>
            </div>
          )}

          {/* Improvements Needed Section - Actionable Checklist */}
          {((report.findings && report.findings.length > 0) || (report.ai_analysis?.improvement_suggestions)) && (
            <div className="bg-gradient-to-br from-amber-50 via-orange-50 to-red-50 rounded-2xl shadow-xl border-2 border-orange-200 p-8 mb-6">
              <div className="flex items-center space-x-3 mb-6">
                <div className="w-12 h-12 bg-gradient-to-br from-orange-500 to-red-500 rounded-xl flex items-center justify-center shadow-lg">
                  <svg className="w-7 h-7 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                  </svg>
                </div>
                <div className="flex-1">
                  <h2 className="text-3xl font-bold text-gray-900">Improvements Needed</h2>
                  <p className="text-gray-600 mt-1">Action items to improve your report quality</p>
                </div>
                <div className="px-4 py-2 bg-white rounded-lg border-2 border-orange-300">
                  <span className="text-2xl font-bold text-orange-600">
                    {(report.findings?.length || 0) + (report.ai_analysis?.improvement_suggestions?.for_takstmann?.length || 0) + (report.ai_analysis?.improvement_suggestions?.for_report_text?.length || 0)}
                  </span>
                  <span className="text-sm text-gray-600 block">items</span>
                </div>
              </div>

              <div className="space-y-6">
                {/* Critical & High Priority Findings */}
                {report.findings && report.findings.filter(f => f.severity === 'critical' || f.severity === 'high').length > 0 && (
                  <div>
                    <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
                      <span className="w-2 h-2 bg-red-500 rounded-full mr-2"></span>
                      High Priority Issues
                    </h3>
                    <div className="space-y-3">
                      {report.findings
                        .filter(f => f.severity === 'critical' || f.severity === 'high')
                        .map((finding, index) => (
                          <div key={index} className="bg-white rounded-lg p-5 border-l-4 border-red-500 shadow-md hover:shadow-lg transition-shadow">
                            <div className="flex items-start space-x-4">
                              <div className="flex-shrink-0 w-8 h-8 bg-red-100 rounded-lg flex items-center justify-center mt-0.5">
                                <svg className="w-5 h-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                                </svg>
                              </div>
                              <div className="flex-1">
                                <h4 className="font-bold text-gray-900 mb-1">{finding.title}</h4>
                                <p className="text-sm text-gray-700 mb-2">{finding.description}</p>
                                {finding.suggestion && (
                                  <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg">
                                    <p className="text-sm font-semibold text-green-800 mb-1">✅ How to fix:</p>
                                    <p className="text-sm text-green-900">{finding.suggestion}</p>
                                  </div>
                                )}
                                {finding.standard_reference && (
                                  <p className="text-xs text-gray-500 mt-2">
                                    <span className="font-medium">Reference:</span> {finding.standard_reference}
                                  </p>
                                )}
                              </div>
                            </div>
                          </div>
                        ))}
                    </div>
                  </div>
                )}

                {/* Medium & Low Priority Findings */}
                {report.findings && report.findings.filter(f => f.severity === 'medium' || f.severity === 'low').length > 0 && (
                  <div>
                    <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
                      <span className="w-2 h-2 bg-yellow-500 rounded-full mr-2"></span>
                      Medium Priority Improvements
                    </h3>
                    <div className="space-y-3">
                      {report.findings
                        .filter(f => f.severity === 'medium' || f.severity === 'low')
                        .map((finding, index) => (
                          <div key={index} className="bg-white rounded-lg p-5 border-l-4 border-yellow-400 shadow-md hover:shadow-lg transition-shadow">
                            <div className="flex items-start space-x-4">
                              <div className="flex-shrink-0 w-8 h-8 bg-yellow-100 rounded-lg flex items-center justify-center mt-0.5">
                                <svg className="w-5 h-5 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                              </div>
                              <div className="flex-1">
                                <h4 className="font-bold text-gray-900 mb-1">{finding.title}</h4>
                                <p className="text-sm text-gray-700 mb-2">{finding.description}</p>
                                {finding.suggestion && (
                                  <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg">
                                    <p className="text-sm font-semibold text-green-800 mb-1">✅ How to fix:</p>
                                    <p className="text-sm text-green-900">{finding.suggestion}</p>
                                  </div>
                                )}
                                {finding.standard_reference && (
                                  <p className="text-xs text-gray-500 mt-2">
                                    <span className="font-medium">Reference:</span> {finding.standard_reference}
                                  </p>
                                )}
                              </div>
                            </div>
                          </div>
                        ))}
                    </div>
                  </div>
                )}

                {/* Improvement Suggestions from AI */}
                {report.ai_analysis?.improvement_suggestions && (
                  <>
                    {report.ai_analysis.improvement_suggestions.for_takstmann && report.ai_analysis.improvement_suggestions.for_takstmann.length > 0 && (
                      <div>
                        <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
                          <span className="w-2 h-2 bg-blue-500 rounded-full mr-2"></span>
                          Recommendations for Surveyor
                        </h3>
                        <div className="space-y-3">
                          {report.ai_analysis.improvement_suggestions.for_takstmann.map((item, index) => {
                            const issue = typeof item === 'object' ? item.issue || item.recommended_text : item
                            const recommendedText = typeof item === 'object' ? item.recommended_text : item
                            return (
                              <div key={index} className="bg-white rounded-lg p-5 border-l-4 border-blue-400 shadow-md hover:shadow-lg transition-shadow">
                                <div className="flex items-start space-x-4">
                                  <div className="flex-shrink-0 w-8 h-8 bg-blue-100 rounded-lg flex items-center justify-center mt-0.5">
                                    <span className="text-blue-600 font-bold">{index + 1}</span>
                                  </div>
                                  <div className="flex-1">
                                    <p className="text-gray-700 leading-relaxed mb-3">{issue}</p>
                                    {recommendedText && (
                                      <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg">
                                        <p className="text-sm font-semibold text-green-800 mb-1">📝 Anbefalt tekst:</p>
                                        <p className="text-sm text-green-900 italic">{recommendedText}</p>
                                      </div>
                                    )}
                                  </div>
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )}

                    {report.ai_analysis.improvement_suggestions.for_report_text && report.ai_analysis.improvement_suggestions.for_report_text.length > 0 && (
                      <div>
                        <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
                          <span className="w-2 h-2 bg-indigo-500 rounded-full mr-2"></span>
                          Text Improvements Needed
                        </h3>
                        <div className="space-y-3">
                          {report.ai_analysis.improvement_suggestions.for_report_text.map((item, index) => {
                            const issue = typeof item === 'object' ? item.issue || item.recommended_text : item
                            const recommendedText = typeof item === 'object' ? item.recommended_text : item
                            return (
                              <div key={index} className="bg-white rounded-lg p-5 border-l-4 border-indigo-400 shadow-md hover:shadow-lg transition-shadow">
                                <div className="flex items-start space-x-4">
                                  <div className="flex-shrink-0 w-8 h-8 bg-indigo-100 rounded-lg flex items-center justify-center mt-0.5">
                                    <span className="text-indigo-600 font-bold">{index + 1}</span>
                                  </div>
                                  <div className="flex-1">
                                    <p className="text-gray-700 leading-relaxed mb-3">{issue}</p>
                                    {recommendedText && (
                                      <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg">
                                        <p className="text-sm font-semibold text-green-800 mb-1">📝 Anbefalt tekst:</p>
                                        <p className="text-sm text-green-900 italic">{recommendedText}</p>
                                      </div>
                                    )}
                                  </div>
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          )}

          {/* Legal Risk & Courtroom Assessment */}
          {(report.ai_analysis?.legal_risk || report.ai_analysis?.courtroom_assessment) && (
            <div className="grid md:grid-cols-2 gap-6 mb-6">
              {report.ai_analysis?.legal_risk && (
                <div className="bg-gradient-to-br from-amber-50 to-orange-50 rounded-2xl shadow-lg border border-amber-200 p-6">
                  <div className="flex items-center space-x-2 mb-4">
                    <span className="text-2xl">⚖️</span>
                    <h3 className="text-xl font-bold text-gray-900">Legal Risk Assessment</h3>
                  </div>
                  <div className="space-y-3">
                    <div>
                      <span className="text-sm font-semibold text-gray-600">Risk Level: </span>
                      <span className={`px-3 py-1 rounded-full text-sm font-bold ${
                        report.ai_analysis.legal_risk.risk_level === 'høy' ? 'bg-red-100 text-red-800' :
                        report.ai_analysis.legal_risk.risk_level === 'middels' ? 'bg-yellow-100 text-yellow-800' :
                        'bg-green-100 text-green-800'
                      }`}>
                        {report.ai_analysis.legal_risk.risk_level?.toUpperCase() || 'N/A'}
                      </span>
                    </div>
                    {report.ai_analysis.legal_risk.explanation && (
                      <p className="text-gray-700">{report.ai_analysis.legal_risk.explanation}</p>
                    )}
                  </div>
                </div>
              )}
              {report.ai_analysis?.courtroom_assessment && (
                <div className="bg-gradient-to-br from-purple-50 to-indigo-50 rounded-2xl shadow-lg border border-purple-200 p-6">
                  <div className="flex items-center space-x-2 mb-4">
                    <span className="text-2xl">🏛️</span>
                    <h3 className="text-xl font-bold text-gray-900">Courtroom Assessment</h3>
                  </div>
                  {report.ai_analysis.courtroom_assessment.assessment && (
                    <p className="text-gray-700">{report.ai_analysis.courtroom_assessment.assessment}</p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Components Section */}
          {report.components && report.components.length > 0 && (
            <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
              <div className="flex items-center space-x-3 mb-6">
                <div className="w-10 h-10 bg-gradient-to-br from-green-600 to-emerald-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold text-gray-900">Building Components</h2>
                <span className="px-3 py-1 bg-gray-100 text-gray-700 rounded-full text-sm font-medium">
                  {report.components.length}
                </span>
              </div>
              <div className="grid md:grid-cols-2 gap-4">
                {report.components.map((component, index) => {
                  const compScoreColor = getScoreColor(component.score)
                  return (
                    <div key={index} className="border-l-4 border-blue-500 bg-gray-50 rounded-lg p-5 hover:shadow-md transition-shadow">
                      <div className="flex justify-between items-start mb-2">
                        <div className="flex-1">
                          <h3 className="font-bold text-gray-900 text-lg mb-1">{component.name}</h3>
                          <p className="text-sm text-gray-600 mb-2">{component.component_type}</p>
                          {component.condition && (
                            <span className="inline-block px-2 py-1 bg-blue-100 text-blue-800 text-xs font-medium rounded mb-2">
                              {component.condition}
                            </span>
                          )}
                        </div>
                        {component.score !== null && (
                          <div className={`w-12 h-12 rounded-lg bg-gradient-to-br ${getScoreGradient(component.score)} flex items-center justify-center text-white font-bold text-lg shadow-md`}>
                            {component.score.toFixed(0)}
                          </div>
                        )}
                      </div>
                      {component.description && (
                        <p className="text-sm text-gray-700 mt-2">{component.description}</p>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Findings Section */}
          {report.findings && report.findings.length > 0 && (
            <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
              <div className="flex items-center space-x-3 mb-6">
                <div className="w-10 h-10 bg-gradient-to-br from-red-600 to-pink-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold text-gray-900">Findings</h2>
                <span className="px-3 py-1 bg-red-100 text-red-800 rounded-full text-sm font-medium">
                  {report.findings.length}
                </span>
              </div>
              <div className="space-y-4">
                {report.findings.map((finding, index) => {
                  const severityConfig = getSeverityConfig(finding.severity)
                  return (
                    <div
                      key={index}
                      className={`border-l-4 ${severityConfig.border} ${severityConfig.bg} rounded-lg p-5 hover:shadow-md transition-all`}
                    >
                      <div className="flex justify-between items-start mb-3">
                        <div className="flex items-start space-x-3 flex-1">
                          <span className="text-2xl">{severityConfig.icon}</span>
                          <div className="flex-1">
                            <h3 className="font-bold text-gray-900 text-lg mb-1">{finding.title}</h3>
                            <p className={`text-sm ${severityConfig.text} mb-2`}>{finding.description}</p>
                          </div>
                        </div>
                        <span className={`px-3 py-1 rounded-full text-xs font-bold ${severityConfig.badge} whitespace-nowrap ml-2`}>
                          {finding.severity.toUpperCase()}
                        </span>
                      </div>
                      {finding.suggestion && (
                        <div className="mt-3 pt-3 border-t border-gray-200">
                          <p className="text-sm font-semibold text-gray-700 mb-1">💡 Suggestion:</p>
                          <p className="text-sm text-gray-700">{finding.suggestion}</p>
                        </div>
                      )}
                      {finding.standard_reference && (
                        <div className="mt-2 flex items-center space-x-2 text-xs text-gray-600">
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                          </svg>
                          <span className="font-medium">Reference: {finding.standard_reference}</span>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Recommendations */}
          {report.ai_analysis?.recommendations && report.ai_analysis.recommendations.length > 0 && (
            <div className="bg-gradient-to-br from-blue-50 to-indigo-50 rounded-2xl shadow-lg border border-blue-200 p-8 mb-6">
              <div className="flex items-center space-x-3 mb-6">
                <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold text-gray-900">Recommendations</h2>
              </div>
              <ul className="space-y-3">
                {report.ai_analysis.recommendations.map((rec, index) => (
                  <li key={index} className="flex items-start space-x-3">
                    <span className="flex-shrink-0 w-6 h-6 bg-blue-600 text-white rounded-full flex items-center justify-center text-sm font-bold mt-0.5">
                      {index + 1}
                    </span>
                    <span className="text-gray-700 text-lg leading-relaxed">{rec}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Verification & Debug Section */}
          <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-6">
            <button
              onClick={() => setShowVerification(!showVerification)}
              className="w-full flex items-center justify-between p-4 hover:bg-gray-50 rounded-lg transition-colors"
            >
              <div className="flex items-center space-x-3">
                <div className="w-10 h-10 bg-gray-100 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                  </svg>
                </div>
                <h2 className="text-xl font-bold text-gray-900">Verification & Debug</h2>
              </div>
              <svg
                className={`w-5 h-5 text-gray-600 transition-transform ${showVerification ? 'rotate-180' : ''}`}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            
            {showVerification && (
              <div className="space-y-6 mt-6 pt-6 border-t border-gray-200">
                {/* Metadata */}
                <div className="bg-gray-50 rounded-xl p-6">
                  <h3 className="font-bold text-gray-900 mb-4">Analysis Metadata</h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {[
                      { label: 'Text Length', value: `${report.extracted_text?.length || 0} chars` },
                      { label: 'Est. Tokens', value: Math.floor((report.extracted_text?.length || 0) / 4).toLocaleString() },
                      { label: 'Components', value: report.components.length },
                      { label: 'Findings', value: report.findings.length },
                    ].map((item, idx) => (
                      <div key={idx} className="bg-white rounded-lg p-3">
                        <p className="text-xs text-gray-600 mb-1">{item.label}</p>
                        <p className="text-lg font-bold text-gray-900">{item.value}</p>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Extracted Text */}
                {report.extracted_text && (
                  <div>
                    <div className="flex justify-between items-center mb-3">
                      <h3 className="font-bold text-gray-900">Extracted PDF Text</h3>
                      <button
                        onClick={() => setShowFullText(!showFullText)}
                        className="text-sm text-blue-600 hover:text-blue-800 font-medium"
                      >
                        {showFullText ? 'Show Less' : 'Show Full Text'}
                      </button>
                    </div>
                    <div className="bg-gray-900 rounded-lg p-4 max-h-96 overflow-y-auto">
                      <pre className="text-xs text-green-400 whitespace-pre-wrap font-mono">
                        {showFullText 
                          ? report.extracted_text 
                          : `${report.extracted_text.substring(0, 2000)}${report.extracted_text.length > 2000 ? '\n\n... (truncated)' : ''}`
                        }
                      </pre>
                    </div>
                  </div>
                )}

                {/* Full AI Analysis JSON */}
                {report.ai_analysis && (
                  <div>
                    <h3 className="font-bold text-gray-900 mb-3">Full AI Analysis (JSON)</h3>
                    <div className="bg-gray-900 rounded-lg p-4 max-h-96 overflow-y-auto">
                      <pre className="text-xs text-yellow-400 whitespace-pre-wrap font-mono">
                        {JSON.stringify(report.ai_analysis, null, 2)}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
