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
}

export default function SystemInsightsSection({ adminToken }: SystemInsightsSectionProps) {
  const [activeTab, setActiveTab] = useState<'status' | 'analytics'>('status')
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [loadingStatus, setLoadingStatus] = useState(false)
  const [loadingAnalytics, setLoadingAnalytics] = useState(false)
  const [analyticsDays, setAnalyticsDays] = useState(30)
  const [runningTest, setRunningTest] = useState(false)

  useEffect(() => {
    if (adminToken) {
      fetchSystemStatus()
      fetchAnalytics()
    }
  }, [adminToken, analyticsDays])

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
            </>
          ) : null}
        </div>
      )}
    </div>
  )
}

