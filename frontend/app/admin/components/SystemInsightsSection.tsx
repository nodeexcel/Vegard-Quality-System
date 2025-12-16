'use client'

import { useState, useEffect } from 'react'
import axios from 'axios'

interface SystemInsightsSectionProps {
  adminToken: string | null
}

interface SystemStatus {
  timestamp: string
  aws_bedrock: {
    enabled: boolean
    region: string
    status: string
  }
  s3_storage: {
    enabled: boolean
    bucket: string
    status: string
  }
  database: {
    status: string
  }
  failed_reports: number
}

interface Analytics {
  period_days: number
  total_reports: number
  score_distribution: {
    "0-20": number
    "20-40": number
    "40-60": number
    "60-80": number
    "80-100": number
  }
  average_score: number
  most_common_findings: Record<string, number>
  most_common_standards: Record<string, number>
  lowest_score_users: Array<{
    user_id: number
    name: string | null
    email: string
    average_score: number
    report_count: number
  }>
  most_active_users: Array<{
    user_id: number
    name: string | null
    email: string
    report_count: number
  }>
  ns3600_errors?: Record<string, number>
  ns3940_errors?: Record<string, number>
  tg2_tg3_stats?: {
    tg2_count: number
    tg3_count: number
    total_tg2_tg3: number
    misuse_patterns: Record<string, number>
  }
  time_series?: Array<{
    date: string
    report_count: number
    average_score: number
  }>
}

interface ErrorLog {
  report_id: number
  filename: string
  user: {
    id: number | null
    name: string | null
    email: string | null
  }
  uploaded_at: string | null
  error_message: string
  extracted_text_length: number
}

