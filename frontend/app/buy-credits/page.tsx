'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '../contexts/AuthContext'
import { loadStripe } from '@stripe/stripe-js'
import {
  Elements,
  PaymentElement,
  useStripe,
  useElements
} from '@stripe/react-stripe-js'

// Initialize Stripe
let stripePromise: Promise<any> | null = null

const getStripe = () => {
  if (!stripePromise) {
    stripePromise = loadStripe(process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY || '')
  }
  return stripePromise
}

interface CreditPackage {
  id: number
  name: string
  credits: number
  price_nok: number
  price_ore: number
  price_per_credit: number
  reports: number
}

interface PaymentFormProps {
  packageId?: number
  customAmount?: number
  credits: number
  onSuccess: () => void
  onCancel: () => void
}

function PaymentForm({ packageId, customAmount, credits, onSuccess, onCancel }: PaymentFormProps) {
  const stripe = useStripe()
  const elements = useElements()
  const [error, setError] = useState<string | null>(null)
  const [processing, setProcessing] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!stripe || !elements) {
      return
    }

    setProcessing(true)
    setError(null)

    const { error: submitError } = await elements.submit()
    if (submitError) {
      setError(submitError.message || 'An error occurred')
      setProcessing(false)
      return
    }

    // Confirm payment - clientSecret is already in Elements options
    const { error: confirmError } = await stripe.confirmPayment({
      elements,
      confirmParams: {
        return_url: `${window.location.origin}/buy-credits?success=true`,
      },
    })

    if (confirmError) {
      setError(confirmError.message || 'Payment failed')
      setProcessing(false)
    } else {
      // Payment will redirect, but just in case:
      onSuccess()
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <PaymentElement />
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
          {error}
        </div>
      )}
      <div className="flex space-x-4">
        <button
          type="button"
          onClick={onCancel}
          className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors"
          disabled={processing}
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={!stripe || processing}
          className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {processing ? 'Processing...' : `Pay ${customAmount ? customAmount.toFixed(2) : ''} NOK`}
        </button>
      </div>
    </form>
  )
}

