'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import axios from 'axios'
import { useAuth } from './contexts/AuthContext'
import UserMenu from './components/UserMenu'

interface ReportListItem {
  id: number
  filename: string
  uploaded_at: string
  status?: string
}

export default function Home() {
  const [file, setFile] = useState<File | null>(null)
  const [reportSystem, setReportSystem] = useState('')
  const [buildingYear, setBuildingYear] = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const [showInsufficientCredits, setShowInsufficientCredits] = useState(false)
  const [insufficientCreditsMessage, setInsufficientCreditsMessage] = useState('')
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
            <p className="mt-4 text-gray-600">Omdirigerer til adminpanelet...</p>
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
          <p className="mt-4 text-gray-600">Laster...</p>
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
          <p className="mt-4 text-gray-600">Omdirigerer...</p>
        </div>
      </div>
    )
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0])
      setError('')
      setInfoMessage('')
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
        setInfoMessage('')
      } else {
        setError('Vennligst last opp en PDF-fil')
      }
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    
    if (!user) {
      setError('Vennligst logg inn for å laste opp rapporter')
      return
    }

    if (!file) {
      setError('Vennligst velg en PDF-fil')
      return
    }

    setUploading(true)
    setError('')
    setInfoMessage('')
    const submitStartedAt = Date.now()

    const normalizeFilename = (name: string): string =>
      (name || '')
        .normalize('NFKC')
        .trim()
        .toLowerCase()
        .replace(/\s+/g, ' ')

    const selectedFilename = normalizeFilename(file?.name || '')

    const waitForReportCompletion = async (
      reportId: number,
      initialStatus?: string
    ): Promise<boolean> => {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const authToken = token || localStorage.getItem('auth_token')
      if (!apiUrl || !authToken) return false

      const maxPollAttempts = 180 // ~15 min at 5s interval
      const pollIntervalMs = 5000
      let attempts = 0
      let lastKnownStatus = (initialStatus || '').toLowerCase()

      setInfoMessage('Analyserer rapporten. Dette kan ta et par minutter.')

      while (attempts < maxPollAttempts) {
        attempts += 1
        try {
          const response = await axios.get(`${apiUrl}/api/v1/reports/${reportId}`, {
            headers: { Authorization: `Bearer ${authToken}` },
            timeout: 30000,
          })
          const status = String(response.data?.status || '').toLowerCase()
          if (status) {
            lastKnownStatus = status
          }

          if (status === 'completed') {
            router.push(`/results/${reportId}`)
            return true
          }
          if (status === 'failed' || status === 'incomplete') {
            setError('Analysen ble ikke fullført. Prøv igjen, eller åpne rapporten i Historikk for detaljer.')
            setInfoMessage('')
            setUploading(false)
            return false
          }
        } catch {
          // Keep polling on transient network/gateway hiccups.
        }
        await new Promise((resolve) => setTimeout(resolve, pollIntervalMs))
      }

      setUploading(false)
      setInfoMessage('')
      setError(
        lastKnownStatus === 'processing'
          ? 'Analysen pågår fortsatt. Sjekk Historikk for oppdatert status.'
          : 'Det tok lengre tid enn forventet å hente status. Sjekk Historikk.'
      )
      return false
    }

    const recoverReportAfterGatewayTimeout = async (): Promise<ReportListItem | null> => {
      if (!file) return null
      const apiUrl = process.env.NEXT_PUBLIC_API_URL
      const authToken = token || localStorage.getItem('auth_token')
      if (!apiUrl || !authToken) return null
      try {
        const listResponse = await axios.get(`${apiUrl}/api/v1/reports/?limit=100`, {
          headers: { Authorization: `Bearer ${authToken}` },
          timeout: 20000,
        })
        const reports = Array.isArray(listResponse.data) ? (listResponse.data as ReportListItem[]) : []
        const now = Date.now()
        const recentWindowStart = submitStartedAt - 10 * 60 * 1000

        const enriched = reports
          .map((item) => {
            const uploadedAt = new Date(item.uploaded_at).getTime()
            const normalizedItemFilename = normalizeFilename(item.filename || '')
            const status = String(item.status || '').toLowerCase()
            const filenameExact = normalizedItemFilename === selectedFilename
            const filenameLoose =
              normalizedItemFilename.includes(selectedFilename) || selectedFilename.includes(normalizedItemFilename)
            const isRecent = Number.isFinite(uploadedAt) && uploadedAt >= recentWindowStart && uploadedAt <= now + 2 * 60 * 1000
            // Some list payloads can have delayed/missing status; do not hard-reject on that.
            const statusEligible = !status || status === 'processing' || status === 'completed' || status === 'incomplete'
            return {
              item,
              uploadedAt: Number.isFinite(uploadedAt) ? uploadedAt : 0,
              filenameExact,
              filenameLoose,
              isRecent,
              statusEligible,
            }
          })
          .filter((row) => row.statusEligible && row.isRecent)
          .sort((a, b) => b.uploadedAt - a.uploadedAt)

        const exact = enriched.find((row) => row.filenameExact)
        if (exact) return exact.item
        const loose = enriched.find((row) => row.filenameLoose)
        if (loose) return loose.item
        // Fallback: if only one fresh eligible report exists, prefer it.
        if (enriched.length === 1) return enriched[0].item
        // Last resort: nearest newly uploaded report around this submit session.
        const nearest = enriched
          .filter((row) => row.uploadedAt > 0)
          .sort((a, b) => Math.abs(a.uploadedAt - submitStartedAt) - Math.abs(b.uploadedAt - submitStartedAt))[0]
        if (nearest) return nearest.item
        return null
      } catch {
        // Best-effort recovery only.
      }
      return null
    }

    const recoverAndWaitForCompletionAfterTimeout = async (): Promise<boolean> => {
      // Keep trying to locate the freshly uploaded report by filename for a short period,
      // then switch to normal report-status polling once we have an id.
      const maxRecoverAttempts = 24 // ~2 minutes (24 * 5s)
      for (let attempt = 0; attempt < maxRecoverAttempts; attempt += 1) {
        const recovered = await recoverReportAfterGatewayTimeout()
        if (recovered?.id) {
          return await waitForReportCompletion(recovered.id, recovered.status)
        }
        await new Promise((resolve) => setTimeout(resolve, 5000))
      }
      setUploading(false)
      setInfoMessage('')
      setError('Vi fant ikke rapporten automatisk ennå. Sjekk Historikk for status om et øyeblikk.')
      return false
    }

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
        setError('Vennligst logg inn for å laste opp rapporter')
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
      const reportId = Number(response?.data?.id)
      const status = String(response?.data?.status || '')
      if (!Number.isFinite(reportId) || reportId <= 0) {
        setUploading(false)
        setError('Opplastingen ble registrert, men mangler rapport-ID. Sjekk Historikk.')
        return
      }
      const redirected = await waitForReportCompletion(reportId, status)
      if (!redirected) {
        setUploading(false)
      }
    } catch (err: any) {
      const getUploadErrorMessage = (): string => {
        const status = err?.response?.status
        const data = err?.response?.data
        const responseText = typeof err?.request?.response === 'string' ? err.request.response : ''
        let parsedResponseText: any = null
        let detail = ''

        if (responseText) {
          try {
            parsedResponseText = JSON.parse(responseText)
          } catch {
            parsedResponseText = null
          }
        }

        if (typeof data?.detail === 'string') {
          detail = data.detail
        } else if (typeof parsedResponseText?.detail === 'string') {
          detail = parsedResponseText.detail
        }
        const rawMessage = typeof err?.message === 'string' ? err.message : ''

        if (
          status === 507 ||
          detail.toLowerCase().includes('no space left on device') ||
          detail.toLowerCase().includes('could not extend file') ||
          detail.toLowerCase().includes('diskfull') ||
          rawMessage.toLowerCase().includes('no space left on device')
        ) {
          return 'Serveren har ikke nok lagringsplass til aa fullfore analysen naa. Prov igjen senere eller kontakt support.'
        }

        if (typeof data === 'string') {
          if (status === 504 || data.toLowerCase().includes('gateway time-out')) {
            return ''
          }
          if (data.includes('<html') && status === 500) {
            return 'Serverfeil (500) fra gateway/proxy. Prøv igjen om 10-20 sekunder. Hvis feilen fortsetter, kontakt support.'
          }
          return data
        }
        if (responseText) {
          if (status === 504 || responseText.toLowerCase().includes('gateway time-out')) {
            return ''
          }
          if (responseText.includes('<html') && status === 500) {
            return 'Serverfeil (500) fra gateway/proxy. Prøv igjen om 10-20 sekunder. Hvis feilen fortsetter, kontakt support.'
          }
          if (!detail) {
            return responseText
          }
        }
        if (detail) {
          return detail
        }
        if (!err?.response) {
          return 'Kunne ikke kontakte serveren for aa analysere rapporten. Kontroller nettverket og prov igjen.'
        }
        return 'Rapporten kunne ikke behandles. Prov igjen. Hvis feilen fortsetter, kontakt support.'
      }
      if (err.response?.status === 401) {
        setError('Vennligst logg inn for å laste opp rapporter. Hvis du allerede er innlogget, prøv å logge ut og inn igjen.')
      } else if (err.response?.status === 402) {
        // Insufficient credits - show modal
        setError('')
        setShowInsufficientCredits(true)
        setInsufficientCreditsMessage(err.response?.data?.detail || 'Ikke nok kreditter til å laste opp denne rapporten.')
      } else {
        const status = err?.response?.status
        const responseData = err?.response?.data
        const responseText = typeof err?.request?.response === 'string' ? err.request.response.toLowerCase() : ''
        const looksLikeGatewayTimeout =
          status === 504
          || (typeof responseData === 'string' && responseData.toLowerCase().includes('gateway time-out'))
          || responseText.includes('gateway time-out')
        if (looksLikeGatewayTimeout) {
          const recoveredReport = await recoverReportAfterGatewayTimeout()
          if (recoveredReport?.id) {
            const redirected = await waitForReportCompletion(recoveredReport.id, recoveredReport.status)
            if (!redirected) {
              setUploading(false)
            }
            return
          }
          setError('')
          setInfoMessage('Rapporten behandles fortsatt i bakgrunnen. Vi prøver å hente status automatisk ...')
          setUploading(true)
          await recoverAndWaitForCompletionAfterTimeout()
          return
        }
        setError(getUploadErrorMessage())
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
                <p className="text-xs text-gray-500">AI-drevet kvalitetsvurdering</p>
              </div>
              </div>
              <nav className="hidden md:flex space-x-4">
                <span className="text-blue-600 font-medium border-b-2 border-blue-600 pb-1">
                  Last opp
                </span>
                <button
                  onClick={() => router.push('/history')}
                  className="text-gray-600 hover:text-blue-600 transition-colors pb-1"
                >
                  Historikk
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
              Evaluer tilstandsrapporten din
            </h2>
            <p className="text-xl text-gray-600 max-w-2xl mx-auto">
              Last opp din tilstandsrapport og motta en automatisert kvalitetsvurdering basert på avhendingslova og forskrift om tryggere bolighandel.
            </p>
          </div>

          {/* Upload Card */}
          <div className="bg-white rounded-2xl shadow-xl border border-gray-100 overflow-hidden">
            <div className="bg-gradient-to-r from-blue-600 to-indigo-600 px-8 py-6">
              <h3 className="text-2xl font-semibold text-white">Last opp rapport</h3>
              <p className="text-blue-100 mt-1">Få umiddelbar kvalitetsanalyse</p>
            </div>

            <form onSubmit={handleSubmit} className="p-8 space-y-6">
              {/* File Upload */}
              <div>
                <label htmlFor="file" className="block text-sm font-semibold text-gray-700 mb-3">
                  PDF-rapport <span className="text-red-500">*</span>
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
                          Fjern fil
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
                            <span className="text-blue-600 hover:text-blue-700 cursor-pointer">Klikk for å laste opp</span> eller dra og slipp
                          </p>
                          <p className="text-xs text-gray-500 mt-1">Kun PDF-filer (maks 10 MB)</p>
                        </div>
                      </div>
                    )}
                  </div>
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
              {infoMessage && (
                <div className="bg-blue-50 border-l-4 border-blue-500 text-blue-800 px-4 py-3 rounded-lg flex items-start space-x-3">
                  <svg className="w-5 h-5 text-blue-500 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M18 10A8 8 0 11-2 10a8 8 0 0120 0zm-9-3a1 1 0 112 0v3a1 1 0 11-2 0V7zm1 8a1.5 1.5 0 100-3 1.5 1.5 0 000 3z" clipRule="evenodd" />
                  </svg>
                  <p className="text-sm">{infoMessage}</p>
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
                    <span>Analyserer rapport...</span>
                  </span>
                ) : (
                  <span className="flex items-center justify-center space-x-2">
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span>Last opp og analyser</span>
                  </span>
                )}
              </button>
            </form>
          </div>

          {/* Info Section */}
          <div className="mt-8 bg-white rounded-2xl shadow-lg border border-gray-100 p-8">
            <h2 className="text-2xl font-bold text-gray-900 mb-6 flex items-center">
              <span className="w-1 h-8 bg-gradient-to-b from-blue-600 to-indigo-600 rounded-full mr-4"></span>
              Slik fungerer det
            </h2>
            <div className="grid md:grid-cols-3 gap-6">
              {[
                { num: '1', title: 'Last opp PDF', desc: 'Last opp tilstandsrapporten din' },
                { num: '2', title: 'Kontroll mot lovkrav', desc: 'Automatisert kontroll av rapporten opp mot avhendingslova og forskrift om tryggere bolighandel.' },
                { num: '3', title: 'Få resultater', desc: 'Motta detaljerte kvalitetsresultater, funn og anbefalinger' },
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
              Kvalitetskravene bygger på forskrift til avhendingslova, gjeldende bransjestandarder og en gjennomgang av 350 publiserte dommer i tvister etter bolighandel (2024–2026).
            </p>
          </div>
        </div>
      </div>

      {/* Insufficient Credits Modal */}
      {showInsufficientCredits && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl shadow-2xl p-8 max-w-md mx-4">
            <div className="text-center mb-6">
              <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <h3 className="text-2xl font-bold text-gray-900 mb-2">Ikke nok kreditter</h3>
              <p className="text-gray-600 mb-4">{insufficientCreditsMessage}</p>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
                <div className="text-sm text-gray-700 mb-2">
                  <strong>Nåværende kreditter:</strong> {user?.credits || 0}
                </div>
                <div className="text-sm text-gray-700">
                  <strong>Kreves:</strong> 10 kreditter for første analyse, 2 kreditter for ny sjekk
                </div>
              </div>
            </div>
            <div className="flex space-x-4">
              <button
                onClick={() => setShowInsufficientCredits(false)}
                className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors"
              >
                Avbryt
              </button>
              <button
                onClick={() => {
                  setShowInsufficientCredits(false)
                  router.push('/buy-credits')
                }}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
              >
                Kjøp kreditter
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
