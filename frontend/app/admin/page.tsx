'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import UsersSection from './components/UsersSection'
import SystemInsightsSection from './components/SystemInsightsSection'
import CreditsBillingSection from './components/CreditsBillingSection'

interface Report {
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
  findings_count: number
  high_risk_findings: number
  status: string
}

export default function AdminDashboard() {
  const router = useRouter()
  const [activeTab, setActiveTab] = useState<'reports' | 'users' | 'credits' | 'system'>('reports')
  const [reports, setReports] = useState<Report[]>([])
  const [loadingReports, setLoadingReports] = useState(false)
  const [adminToken, setAdminToken] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [sortField, setSortField] = useState<string>('uploaded_at')
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc')
  const [filters, setFilters] = useState({
    score_min: '',
    score_max: '',
    status: '',
    low_score_only: false,
    date_from: '',
    date_to: '',
    user_search: '',
    company_search: '',
    standard: '',
    high_risk_only: false,
    min_findings: '',
  })

  useEffect(() => {
    // Check for admin token immediately
    const token = localStorage.getItem('admin_token')
    if (!token) {
      // Immediately redirect to admin login (not regular login)
      window.location.href = '/admin/login'
      return
    }
    setAdminToken(token)
    setLoading(false)
  }, [])

  useEffect(() => {
    if (adminToken && activeTab === 'reports') {
      fetchReports()
    }
  }, [adminToken, activeTab, filters, sortField, sortDirection])

  const handleSort = (field: string) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDirection('asc')
    }
  }

  const sortedReports = [...reports].sort((a, b) => {
    let aValue: any
    let bValue: any

    switch (sortField) {
      case 'id':
        aValue = a.id
        bValue = b.id
        break
      case 'uploaded_at':
        aValue = new Date(a.uploaded_at).getTime()
        bValue = new Date(b.uploaded_at).getTime()
        break
      case 'overall_score':
        aValue = a.overall_score ?? 0
        bValue = b.overall_score ?? 0
        break
      case 'findings_count':
        aValue = a.findings_count
        bValue = b.findings_count
        break
      case 'filename':
        aValue = a.filename.toLowerCase()
        bValue = b.filename.toLowerCase()
        break
      case 'user':
        aValue = (a.user.name || a.user.email || '').toLowerCase()
        bValue = (b.user.name || b.user.email || '').toLowerCase()
        break
      case 'status':
        aValue = a.status
        bValue = b.status
        break
      default:
        return 0
    }

    if (aValue < bValue) return sortDirection === 'asc' ? -1 : 1
    if (aValue > bValue) return sortDirection === 'asc' ? 1 : -1
    return 0
  })

  const fetchReports = async () => {
    if (!adminToken) return
    
    setLoadingReports(true)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const params = new URLSearchParams()
      if (filters.score_min) params.append('score_min', filters.score_min)
      if (filters.score_max) params.append('score_max', filters.score_max)
      if (filters.status) params.append('status', filters.status)
      if (filters.low_score_only) params.append('low_score_only', 'true')
      if (filters.date_from) params.append('date_from', filters.date_from)
      if (filters.date_to) params.append('date_to', filters.date_to)
      if (filters.user_search) params.append('user_id', filters.user_search)
      if (filters.company_search) params.append('company', filters.company_search)
      if (filters.standard) params.append('report_system', filters.standard)
      if (filters.high_risk_only) params.append('high_risk_only', 'true')
      if (filters.min_findings) params.append('min_findings', filters.min_findings)

      const response = await axios.get(
        `${apiUrl}/api/v1/admin/reports?${params.toString()}`,
        {
          headers: { Authorization: `Bearer ${adminToken}` }
        }
      )
      setReports(response.data.reports || [])
    } catch (error: any) {
      console.error('Error fetching reports:', error)
      if (error.response?.status === 401 || error.response?.status === 403) {
        localStorage.removeItem('admin_token')
        window.location.href = '/admin/login'
      }
    } finally {
      setLoadingReports(false)
    }
  }

  if (loading || !adminToken) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading...</p>
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
            <div className="flex items-center space-x-6">
              <div className="flex items-center space-x-3">
                <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <div>
                  <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
                    Verifisert Admin
                  </h1>
                  <p className="text-xs text-gray-500">Internal Dashboard</p>
                </div>
              </div>
            </div>
            <button
              onClick={() => {
                localStorage.removeItem('admin_token')
                window.location.href = '/admin/login'
              }}
              className="flex items-center space-x-2 text-gray-600 hover:text-gray-900 px-3 py-2 rounded-lg hover:bg-gray-100 transition-colors"
            >
              <span>Sign Out</span>
            </button>
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-8">
        {/* Tabs */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 mb-6">
          <div className="flex border-b border-gray-200">
            {[
              { id: 'reports', label: 'Reports & Feedback', icon: '📊' },
              { id: 'users', label: 'Users', icon: '👥' },
              { id: 'credits', label: 'Credits & Billing', icon: '💳' },
              { id: 'system', label: 'System & Insights', icon: '🔧' },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={`flex-1 px-6 py-4 text-sm font-medium transition-colors ${
                  activeTab === tab.id
                    ? 'text-blue-600 border-b-2 border-blue-600 bg-blue-50'
                    : 'text-gray-600 hover:text-gray-900 hover:bg-gray-50'
                }`}
              >
                <span className="mr-2">{tab.icon}</span>
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          {activeTab === 'reports' && (
            <div>
              <div className="mb-6">
                <h2 className="text-2xl font-bold text-gray-900 mb-4">Reports & Feedback</h2>
                
                {/* Filters */}
                <div className="space-y-4 mb-4">
                  {/* First Row */}
                  <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Min Score</label>
                      <input
                        type="number"
                        value={filters.score_min}
                        onChange={(e) => setFilters({ ...filters, score_min: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                        placeholder="0"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Max Score</label>
                      <input
                        type="number"
                        value={filters.score_max}
                        onChange={(e) => setFilters({ ...filters, score_max: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                        placeholder="100"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Status</label>
                      <select
                        value={filters.status}
                        onChange={(e) => setFilters({ ...filters, status: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                      >
                        <option value="">All</option>
                        <option value="completed">Completed</option>
                        <option value="processing">Processing</option>
                        <option value="failed">Failed</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Standard</label>
                      <select
                        value={filters.standard}
                        onChange={(e) => setFilters({ ...filters, standard: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                      >
                        <option value="">All</option>
                        <option value="NT">NT</option>
                        <option value="NITO">NITO</option>
                        <option value="Fremtind">Fremtind</option>
                        <option value="BMTF">BMTF</option>
                      </select>
                    </div>
                  </div>
                  
                  {/* Second Row */}
                  <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Date From</label>
                      <input
                        type="date"
                        value={filters.date_from}
                        onChange={(e) => setFilters({ ...filters, date_from: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Date To</label>
                      <input
                        type="date"
                        value={filters.date_to}
                        onChange={(e) => setFilters({ ...filters, date_to: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">User ID</label>
                      <input
                        type="number"
                        value={filters.user_search}
                        onChange={(e) => setFilters({ ...filters, user_search: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                        placeholder="User ID (from Users tab)"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Company</label>
                      <input
                        type="text"
                        value={filters.company_search}
                        onChange={(e) => setFilters({ ...filters, company_search: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                        placeholder="Search by company..."
                      />
                    </div>
                  </div>
                  
                  {/* Third Row - Checkboxes and Min Findings */}
                  <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div className="flex items-center">
                      <label className="flex items-center space-x-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={filters.low_score_only}
                          onChange={(e) => setFilters({ ...filters, low_score_only: e.target.checked })}
                          className="w-4 h-4 text-blue-600 rounded"
                        />
                        <span className="text-sm font-medium text-gray-700">Low score only (&lt;70)</span>
                      </label>
                    </div>
                    <div className="flex items-center">
                      <label className="flex items-center space-x-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={filters.high_risk_only}
                          onChange={(e) => setFilters({ ...filters, high_risk_only: e.target.checked })}
                          className="w-4 h-4 text-blue-600 rounded"
                        />
                        <span className="text-sm font-medium text-gray-700">High-risk only</span>
                      </label>
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Min Findings</label>
                      <input
                        type="number"
                        value={filters.min_findings}
                        onChange={(e) => setFilters({ ...filters, min_findings: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                        placeholder="0"
                        min="0"
                      />
                    </div>
                    <div className="flex items-end">
                      <button
                        onClick={() => setFilters({
                          score_min: '',
                          score_max: '',
                          status: '',
                          low_score_only: false,
                          date_from: '',
                          date_to: '',
                          user_search: '',
                          company_search: '',
                          standard: '',
                          high_risk_only: false,
                          min_findings: '',
                        })}
                        className="w-full px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition-colors"
                      >
                        Clear Filters
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              {/* Reports Table */}
              {loadingReports ? (
                <div className="text-center py-12">
                  <div className="animate-spin rounded-full h-8 w-8 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
                  <p className="mt-4 text-gray-600">Loading reports...</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th 
                          className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 transition-colors select-none"
                          onClick={() => handleSort('id')}
                          title="Click to sort by Report ID"
                        >
                          <div className="flex items-center space-x-2">
                            <span>Report ID</span>
                            {sortField === 'id' ? (
                              <span className="text-blue-600 font-bold">{sortDirection === 'asc' ? '↑' : '↓'}</span>
                            ) : (
                              <span className="text-gray-400">↕</span>
                            )}
                          </div>
                        </th>
                        <th 
                          className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 transition-colors select-none"
                          onClick={() => handleSort('uploaded_at')}
                          title="Click to sort by Date/Time"
                        >
                          <div className="flex items-center space-x-2">
                            <span>Date/Time</span>
                            {sortField === 'uploaded_at' ? (
                              <span className="text-blue-600 font-bold">{sortDirection === 'asc' ? '↑' : '↓'}</span>
                            ) : (
                              <span className="text-gray-400">↕</span>
                            )}
                          </div>
                        </th>
                        <th 
                          className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 transition-colors select-none"
                          onClick={() => handleSort('user')}
                          title="Click to sort by User/Company"
                        >
                          <div className="flex items-center space-x-2">
                            <span>User / Company</span>
                            {sortField === 'user' ? (
                              <span className="text-blue-600 font-bold">{sortDirection === 'asc' ? '↑' : '↓'}</span>
                            ) : (
                              <span className="text-gray-400">↕</span>
                            )}
                          </div>
                        </th>
                        <th 
                          className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 transition-colors select-none"
                          onClick={() => handleSort('filename')}
                          title="Click to sort by File Name"
                        >
                          <div className="flex items-center space-x-2">
                            <span>File Name</span>
                            {sortField === 'filename' ? (
                              <span className="text-blue-600 font-bold">{sortDirection === 'asc' ? '↑' : '↓'}</span>
                            ) : (
                              <span className="text-gray-400">↕</span>
                            )}
                          </div>
                        </th>
                        <th 
                          className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 transition-colors select-none"
                          onClick={() => handleSort('overall_score')}
                          title="Click to sort by Score"
                        >
                          <div className="flex items-center space-x-2">
                            <span>Score</span>
                            {sortField === 'overall_score' ? (
                              <span className="text-blue-600 font-bold">{sortDirection === 'asc' ? '↑' : '↓'}</span>
                            ) : (
                              <span className="text-gray-400">↕</span>
                            )}
                          </div>
                        </th>
                        <th 
                          className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 transition-colors select-none"
                          onClick={() => handleSort('findings_count')}
                          title="Click to sort by Findings"
                        >
                          <div className="flex items-center space-x-2">
                            <span>Findings</span>
                            {sortField === 'findings_count' ? (
                              <span className="text-blue-600 font-bold">{sortDirection === 'asc' ? '↑' : '↓'}</span>
                            ) : (
                              <span className="text-gray-400">↕</span>
                            )}
                          </div>
                        </th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Standard</th>
                        <th 
                          className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 transition-colors select-none"
                          onClick={() => handleSort('status')}
                          title="Click to sort by Status"
                        >
                          <div className="flex items-center space-x-2">
                            <span>Status</span>
                            {sortField === 'status' ? (
                              <span className="text-blue-600 font-bold">{sortDirection === 'asc' ? '↑' : '↓'}</span>
                            ) : (
                              <span className="text-gray-400">↕</span>
                            )}
                          </div>
                        </th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Action</th>
                      </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                      {sortedReports.length === 0 ? (
                        <tr>
                          <td colSpan={9} className="px-6 py-12 text-center text-gray-500">
                            No reports found
                          </td>
                        </tr>
                      ) : (
                        sortedReports.map((report) => (
                          <tr key={report.id} className="hover:bg-gray-50">
                            <td className="px-6 py-4 whitespace-nowrap text-sm font-mono text-gray-600">
                              #{report.id}
                            </td>
                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                              {new Date(report.uploaded_at).toLocaleString()}
                            </td>
                            <td className="px-6 py-4 whitespace-nowrap">
                              <div className="text-sm font-medium text-gray-900">{report.user.name || report.user.email}</div>
                              {report.user.company && (
                                <div className="text-sm text-gray-500">{report.user.company}</div>
                              )}
                            </td>
                            <td className="px-6 py-4 text-sm text-gray-900">{report.filename}</td>
                            <td className="px-6 py-4 whitespace-nowrap">
                              <span className={`text-sm font-medium ${
                                (report.overall_score || 0) >= 80 ? 'text-green-600' :
                                (report.overall_score || 0) >= 60 ? 'text-yellow-600' : 'text-red-600'
                              }`}>
                                {report.overall_score?.toFixed(1) || 'N/A'}
                              </span>
                            </td>
                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                              {report.findings_count}
                              {report.high_risk_findings > 0 && (
                                <span className="ml-2 text-xs text-red-600">({report.high_risk_findings} high-risk)</span>
                              )}
                            </td>
                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                              {report.report_system || 'N/A'}
                            </td>
                            <td className="px-6 py-4 whitespace-nowrap">
                              <span className={`px-2 py-1 text-xs font-medium rounded-full ${
                                report.status === 'completed' ? 'bg-green-100 text-green-800' :
                                report.status === 'processing' ? 'bg-yellow-100 text-yellow-800' :
                                'bg-red-100 text-red-800'
                              }`}>
                                {report.status}
                              </span>
                            </td>
                            <td className="px-6 py-4 whitespace-nowrap text-sm">
                              <a
                                href={`/admin/reports/${report.id}`}
                                className="text-blue-600 hover:text-blue-800 font-medium"
                              >
                                Open
                              </a>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {activeTab === 'users' && (
            <UsersSection adminToken={adminToken} router={router} />
          )}

          {activeTab === 'credits' && (
            <CreditsBillingSection adminToken={adminToken} />
          )}

          {activeTab === 'system' && (
            <SystemInsightsSection adminToken={adminToken} />
          )}
        </div>
      </div>
    </div>
  )
}

