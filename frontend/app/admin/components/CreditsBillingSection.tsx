'use client'

import { useState, useEffect } from 'react'
import axios from 'axios'

interface UserCredit {
  id: number
  name: string | null
  email: string
  company: string | null
  credits: number
}

interface CreditTransaction {
  id: number
  user: {
    id: number
    name: string | null
    email: string
  }
  amount: number
  transaction_type: string
  description: string | null
  report_id: number | null
  created_at: string
}

interface CreditsBillingSectionProps {
  adminToken: string | null
}

export default function CreditsBillingSection({ adminToken }: CreditsBillingSectionProps) {
  const [activeTab, setActiveTab] = useState<'credits' | 'transactions'>('credits')
  const [users, setUsers] = useState<UserCredit[]>([])
  const [transactions, setTransactions] = useState<CreditTransaction[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingTransactions, setLoadingTransactions] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedUser, setSelectedUser] = useState<UserCredit | null>(null)
  const [creditAmount, setCreditAmount] = useState<number>(10)
  const [actionType, setActionType] = useState<'add' | 'remove'>('add')
  const [description, setDescription] = useState('')
  const [processing, setProcessing] = useState(false)
  const [totalCredits, setTotalCredits] = useState(0)
  const [showUserSelection, setShowUserSelection] = useState(false)
  const [quickAddAmount, setQuickAddAmount] = useState<number | null>(null)

  useEffect(() => {
    if (adminToken) {
      if (activeTab === 'credits') {
        fetchUsers()
      } else {
        fetchTransactions()
      }
    }
  }, [adminToken, activeTab, searchQuery])

  const fetchUsers = async () => {
    if (!adminToken) return
    setLoading(true)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const params = searchQuery ? { search: searchQuery } : {}
      const response = await axios.get(`${apiUrl}/api/v1/admin/credits`, {
        headers: { Authorization: `Bearer ${adminToken}` },
        params
      })
      setUsers(response.data.users || [])
      setTotalCredits(response.data.total_credits || 0)
    } catch (error) {
      console.error('Error fetching users:', error)
    } finally {
      setLoading(false)
    }
  }

  const fetchTransactions = async () => {
    if (!adminToken) return
    setLoadingTransactions(true)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const response = await axios.get(`${apiUrl}/api/v1/admin/credits/transactions`, {
        headers: { Authorization: `Bearer ${adminToken}` },
        params: { limit: 100 }
      })
      setTransactions(response.data.transactions || [])
    } catch (error) {
      console.error('Error fetching transactions:', error)
    } finally {
      setLoadingTransactions(false)
    }
  }

  const handleManageCredits = async () => {
    if (!adminToken || !selectedUser) return
    
    const amount = actionType === 'add' ? creditAmount : -creditAmount
    const desc = description || `Admin ${actionType === 'add' ? 'added' : 'removed'} ${Math.abs(amount)} credits`

    setProcessing(true)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const params = new URLSearchParams()
      params.append('amount', amount.toString())
      if (desc) params.append('description', desc)
      
      await axios.post(
        `${apiUrl}/api/v1/admin/users/${selectedUser.id}/credits?${params.toString()}`,
        {},
        {
          headers: { Authorization: `Bearer ${adminToken}` }
        }
      )
      
      // Refresh data
      await fetchUsers()
      if (activeTab === 'transactions') {
        await fetchTransactions()
      }
      
      // Reset form
      setSelectedUser(null)
      setCreditAmount(10)
      setActionType('add')
      setDescription('')
      
      alert(`Successfully ${actionType === 'add' ? 'added' : 'removed'} ${Math.abs(amount)} credits`)
    } catch (error: any) {
      console.error('Error managing credits:', error)
      alert(error.response?.data?.detail || 'Failed to manage credits')
    } finally {
      setProcessing(false)
    }
  }

  const getTransactionTypeColor = (type: string) => {
    if (type.includes('purchase') || type.includes('add')) return 'text-green-600 bg-green-100'
    if (type.includes('usage') || type.includes('remove')) return 'text-red-600 bg-red-100'
    if (type.includes('refund')) return 'text-blue-600 bg-blue-100'
    return 'text-gray-600 bg-gray-100'
  }

  const formatDate = (dateString: string) => {
    const date = new Date(dateString)
    return date.toLocaleDateString('nb-NO', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-4">Credits & Billing</h2>
        
        {/* Tabs */}
        <div className="flex border-b border-gray-200 mb-6">
          <button
            onClick={() => setActiveTab('credits')}
            className={`px-6 py-3 text-sm font-medium transition-colors ${
              activeTab === 'credits'
                ? 'text-blue-600 border-b-2 border-blue-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Credit Management
          </button>
          <button
            onClick={() => setActiveTab('transactions')}
            className={`px-6 py-3 text-sm font-medium transition-colors ${
              activeTab === 'transactions'
                ? 'text-blue-600 border-b-2 border-blue-600'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Transaction Log
          </button>
        </div>
      </div>

      {/* Credit Management Tab */}
      {activeTab === 'credits' && (
        <div>
          {/* Summary Card */}
          <div className="bg-gradient-to-r from-blue-500 to-indigo-600 rounded-lg p-6 text-white mb-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-blue-100 text-sm mb-1">Total Credits in System</p>
                <p className="text-3xl font-bold">{totalCredits.toLocaleString()}</p>
              </div>
              <div className="text-right">
                <p className="text-blue-100 text-sm mb-1">Active Users</p>
                <p className="text-3xl font-bold">{users.length}</p>
              </div>
            </div>
          </div>

          {/* Search and Quick Actions */}
          <div className="bg-white rounded-lg shadow p-4 mb-6">
            <div className="flex items-center space-x-4">
              <div className="flex-1">
                <input
                  type="text"
                  placeholder="Search by name or email..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>
              <div className="flex space-x-2">
                <button
                  onClick={() => {
                    setQuickAddAmount(10)
                    setShowUserSelection(true)
                  }}
                  className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
                >
                  Quick Add: 10
                </button>
                <button
                  onClick={() => {
                    setQuickAddAmount(20)
                    setShowUserSelection(true)
                  }}
                  className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
                >
                  Quick Add: 20
                </button>
                <button
                  onClick={() => {
                    setQuickAddAmount(100)
                    setShowUserSelection(true)
                  }}
                  className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
                >
                  Quick Add: 100
                </button>
              </div>
            </div>
          </div>

          {/* User Selection Modal for Quick Add */}
          {showUserSelection && quickAddAmount && (
            <div className="fixed inset-0 bg-gray-600 bg-opacity-75 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg shadow-xl p-6 max-w-2xl w-full mx-4 max-h-[80vh] overflow-hidden flex flex-col">
                <h3 className="text-xl font-bold text-gray-900 mb-4">
                  Select User to Add {quickAddAmount} Credits
                </h3>
                <div className="flex-1 overflow-y-auto mb-4">
                  <div className="space-y-2">
                    {users.length === 0 ? (
                      <p className="text-center text-gray-500 py-8">No users found</p>
                    ) : (
                      users.map((user) => (
                        <button
                          key={user.id}
                          onClick={() => {
                            setSelectedUser(user)
                            setCreditAmount(quickAddAmount)
                            setActionType('add')
                            setShowUserSelection(false)
                            setQuickAddAmount(null)
                          }}
                          className="w-full text-left p-4 border border-gray-200 rounded-lg hover:bg-blue-50 hover:border-blue-300 transition-colors"
                        >
                          <div className="flex items-center justify-between">
                            <div>
                              <p className="font-medium text-gray-900">
                                {user.name || 'N/A'} ({user.email})
                              </p>
                              {user.company && (
                                <p className="text-sm text-gray-500">{user.company}</p>
                              )}
                            </div>
                            <div className="text-right">
                              <p className="text-sm text-gray-500">Current:</p>
                              <p className="font-semibold text-gray-900">{user.credits} credits</p>
                            </div>
                          </div>
                        </button>
                      ))
                    )}
                  </div>
                </div>
                <div className="flex justify-end">
                  <button
                    onClick={() => {
                      setShowUserSelection(false)
                      setQuickAddAmount(null)
                    }}
                    className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Manage Credits Modal */}
          {selectedUser && !showUserSelection && (
            <div className="fixed inset-0 bg-gray-600 bg-opacity-75 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg shadow-xl p-6 max-w-md w-full mx-4">
                <h3 className="text-xl font-bold text-gray-900 mb-4">
                  {actionType === 'add' ? 'Add' : 'Remove'} Credits
                </h3>
                <div className="mb-4">
                  <p className="text-sm text-gray-600 mb-1">User</p>
                  <p className="font-medium">{selectedUser.name || 'N/A'} ({selectedUser.email})</p>
                  <p className="text-sm text-gray-500">Current balance: {selectedUser.credits} credits</p>
                </div>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Action
                  </label>
                  <div className="flex space-x-4">
                    <button
                      onClick={() => setActionType('add')}
                      className={`flex-1 px-4 py-2 rounded-lg transition-colors ${
                        actionType === 'add'
                          ? 'bg-green-600 text-white'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      }`}
                    >
                      Add Credits
                    </button>
                    <button
                      onClick={() => setActionType('remove')}
                      className={`flex-1 px-4 py-2 rounded-lg transition-colors ${
                        actionType === 'remove'
                          ? 'bg-red-600 text-white'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      }`}
                    >
                      Remove Credits
                    </button>
                  </div>
                </div>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Amount
                  </label>
                  <div className="flex space-x-2 mb-2">
                    {[10, 20, 100].map((amt) => (
                      <button
                        key={amt}
                        onClick={() => setCreditAmount(amt)}
                        className={`px-4 py-2 rounded-lg transition-colors ${
                          creditAmount === amt
                            ? 'bg-blue-600 text-white'
                            : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                        }`}
                      >
                        {amt}
                      </button>
                    ))}
                  </div>
                  <input
                    type="number"
                    min="1"
                    value={creditAmount}
                    onChange={(e) => setCreditAmount(parseInt(e.target.value) || 0)}
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                </div>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Description (optional)
                  </label>
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Add a note for this transaction..."
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    rows={3}
                  />
                </div>
                <div className="flex space-x-4">
                  <button
                    onClick={() => {
                      setSelectedUser(null)
                      setDescription('')
                    }}
                    className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors"
                    disabled={processing}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleManageCredits}
                    disabled={processing || creditAmount <= 0}
                    className={`flex-1 px-4 py-2 rounded-lg text-white transition-colors ${
                      actionType === 'add'
                        ? 'bg-green-600 hover:bg-green-700'
                        : 'bg-red-600 hover:bg-red-700'
                    } disabled:opacity-50 disabled:cursor-not-allowed`}
                  >
                    {processing ? 'Processing...' : `${actionType === 'add' ? 'Add' : 'Remove'} Credits`}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Users Table */}
          {loading ? (
            <div className="text-center py-12">
              <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
              <p className="mt-4 text-gray-600">Loading users...</p>
            </div>
          ) : (
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      User
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Company
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Credits
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {users.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-6 py-8 text-center text-gray-500">
                        No users found
                      </td>
                    </tr>
                  ) : (
                    users.map((user) => (
                      <tr key={user.id} className="hover:bg-gray-50">
                        <td className="px-6 py-4 whitespace-nowrap">
                          <div>
                            <div className="text-sm font-medium text-gray-900">
                              {user.name || 'N/A'}
                            </div>
                            <div className="text-sm text-gray-500">{user.email}</div>
                          </div>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <div className="text-sm text-gray-900">{user.company || 'N/A'}</div>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <span className={`px-3 py-1 inline-flex text-sm leading-5 font-semibold rounded-full ${
                            user.credits > 100
                              ? 'bg-green-100 text-green-800'
                              : user.credits > 20
                              ? 'bg-yellow-100 text-yellow-800'
                              : 'bg-red-100 text-red-800'
                          }`}>
                            {user.credits}
                          </span>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                          <button
                            onClick={() => {
                              setSelectedUser(user)
                              setActionType('add')
                              setCreditAmount(10)
                            }}
                            className="text-green-600 hover:text-green-900 mr-4"
                          >
                            Add
                          </button>
                          <button
                            onClick={() => {
                              setSelectedUser(user)
                              setActionType('remove')
                              setCreditAmount(10)
                            }}
                            className="text-red-600 hover:text-red-900"
                          >
                            Remove
                          </button>
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

      {/* Transaction Log Tab */}
      {activeTab === 'transactions' && (
        <div>
          {loadingTransactions ? (
            <div className="text-center py-12">
              <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
              <p className="mt-4 text-gray-600">Loading transactions...</p>
            </div>
          ) : (
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Date
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      User
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Type
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Amount
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Description
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {transactions.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-6 py-8 text-center text-gray-500">
                        No transactions found
                      </td>
                    </tr>
                  ) : (
                    transactions.map((transaction) => (
                      <tr key={transaction.id} className="hover:bg-gray-50">
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          {formatDate(transaction.created_at)}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <div>
                            <div className="text-sm font-medium text-gray-900">
                              {transaction.user.name || 'N/A'}
                            </div>
                            <div className="text-sm text-gray-500">{transaction.user.email}</div>
                          </div>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <span className={`px-2 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${getTransactionTypeColor(transaction.transaction_type)}`}>
                            {transaction.transaction_type.replace(/_/g, ' ')}
                          </span>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <span className={`text-sm font-medium ${
                            transaction.amount > 0 ? 'text-green-600' : 'text-red-600'
                          }`}>
                            {transaction.amount > 0 ? '+' : ''}{transaction.amount}
                          </span>
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
          )}
        </div>
      )}
    </div>
  )
}

