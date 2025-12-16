'use client'

import { useState, useEffect } from 'react'
import { useRouter, useParams } from 'next/navigation'
import axios from 'axios'

interface ReportDetail {
  id: number
  filename: string
  uploaded_at: string
  user: {
    id: number
    name: string | null
    email: string
    company: string | null
  }
  report_system: string | null
  building_year: number | null
  overall_score: number | null
  quality_score: number | null
  completeness_score: number | null
  compliance_score: number | null
  status: string
  components: Array<{
    component_type: string
    name: string
    condition: string | null
    description: string | null
    score: number | null
  }>
  findings: Array<{
    finding_type: string
    severity: string
    title: string
    description: string
    suggestion: string | null
    standard_reference: string | null
  }>
  tg2_tg3_issues: Array<any>
  ns3600_deviations: Array<any>
  ns3940_deviations: Array<any>
  regulation_deviations: Array<any>
  prop44_deviations: Array<any>
  risk_findings: Array<any>
  ai_analysis: any
  s3_key: string | null
  pdf_download_url: string | null
  trygghetsscore: {
    score: number
    explanation: string
    factors_positive: string
    factors_negative: string
  } | null
  forbedringsliste: Array<{
    nummer: number
    kategori: string
    hva_er_feil: string
    hvor_i_rapporten: string
    hvorfor_problem: string
    hva_må_endres: string
    konsekvens_ikke_rettet: string
  }>
  sperrer_96: Array<string>
  rettssaksvurdering: {
    title: string
    sterke_sider: string
    svake_sider: string
    angrepspunkter: string
    ansvarseksponering: string
    samlet_vurdering: string
  } | null
}