export default function SystemInsightsSection({ adminToken }: SystemInsightsSectionProps) {
  const [activeTab, setActiveTab] = useState<'status' | 'analytics' | 'errors'>('status')
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [errorLogs, setErrorLogs] = useState<ErrorLog[]>([])
  const [loadingStatus, setLoadingStatus] = useState(false)
  const [loadingAnalytics, setLoadingAnalytics] = useState(false)
  const [loadingErrors, setLoadingErrors] = useState(false)
  const [analyticsDays, setAnalyticsDays] = useState(30)
  const [runningTest, setRunningTest] = useState(false)

  useEffect(() => {
    if (adminToken) {
      fetchSystemStatus()
      if (activeTab === 'analytics') {
        fetchAnalytics()
      } else if (activeTab === 'errors') {
        fetchErrorLogs()
      }
    }
  }, [adminToken, analyticsDays, activeTab])

  const fetchErrorLogs = async () => {
    if (!adminToken) return
    setLoadingErrors(true)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const response = await axios.get(`${apiUrl}/api/v1/admin/system/error-logs`, {
        headers: { Authorization: `Bearer ${adminToken}` }
      })
      setErrorLogs(response.data.error_logs || [])
    } catch (error) {
      console.error('Error fetching error logs:', error)
    } finally {
      setLoadingErrors(false)
    }
  }

  const fetchSystemStatus = async () => {
    if (!adminToken) return
    setLoadingStatus(true)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const response = await axios.get(`${apiUrl}/api/v1/admin/system/status`, {
        headers: { Authorization: `Bearer ${adminToken}` }
      })
      setSystemStatus(response.data)
    } catch (error) {
      console.error('Error fetching system status:', error)
    } finally {
      setLoadingStatus(false)
    }
  }

  const fetchAnalytics = async () => {
    if (!adminToken) return
    setLoadingAnalytics(true)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const response = await axios.get(`${apiUrl}/api/v1/admin/analytics?days=${analyticsDays}`, {
        headers: { Authorization: `Bearer ${adminToken}` }
      })
      setAnalytics(response.data)
    } catch (error) {
      console.error('Error fetching analytics:', error)
    } finally {
      setLoadingAnalytics(false)
    }
  }

  const runTestReport = async () => {
    if (!adminToken) return
    setRunningTest(true)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const response = await axios.post(
        `${apiUrl}/api/v1/admin/system/test-report`,
        {},
        {
          headers: { Authorization: `Bearer ${adminToken}` }
        }
      )
      
      if (response.data.status === 'success') {
        alert(`Test report processed successfully!\n\nReport ID: ${response.data.report.id}\nOverall Score: ${response.data.report.overall_score?.toFixed(1) || 'N/A'}\nComponents: ${response.data.report.components_count}\nFindings: ${response.data.report.findings_count}`)
        // Refresh system status to update failed reports count if needed
        fetchSystemStatus()
      } else {
        alert('Test report completed but with unexpected response')
      }
    } catch (error: any) {
      console.error('Error running test report:', error)
      const errorMessage = error.response?.data?.detail || error.message || 'Failed to run test report'
      alert(`Failed to run test report: ${errorMessage}`)
    } finally {
      setRunningTest(false)
    }
  }

  const getStatusColor = (status: string) => {
    if (status === 'operational') return 'text-green-600 bg-green-100'
    if (status === 'disabled' || status === 'not_configured') return 'text-gray-600 bg-gray-100'
    if (status === 'permission_denied') return 'text-orange-600 bg-orange-100'
    if (status.includes('error')) return 'text-red-600 bg-red-100'
    return 'text-yellow-600 bg-yellow-100'
  }

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-4">System & Insights</h2>
        
        {/* Tabs */}
        <div className="flex border-b border-gray-200 mb-6">
          <button
            onClick={() => setActiveTab('status')}
            className={`px-6 py-3 text-sm font-medium transition-colors ${
              activeTab === 'status'
                ? 'text-blue-600 border-b-2 border-blue-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            System Status
          </button>
          <button
            onClick={() => setActiveTab('analytics')}
            className={`px-6 py-3 text-sm font-medium transition-colors ${
              activeTab === 'analytics'
                ? 'text-blue-600 border-b-2 border-blue-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Analytics
          </button>
          <button
            onClick={() => setActiveTab('errors')}
            className={`px-6 py-3 text-sm font-medium transition-colors ${
              activeTab === 'errors'
                ? 'text-blue-600 border-b-2 border-blue-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Error Logs
          </button>
        </div>
      </div>

      {/* System Status Tab */}
      {activeTab === 'status' && (
        <div className="space-y-6">
          {loadingStatus ? (
            <div className="text-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
              <p className="mt-4 text-gray-600">Loading system status...</p>
            </div>
          ) : systemStatus ? (
            <>
              {/* System Components */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 flex flex-col">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">AWS Bedrock</h3>
                  <div className="space-y-2 flex-grow">
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-gray-600">Status:</span>
                      <span className={`px-3 py-1 rounded-full text-xs font-medium ${getStatusColor(systemStatus.aws_bedrock.status)}`}>
                        {systemStatus.aws_bedrock.status}
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-gray-600">Enabled:</span>
                      <span className="text-sm font-medium">{systemStatus.aws_bedrock.enabled ? 'Yes' : 'No'}</span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-gray-600">Region:</span>
                      <span className="text-sm font-medium">{systemStatus.aws_bedrock.region || 'N/A'}</span>
                    </div>
                  </div>
                </div>

                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 flex flex-col">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">S3 Storage</h3>
                  <div className="space-y-2 flex-grow">
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-gray-600">Status:</span>
                      <span className={`px-3 py-1 rounded-full text-xs font-medium ${getStatusColor(systemStatus.s3_storage.status)}`}>
                        {systemStatus.s3_storage.status}
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-gray-600">Enabled:</span>
                      <span className="text-sm font-medium">{systemStatus.s3_storage.enabled ? 'Yes' : 'No'}</span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-gray-600">Bucket:</span>
                      <span className="text-sm font-medium">{systemStatus.s3_storage.bucket || 'N/A'}</span>
                    </div>
                  </div>
                </div>

                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 flex flex-col">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">Database</h3>
                  <div className="space-y-2 flex-grow">
                    <div className="flex justify-between items-center">
                      <span className="text-sm text-gray-600">Status:</span>
                      <span className={`px-3 py-1 rounded-full text-xs font-medium ${getStatusColor(systemStatus.database.status)}`}>
                        {systemStatus.database.status}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Failed Reports */}
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-semibold text-gray-900">Failed Analyses</h3>
                  <span className="text-2xl font-bold text-red-600">{systemStatus.failed_reports}</span>
                </div>
                <p className="text-sm text-gray-600">Total reports that failed during analysis</p>
              </div>

              {/* Test Report Button */}
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Test Report</h3>
                <button
                  onClick={runTestReport}
                  disabled={runningTest}
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {runningTest ? 'Running...' : 'Run Test Report'}
                </button>
                <p className="mt-2 text-sm text-gray-600">Test the analysis pipeline with a sample report</p>
              </div>
            </>
          ) : null}
        </div>
      )}

      {/* Analytics Tab */}
      {activeTab === 'analytics' && (
        <div className="space-y-6">
          {/* Period Selector */}
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Analytics Period (days)
            </label>
            <input
              type="number"
              value={analyticsDays}
              onChange={(e) => setAnalyticsDays(parseInt(e.target.value) || 30)}
              min="1"
              max="365"
              className="w-32 px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {loadingAnalytics ? (
            <div className="text-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
              <p className="mt-4 text-gray-600">Loading analytics...</p>
            </div>
          ) : analytics ? (
            <>
              {/* Overview Stats */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                  <h3 className="text-sm font-medium text-gray-600 mb-2">Total Reports</h3>
                  <p className="text-3xl font-bold text-gray-900">{analytics.total_reports}</p>
                  <p className="text-xs text-gray-500 mt-1">Last {analytics.period_days} days</p>
                </div>
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                  <h3 className="text-sm font-medium text-gray-600 mb-2">Average Score</h3>
                  <p className="text-3xl font-bold text-gray-900">{analytics.average_score.toFixed(1)}</p>
                  <p className="text-xs text-gray-500 mt-1">Out of 100</p>
                </div>
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                  <h3 className="text-sm font-medium text-gray-600 mb-2">Period</h3>
                  <p className="text-3xl font-bold text-gray-900">{analytics.period_days}</p>
                  <p className="text-xs text-gray-500 mt-1">Days</p>
                </div>
              </div>

              {/* Score Distribution */}
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Score Distribution</h3>
                <div className="space-y-3">
                  {Object.entries(analytics.score_distribution).map(([range, count]) => (
                    <div key={range}>
                      <div className="flex justify-between mb-1">
                        <span className="text-sm font-medium text-gray-700">{range}</span>
                        <span className="text-sm text-gray-600">{count} reports</span>
                      </div>
                      <div className="w-full bg-gray-200 rounded-full h-2">
                        <div
                          className="bg-blue-600 h-2 rounded-full"
                          style={{ width: `${(count / analytics.total_reports) * 100}%` }}
                        ></div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Most Common Findings */}
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Most Common Findings</h3>
                <div className="space-y-2">
                  {Object.entries(analytics.most_common_findings).slice(0, 10).map(([finding, count]) => (
                    <div key={finding} className="flex justify-between items-center py-2 border-b border-gray-100">
                      <span className="text-sm text-gray-700">{finding}</span>
                      <span className="px-3 py-1 bg-blue-100 text-blue-800 rounded-full text-xs font-medium">
                        {count}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Most Common Standards */}
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Most Common Standard References</h3>
                <div className="space-y-2">
                  {Object.entries(analytics.most_common_standards).slice(0, 10).map(([standard, count]) => (
                    <div key={standard} className="flex justify-between items-center py-2 border-b border-gray-100">
                      <span className="text-sm text-gray-700">{standard}</span>
                      <span className="px-3 py-1 bg-green-100 text-green-800 rounded-full text-xs font-medium">
                        {count}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Lowest Score Users */}
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Users with Lowest Average Score</h3>
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Email</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Avg Score</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Reports</th>
                      </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                      {analytics.lowest_score_users.map((user) => (
                        <tr key={user.user_id}>
                          <td className="px-4 py-3 text-sm text-gray-900">{user.name || 'N/A'}</td>
                          <td className="px-4 py-3 text-sm text-gray-600">{user.email}</td>
                          <td className="px-4 py-3 text-sm font-medium text-red-600">{user.average_score.toFixed(1)}</td>
                          <td className="px-4 py-3 text-sm text-gray-600">{user.report_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Most Active Users */}
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Most Active Users</h3>
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Email</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Reports</th>
                      </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                      {analytics.most_active_users.map((user) => (
                        <tr key={user.user_id}>
                          <td className="px-4 py-3 text-sm text-gray-900">{user.name || 'N/A'}</td>
                          <td className="px-4 py-3 text-sm text-gray-600">{user.email}</td>
                          <td className="px-4 py-3 text-sm font-medium text-blue-600">{user.report_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* NS3600 Errors */}
              {analytics.ns3600_errors && Object.keys(analytics.ns3600_errors).length > 0 && (
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">NS 3600 Specific Errors</h3>
                  <div className="space-y-2">
                    {Object.entries(analytics.ns3600_errors).slice(0, 10).map(([error, count]) => (
                      <div key={error} className="flex justify-between items-center py-2 border-b border-gray-100">
                        <span className="text-sm text-gray-700">{error}</span>
                        <span className="px-3 py-1 bg-orange-100 text-orange-800 rounded-full text-xs font-medium">
                          {count}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* NS3940 Errors */}
              {analytics.ns3940_errors && Object.keys(analytics.ns3940_errors).length > 0 && (
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">NS 3940 Specific Errors</h3>
                  <div className="space-y-2">
                    {Object.entries(analytics.ns3940_errors).slice(0, 10).map(([error, count]) => (
                      <div key={error} className="flex justify-between items-center py-2 border-b border-gray-100">
                        <span className="text-sm text-gray-700">{error}</span>
                        <span className="px-3 py-1 bg-purple-100 text-purple-800 rounded-full text-xs font-medium">
                          {count}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* TG2/TG3 Misuse Tracking */}
              {analytics.tg2_tg3_stats && (
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">TG2/TG3 Statistics</h3>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                    <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                      <h4 className="text-sm font-medium text-yellow-900 mb-1">TG2 Count</h4>
                      <p className="text-2xl font-bold text-yellow-700">{analytics.tg2_tg3_stats.tg2_count}</p>
                    </div>
                    <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                      <h4 className="text-sm font-medium text-red-900 mb-1">TG3 Count</h4>
                      <p className="text-2xl font-bold text-red-700">{analytics.tg2_tg3_stats.tg3_count}</p>
                    </div>
                    <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                      <h4 className="text-sm font-medium text-gray-900 mb-1">Total TG2/TG3</h4>
                      <p className="text-2xl font-bold text-gray-700">{analytics.tg2_tg3_stats.total_tg2_tg3}</p>
                    </div>
                  </div>
                  {Object.keys(analytics.tg2_tg3_stats.misuse_patterns).length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold text-gray-700 mb-3">Misuse Patterns</h4>
                      <div className="space-y-2">
                        {Object.entries(analytics.tg2_tg3_stats.misuse_patterns).map(([pattern, count]) => (
                          <div key={pattern} className="flex justify-between items-center py-2 border-b border-gray-100">
                            <span className="text-sm text-gray-700">{pattern}</span>
                            <span className="px-3 py-1 bg-red-100 text-red-800 rounded-full text-xs font-medium">
                              {count}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Time-Series Trends */}
              {analytics.time_series && analytics.time_series.length > 0 && (
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">Trends Over Time</h3>
                  <div className="space-y-6">
                    {/* Report Count Trend */}
                    <div>
                      <h4 className="text-sm font-semibold text-gray-700 mb-3">Daily Report Count</h4>
                      <div className="h-48 flex items-end space-x-1 overflow-x-auto">
                        {analytics.time_series.map((item, idx) => {
                          const maxCount = Math.max(...analytics.time_series!.map(ts => ts.report_count), 1)
                          return (
                            <div key={idx} className="flex-1 flex flex-col items-center min-w-[30px]">
                              <div
                                className="w-full bg-blue-500 hover:bg-blue-600 rounded-t transition-colors"
                                style={{ height: `${(item.report_count / maxCount) * 100}%` }}
                                title={`${item.date}: ${item.report_count} reports`}
                              />
                              <span className="text-xs text-gray-500 mt-1 transform -rotate-45 origin-top-left whitespace-nowrap">
                                {new Date(item.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                              </span>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                    {/* Average Score Trend */}
                    <div>
                      <h4 className="text-sm font-semibold text-gray-700 mb-3">Daily Average Score</h4>
                      <div className="h-48 flex items-end space-x-1 overflow-x-auto">
                        {analytics.time_series.map((item, idx) => (
                          <div key={idx} className="flex-1 flex flex-col items-center min-w-[30px]">
                            <div
                              className={`w-full rounded-t transition-colors ${
                                item.average_score >= 80 ? 'bg-green-500 hover:bg-green-600' :
                                item.average_score >= 60 ? 'bg-yellow-500 hover:bg-yellow-600' :
                                'bg-red-500 hover:bg-red-600'
                              }`}
                              style={{ height: `${item.average_score}%` }}
                              title={`${item.date}: ${item.average_score.toFixed(1)}`}
                            />
                            <span className="text-xs text-gray-500 mt-1 transform -rotate-45 origin-top-left whitespace-nowrap">
                              {new Date(item.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </>
          ) : null}
        </div>
      )}

      {/* Error Logs Tab */}
      {activeTab === 'errors' && (
        <div className="space-y-6">
          {loadingErrors ? (
            <div className="text-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
              <p className="mt-4 text-gray-600">Loading error logs...</p>
            </div>
          ) : (
            <>
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Failed Reports Error Logs</h3>
                {errorLogs.length === 0 ? (
                  <p className="text-gray-500">No error logs found</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200">
                      <thead className="bg-gray-50">
                        <tr>
                          <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Report ID</th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Filename</th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Date</th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Error Message</th>
                          <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Text Length</th>
                        </tr>
                      </thead>
                      <tbody className="bg-white divide-y divide-gray-200">
                        {errorLogs.map((log) => (
                          <tr key={log.report_id} className="hover:bg-gray-50">
                            <td className="px-4 py-3 text-sm font-mono text-gray-600">
                              <a href={`/admin/reports/${log.report_id}`} className="text-blue-600 hover:text-blue-800">
                                #{log.report_id}
                              </a>
                            </td>
                            <td className="px-4 py-3 text-sm text-gray-900">{log.filename}</td>
                            <td className="px-4 py-3 text-sm text-gray-600">{log.user.name || log.user.email || 'N/A'}</td>
                            <td className="px-4 py-3 text-sm text-gray-600">
                              {log.uploaded_at ? new Date(log.uploaded_at).toLocaleString() : 'N/A'}
                            </td>
                            <td className="px-4 py-3 text-sm text-red-600 max-w-md" title={log.error_message}>
                              <div className="truncate">{log.error_message}</div>
                            </td>
                            <td className="px-4 py-3 text-sm text-gray-600">{log.extracted_text_length} chars</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

