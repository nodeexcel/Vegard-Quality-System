'use client'

import { useState, useEffect } from 'react'
import { useRouter, useParams } from 'next/navigation'
import axios from 'axios'

interface UserDetail {
  id: number
  name: string | null
  email: string
  phone: string | null
  company: string | null
  picture: string | null
  credits: number
  status: string
  created_at: string | null
  last_login: string | null
  total_reports: number
  average_score: number
  score_history: Array<{ date: string; score: number }>
  credit_transactions: Array<{
    id: number
    amount: number
    transaction_type: string
    description: string | null
    created_at: string
  }>
  reports: Array<{
    id: number
    filename: string
    uploaded_at: string
    overall_score: number | null
    status: string
  }>
}

export default function AdminUserDetail() {
  const router = useRouter()
  const params = useParams()
  const userId = params.id as string
  const [user, setUser] = useState<UserDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creditAmount, setCreditAmount] = useState('')
  const [creditDescription, setCreditDescription] = useState('')
  const [showCreditModal, setShowCreditModal] = useState(false)
  const [actionLoading, setActionLoading] = useState(false)

  useEffect(() => {
    const adminToken = localStorage.getItem('admin_token')
    if (!adminToken) {
      router.push('/admin/login')
      return
    }
    fetchUser()
  }, [userId, router])

  const fetchUser = async () => {
    setLoading(true)
    setError('')
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const adminToken = localStorage.getItem('admin_token')
      const response = await axios.get(
        `${apiUrl}/api/v1/admin/users/${userId}`,
        {
          headers: { Authorization: `Bearer ${adminToken}` }
        }
      )
      setUser(response.data)
    } catch (err: any) {
      console.error('Error fetching user:', err)
      if (err.response?.status === 401 || err.response?.status === 403) {
        localStorage.removeItem('admin_token')
        router.push('/admin/login')
      } else {
        setError(err.response?.data?.detail || 'Failed to load user')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleCreditAction = async (amount: number) => {
    if (!user) return
    
    setActionLoading(true)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const adminToken = localStorage.getItem('admin_token')
      const params = new URLSearchParams()
      params.append('amount', amount.toString())
      if (creditDescription) params.append('description', creditDescription)

      await axios.post(
        `${apiUrl}/api/v1/admin/users/${userId}/credits?${params.toString()}`,
        {},
        {
          headers: { Authorization: `Bearer ${adminToken}` }
        }
      )
      
      setShowCreditModal(false)
      setCreditAmount('')
      setCreditDescription('')
      fetchUser() // Refresh user data
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to update credits')
    } finally {
      setActionLoading(false)
    }
  }

  const handleDisableUser = async () => {
    if (!user || !confirm(`Are you sure you want to ${user.status === 'active' ? 'disable' : 'enable'} this user?`)) return
    
    setActionLoading(true)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const adminToken = localStorage.getItem('admin_token')
      const endpoint = user.status === 'active' ? 'disable' : 'enable'

      await axios.post(
        `${apiUrl}/api/v1/admin/users/${userId}/${endpoint}`,
        {},
        {
          headers: { Authorization: `Bearer ${adminToken}` }
        }
      )
      
      fetchUser() // Refresh user data
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to update user status')
    } finally {
      setActionLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading user...</p>
        </div>
      </div>
    )
  }

  if (error || !user) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error || 'User not found'}</p>
          <button
            onClick={() => router.push('/admin')}
            className="text-blue-600 hover:text-blue-800"
          >
            ← Back to Admin
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
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                </div>
                <div>
                  <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
                    User Management
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
        {/* User Profile Card */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 mb-6">
          <div className="flex items-start justify-between">
            <div className="flex items-center space-x-4">
              {user.picture ? (
                <img src={user.picture} alt={user.name || user.email} className="w-16 h-16 rounded-full" />
              ) : (
                <div className="w-16 h-16 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-full flex items-center justify-center text-white text-2xl font-bold">
                  {(user.name || user.email)[0].toUpperCase()}
                </div>
              )}
              <div>
                <h2 className="text-2xl font-bold text-gray-900">{user.name || 'No Name'}</h2>
                <p className="text-gray-600">{user.email}</p>
                {user.company && <p className="text-sm text-gray-500">{user.company}</p>}
                {user.phone && <p className="text-sm text-gray-500">Phone: {user.phone}</p>}
              </div>
            </div>
            <div className="flex items-center space-x-2">
              <span className={`px-3 py-1 text-sm font-medium rounded-full ${
                user.status === 'active' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
              }`}>
                {user.status}
              </span>
              <button
                onClick={handleDisableUser}
                disabled={actionLoading}
                className={`px-4 py-2 text-sm font-medium rounded-lg ${
                  user.status === 'active'
                    ? 'bg-red-600 text-white hover:bg-red-700'
                    : 'bg-green-600 text-white hover:bg-green-700'
                } disabled:opacity-50`}
              >
                {user.status === 'active' ? 'Disable User' : 'Enable User'}
              </button>
            </div>
          </div>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
            <h3 className="text-sm font-medium text-gray-500 mb-1">Total Reports</h3>
            <p className="text-2xl font-bold text-gray-900">{user.total_reports}</p>
          </div>
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
            <h3 className="text-sm font-medium text-gray-500 mb-1">Average Score</h3>
            <p className="text-2xl font-bold text-gray-900">{user.average_score.toFixed(1)}</p>
          </div>
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
            <h3 className="text-sm font-medium text-gray-500 mb-1">Credits</h3>
            <p className="text-2xl font-bold text-gray-900">{user.credits}</p>
          </div>
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
            <h3 className="text-sm font-medium text-gray-500 mb-1">Registration</h3>
            <p className="text-sm font-medium text-gray-900">
              {user.created_at ? new Date(user.created_at).toLocaleDateString() : 'N/A'}
            </p>
            <p className="text-xs text-gray-500 mt-1">
              Last login: {user.last_login ? new Date(user.last_login).toLocaleDateString() : 'Never'}
            </p>
          </div>
        </div>

        {/* Quick Credit Actions */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-gray-900">Quick Credit Actions</h3>
            <button
              onClick={() => setShowCreditModal(true)}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium"
            >
              Custom Amount
            </button>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <button
              onClick={() => handleCreditAction(10)}
              disabled={actionLoading}
              className="px-4 py-3 bg-green-50 border border-green-200 rounded-lg hover:bg-green-100 text-green-700 font-medium disabled:opacity-50"
            >
              +10 Credits
            </button>
            <button
              onClick={() => handleCreditAction(20)}
              disabled={actionLoading}
              className="px-4 py-3 bg-green-50 border border-green-200 rounded-lg hover:bg-green-100 text-green-700 font-medium disabled:opacity-50"
            >
              +20 Credits
            </button>
            <button
              onClick={() => handleCreditAction(100)}
              disabled={actionLoading}
              className="px-4 py-3 bg-green-50 border border-green-200 rounded-lg hover:bg-green-100 text-green-700 font-medium disabled:opacity-50"
            >
              +100 Credits
            </button>
          </div>
        </div>

        {/* Credit Transactions */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 mb-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Credit Transaction Log</h3>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Date</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Amount</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Description</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {user.credit_transactions.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-6 py-8 text-center text-gray-500">
                      No transactions found
                    </td>
                  </tr>
                ) : (
                  user.credit_transactions.map((transaction) => (
                    <tr key={transaction.id} className="hover:bg-gray-50">
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        {new Date(transaction.created_at).toLocaleString()}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        {transaction.transaction_type}
                      </td>
                      <td className={`px-6 py-4 whitespace-nowrap text-sm font-medium ${
                        transaction.amount > 0 ? 'text-green-600' : 'text-red-600'
                      }`}>
                        {transaction.amount > 0 ? '+' : ''}{transaction.amount}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500">
                        {transaction.description || 'N/A'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Score History */}
        {user.score_history.length > 0 && (
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 mb-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Score History (Last 30 Reports)</h3>
            <div className="h-64 flex items-end space-x-1">
              {user.score_history.map((item, idx) => (
                <div
                  key={idx}
                  className="flex-1 bg-blue-500 hover:bg-blue-600 rounded-t"
                  style={{ height: `${(item.score / 100) * 100}%` }}
                  title={`${new Date(item.date).toLocaleDateString()}: ${item.score.toFixed(1)}`}
                />
              ))}
            </div>
            <div className="mt-4 text-xs text-gray-500 text-center">
              Score trend over time
            </div>
          </div>
        )}

        {/* Report History */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Report History</h3>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Date</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Filename</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Score</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Action</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {user.reports.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-6 py-8 text-center text-gray-500">
                      No reports found
                    </td>
                  </tr>
                ) : (
                  user.reports.map((report) => (
                    <tr key={report.id} className="hover:bg-gray-50">
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        {new Date(report.uploaded_at).toLocaleDateString()}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-900">{report.filename}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                        {report.overall_score?.toFixed(1) || 'N/A'}
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
                          View
                        </a>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Credit Modal */}
      {showCreditModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Add/Remove Credits</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Amount</label>
                <input
                  type="number"
                  value={creditAmount}
                  onChange={(e) => setCreditAmount(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                  placeholder="Positive to add, negative to remove"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Description (Optional)</label>
                <input
                  type="text"
                  value={creditDescription}
                  onChange={(e) => setCreditDescription(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                  placeholder="Reason for credit adjustment"
                />
              </div>
              <div className="flex space-x-3">
                <button
                  onClick={() => {
                    const amount = parseInt(creditAmount)
                    if (!isNaN(amount)) {
                      handleCreditAction(amount)
                    }
                  }}
                  disabled={actionLoading || !creditAmount}
                  className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                  Apply
                </button>
                <button
                  onClick={() => {
                    setShowCreditModal(false)
                    setCreditAmount('')
                    setCreditDescription('')
                  }}
                  className="flex-1 px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