export default function AdminReportDetail() {
  const router = useRouter()
  const params = useParams()
  const reportId = params.id as string
  const [report, setReport] = useState<ReportDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeSection, setActiveSection] = useState<'overview' | 'arkat' | 'findings' | 'components' | 'raw'>('overview')

  useEffect(() => {
    const adminToken = localStorage.getItem('admin_token')
    if (!adminToken) {
      router.push('/admin/login')
      return
    }
    fetchReport()
  }, [reportId, router])

  const fetchReport = async () => {
    setLoading(true)
    setError('')
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const adminToken = localStorage.getItem('admin_token')
      const response = await axios.get(
        `${apiUrl}/api/v1/admin/reports/${reportId}`,
        {
          headers: { Authorization: `Bearer ${adminToken}` }
        }
      )
      setReport(response.data)
    } catch (err: any) {
      console.error('Error fetching report:', err)
      if (err.response?.status === 401 || err.response?.status === 403) {
        localStorage.removeItem('admin_token')
        router.push('/admin/login')
      } else {
        setError(err.response?.data?.detail || 'Failed to load report')
      }
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading report...</p>
        </div>
      </div>
    )
  }

  if (error || !report) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error || 'Report not found'}</p>
          <button
            onClick={() => router.push('/admin')}
            className="text-blue-600 hover:text-blue-800"
          >
            ← Back to Reports
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-50">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-4">
              <button
                onClick={() => router.push('/admin')}
                className="text-gray-600 hover:text-gray-900"
              >
                ← Back
              </button>
              <div className="flex items-center space-x-3">
                <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <div>
                  <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
                    Report Details
                  </h1>
                  <p className="text-xs text-gray-500">Internal Dashboard</p>
                </div>
              </div>
            </div>
            <button
              onClick={() => {
                localStorage.removeItem('admin_token')
                router.push('/admin/login')
              }}
              className="flex items-center space-x-2 text-gray-600 hover:text-gray-900 px-3 py-2 rounded-lg hover:bg-gray-100 transition-colors"
            >
              <span>Sign Out</span>
            </button>
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-8">
        {/* Report Info Card */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 mb-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div>
              <h3 className="text-sm font-medium text-gray-500 mb-1">File Name</h3>
              <p className="text-sm font-medium text-gray-900">{report.filename}</p>
            </div>
            <div>
              <h3 className="text-sm font-medium text-gray-500 mb-1">User / Company</h3>
              <p className="text-sm font-medium text-gray-900">{report.user.name || report.user.email}</p>
              {report.user.company && (
                <p className="text-sm text-gray-500">{report.user.company}</p>
              )}
            </div>
            <div>
              <h3 className="text-sm font-medium text-gray-500 mb-1">Uploaded At</h3>
              <p className="text-sm font-medium text-gray-900">
                {new Date(report.uploaded_at).toLocaleString()}
              </p>
            </div>
          </div>
        </div>

        {/* Score Breakdown Section */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 mb-6">
          <h2 className="text-xl font-bold text-gray-900 mb-4">Full Score Breakdown</h2>
          
          {/* Trygghetsscore (Primary Score) - Only show if score exists */}
          {report.trygghetsscore && report.trygghetsscore.score !== null && report.trygghetsscore.score !== undefined && (
            <div className="mb-6 pb-6 border-b border-gray-200">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-lg font-semibold text-gray-900">Trygghetsscore</h3>
                <p className={`text-3xl font-bold ${
                  (report.trygghetsscore.score || 0) >= 80 ? 'text-green-600' :
                  (report.trygghetsscore.score || 0) >= 60 ? 'text-yellow-600' : 'text-red-600'
                }`}>
                  {report.trygghetsscore.score.toFixed(1)} / 100
                </p>
              </div>
              {report.trygghetsscore.explanation && (
                <p className="text-sm text-gray-600 mb-3">{report.trygghetsscore.explanation}</p>
              )}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
                {report.trygghetsscore.factors_positive && (
                  <div className="bg-green-50 border border-green-200 rounded-lg p-3">
                    <h4 className="text-sm font-medium text-green-900 mb-1">Positive Factors</h4>
                    <p className="text-sm text-green-700">{report.trygghetsscore.factors_positive}</p>
                  </div>
                )}
                {report.trygghetsscore.factors_negative && (
                  <div className="bg-red-50 border border-red-200 rounded-lg p-3">
                    <h4 className="text-sm font-medium text-red-900 mb-1">Negative Factors</h4>
                    <p className="text-sm text-red-700">{report.trygghetsscore.factors_negative}</p>
                  </div>
                )}
              </div>
            </div>
          )}
          
          {/* Legacy Scores */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="bg-gray-50 rounded-lg border border-gray-200 p-4">
              <h3 className="text-sm font-medium text-gray-500 mb-1">Overall Score</h3>
              <p className={`text-2xl font-bold ${
                (report.overall_score || 0) >= 80 ? 'text-green-600' :
                (report.overall_score || 0) >= 60 ? 'text-yellow-600' : 'text-red-600'
              }`}>
                {report.overall_score?.toFixed(1) || 'N/A'}
              </p>
            </div>
            <div className="bg-gray-50 rounded-lg border border-gray-200 p-4">
              <h3 className="text-sm font-medium text-gray-500 mb-1">Quality Score</h3>
              <p className="text-2xl font-bold text-gray-900">
                {report.quality_score?.toFixed(1) || 'N/A'}
              </p>
            </div>
            <div className="bg-gray-50 rounded-lg border border-gray-200 p-4">
              <h3 className="text-sm font-medium text-gray-500 mb-1">Completeness</h3>
              <p className="text-2xl font-bold text-gray-900">
                {report.completeness_score?.toFixed(1) || 'N/A'}
              </p>
            </div>
            <div className="bg-gray-50 rounded-lg border border-gray-200 p-4">
              <h3 className="text-sm font-medium text-gray-500 mb-1">Compliance</h3>
              <p className="text-2xl font-bold text-gray-900">
                {report.compliance_score?.toFixed(1) || 'N/A'}
              </p>
            </div>
          </div>
          
          {/* PDF Download Link */}
          {report.s3_key && (
            <div className="mt-6 pt-6 border-t border-gray-200">
              {report.pdf_download_url ? (
                <a
                  href={report.pdf_download_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  <span>Download Original PDF</span>
                </a>
              ) : (
                <button
                  onClick={async () => {
                    const adminToken = localStorage.getItem('admin_token')
                    if (!adminToken) {
                      alert('Please log in to download PDF')
                      return
                    }
                    
                    try {
                      const response = await fetch(
                        `${process.env.NEXT_PUBLIC_API_URL}/api/v1/admin/reports/${report.id}/download-pdf`,
                        {
                          headers: {
                            'Authorization': `Bearer ${adminToken}`
                          }
                        }
                      )
                      
                      if (!response.ok) {
                        const errorText = await response.text()
                        throw new Error(errorText || `HTTP ${response.status}`)
                      }
                      
                      // Check if response is actually a PDF
                      const contentType = response.headers.get('content-type')
                      if (!contentType || !contentType.includes('application/pdf')) {
                        throw new Error('Response is not a PDF file')
                      }
                      
                      const blob = await response.blob()
                      
                      // Validate blob is actually a PDF by checking first bytes
                      const arrayBuffer = await blob.slice(0, 4).arrayBuffer()
                      const uint8Array = new Uint8Array(arrayBuffer)
                      const pdfMagicBytes = [0x25, 0x50, 0x44, 0x46] // %PDF
                      const isPDF = uint8Array.every((byte, index) => byte === pdfMagicBytes[index])
                      
                      if (!isPDF) {
                        throw new Error('Downloaded file is not a valid PDF')
                      }
                      
                      // Create download link
                      const url = window.URL.createObjectURL(blob)
                      const a = document.createElement('a')
                      a.href = url
                      a.download = report.filename || 'report.pdf'
                      document.body.appendChild(a)
                      a.click()
                      
                      // Cleanup
                      setTimeout(() => {
                        window.URL.revokeObjectURL(url)
                        document.body.removeChild(a)
                      }, 100)
                    } catch (err: any) {
                      console.error('Download failed:', err)
                      alert(`Failed to download PDF: ${err.message || 'Unknown error'}`)
                    }
                  }}
                  className="inline-flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  <span>Download Original PDF</span>
                </button>
              )}
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 mb-6">
          <div className="flex border-b border-gray-200 overflow-x-auto">
            {[
              { id: 'overview', label: 'Overview' },
              { id: 'arkat', label: `ARKAT Findings (${report.forbedringsliste?.length || 0})` },
              { id: 'findings', label: `All Findings (${report.findings.length})` },
              { id: 'components', label: `Components (${report.components.length})` },
              { id: 'raw', label: 'Raw Analysis' },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveSection(tab.id as any)}
                className={`px-6 py-4 text-sm font-medium transition-colors whitespace-nowrap ${
                  activeSection === tab.id
                    ? 'text-blue-600 border-b-2 border-blue-600 bg-blue-50'
                    : 'text-gray-600 hover:text-gray-900 hover:bg-gray-50'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="p-6">
            {activeSection === 'overview' && (
              <div className="space-y-6">
                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">TG2/TG3 Assessment Issues</h3>
                  {report.tg2_tg3_issues.length > 0 ? (
                    <div className="space-y-2">
                      {report.tg2_tg3_issues.map((issue, idx) => (
                        <div key={idx} className="bg-yellow-50 border-l-4 border-yellow-400 p-4 rounded">
                          <p className="font-medium text-gray-900">{issue.title}</p>
                          <p className="text-sm text-gray-600 mt-1">{issue.description}</p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-gray-500">No TG2/TG3 issues found</p>
                  )}
                </div>

                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">NS 3600 Deviations</h3>
                  {report.ns3600_deviations.length > 0 ? (
                    <div className="space-y-2">
                      {report.ns3600_deviations.map((dev, idx) => (
                        <div key={idx} className="bg-red-50 border-l-4 border-red-400 p-4 rounded">
                          <p className="font-medium text-gray-900">{dev.title}</p>
                          <p className="text-sm text-gray-600 mt-1">{dev.description}</p>
                          {dev.standard_reference && (
                            <p className="text-xs text-gray-500 mt-1">Reference: {dev.standard_reference}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-gray-500">No NS 3600 deviations found</p>
                  )}
                </div>

                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">NS 3940 Deviations</h3>
                  {report.ns3940_deviations.length > 0 ? (
                    <div className="space-y-2">
                      {report.ns3940_deviations.map((dev, idx) => (
                        <div key={idx} className="bg-red-50 border-l-4 border-red-400 p-4 rounded">
                          <p className="font-medium text-gray-900">{dev.title}</p>
                          <p className="text-sm text-gray-600 mt-1">{dev.description}</p>
                          {dev.standard_reference && (
                            <p className="text-xs text-gray-500 mt-1">Reference: {dev.standard_reference}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-gray-500">No NS 3940 deviations found</p>
                  )}
                </div>

                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">Regulation (Forskrift) Deviations</h3>
                  {report.regulation_deviations && report.regulation_deviations.length > 0 ? (
                    <div className="space-y-2">
                      {report.regulation_deviations.map((dev, idx) => (
                        <div key={idx} className="bg-orange-50 border-l-4 border-orange-400 p-4 rounded">
                          <p className="font-medium text-gray-900">{dev.title}</p>
                          <p className="text-sm text-gray-600 mt-1">{dev.description}</p>
                          {dev.standard_reference && (
                            <p className="text-xs text-gray-500 mt-1">Reference: {dev.standard_reference}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-gray-500">No Regulation deviations found</p>
                  )}
                </div>

                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">Prop. 44 L (Forarbeid) Deviations</h3>
                  {report.prop44_deviations && report.prop44_deviations.length > 0 ? (
                    <div className="space-y-2">
                      {report.prop44_deviations.map((dev, idx) => (
                        <div key={idx} className="bg-purple-50 border-l-4 border-purple-400 p-4 rounded">
                          <p className="font-medium text-gray-900">{dev.title}</p>
                          <p className="text-sm text-gray-600 mt-1">{dev.description}</p>
                          {dev.standard_reference && (
                            <p className="text-xs text-gray-500 mt-1">Reference: {dev.standard_reference}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-gray-500">No Prop. 44 deviations found</p>
                  )}
                </div>

                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">Sperrer mot Trygghetsscore ≥96</h3>
                  {report.sperrer_96 && report.sperrer_96.length > 0 ? (
                    <div className="space-y-2">
                      {report.sperrer_96.map((sperre, idx) => (
                        <div key={idx} className="bg-red-50 border-l-4 border-red-500 p-4 rounded">
                          <p className="font-medium text-red-900">🚫 {sperre}</p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-gray-500">No blockers found</p>
                  )}
                </div>

                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">High-Risk Findings</h3>
                  {report.risk_findings.length > 0 ? (
                    <div className="space-y-2">
                      {report.risk_findings.map((finding, idx) => (
                        <div key={idx} className="bg-red-50 border-l-4 border-red-400 p-4 rounded">
                          <div className="flex items-start justify-between">
                            <div>
                              <p className="font-medium text-gray-900">{finding.title}</p>
                              <p className="text-sm text-gray-600 mt-1">{finding.description}</p>
                              {finding.suggestion && (
                                <p className="text-sm text-blue-600 mt-2">💡 {finding.suggestion}</p>
                              )}
                            </div>
                            <span className={`px-2 py-1 text-xs font-medium rounded ${
                              finding.severity === 'critical' ? 'bg-red-100 text-red-800' :
                              finding.severity === 'high' ? 'bg-orange-100 text-orange-800' :
                              'bg-yellow-100 text-yellow-800'
                            }`}>
                              {finding.severity}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-gray-500">No high-risk findings</p>
                  )}
                </div>
              </div>
            )}

            {activeSection === 'arkat' && (
              <div className="space-y-6">
                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">Findings in ARKAT Format</h3>
                  {report.forbedringsliste && report.forbedringsliste.length > 0 ? (
                    <div className="space-y-4">
                      {report.forbedringsliste.map((item) => (
                        <div key={item.nummer} className="border border-gray-200 rounded-lg p-5 bg-gray-50">
                          <div className="flex items-start justify-between mb-3">
                            <div className="flex items-center space-x-2">
                              <span className="text-lg font-bold text-blue-600">#{item.nummer}</span>
                              <span className={`px-3 py-1 text-xs font-medium rounded ${
                                item.kategori === 'SPERRE ≥96' ? 'bg-red-100 text-red-800' :
                                item.kategori === 'Vesentlig avvik' ? 'bg-orange-100 text-orange-800' :
                                'bg-yellow-100 text-yellow-800'
                              }`}>
                                {item.kategori}
                              </span>
                            </div>
                          </div>
                          
                          <div className="space-y-3">
                            <div>
                              <h5 className="text-sm font-semibold text-gray-700 mb-1">Hva er feil eller mangler:</h5>
                              <p className="text-sm text-gray-900 bg-white p-2 rounded border border-gray-200">{item.hva_er_feil}</p>
                            </div>
                            
                            <div>
                              <h5 className="text-sm font-semibold text-gray-700 mb-1">Hvor i rapporten:</h5>
                              <p className="text-sm text-gray-900 bg-white p-2 rounded border border-gray-200">{item.hvor_i_rapporten}</p>
                            </div>
                            
                            <div>
                              <h5 className="text-sm font-semibold text-gray-700 mb-1">Hvorfor dette er et problem:</h5>
                              <p className="text-sm text-gray-900 bg-white p-2 rounded border border-gray-200">{item.hvorfor_problem}</p>
                            </div>
                            
                            <div>
                              <h5 className="text-sm font-semibold text-gray-700 mb-1">Hva som må endres i rapporten:</h5>
                              <p className="text-sm text-blue-700 bg-blue-50 p-2 rounded border border-blue-200">{item.hva_må_endres}</p>
                            </div>
                            
                            <div>
                              <h5 className="text-sm font-semibold text-gray-700 mb-1">Konsekvens dersom det ikke rettes:</h5>
                              <p className="text-sm text-red-700 bg-red-50 p-2 rounded border border-red-200">{item.konsekvens_ikke_rettet}</p>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-gray-500">No ARKAT findings available</p>
                  )}
                </div>
              </div>
            )}

            {activeSection === 'findings' && (
              <div className="space-y-4">
                {report.findings.map((finding, idx) => (
                  <div key={idx} className="border border-gray-200 rounded-lg p-4">
                    <div className="flex items-start justify-between mb-2">
                      <h4 className="font-medium text-gray-900">{finding.title}</h4>
                      <div className="flex items-center space-x-2">
                        <span className={`px-2 py-1 text-xs font-medium rounded ${
                          finding.severity === 'critical' ? 'bg-red-100 text-red-800' :
                          finding.severity === 'high' ? 'bg-orange-100 text-orange-800' :
                          finding.severity === 'medium' ? 'bg-yellow-100 text-yellow-800' :
                          'bg-blue-100 text-blue-800'
                        }`}>
                          {finding.severity}
                        </span>
                        <span className="px-2 py-1 text-xs font-medium bg-gray-100 text-gray-800 rounded">
                          {finding.finding_type}
                        </span>
                      </div>
                    </div>
                    <p className="text-sm text-gray-600 mb-2">{finding.description}</p>
                    {finding.suggestion && (
                      <p className="text-sm text-blue-600 bg-blue-50 p-2 rounded mt-2">
                        💡 <strong>Suggestion:</strong> {finding.suggestion}
                      </p>
                    )}
                    {finding.standard_reference && (
                      <p className="text-xs text-gray-500 mt-2">Reference: {finding.standard_reference}</p>
                    )}
                  </div>
                ))}
              </div>
            )}

            {activeSection === 'components' && (
              <div className="space-y-4">
                {report.components.map((component, idx) => (
                  <div key={idx} className="border border-gray-200 rounded-lg p-4">
                    <div className="flex items-start justify-between mb-2">
                      <div>
                        <h4 className="font-medium text-gray-900">{component.name}</h4>
                        <p className="text-sm text-gray-500">{component.component_type}</p>
                      </div>
                      {component.score !== null && (
                        <span className={`text-lg font-bold ${
                          component.score >= 80 ? 'text-green-600' :
                          component.score >= 60 ? 'text-yellow-600' : 'text-red-600'
                        }`}>
                          {component.score.toFixed(1)}
                        </span>
                      )}
                    </div>
                    {component.condition && (
                      <p className="text-sm text-gray-600 mb-1">Condition: {component.condition}</p>
                    )}
                    {component.description && (
                      <p className="text-sm text-gray-600">{component.description}</p>
                    )}
                  </div>
                ))}
              </div>
            )}

            {activeSection === 'raw' && (
              <div>
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Raw AI Analysis (Debug Mode)</h3>
                <div className="mb-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                  <p className="text-sm text-yellow-800">
                    ⚠️ This is the raw JSON output from the AI analysis. Use this for debugging and verification.
                  </p>
                </div>
                <pre className="bg-gray-50 border border-gray-200 rounded-lg p-4 overflow-auto text-xs max-h-96">
                  {JSON.stringify(report.ai_analysis, null, 2)}
                </pre>
                
                {report.rettssaksvurdering && (
                  <div className="mt-6">
                    <h4 className="text-lg font-semibold text-gray-900 mb-3">Rettssaksvurdering</h4>
                    <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-3">
                      <div>
                        <h5 className="text-sm font-semibold text-gray-700 mb-1">Sterke sider:</h5>
                        <p className="text-sm text-gray-900">{report.rettssaksvurdering.sterke_sider || 'N/A'}</p>
                      </div>
                      <div>
                        <h5 className="text-sm font-semibold text-gray-700 mb-1">Svake sider:</h5>
                        <p className="text-sm text-gray-900">{report.rettssaksvurdering.svake_sider || 'N/A'}</p>
                      </div>
                      <div>
                        <h5 className="text-sm font-semibold text-gray-700 mb-1">Angrepspunkter:</h5>
                        <p className="text-sm text-gray-900">{report.rettssaksvurdering.angrepspunkter || 'N/A'}</p>
                      </div>
                      <div>
                        <h5 className="text-sm font-semibold text-gray-700 mb-1">Ansvarseksponering:</h5>
                        <span className={`px-3 py-1 text-sm font-medium rounded ${
                          report.rettssaksvurdering.ansvarseksponering === 'høy' ? 'bg-red-100 text-red-800' :
                          report.rettssaksvurdering.ansvarseksponering === 'moderat' ? 'bg-yellow-100 text-yellow-800' :
                          'bg-green-100 text-green-800'
                        }`}>
                          {report.rettssaksvurdering.ansvarseksponering || 'N/A'}
                        </span>
                      </div>
                      <div>
                        <h5 className="text-sm font-semibold text-gray-700 mb-1">Samlet vurdering:</h5>
                        <p className="text-sm text-gray-900">{report.rettssaksvurdering.samlet_vurdering || 'N/A'}</p>
                      </div>
                    </div>
                  </div>
                )}
                
                {report.s3_key && (
                  <div className="mt-4">
                    <h4 className="text-sm font-medium text-gray-900 mb-2">PDF Location</h4>
                    <p className="text-sm text-gray-600 font-mono bg-gray-50 p-2 rounded">
                      S3 Key: {report.s3_key}
                    </p>
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