export default function BuyCreditsPage() {
  const router = useRouter()
  const { user, token, refreshUser, loading: authLoading } = useAuth()
  const [packages, setPackages] = useState<CreditPackage[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedPackage, setSelectedPackage] = useState<number | null>(null)
  const [customAmount, setCustomAmount] = useState<number>(100)
  const [showPayment, setShowPayment] = useState(false)
  const [stripePublishableKey, setStripePublishableKey] = useState<string>('')
  const [clientSecret, setClientSecret] = useState<string>('')
  const [creatingPayment, setCreatingPayment] = useState(false)
  const [success, setSuccess] = useState(false)

  useEffect(() => {
    // Check for success parameter
    const params = new URLSearchParams(window.location.search)
    if (params.get('success') === 'true') {
      setSuccess(true)
      refreshUser()
      setTimeout(() => {
        router.push('/')
      }, 3000)
    }
  }, [router, refreshUser])

  useEffect(() => {
    fetchPackages()
    fetchStripeKey()
  }, [])

  const fetchPackages = async () => {
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const response = await fetch(`${apiUrl}/api/v1/payments/packages`)
      const data = await response.json()
      setPackages(data.packages || [])
    } catch (error) {
      console.error('Error fetching packages:', error)
    } finally {
      setLoading(false)
    }
  }

  const fetchStripeKey = async () => {
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const response = await fetch(`${apiUrl}/api/v1/payments/publishable-key`)
      const data = await response.json()
      if (data.publishable_key) {
        setStripePublishableKey(data.publishable_key)
      }
    } catch (error) {
      console.error('Error fetching Stripe key:', error)
    }
  }

  const handlePackageSelect = (pkgId: number) => {
    setSelectedPackage(pkgId)
    setCustomAmount(0)
  }

  const handleCustomAmountChange = (value: number) => {
    setCustomAmount(value)
    setSelectedPackage(null)
  }

  const handlePurchase = async () => {
    if (!selectedPackage && (!customAmount || customAmount < 100)) {
      alert('Please select a package or enter a custom amount (minimum 100 NOK)')
      return
    }
    
    setCreatingPayment(true)
    try {
      // Get token from context or localStorage (AuthContext uses 'auth_token')
      const authToken = token || localStorage.getItem('auth_token')
      if (!authToken) {
        throw new Error('Please log in to purchase credits')
      }
      
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      
      // Prepare request body - ensure we have valid values
      let requestBody: { package_id?: number; custom_amount_nok?: number } = {}
      
      if (selectedPackage && selectedPackage > 0) {
        requestBody = { package_id: selectedPackage }
      } else if (customAmount && customAmount >= 100) {
        requestBody = { custom_amount_nok: customAmount }
      } else {
        throw new Error('Please select a package or enter a custom amount (minimum 100 NOK)')
      }
      
      console.log('Creating payment intent with:', requestBody, 'selectedPackage:', selectedPackage, 'customAmount:', customAmount)
      
      const response = await fetch(`${apiUrl}/api/v1/payments/create-intent`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify(requestBody)
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to create payment')
      }

      const data = await response.json()
      setClientSecret(data.client_secret)
      setShowPayment(true)
    } catch (error: any) {
      alert(`Error: ${error.message || 'Failed to create payment. Please try again.'}`)
      console.error('Error creating payment intent:', error)
    } finally {
      setCreatingPayment(false)
    }
  }

  const getSelectedCredits = () => {
    if (selectedPackage) {
      const pkg = packages.find(p => p.id === selectedPackage)
      return pkg?.credits || 0
    }
    // Custom: 100 NOK = 10 credits
    return Math.floor((customAmount / 100) * 10)
  }

  // Show loading while auth is initializing
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

  // Only show login message after auth has finished loading
  if (!user) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 flex items-center justify-center">
        <div className="text-center">
          <p className="text-gray-600 mb-4">Please log in to purchase credits</p>
          <button
            onClick={() => router.push('/')}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            Go to Login
          </button>
        </div>
      </div>
    )
  }

  if (success) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 flex items-center justify-center">
        <div className="bg-white rounded-2xl shadow-xl p-8 max-w-md text-center">
          <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-2xl font-bold text-gray-900 mb-2">Payment Successful!</h2>
          <p className="text-gray-600 mb-6">Your credits have been added to your account.</p>
          <p className="text-sm text-gray-500">Redirecting to home page...</p>
        </div>
      </div>
    )
  }

  if (showPayment && stripePublishableKey && clientSecret) {
    const selectedCredits = getSelectedCredits()
    const selectedPkg = selectedPackage ? packages.find(p => p.id === selectedPackage) : null

    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50">
        <div className="container mx-auto px-4 py-12">
          <div className="max-w-2xl mx-auto">
            <div className="bg-white rounded-2xl shadow-xl p-8">
              <h2 className="text-2xl font-bold text-gray-900 mb-6">Complete Payment</h2>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
                <div className="flex justify-between items-center">
                  <span className="text-gray-700">Credits:</span>
                  <span className="text-lg font-bold text-blue-600">{selectedCredits}</span>
                </div>
                <div className="flex justify-between items-center mt-2">
                  <span className="text-gray-700">Amount:</span>
                  <span className="text-lg font-bold text-gray-900">
                    {selectedPkg ? `${selectedPkg.price_nok.toFixed(2)} NOK` : `${customAmount.toFixed(2)} NOK`}
                  </span>
                </div>
              </div>
              <Elements stripe={getStripe()} options={{ clientSecret }}>
                <PaymentForm
                  packageId={selectedPackage || undefined}
                  customAmount={selectedPackage ? undefined : customAmount}
                  credits={selectedCredits}
                  onSuccess={() => {
                    setSuccess(true)
                    refreshUser()
                  }}
                  onCancel={() => {
                    setShowPayment(false)
                    setClientSecret('')
                  }}
                />
              </Elements>
            </div>
          </div>
        </div>
      </div>
    )
  }

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
              <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
                Buy Credits
              </h1>
            </div>
            <div className="flex items-center space-x-4">
              <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-2">
                <span className="text-sm text-gray-600">Your Credits: </span>
                <span className="text-lg font-bold text-blue-600">{user.credits || 0}</span>
              </div>
            </div>
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-12">
        <div className="max-w-6xl mx-auto">
          {/* Info Section */}
          <div className="bg-white rounded-2xl shadow-xl p-8 mb-8">
            <h2 className="text-2xl font-bold text-gray-900 mb-4">Credit System</h2>
            <div className="space-y-3 text-gray-700">
              <div className="flex items-start">
                <span className="text-blue-600 mr-2">•</span>
                <span><strong>First validation of a report:</strong> 10 credits</span>
              </div>
              <div className="flex items-start">
                <span className="text-blue-600 mr-2">•</span>
                <span><strong>New validation of same report:</strong> 2 credits</span>
              </div>
              <div className="flex items-start">
                <span className="text-green-600 mr-2">•</span>
                <span><strong>96% safety score or higher:</strong> Credits for the last validation are automatically refunded</span>
              </div>
            </div>
          </div>

          {/* Packages */}
          {loading ? (
            <div className="text-center py-12">
              <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
              <p className="mt-4 text-gray-600">Loading packages...</p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
                {packages.map((pkg) => (
                  <div
                    key={pkg.id}
                    onClick={() => handlePackageSelect(pkg.id)}
                    className={`bg-white rounded-xl shadow-lg border-2 p-6 cursor-pointer transition-all ${
                      selectedPackage === pkg.id
                        ? 'border-blue-600 ring-2 ring-blue-200'
                        : 'border-gray-200 hover:border-blue-300'
                    }`}
                  >
                    <div className="flex justify-between items-start mb-4">
                      <h3 className="text-xl font-bold text-gray-900">{pkg.name}</h3>
                      {selectedPackage === pkg.id && (
                        <div className="w-6 h-6 bg-blue-600 rounded-full flex items-center justify-center">
                          <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                          </svg>
                        </div>
                      )}
                    </div>
                    <div className="mb-4">
                      <div className="text-3xl font-bold text-gray-900 mb-1">
                        {pkg.price_nok.toFixed(0)} <span className="text-lg text-gray-600">kr</span>
                      </div>
                      <div className="text-sm text-gray-500">
                        {pkg.credits} credits • ~{pkg.reports} reports
                      </div>
                      <div className="text-xs text-gray-400 mt-1">
                        {pkg.price_per_credit.toFixed(2)} kr per credit
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {/* Custom Amount */}
              <div className="bg-white rounded-xl shadow-lg border-2 border-gray-200 p-6 mb-8">
                <h3 className="text-xl font-bold text-gray-900 mb-4">Custom Amount</h3>
                <div className="flex items-center space-x-4">
                  <input
                    type="number"
                    min="100"
                    step="10"
                    value={customAmount}
                    onChange={(e) => handleCustomAmountChange(parseFloat(e.target.value) || 0)}
                    className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    placeholder="Enter amount (minimum 100 NOK)"
                  />
                  <div className="text-right">
                    <div className="text-sm text-gray-500">Credits:</div>
                    <div className="text-lg font-bold text-blue-600">
                      {Math.floor((customAmount / 100) * 10)}
                    </div>
                  </div>
                </div>
                {customAmount > 0 && customAmount < 100 && (
                  <p className="text-red-600 text-sm mt-2">Minimum purchase is 100 NOK</p>
                )}
              </div>

              {/* Purchase Button */}
              <div className="text-center">
                <button
                  onClick={handlePurchase}
                  disabled={(!selectedPackage && (!customAmount || customAmount < 100)) || creatingPayment}
                  className="px-8 py-4 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-lg font-semibold"
                >
                  {creatingPayment ? (
                    <>
                      <span className="inline-block animate-spin mr-2">⏳</span>
                      Creating Payment...
                    </>
                  ) : (
                    `Purchase ${selectedPackage 
                      ? `${packages.find(p => p.id === selectedPackage)?.credits || 0} Credits`
                      : `${Math.floor((customAmount / 100) * 10)} Credits`
                    }`
                  )}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

