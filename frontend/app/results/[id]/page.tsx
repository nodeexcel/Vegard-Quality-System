'use client'

import { useEffect, useState, useMemo } from 'react'
import { useParams, useRouter } from 'next/navigation'
import axios from 'axios'
import { useAuth } from '../../contexts/AuthContext'
import { translate } from '../../utils/translations'

interface Evidence {
  point_id: string
  tg: string
  page: number
  heading: string
  source: 'LOCAL' | 'SUMMARY'
  snippet: string
  match_explain: string
}

interface ArkatField {
  status: 'present' | 'missing' | 'not_required' | 'unclear'
  required: boolean
  comment?: string
  evidence?: Evidence[]
}

interface ArkatSource {
  found: boolean
  where: 'under_bygningsdel' | 'merknader' | 'oppsummering' | 'annet' | 'ikke_funnet'
  page_refs?: number[]
  traceability_ok?: boolean
}

interface Arkat {
  arsak: ArkatField
  risiko: ArkatField
  konsekvens: ArkatField
  anbefalt_tiltak: ArkatField
  source: ArkatSource
}

interface Issue {
  issue_id: string
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info'
  summary: string
  details: string
  rule_refs: string[]
  evidence: Evidence[]
}

interface Deduction {
  rule_id: string
  points: number
  reason: string
  category_id?: 'A' | 'B' | 'C' | 'D' | 'E' | 'F'
  evidence?: Evidence[]
}

interface ComponentFinding {
  component_id: string
  component_title: string
  location?: string
  tg: 'TG0' | 'TG1' | 'TG2' | 'TG3' | 'TGIU'
  arkat: Arkat
  issues: Issue[]
  deductions: Deduction[]
}

interface ScoreByCategory {
  category_id: 'A' | 'B' | 'C' | 'D' | 'E' | 'F'
  category_name: string
  deduction: number
  max_deduction: number
}

interface TopScoreDriver {
  title: string
  severity: 'critical' | 'high' | 'medium' | 'low'
  reason: string
  deduction_points: number
  rule_refs: string[]
  evidence: Evidence[]
}

interface Improvement {
  title: string
  priority: 'critical' | 'high' | 'medium' | 'low'
  what_to_change: string
  suggested_text: string
  applies_to?: string[]
}

interface DeductionSummary {
  rule_id: string
  points: number
  reason: string
  category?: string
  severity: 'critical' | 'high' | 'medium' | 'low'
  point_id?: string
  component_title?: string
  suggestion?: string
  /** Full consequence/improvement text from admin-level feedback (e.g. example_fix.good_example) */
  exampleFix?: string
  /** Where in the report (v1.6 evidence_snippets or location hint) */
  evidence_snippets?: string[]
}

interface FeedbackPointOverview {
  point_id?: string | null
  canonical_id?: string
  title: string
  tg: string
  status: 'ok' | 'improve' | 'deduction' | 'blocking' | 'FOUND' | 'NOT_FOUND_IN_REPORT' | 'incomplete_analysis'
  summary: string
  deduction_total?: number
  deduction_band?: 'none' | 'low' | 'medium' | 'high'
  finding_ids?: string[]
  parent_id?: string | null
  requirement_tag?: string
  /** lovpålagt | ikke lovpålagt | uklart/avhenger av rapporttype */
  legal_status?: string
}

interface FeedbackFinding {
  finding_id: string
  rule_id: string
  rule_family: string
  severity: 'info' | 'low' | 'medium' | 'high' | 'critical'
  affects_96_gate: boolean
  point_id: string
  arkat_section: string
  message: string
  what_to_change: string
  example_fix: {
    good_example: string
    bad_example?: string
  }
  evidence: {
    page: number
    snippet: string
    match: string
  }
  deduction?: number
}

interface FeedbackV11 {
  version: 'v1.1'
  report_id: string
  document_hash?: string
  score: {
    total: number
    category_deductions: Array<{
      category: string
      deduction: number
      max_deduction: number
    }>
    top_drivers?: Array<{
      rule_id: string
      deduction: number
      message?: string
    }>
  }
  gate: {
    active: boolean
    blocked_96: boolean
    blocked_by: string[]
  }
  points_overview: FeedbackPointOverview[]
  findings: FeedbackFinding[]
}

interface AnalysisV14 {
  meta: {
    schema_version: '1.4'
    analysis_timestamp_utc: string
    document_title: string
    document_id?: string
    language?: 'no' | 'nb' | 'nn'
    model_notes?: string
  }
  score_total: number
  score_band: string
  score_by_category: ScoreByCategory[]
  top_score_drivers: TopScoreDriver[]
  findings: ComponentFinding[]
  improvements: Improvement[]
  disclaimers: string[]
}

/** v1.6 payload shape (trygghetsscore, top_issues, all_findings, category_breakdown, how_to_improve, gate) */
interface TopIssueV16 {
  category: string
  title: string
  severity: string
  deduction_band?: string
  message: string
  point_id?: string
  rule_id?: string
  recommended_fix_text?: string
  rewrite_strategy?: string
  suggested_rewrite_text?: string
  gate_effect?: { blocks_96_gate?: boolean; caps_total_score_to?: number }
}
interface AllFindingV16 {
  finding_id: string
  rule_id?: string
  category: string
  severity: string
  title: string
  message: string
  deduction_band?: string
  recommended_fix_text?: string
  rewrite_strategy?: string
  suggested_rewrite_text?: string
  duplicate_safe_key?: string
  evidence_snippets?: string[]
  gate_effect?: { blocks_96_gate?: boolean; caps_total_score_to?: number }
}
interface CategoryBreakdownV16 {
  category: string
  deduction_band: string
  summary: string
}
interface HowToImproveV16 {
  category: string
  title: string
  recommended_fix_text: string
  rewrite_strategy?: string
  suggested_rewrite_text?: string
}
interface GateV16 {
  active?: boolean
  blocked_96?: boolean
  max_score_if_blocked?: number
  blocked_by_count?: number
  message?: string
}
interface AnalysisV16 {
  trygghetsscore?: number
  score_band?: string
  gate?: GateV16
  top_issues?: TopIssueV16[]
  all_findings?: AllFindingV16[]
  how_to_improve?: HowToImproveV16[]
  category_breakdown?: CategoryBreakdownV16[]
  score_total?: number
  score_by_category?: ScoreByCategory[]
  score_reconciliation?: {
    score_start: number
    score_total: number
    deduction_total: number
    category_deduction_total: number
    other_deduction_total: number
    reconciles: boolean
    other_deductions?: Array<{ reason: string; points: number; meta?: any }>
  }
  top_score_drivers?: TopScoreDriver[]
  findings?: ComponentFinding[]
  improvements?: Improvement[]
  disclaimers?: string[]
}

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
  ai_analysis: AnalysisV14 | any | null
  scoring_result?: { feedback_v11?: FeedbackV11 } | null
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
        console.log('Starting fetch for report', params.id)
        const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
        const authToken = localStorage.getItem('auth_token')
        const response = await axios.get(`${apiUrl}/api/v1/reports/${params.id}`, {
          timeout: 60000,
          headers: authToken ? { Authorization: `Bearer ${authToken}` } : undefined,
        })
        console.log('Fetch successful, response size:', JSON.stringify(response.data).length)
        setReport(response.data)
      } catch (err: any) {
        console.error('Fetch error:', err)
        if (err.response?.status === 401) {
          router.push('/login')
        } else if (err.code === 'ECONNABORTED') {
          setError('Tidsavbrudd ved henting av rapport. Prøv igjen.')
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

  const analysisData = useMemo(() => {
    if (!report) return null
    const analysis = report.ai_analysis as (AnalysisV14 & AnalysisV16) | null
    const scoringResult = report.scoring_result as any
    console.log('Analysis loaded:', !!analysis)
    const feedbackV11 = report.scoring_result?.feedback_v11 || null
    const hasFeedbackV11 = Boolean(feedbackV11 && feedbackV11.points_overview)
    const safeStopActive = Boolean(
      (scoringResult?.safe_stop_due_to_invariant_failure) ||
      ((analysis as any)?.safe_stop_due_to_invariant_failure) ||
      ((analysis as any)?.meta?.safe_stop_due_to_invariant_failure)
    )
    const safeStopMessage =
      ((analysis as any)?.meta?.limited_analysis_warning as string | undefined) ||
      ((analysis as any)?.limited_analysis_warning as string | undefined) ||
      (scoringResult?.limited_analysis_warning as string | undefined) ||
      'Rapporten kunne ikke analyseres ennå.'
    const hasV16 = Boolean(
      analysis &&
      (typeof (analysis as AnalysisV16).trygghetsscore === 'number' ||
        Array.isArray((analysis as AnalysisV16).top_issues) ||
        Array.isArray((analysis as AnalysisV16).all_findings))
    )
    const hasV14 = Boolean(
      analysis &&
      (typeof analysis.score_total === 'number' ||
        (hasV16 && ((analysis as AnalysisV16).trygghetsscore != null || (analysis as AnalysisV16).top_issues?.length)))
    )
    const legacyAnalysis = !hasV14 ? (report.ai_analysis as any) : null
    const scoreTotal: number | null = null
    return { analysis, feedbackV11, hasFeedbackV11, hasV16, hasV14, legacyAnalysis, scoreTotal, safeStopActive, safeStopMessage }
  }, [report])

  // Show loading while checking auth
  if (authLoading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-4 border-blue-200 border-t-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Laster...</p>
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
          <p className="mt-6 text-lg font-medium text-gray-700">Laster analyseresultater...</p>
          <p className="mt-2 text-sm text-gray-500">Dette kan ta noen sekunder</p>
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
            <h3 className="text-xl font-bold text-gray-900 mb-2">Feil</h3>
            <p className="text-red-600 mb-6">{error || 'Rapport ikke funnet'}</p>
            <button
              onClick={() => router.push('/')}
              className="w-full bg-gradient-to-r from-blue-600 to-indigo-600 text-white py-3 px-6 rounded-lg font-semibold hover:from-blue-700 hover:to-indigo-700 transition-all shadow-lg hover:shadow-xl"
            >
              Tilbake til opplasting
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (!analysisData) return null

  const { analysis, feedbackV11, hasFeedbackV11, hasV16, hasV14, legacyAnalysis, scoreTotal, safeStopActive, safeStopMessage } = analysisData
  if (safeStopActive) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
        <p className="text-lg font-medium text-gray-900">{safeStopMessage}</p>
      </div>
    )
  }
  const improvementPriorityOrder: Record<string, number> = {
    critical: 0,
    high: 1,
    medium: 2,
    low: 3
  }
  const allFindingsV16 = (analysis as AnalysisV16)?.all_findings
  const topIssuesV16 = (analysis as AnalysisV16)?.top_issues
  const howToImproveV16 = (analysis as AnalysisV16)?.how_to_improve
  const categoryBreakdown = (analysis as AnalysisV16)?.category_breakdown
  const scoreByCategoryRaw: ScoreByCategory[] =
    analysis && Array.isArray((analysis as any).score_by_category)
      ? ((analysis as any).score_by_category as ScoreByCategory[])
      : feedbackV11?.score?.category_deductions?.length
        ? (feedbackV11.score.category_deductions.map((c) => ({
            category_id: c.category as any,
            category_name: `Kategori ${c.category}`,
            deduction: c.deduction,
            max_deduction: c.max_deduction,
          })) as ScoreByCategory[])
        : []
  const totalDeductionFromScore = typeof scoreTotal === 'number' ? Math.max(0, 100 - scoreTotal) : 0
  const publicBandToPoints = (band?: string | null): number => {
    if (band === 'Høyt trekk') return 5
    if (band === 'Middels trekk') return 3
    if (band === 'Lavt trekk') return 1
    return 0
  }
  const normalizeCategoryId = (value?: string | null): string | undefined => {
    const raw = (value || '').toString().trim().toUpperCase()
    if (/^[A-F]$/.test(raw)) return raw
    const match = raw.match(/KATEGORI\s+([A-F])|^([A-F])[\s_.-]/)
    return match ? (match[1] || match[2]) : undefined
  }
  const inferCategoryFromRuleId = (ruleId?: string | null): string | undefined => {
    const raw = (ruleId || '').toString().trim().toUpperCase()
    const match = raw.match(/^([A-F])(?:[_.-]|$)/)
    return match ? match[1] : undefined
  }
  const categoryDeductionById = new Map<string, number>(
    scoreByCategoryRaw.map((category) => [
      category.category_id,
      Number(category.deduction || 0),
    ])
  )
  const categoryHasScoreDeduction = (category?: string | null): boolean => {
    const normalized = normalizeCategoryId(category)
    if (!normalized || !categoryDeductionById.size) return true
    return (categoryDeductionById.get(normalized) || 0) > 0
  }
  const isScoreReconciledItem = (item: { category?: string; rule_id?: string; gate_effect?: { blocks_96_gate?: boolean } }): boolean => {
    if (item.gate_effect?.blocks_96_gate) return true
    return categoryHasScoreDeduction(item.category || inferCategoryFromRuleId(item.rule_id))
  }
  const scoredFindingsV16 = (allFindingsV16 || []).filter((f) => publicBandToPoints(f.deduction_band) > 0 && isScoreReconciledItem(f))
  const scoreReconciledTopIssuesV16 = (topIssuesV16 || []).filter((f) => publicBandToPoints(f.deduction_band) > 0 && isScoreReconciledItem(f))
  const visibleFindingsV16Base = (allFindingsV16 || []).filter((f) => {
    const points = publicBandToPoints(f.deduction_band)
    if (points > 0) return true
    if (f.rule_id === 'E_METHOD.egenerklaring_missing') return true
    if (f.rule_id === 'E_METHOD.vinterhage_not_assessed_ns3600') return true
    if (f.deduction_band === 'Ikke scoretrekk') return true
    if ((f.severity || '').toLowerCase() === 'info') return true
    return false
  })
  const hasVisibleEgenerklaringAdvisory = visibleFindingsV16Base.some((f) => f.rule_id === 'E_METHOD.egenerklaring_missing')
  const hasVisibleVinterhageAdvisory = visibleFindingsV16Base.some((f) => f.rule_id === 'E_METHOD.vinterhage_not_assessed_ns3600')
  const reportTextForInfoFallback = (report?.extracted_text || '').toLowerCase()
  const shouldShowEgenerklaringAdvisoryFallback = !hasVisibleEgenerklaringAdvisory && /egenerkl/.test(reportTextForInfoFallback) && new RegExp('egenerkl[^\n.]{0,120}(ikke levert|foreligger ikke|ikke foreligger|mangler)').test(reportTextForInfoFallback)
  const shouldShowVinterhageAdvisoryFallback =
    !hasVisibleVinterhageAdvisory &&
    /vinterhage/.test(reportTextForInfoFallback) &&
    (
      /vinterhage[^\n.]{0,260}(ikke\s+tilstandsvurdert|ikke\s+vurdert|ikke\s+omfattet)/.test(reportTextForInfoFallback) ||
      /(bygget|bygningen|tilleggsbygg)[^\n.]{0,220}(ikke\s+tilstandsvurdert|ikke\s+vurdert|ikke\s+omfattet)[^\n.]{0,220}(avhendingslova|avhendingsloven|ns ?3600)/.test(reportTextForInfoFallback) ||
      /vinterhage\s*[•\-][^\n]{0,220}(det foreligger ikke tegninger|det er ikke fremlagt tegninger)/.test(reportTextForInfoFallback)
    ) &&
    (
      /(avhendingslova|avhendingsloven|ns ?3600)/.test(reportTextForInfoFallback) ||
      /vinterhage\s*[•\-][^\n]{0,220}(det foreligger ikke tegninger|det er ikke fremlagt tegninger)/.test(reportTextForInfoFallback)
    )

  const fallbackInfoFindings: any[] = []
  if (shouldShowEgenerklaringAdvisoryFallback) {
    fallbackInfoFindings.push({
      finding_id: 'ui-fallback-egenerklaring-missing',
      rule_id: 'E_METHOD.egenerklaring_missing',
      category: 'E',
      severity: 'info',
      title: 'Egenerklæring ikke levert',
      message: 'Rapporten opplyser at egenerklæring ikke er levert. Dette skal ikke gi scoretrekk, men bør fremgå som en faglig merknad fordi analysegrunnlaget blir svakere når forhold bare eier kjenner til kan være utelatt.',
      deduction_band: 'Ikke scoretrekk',
      recommended_fix_text: 'Legg inn en kort opplysning om at rapporten er ferdigstilt uten egenerklæring, og forklar hvilken metodisk usikkerhet og hvilket ansvar dette kan medføre for takstmannen.',
      suggested_rewrite_text: 'Egenerklæring er ikke levert i forbindelse med oppdraget. Dette er ikke et forskriftsavvik og skal ikke gi scoretrekk, men det svekker analysegrunnlaget fordi forhold bare eier kjenner til kan være utelatt. NS 3600:2018 pkt. 5 og 9 krever egenerklæring før analysen, og NS 3600:2025 pkt. 8 c) krever at den foreligger før rapporten ferdigstilles.',
      evidence_snippets: ['Egenerklæringsskjema er ikke levert i forbindelse med oppdraget.'],
    })
  }
  if (shouldShowVinterhageAdvisoryFallback) {
    fallbackInfoFindings.push({
      finding_id: 'ui-fallback-vinterhage-ns3600',
      rule_id: 'E_METHOD.vinterhage_not_assessed_ns3600',
      category: 'E',
      severity: 'info',
      title: 'Vinterhage er ikke vurdert etter NS 3600',
      message: 'Rapporten opplyser at vinterhagen ikke er tilstandsvurdert etter forskrift til avhendingslova og NS 3600. Dette er vesentlig informasjon for kjøper og bør fremgå som en faglig merknad.',
      deduction_band: 'Ikke scoretrekk',
      recommended_fix_text: 'Legg inn en tydelig merknad om at vinterhagen ikke er omfattet av tilstandsvurderingen etter forskrift til avhendingslova og NS 3600, slik at kjøper forstår avgrensningen i rapporten.',
      suggested_rewrite_text: 'Vinterhagen er ikke tilstandsvurdert etter forskrift til avhendingslova og NS 3600. Kjøper må derfor være oppmerksom på at denne bygningen ikke er omfattet av den bygningssakkyndige vurderingen i rapporten.',
      evidence_snippets: ['Bygget er ikke tilstandsvurdert ihht Forskrift til avhendingslova og NS3600.'],
    })
  }
  const visibleFindingsV16 = fallbackInfoFindings.length
    ? [...visibleFindingsV16Base, ...fallbackInfoFindings]
    : visibleFindingsV16Base
  const categoryBreakdownByCategory = new Map<string, CategoryBreakdownV16>()
  ;(categoryBreakdown || []).forEach((cb) => {
    if (cb?.category) categoryBreakdownByCategory.set(cb.category, cb)
  })
  // BUG 3: never reconstruct category deductions from findings/bands.
  // Always render authoritative backend category deductions.
  const scoreByCategory = scoreByCategoryRaw
  type ScoreByCategoryDisplay = ScoreByCategory & { deduction_band?: string; summary?: string }
  const scoreByCategoryDisplay = (scoreByCategory as ScoreByCategoryDisplay[]).map((category) => {
    const breakdown = categoryBreakdownByCategory.get(category.category_id)
    const hasDeduction = (category.deduction || 0) > 0
    return {
      ...category,
      // Keep UI band/summary aligned with authoritative numeric deductions.
      deduction_band: hasDeduction
        ? (breakdown?.deduction_band ?? 'Trekk')
        : 'Ikke scoretrekk',
      summary: hasDeduction
        ? (breakdown?.summary ?? '')
        : 'Ingen synlige funn i denne kategorien.',
    }
  })
  const topScoreDriversSource =
    scoreReconciledTopIssuesV16.length
      ? scoreReconciledTopIssuesV16
          .slice()
          .sort((a, b) => publicBandToPoints(b.deduction_band) - publicBandToPoints(a.deduction_band))
          .slice(0, 5)
          .map((f) => ({
            title: f.title,
            severity: (f.severity === 'critical' ? 'critical' : f.severity === 'major' ? 'high' : f.severity === 'minor' ? 'low' : 'medium') as TopScoreDriver['severity'],
            reason: f.message,
            deduction_points: publicBandToPoints(f.deduction_band),
            rule_refs: [f.category],
            evidence: [],
            recommended_fix_text: undefined,
          }))
      : scoredFindingsV16.length
      ? scoredFindingsV16
          .slice()
          .sort((a, b) => publicBandToPoints(b.deduction_band) - publicBandToPoints(a.deduction_band))
          .slice(0, 5)
          .map((f) => ({
            title: f.title,
            severity: (f.severity === 'critical' ? 'critical' : f.severity === 'major' ? 'high' : f.severity === 'minor' ? 'low' : 'medium') as TopScoreDriver['severity'],
            reason: f.message,
            deduction_points: publicBandToPoints(f.deduction_band),
            rule_refs: [f.category],
            evidence: (f.evidence_snippets || []).slice(0, 1).map((snippet) => ({
              point_id: (f as { point_id?: string }).point_id || '',
              tg: '',
              page: 1,
              heading: f.title,
              source: 'LOCAL' as const,
              snippet,
              match_explain: 'Fra scored all_findings.',
            })),
            recommended_fix_text: undefined,
          }))
      : analysis?.top_score_drivers ?? []
  const improvementBadgeClasses = (priority: string) => {
    switch (priority) {
      case 'critical':
        return 'bg-red-100 text-red-800'
      case 'high':
        return 'bg-orange-100 text-orange-800'
      case 'medium':
        return 'bg-yellow-100 text-yellow-800'
      case 'low':
        return 'bg-blue-100 text-blue-800'
      default:
        return 'bg-gray-100 text-gray-800'
    }
  }
  const improvementCardClasses = (priority: string) => {
    switch (priority) {
      case 'critical':
        return 'border-red-200 bg-red-50'
      case 'high':
        return 'border-orange-200 bg-orange-50'
      case 'medium':
        return 'border-yellow-200 bg-yellow-50'
      case 'low':
        return 'border-blue-200 bg-blue-50'
      default:
        return 'border-gray-200 bg-white'
    }
  }
  const pointStatusClasses = (status: FeedbackPointOverview['status']) => {
    switch (status) {
      case 'ok':
      case 'FOUND':
        return 'bg-green-100 text-green-800'
      case 'improve':
        return 'bg-yellow-100 text-yellow-800'
      case 'deduction':
        return 'bg-red-100 text-red-800'
      case 'blocking':
        return 'bg-red-200 text-red-900'
      case 'NOT_FOUND_IN_REPORT':
        return 'bg-slate-100 text-slate-600'
      default:
        return 'bg-gray-100 text-gray-800'
    }
  }
  const deductionBandLabel = (band: string | undefined) => {
    if (!band || band === 'none') return null
    if (band === 'low') return 'Lavt trekk'
    if (band === 'medium') return 'Middels trekk'
    if (band === 'high') return 'Høyt trekk'
    return null
  }
  /** Replace vague AI phrasing with precise, legally clear wording in user-facing text only. */
  const formatFeedbackText = (text: string | null | undefined): string => {
    if (text == null || typeof text !== 'string') return ''
    return text
      .replace(/\bmed\s+[nN]\s+kort\s+setning\b/gi, 'med en kort setning')
      .replace(/\bmed\s+n\b/gi, 'med en')
      .replace(/\bmed n kort setning\b/gi, 'med en kort setning')
      .replace(/\b n kort setning\b/gi, ' en kort setning')
      .replace(/Konsekvens uklar for kjøper/g, 'Konsekvens kan presiseres i forhold til kjøpers bruk, økonomi eller risiko.')
      .replace(/Konsekvens ikke eksplisitt oversatt til kjøpers situasjon\.?/g, 'Dette innebærer at kjøper må påregne utbedring før normal bruk kan anses trygg, og at det foreligger risiko for videre konstruksjonsskade dersom tiltak utsettes.')
      .replace(/\s{2,}/g, ' ')
      .trim()
  }

  const isTooGenericSuggestedRewrite = (text: string | null | undefined): boolean => {
    if (!text || typeof text !== 'string') return true
    const low = text.toLowerCase().trim()
    if (low.length < 40) return true
    const arkatFieldLabelHits = ['årsak:', 'arsak:', 'risiko:', 'konsekvens:', 'anbefalt tiltak:', 'anbefalte tiltak:']
      .reduce((count, marker) => count + (low.split(marker).length - 1), 0)
    if (low.includes('ikke følges opp')) return true
    if (arkatFieldLabelHits >= 2 && low.length >= 160) return true
    if (low.startsWith('punkt ') && low.includes('det er risiko for videre utvikling dersom')) return true
    if (low.startsWith('punkt ') && low.includes('konsekvensen bør presiseres praktisk ved å forklare hva')) return true
    const genericPhrases = [
      'forbedre teksten',
      'presiser teksten',
      'legg til mer informasjon',
      'oppdater rapporten',
      'se forbedringsforslag',
      'vurder å',
      'oppdater innholdet',
      'tydelig adresserer funnet',
      'beskriv konkret hva som mangler'
    ]
    return genericPhrases.some((p) => low.includes(p))
  }

  /** Split recommendation into problem (top) and fix (green box only). "Slik retter du:" belongs only in the green box. */
  const splitProblemAndFix = (text: string): { problemPart: string; fixPart: string } => {
    if (!text || typeof text !== 'string') return { problemPart: '', fixPart: '' }
    const marker = /Slik retter du\s*:\s*/i
    const idx = text.search(marker)
    if (idx === -1) return { problemPart: text.trim(), fixPart: '' }
    const problemPart = text.slice(0, idx).trim()
    const fixPart = text.slice(idx).replace(marker, '').trim()
    return { problemPart, fixPart }
  }

  const normalizeForTextCompare = (text: string | undefined): string =>
    (text || '').replace(/\s+/g, ' ').trim().toLowerCase()

  const buildComponentEvidenceBlob = (component: ComponentFinding): string => {
    const parts: string[] = [
      component.component_id || '',
      component.component_title || '',
      component.location || '',
    ]
    ;(component.issues || []).forEach((issue) => {
      parts.push(issue.summary || '', issue.details || '')
      ;(issue.evidence || []).forEach((evidence) => {
        parts.push(evidence.heading || '', evidence.snippet || '', evidence.match_explain || '')
      })
    })
    ;(component.deductions || []).forEach((deduction) => {
      parts.push(deduction.reason || '')
      ;(deduction.evidence || []).forEach((evidence) => {
        parts.push(evidence.heading || '', evidence.snippet || '', evidence.match_explain || '')
      })
    })
    return normalizeForTextCompare(parts.join('\n'))
  }

  const hasVisibleArkatFieldSignal = (
    component: ComponentFinding,
    field: 'arsak' | 'risiko' | 'konsekvens' | 'anbefalt_tiltak'
  ): boolean => {
    const blob = buildComponentEvidenceBlob(component)
    const title = normalizeForTextCompare(component.component_title)
    if (!blob) return false

    if (field === 'arsak') {
      return (
        /\b(?:årsak|arsak)\b/.test(blob) ||
        /\b(?:skyldes|som følge av|på grunn av|har sin sannsynlige årsak i|årsaken er)\b/.test(blob) ||
        (title.includes('ventilasjon') && /\b(?:mekanisk avtrekk|tilluft|lufting|luftutskiftning)\b/.test(blob))
      )
    }

    if (field === 'risiko') {
      return (
        /\brisiko\b/.test(blob) ||
        /\b(?:kan ikke utelukkes|kan føre til|kan medføre|skjulte skader|jevnlig ettersyn|oppfølging)\b/.test(blob) ||
        (
          title.includes('varmtvannsbereder') &&
          /\b(?:bereder|varmtvannsbereder)\b/.test(blob) &&
          /\b(?:alder|begrenset inspeksjonsmulighet|forventet levetid)\b/.test(blob)
        ) ||
        (
          title.includes('ventilasjon') &&
          /\b(?:mekanisk avtrekk|tilluft|lufting|luftutskiftning)\b/.test(blob)
        )
      )
    }

    if (field === 'konsekvens') {
      return (
        /\bkonsekvens\b/.test(blob) ||
        /\b(?:redusert gjenstående brukstid|redusert levetid|medfører|for kjøper|må påregne|behov for tiltak|mugg|svertesopp|redusert inneklima)\b/.test(blob)
      )
    }

    if (field === 'anbefalt_tiltak') {
      return /\b(?:anbefalt tiltak|anbefalte tiltak|det anbefales|bør utbedres|bør undersøkes|bør kontrolleres|må undersøkes nærmere)\b/.test(blob)
    }

    return false
  }

  const getDisplayedArkatStatus = (
    component: ComponentFinding,
    field: 'arsak' | 'risiko' | 'konsekvens' | 'anbefalt_tiltak'
  ): ArkatField['status'] => {
    const current = component.arkat?.[field]?.status ?? 'unclear'
    if (current !== 'unclear') return current
    if ((component.arkat?.[field]?.comment || '').trim()) return current
    return hasVisibleArkatFieldSignal(component, field) ? 'present' : current
  }

  const buildVisibleProblemAndFix = (
    combinedText: string,
    pointId?: string,
    fallbackFix?: string
  ): { problemText: string; fixText: string } => {
    const { problemPart, fixPart } = splitProblemAndFix(combinedText)
    const problemText =
      pointId && problemPart && improvementTextRefersToDifferentPoint(problemPart, pointId)
        ? ''
        : problemPart
    const fixText = formatFeedbackText(fixPart || fallbackFix || '')
    if (!fixText || isTooGenericSuggestedRewrite(fixText)) {
      return { problemText, fixText: '' }
    }
    if (normalizeForTextCompare(problemText) === normalizeForTextCompare(fixText)) {
      return { problemText, fixText: '' }
    }
    return { problemText, fixText }
  }

  const buildDeductionSummariesFromAnalysis = (analysisPayload: AnalysisV14): DeductionSummary[] => {
    const items = Array.isArray(analysisPayload.findings) ? analysisPayload.findings : []
    const issueByRule: Record<string, Issue> = {}
    items.forEach((component) => {
      const issues = Array.isArray(component.issues) ? component.issues : []
      issues.forEach((issue) => {
        const ruleRefs = Array.isArray(issue.rule_refs) ? issue.rule_refs : []
        ruleRefs.forEach((rule) => {
          if (!issueByRule[rule]) {
            issueByRule[rule] = issue
          }
        })
      })
    })
    const summaries: DeductionSummary[] = []
    items.forEach((component) => {
      const deductions = Array.isArray(component.deductions) ? component.deductions : []
      deductions.forEach((deduction) => {
        if (!deduction || typeof deduction.points !== 'number' || deduction.points <= 0) {
          return
        }
        const issue = issueByRule[deduction.rule_id]
        const severityRaw = issue?.severity
          || (deduction.category_id === 'F' ? 'high' : 'medium')
        const severity = severityRaw === 'info' ? 'low' : severityRaw
        summaries.push({
          rule_id: deduction.rule_id,
          points: deduction.points,
          reason: issue?.summary || deduction.reason || deduction.rule_id,
          category: deduction.category_id,
          severity: severity as DeductionSummary['severity'],
          point_id: component.component_id,
          component_title: component.component_title,
          suggestion: issue?.details || deduction.reason
        })
      })
    })
    const scoreCaps = (analysisPayload as any).score_caps
    if (Array.isArray(scoreCaps)) {
      scoreCaps.forEach((cap) => {
        if (!cap || typeof cap.points_deducted !== 'number' || cap.points_deducted <= 0) {
          return
        }
        const ruleIds = Array.isArray(cap.rule_ids) ? cap.rule_ids.join(', ') : ''
        const reason = ruleIds
          ? `Scoretak er aktivert (maks ${cap.max_total_score}). Utløst av: ${ruleIds}.`
          : `Scoretak er aktivert (maks ${cap.max_total_score}).`
        summaries.push({
          rule_id: 'SCORE_CAP',
          points: cap.points_deducted,
          reason,
          category: undefined,
          severity: 'high',
          suggestion: 'Utbedre forholdet som utløser scoretaket for å heve totalscoren.'
        })
      })
    }
    return summaries
  }

  const buildDeductionSummariesFromFeedback = (items: FeedbackFinding[]): DeductionSummary[] => {
    return items
      .filter((finding) => typeof finding.deduction === 'number' && finding.deduction > 0 && categoryHasScoreDeduction(inferCategoryFromRuleId(finding.rule_id)))
      .map((finding) => ({
        rule_id: finding.rule_id,
        points: finding.deduction || 0,
        reason: finding.message || finding.rule_id,
        category: inferCategoryFromRuleId(finding.rule_id),
        severity: finding.severity === 'info' ? 'low' : finding.severity,
        point_id: finding.point_id,
        suggestion: finding.what_to_change || finding.message,
        exampleFix: finding.example_fix?.good_example
      }))
  }

  /** Parse point id from title/message (e.g. "Punkt 4 (TG2) mangler..." -> "4", "Punkt 10.5 (TG3)..." -> "10.5"). */
  const parsePointFromTitle = (title: string): string | undefined => {
    if (!title || typeof title !== 'string') return undefined
    const m = title.match(/(?:Punkt|punkt)\s+(\d+(?:\.\d+)*)/i)
    return m ? m[1] : undefined
  }

  /** True if text clearly refers to a different point (e.g. "TG2-punkt 10.5" when currentPointId is "4"). */
  const improvementTextRefersToDifferentPoint = (text: string | undefined, currentPointId: string | undefined): boolean => {
    if (!text || !currentPointId) return false
    const lower = text.toLowerCase()
    const pointRefs = lower.match(/(?:punkt|pkt)\s*(\d+(?:\.\d+)*)/g)
    if (!pointRefs) return false
    const normalizedCurrent = currentPointId.replace(/\s/g, '')
    for (const ref of pointRefs) {
      const m = ref.match(/(\d+(?:\.\d+)*)/)
      if (m && m[1] !== normalizedCurrent) return true
    }
    return false
  }

  /** Find improvement that applies to this deduction (same point + matching theme) so we show admin-level guidance. */
  const findMatchingImprovement = (
    deduction: DeductionSummary,
    improvements: Improvement[] | undefined
  ): Improvement | undefined => {
    if (!improvements?.length) return undefined
    const pointId = (deduction.point_id || '').toString().trim()
    const ruleId = (deduction.rule_id || '').toLowerCase()
    const reason = (deduction.reason || '').toLowerCase()
    const appliesToPoint = (imp: Improvement) => {
      const to = imp.applies_to
      if (!to || !Array.isArray(to) || to.length === 0) return true
      return to.some((id) => String(id).toLowerCase() === pointId || String(id).toLowerCase() === 'global')
    }
    const titleMatches = (imp: Improvement, keywords: string[]) => {
      const t = (imp.title || '').toLowerCase()
      const w = (imp.what_to_change || '').toLowerCase()
      return keywords.some((k) => t.includes(k.toLowerCase()) || w.includes(k.toLowerCase()))
    }
    const candidates = improvements.filter(appliesToPoint)
    if (candidates.length === 0) return undefined

    // When deduction is for a specific point, only use an improvement that refers to that point (avoid overwriting with first improvement for all).
    if (pointId) {
      const pointSpecific = candidates.find((imp) => parsePointFromTitle(imp.title || '') === pointId)
      if (pointSpecific) return pointSpecific
      // No improvement explicitly for this point – do not overwrite; keep deduction's own suggestion/exampleFix.
      return undefined
    }

    if (ruleId.includes('konsekvens_mangler') || reason.includes('manglende konsekvens'))
      return candidates.find((imp) => titleMatches(imp, ['legg til konsekvens', 'konsekvens for kjøper', 'manglende'])) ?? undefined
    if (ruleId.includes('konsekvens_uklart') || reason.includes('uklar konsekvens'))
      return candidates.find((imp) => titleMatches(imp, ['utdyp', 'tekniske konsekvenser', 'kjøperbetydning'])) ?? undefined
    if (ruleId.includes('tiltak_imperativ') || reason.includes('imperativ'))
      return candidates.find((imp) => titleMatches(imp, ['imperativ', 'erstatt', 'veiledende'])) ?? undefined
    if (ruleId.includes('l-fa-01') || reason.includes('ferdigattest'))
      return candidates.find((imp) => titleMatches(imp, ['ferdigattest', 'beskriv konsekvens'])) ?? undefined
    return undefined
  }

  const enrichWithImprovements = (
    list: DeductionSummary[],
    improvements: Improvement[] | undefined
  ): DeductionSummary[] => {
    if (!improvements?.length) return list
    return list.map((item) => {
      const imp = findMatchingImprovement(item, improvements)
      if (!imp) return item
      // Never overwrite with improvement text that clearly refers to another point (e.g. "TG2-punkt 10.5" on a Punkt 4 card)
      const useSuggestion = imp.what_to_change && !improvementTextRefersToDifferentPoint(imp.what_to_change, item.point_id)
      const useExampleFix = imp.suggested_text && !improvementTextRefersToDifferentPoint(imp.suggested_text, item.point_id)
      return {
        ...item,
        suggestion: useSuggestion ? imp.what_to_change : item.suggestion,
        exampleFix: item.exampleFix || (useExampleFix ? imp.suggested_text : undefined)
      }
    })
  }

  const sortedPointsOverview: FeedbackPointOverview[] =
    hasFeedbackV11 && feedbackV11?.points_overview?.length
      ? [...feedbackV11.points_overview].sort((a, b) => {
          const ai = ((a as { display_index?: number }).display_index ?? Number.MAX_SAFE_INTEGER)
          const bi = ((b as { display_index?: number }).display_index ?? Number.MAX_SAFE_INTEGER)
          if (ai !== bi) return ai - bi
          const ac = (a.canonical_id || '').toString()
          const bc = (b.canonical_id || '').toString()
          return ac.localeCompare(bc)
        })
      : []
  const customerPointsOverview = sortedPointsOverview.filter(
    (point) => point.status !== 'incomplete_analysis'
  )
  /** Try to extract point reference from message (e.g. "Punkt 4.2", "i punkt 5.1", "punkt 4.2") */
  const parsePointIdFromMessage = (message: string): string | undefined => {
    if (!message || typeof message !== 'string') return undefined
    const m = message.match(/(?:Punkt|punkt)\s+(\d+(?:\.\d+)*)/i) || message.match(/(?:punkt|ved)\s+(\d+(?:\.\d+)*)/i)
    return m ? m[1] : undefined
  }
  const parsePointFromRuleId = (ruleId: string | undefined): string | undefined => {
    if (!ruleId || typeof ruleId !== 'string') return undefined
    const m = ruleId.match(/\b(P\d{2}[A-Z](?:_[A-Z0-9_]+)?)\b/i)
    return m ? m[1] : undefined
  }
  const resolveDisplayPointId = (pointId: string | undefined, ruleId: string | undefined): string | undefined => {
    const fromRule = parsePointFromRuleId(ruleId)
    if (!pointId) return fromRule
    if (!fromRule) return pointId
    const p = pointId.toUpperCase()
    const r = fromRule.toUpperCase()
    if (p.includes(r) || r.includes(p)) return pointId
    return fromRule
  }
  const buildDeductionSummariesFromV16 = (findings: AllFindingV16[]): DeductionSummary[] =>
    findings
      .filter((f) => publicBandToPoints(f.deduction_band) > 0 && isScoreReconciledItem(f))
      .map((f) => ({
        rule_id: f.finding_id,
        points: f.deduction_band === 'Høyt trekk' ? 5 : f.deduction_band === 'Middels trekk' ? 3 : f.deduction_band === 'Lavt trekk' ? 1 : 0,
        reason: f.title,
        category: f.category,
        severity: (f.severity === 'critical' ? 'critical' : f.severity === 'major' ? 'high' : f.severity === 'minor' ? 'low' : 'medium') as DeductionSummary['severity'],
        suggestion: f.recommended_fix_text,
        exampleFix: f.suggested_rewrite_text || f.recommended_fix_text,
        point_id: resolveDisplayPointId(
          (f as { exact_point_id?: string }).exact_point_id
            ?? (f as { point_id?: string }).point_id
            ?? parsePointIdFromMessage(f.message)
            ?? parsePointIdFromMessage(f.title)
            ?? parsePointIdFromMessage((f as { exact_point_title?: string }).exact_point_title || '')
            ?? parsePointIdFromMessage((f as { exact_point_text?: string }).exact_point_text || '')
            ?? (f as { location?: string }).location,
          f.finding_id
        ),
        evidence_snippets: f.evidence_snippets?.length ? f.evidence_snippets : undefined
      }))
  const buildDeductionSummariesFromTopIssuesV16 = (issues: TopIssueV16[]): DeductionSummary[] =>
    issues
      .map((issue) => ({
        rule_id: issue.rule_id || issue.title,
        points: publicBandToPoints(issue.deduction_band),
        reason: issue.title,
        category: issue.category,
        severity: (issue.severity === 'critical' ? 'critical' : issue.severity === 'major' ? 'high' : issue.severity === 'minor' ? 'low' : 'medium') as DeductionSummary['severity'],
        suggestion: issue.recommended_fix_text,
        exampleFix: issue.suggested_rewrite_text || issue.recommended_fix_text,
        point_id: resolveDisplayPointId(
          issue.point_id ?? parsePointIdFromMessage(issue.message) ?? parsePointIdFromMessage(issue.title),
          issue.rule_id || issue.title
        ),
      }))
      .filter((item) => (item.points || 0) > 0 && categoryHasScoreDeduction(item.category || inferCategoryFromRuleId(item.rule_id)))
  const improvementsForFixes = hasV16 && howToImproveV16?.length && !analysis?.improvements?.length
    ? howToImproveV16.filter((h) => categoryHasScoreDeduction(h.category)).map((h) => ({
        title: h.title,
        priority: 'medium' as const,
        what_to_change: h.recommended_fix_text,
        suggested_text: h.suggested_rewrite_text || h.recommended_fix_text
      }))
    : hasV16 && scoreReconciledTopIssuesV16.length && !analysis?.improvements?.length
    ? scoreReconciledTopIssuesV16.map((h) => ({
        title: h.title,
        priority: 'medium' as const,
        what_to_change: h.recommended_fix_text || h.message,
        suggested_text: h.suggested_rewrite_text || h.recommended_fix_text || h.message
      }))
    : analysis?.improvements
  const allDeductions: DeductionSummary[] = (() => {
    const fromV16 = hasV16 && allFindingsV16?.length ? buildDeductionSummariesFromV16(allFindingsV16) : []
    const fromTopIssuesV16 = !fromV16.length && hasV16 && topIssuesV16?.length ? buildDeductionSummariesFromTopIssuesV16(topIssuesV16) : []
    const fromFeedback = !fromV16.length && !fromTopIssuesV16.length && feedbackV11?.findings?.length ? buildDeductionSummariesFromFeedback(feedbackV11.findings) : []
    const fromV14 = (!fromFeedback.length && !fromTopIssuesV16.length && !fromV16.length && hasV14 && analysis)
      ? buildDeductionSummariesFromAnalysis(analysis)
      : []
    const merged = fromV16.length ? fromV16 : fromTopIssuesV16.length ? fromTopIssuesV16 : [...fromFeedback, ...fromV14]
    const seen = new Set<string>()
    return merged.map((item) => ({
      ...item,
      point_id: resolveDisplayPointId(item.point_id, item.rule_id)
    })).filter((item) => {
      if ((item.points || 0) > 0 && !categoryHasScoreDeduction(item.category || inferCategoryFromRuleId(item.rule_id))) {
        return false
      }
      const key = `${item.point_id || 'GLOBAL'}|${item.rule_id}|${item.reason}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  })()

  const toParentBucket = (rawPointId: string | null | undefined): string | undefined => {
    if (!rawPointId) return undefined
    const up = rawPointId.toUpperCase().trim()
    const canonicalMatch = up.match(/^P(\d{2})/)
    if (canonicalMatch) return `P${canonicalMatch[1]}`
    const numericMatch = up.match(/^(\d{1,2})(?:\.\d+)*$/)
    if (numericMatch) return `P${String(parseInt(numericMatch[1], 10)).padStart(2, '0')}`
    return undefined
  }

  const deductionPointsByParentBucket = (() => {
    const map = new Map<string, number>()
    allDeductions.forEach((item) => {
      if (!item || (item.points || 0) <= 0) return
      const bucket = toParentBucket(item.point_id)
      if (!bucket) return
      map.set(bucket, (map.get(bucket) || 0) + (item.points || 0))
    })
    return map
  })()
  const deductionBandByParentBucket = (() => {
    const map = new Map<string, 'high' | 'medium' | 'low'>()
    allDeductions.forEach((item) => {
      if (!item || (item.points || 0) <= 0) return
      const bucket = toParentBucket(item.point_id)
      if (!bucket) return
      const nextBand: 'high' | 'medium' | 'low' =
        (item.points || 0) >= 5 ? 'high' : (item.points || 0) >= 3 ? 'medium' : 'low'
      const existing = map.get(bucket)
      const rank = { low: 1, medium: 2, high: 3 }
      if (!existing || rank[nextBand] > rank[existing]) {
        map.set(bucket, nextBand)
      }
    })
    return map
  })()
  const scoredRuleIds = new Set(
    allDeductions
      .filter((item) => (item.points || 0) > 0)
      .map((item) => (item.rule_id || '').toString().trim())
      .filter(Boolean)
  )

  const sortedDeductions = [...allDeductions].sort((a, b) => {
    const priorityA = improvementPriorityOrder[a.severity] ?? 99
    const priorityB = improvementPriorityOrder[b.severity] ?? 99
    if (priorityA !== priorityB) return priorityA - priorityB
    return (b.points || 0) - (a.points || 0)
  })

  const dedupedFixes = (() => {
    const seen = new Map<string, DeductionSummary>()
    sortedDeductions.forEach((item) => {
      const key = `${item.point_id || 'GLOBAL'}::${item.reason}`
      const existing = seen.get(key)
      if (!existing) {
        seen.set(key, { ...item })
        return
      }
      const existingPriority = improvementPriorityOrder[existing.severity] ?? 99
      const itemPriority = improvementPriorityOrder[item.severity] ?? 99
      seen.set(key, {
        ...existing,
        points: (existing.points || 0) + (item.points || 0),
        severity: itemPriority < existingPriority ? item.severity : existing.severity,
        suggestion: existing.suggestion || item.suggestion,
        exampleFix: existing.exampleFix || item.exampleFix,
      })
    })
    return Array.from(seen.values())
  })()

  const deductionsForAlleTrekk = enrichWithImprovements(sortedDeductions, improvementsForFixes)
  const enrichedDedupedFixes = enrichWithImprovements(dedupedFixes, improvementsForFixes)
  const visibleDeductionPointsTotal = deductionsForAlleTrekk.reduce((sum, item) => sum + (item.points || 0), 0)
  const hiddenDeductionGap = totalDeductionFromScore > visibleDeductionPointsTotal
    ? totalDeductionFromScore - visibleDeductionPointsTotal
    : 0

  const impactLevel = (item: DeductionSummary): 'high' | 'medium' | 'low' => {
    if ((item.points || 0) >= 5) return 'high'
    if ((item.points || 0) >= 3) return 'medium'
    return 'low'
  }
  const impactLabel = (level: 'high' | 'medium' | 'low') => {
    if (level === 'high') return 'Høy påvirkning'
    if (level === 'medium') return 'Middels påvirkning'
    return 'Lav påvirkning'
  }
  const highPriorityDeductions = enrichedDedupedFixes.filter((item) => impactLevel(item) === 'high')
  const mediumPriorityDeductions = enrichedDedupedFixes.filter((item) => impactLevel(item) === 'medium')
  const lowPriorityDeductions = enrichedDedupedFixes.filter((item) => impactLevel(item) === 'low')


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
                    Analyseresultater
                  </h1>
                  <p className="text-xs text-gray-500">Rapport nr. {report.id}</p>
                </div>
              </div>
              <nav className="hidden md:flex space-x-4">
                <button
                  onClick={() => router.push('/')}
                  className="text-gray-600 hover:text-blue-600 transition-colors"
                >
                  Last opp
                </button>
                <button
                  onClick={() => router.push('/history')}
                  className="text-gray-600 hover:text-blue-600 transition-colors"
                >
                  Historikk
                </button>
              </nav>
            </div>
            <button
              onClick={() => router.push('/')}
              className="px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 text-white rounded-lg font-medium hover:from-blue-700 hover:to-indigo-700 transition-all shadow-md hover:shadow-lg"
            >
              Last opp ny rapport
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
                  {scoreTotal !== null && scoreTotal !== undefined && (
                    <div className="relative inline-block">
                      <img
                        src="/Trygghetsscore_Topp_s.png"
                        alt="Trygghetsscore"
                        className="h-32 w-auto"
                      />
                      {/* Score number in the middle of the image */}
                      <div className="absolute inset-0 flex items-center justify-center">
                        <div className="text-5xl font-bold text-red-600 drop-shadow-[0_2px_4px_rgba(255,255,255,0.8)]">
                          {Math.max(0, Math.min(100, Math.round(scoreTotal))).toFixed(0)}
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

          {hasFeedbackV11 && feedbackV11 && customerPointsOverview.length > 0 && (
            <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
              <div className="flex items-start justify-between mb-6">
                <div>
                  <h2 className="text-2xl font-bold text-gray-900">Punkt-for-punkt oversikt</h2>
                  <p className="text-sm text-gray-500 mt-1">Fast liste – funnet i rapporten eller ikke funnet</p>
                </div>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                {customerPointsOverview.map((point, idx) => {
                  const canonical = (point as { canonical_id?: string }).canonical_id
                  const parentBucket = toParentBucket(canonical || point.point_id)
                  const fallbackDeduction = parentBucket ? (deductionPointsByParentBucket.get(parentBucket) || 0) : 0
                  const pointDeductionTotal = (point as { deduction_total?: number }).deduction_total || 0
                  const pointFindingIds = ((point as { finding_ids?: string[] }).finding_ids || [])
                    .map((id) => (id || '').toString().trim())
                    .filter(Boolean)
                  const hasScoredFindingId = pointFindingIds.some((id) => scoredRuleIds.has(id))
                  const hasActiveFindings =
                    point.status !== 'NOT_FOUND_IN_REPORT'
                    && (pointDeductionTotal > 0 || fallbackDeduction > 0 || hasScoredFindingId)
                  const effectiveSummary = point.status === 'NOT_FOUND_IN_REPORT'
                    ? 'Ikke vurdert i rapport'
                    : (hasActiveFindings ? 'Avvik funnet' : (point.summary || 'OK'))
                  const fallbackBand = parentBucket ? deductionBandLabel(deductionBandByParentBucket.get(parentBucket)) : null
                  const effectiveBand = deductionBandLabel((point as { deduction_band?: string }).deduction_band)
                    || fallbackBand
                    || (hasActiveFindings ? 'Lavt trekk' : null)
                  return (
                    <div
                      key={(point as { canonical_id?: string }).canonical_id || point.point_id || `p-${idx}`}
                      className={`border border-gray-200 rounded-xl p-5 bg-white shadow-sm ${point.parent_id ? 'ml-4 md:ml-6 border-l-4 border-l-blue-200' : ''}`}
                    >
                      <div className="flex items-start justify-between gap-3 mb-2">
                        <div>
                          <p className="text-xs font-semibold uppercase text-gray-500">
                            {point.point_id ? `Punkt ${point.point_id}` : `P${String((point as { display_index?: number }).display_index || idx + 1).padStart(2, '0')}`}
                          </p>
                          <h3 className="text-lg font-semibold text-gray-900">
                            {(point as { title_display?: string }).title_display || point.title}
                            {(point.legal_status === 'ikke lovpålagt' || (point as { ui_badge?: string }).ui_badge) && (
                              <span className="ml-2 text-sm font-normal text-amber-700">
                                {(point as { ui_badge?: string }).ui_badge || '(ikke lovpålagt)'}
                              </span>
                            )}
                          </h3>
                        </div>
                        <div className="text-right">
                          <span className={`px-3 py-1 text-xs font-semibold rounded-full ${pointStatusClasses(point.status)}`}>
                            {point.status === 'FOUND' ? 'Funnet' : point.status === 'NOT_FOUND_IN_REPORT' ? 'Ikke funnet' : translate(point.status)}
                          </span>
                          {point.status !== 'NOT_FOUND_IN_REPORT' && (
                            <p className="text-xs text-gray-500 mt-2">{translate(point.tg)}</p>
                          )}
                        </div>
                      </div>
                      <p className="text-sm text-gray-700">{effectiveSummary}</p>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {hasFeedbackV11 && feedbackV11 && (
            <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
              <h2 className="text-2xl font-bold text-gray-900 mb-6">Tilbakemeldinger</h2>
              {feedbackV11.findings.length === 0 ? (
                <p className="text-gray-700">Ingen forbedringspunkter ble funnet.</p>
              ) : (
                <div className="space-y-4">
                  {feedbackV11.findings.map((finding, index) => (
                    <article key={`${finding.finding_id}-${index}`} className="rounded-xl border border-gray-200 p-5">
                      <p className="font-semibold text-gray-900">{finding.message}</p>
                      {finding.what_to_change && (
                        <div className="mt-3">
                          <p className="text-sm font-semibold text-gray-800">Hva bør forbedres</p>
                          <p className="mt-1 text-sm text-gray-700">{finding.what_to_change}</p>
                        </div>
                      )}
                      {finding.evidence?.snippet && (
                        <div className="mt-3 rounded-lg bg-gray-50 p-3">
                          <p className="text-xs font-semibold text-gray-600">Dokumentasjon fra rapporten</p>
                          <p className="mt-1 text-sm text-gray-700">{finding.evidence.snippet}</p>
                        </div>
                      )}
                    </article>
                  ))}
                </div>
              )}
            </div>
          )}

          {typeof scoreTotal === 'number' && (
            <>
              <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
                <div className="flex items-start justify-between mb-6">
                  <div>
                    <h2 className="text-2xl font-bold text-gray-900">Trekk per kategori</h2>
                    <p className="text-sm text-gray-500 mt-1">Kategoritrekk mot totalpoeng</p>
                  </div>
                  <div className="hidden md:flex items-center space-x-2 text-xs text-gray-500">
                    <span className="w-2 h-2 rounded-full bg-red-500"></span>
                    <span>Høyere trekk</span>
                    <span className="w-2 h-2 rounded-full bg-green-500 ml-3"></span>
                    <span>Lavere trekk</span>
                  </div>
                </div>
                <div className="mb-6 rounded-xl border border-gray-200 bg-slate-50 p-4 text-sm text-gray-700">
                  {(() => {
                    const reconciliation = (analysis as AnalysisV16 | undefined)?.score_reconciliation
                    const categorySum = scoreByCategoryDisplay.reduce((s, c) => s + (c.deduction || 0), 0)
                    const actualDeduction = Math.max(0, 100 - scoreTotal)
                    const other = Math.max(0, actualDeduction - categorySum)
                    const scoreStart = reconciliation?.score_start ?? 100
                    return (
                      <div className="space-y-1">
                        <p>
                          <span className="font-semibold">Scoring formula:</span> Score = {scoreStart} − (sum category deductions) − (other adjustments)
                        </p>
                        <p>
                          <span className="font-semibold">This report:</span> {scoreStart} − {categorySum} − {other} = {scoreTotal}
                        </p>
                        {reconciliation && (
                          <p className="text-xs text-gray-600">
                            Reconciled: {reconciliation.reconciles ? 'yes' : 'no'} (categories {reconciliation.category_deduction_total} + other {reconciliation.other_deduction_total} = total {reconciliation.deduction_total})
                          </p>
                        )}
                      </div>
                    )
                  })()}
                </div>
                {scoreByCategoryDisplay.length > 0 && (
                  <div className="grid md:grid-cols-2 gap-5">
                    {scoreByCategoryDisplay.map((category) => {
                    const deductionPct = (category.deduction / Math.max(category.max_deduction, 1)) * 100
                    const bandPct =
                      category.deduction > 0
                        ? deductionPct
                        : (category as ScoreByCategoryDisplay).deduction_band === 'Høyt trekk'
                          ? 100
                          : (category as ScoreByCategoryDisplay).deduction_band === 'Middels trekk'
                            ? 50
                            : (category as ScoreByCategoryDisplay).deduction_band === 'Lavt trekk'
                              ? 25
                              : deductionPct
                      return (
                      <div key={category.category_id} className="relative overflow-hidden rounded-2xl border border-gray-200 bg-gradient-to-br from-white to-slate-50 p-5 shadow-sm">
                        <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-red-400 via-amber-400 to-green-400"></div>
                        <div className="flex items-start justify-between gap-4">
                          <div>
                            <div className="flex items-center space-x-2 mb-1">
                              <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                                Kategori {category.category_id}
                              </span>
                            </div>
                            <p className="text-lg font-semibold text-gray-900">{category.category_name}</p>
                          </div>
                          <div className="text-right">
                            <p className="text-3xl font-bold text-gray-900">-{category.deduction}</p>
                            {(category as ScoreByCategoryDisplay).deduction_band && (
                              <p className="text-xs font-medium text-gray-600">{(category as ScoreByCategoryDisplay).deduction_band}</p>
                            )}
                            <p className="text-xs text-gray-500">maks {category.max_deduction}</p>
                          </div>
                        </div>
                        {(category as ScoreByCategoryDisplay).summary && (
                          <p className="text-sm text-gray-600 mt-2">{(category as ScoreByCategoryDisplay).summary}</p>
                        )}
                        <div className="mt-4">
                          <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
                            <span>Trekk</span>
                            <span>{category.deduction}/{category.max_deduction}</span>
                          </div>
                          <div className="h-2 rounded-full bg-gray-200 overflow-hidden">
                            <div
                              className="h-full rounded-full bg-gradient-to-r from-red-500 via-amber-400 to-green-400"
                              style={{ width: `${Math.min(100, bandPct)}%` }}
                            ></div>
                          </div>
                        </div>
                      </div>
                      )
                    })}
                  </div>
                )}
              </div>

              <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
                <h2 className="text-2xl font-bold text-gray-900 mb-4">Viktigste score-drivere</h2>
                <div className="space-y-4">
                  {topScoreDriversSource.length === 0 ? (
                    <p className="text-sm text-gray-600">Ingen score-drivere listet.</p>
                  ) : (
                    topScoreDriversSource.map((driver, index) => {
                      const severityConfig = getSeverityConfig(driver.severity)
                      const reason = 'reason' in driver ? driver.reason : (driver as unknown as TopIssueV16).message
                      const recFix = (driver as unknown as TopIssueV16).recommended_fix_text
                      return (
                        <div key={index} className={`border-l-4 ${severityConfig.border} ${severityConfig.bg} rounded-xl p-5`}>
                          <div className="flex items-start justify-between mb-2">
                            <div>
                              <h3 className="text-lg font-bold text-gray-900">{formatFeedbackText(driver.title)}</h3>
                              <p className="text-sm text-gray-700">{formatFeedbackText(reason)}</p>
                              {recFix && (
                                <p className="text-sm text-green-800 mt-2 bg-green-50 border border-green-200 rounded-lg p-2">
                                  <span className="font-semibold">Anbefaling: </span>{formatFeedbackText(recFix)}
                                </p>
                              )}
                            </div>
                            {driver.deduction_points > 0 && (
                              <span className={`px-3 py-1 rounded-full text-sm font-bold ${severityConfig.badge}`}>
                                -{driver.deduction_points}
                              </span>
                            )}
                          </div>
                          {driver.rule_refs?.length > 0 && (
                            <p className="text-xs text-gray-500 mb-3">
                              Kategori: {Array.from(new Set(driver.rule_refs)).map(ref => translate(ref)).join(', ')}
                            </p>
                          )}
                          {driver.evidence && driver.evidence.length > 0 && (
                            <div className="bg-white/80 rounded-lg border border-gray-200 p-3 text-sm text-gray-700">
                              <p className="font-semibold text-gray-900 mb-1">Dokumentasjon</p>
                              <p>
                                {driver.evidence[0].heading}
                                {driver.evidence[0].point_id ? ` (Punkt ${driver.evidence[0].point_id}, ${translate(driver.evidence[0].tg)})` : ''}
                              </p>
                              <p>{translate('Page')} {driver.evidence[0].page} • {translate(driver.evidence[0].source)}</p>
                              <p className="italic mt-1">"{driver.evidence[0].snippet}"</p>
                              <p className="text-xs text-gray-500 mt-1">{translate(driver.evidence[0].match_explain)}</p>
                            </div>
                          )}
                        </div>
                      )
                    })
                  )}
                </div>
              </div>

              <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
                <h2 className="text-2xl font-bold text-gray-900 mb-4">
                  {visibleFindingsV16?.length ? 'Alle funn' : 'Funn per bygningsdel'}
                </h2>
                <div className="space-y-6">
                  {visibleFindingsV16?.length ? (
                    visibleFindingsV16.map((f, index) => {
                      const severityConfig = getSeverityConfig(f.severity)
                      const pointId = resolveDisplayPointId(
                        parsePointIdFromMessage(f.message) ?? parsePointIdFromMessage(f.title) ?? (f as { point_id?: string }).point_id,
                        f.finding_id
                      )
                      const normalizedTitle = normalizeForTextCompare(f.title)
                      const normalizedMessage = normalizeForTextCompare(f.message)
                      const guidanceFromImprove = (howToImproveV16 || []).find((item) => {
                        const itemPointId = resolveDisplayPointId(
                          parsePointIdFromMessage(item.title) ?? (item as { point_id?: string }).point_id,
                          item.title
                        )
                        if (pointId && itemPointId && pointId !== itemPointId) return false
                        const itemTitle = normalizeForTextCompare(item.title)
                        const itemFix = normalizeForTextCompare(item.recommended_fix_text)
                        return (
                          (normalizedTitle && itemTitle && (itemTitle.includes(normalizedTitle) || normalizedTitle.includes(itemTitle))) ||
                          (normalizedMessage && itemTitle && (itemTitle.includes(normalizedMessage) || normalizedMessage.includes(itemTitle))) ||
                          (normalizedTitle && itemFix && itemFix.includes(normalizedTitle))
                        )
                      })
                      const guidanceFromTopIssue = (topIssuesV16 || []).find((item) => {
                        const itemPointId = resolveDisplayPointId(
                          item.point_id ?? parsePointIdFromMessage(item.message) ?? parsePointIdFromMessage(item.title),
                          item.rule_id || item.title
                        )
                        if (pointId && itemPointId && pointId !== itemPointId) return false
                        const itemTitle = normalizeForTextCompare(item.title)
                        const itemMessage = normalizeForTextCompare(item.message)
                        return (
                          (normalizedTitle && itemTitle && (itemTitle.includes(normalizedTitle) || normalizedTitle.includes(itemTitle))) ||
                          (normalizedMessage && itemMessage && (itemMessage.includes(normalizedMessage) || normalizedMessage.includes(itemMessage)))
                        )
                      })
                      const recommendedFixText = f.recommended_fix_text || guidanceFromImprove?.recommended_fix_text || guidanceFromTopIssue?.recommended_fix_text || ''
                      const suggestedRewriteText = f.suggested_rewrite_text || guidanceFromImprove?.suggested_rewrite_text || guidanceFromTopIssue?.suggested_rewrite_text || guidanceFromTopIssue?.recommended_fix_text || ''
                      return (
                        <div key={index} className={`border-l-4 ${severityConfig.border} ${severityConfig.bg} rounded-xl p-5`}>
                          <div className="flex items-start justify-between gap-4">
                            <div>
                              <p className="text-xs font-semibold uppercase text-gray-500">Kategori {f.category}</p>
                              <h3 className="text-lg font-bold text-gray-900 mt-1">{formatFeedbackText(f.title)}</h3>
                              <p className="text-sm text-gray-700 mt-2">{formatFeedbackText(f.message)}</p>
                              {recommendedFixText && (
                                <div className="mt-3 bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-900">
                                  <p className="font-semibold mb-1">Forbedringsveiledning:</p>
                                  <p>{formatFeedbackText(recommendedFixText)}</p>
                                </div>
                              )}
                              {suggestedRewriteText && !isTooGenericSuggestedRewrite(suggestedRewriteText) && (
                                <div className="mt-3 bg-emerald-50 border border-emerald-200 rounded-lg p-3 text-sm text-emerald-900">
                                  <p className="font-semibold mb-1">Eksempeltekst du kan bruke:</p>
                                  <p className="italic">{formatFeedbackText(suggestedRewriteText)}</p>
                                </div>
                              )}
                            </div>
                            <span className={`px-3 py-1 rounded-full text-sm font-bold ${severityConfig.badge}`}>
                              {f.deduction_band ?? translate(f.severity)}
                            </span>
                          </div>
                          {f.evidence_snippets && f.evidence_snippets.length > 0 && (
                            <div className="mt-4 pt-4 border-t border-gray-200">
                              <p className="text-xs font-semibold text-gray-500 mb-2">Dokumentasjon</p>
                              <ul className="text-sm text-gray-700 space-y-1">
                                {(Array.isArray(f.evidence_snippets) ? (f.evidence_snippets as string[]) : []).slice(0, 3).map((snip: string, i: number) => (
                                  <li key={i} className="italic">&quot;{snip}&quot;</li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>
                      )
                    })
                  ) : null}

                  {analysis?.findings?.length ? (
                  <>
                  {visibleFindingsV16?.length ? (
                    <div className="pt-2">
                      <h3 className="text-xl font-bold text-gray-900">Funn per bygningsdel</h3>
                    </div>
                  ) : null}
                  {analysis?.findings?.map((component, index) => (
                    <div key={index} className="border border-gray-200 rounded-xl p-5">
                      <div className="flex items-center justify-between mb-3">
                        <div>
                          <h3 className="text-lg font-bold text-gray-900">{component.component_title}</h3>
                          <p className="text-sm text-gray-500">
                            Punkt {component.component_id} {component.location ? `• ${component.location}` : ''}
                          </p>
                        </div>
                        <span className="px-3 py-1 bg-gray-100 rounded-full text-sm font-semibold text-gray-700">
                          {translate(component.tg)}
                        </span>
                      </div>

                      <div className="bg-gray-50 rounded-lg p-4 mb-4">
                        <p className="text-sm font-semibold text-gray-900 mb-2">ARKAT (Årsak–Risiko–Konsekvens–Anbefalt tiltak)</p>
                        <div className="grid md:grid-cols-2 gap-3 text-sm text-gray-700">
                          <div>
                            <span className="font-semibold">Årsak:</span> {translate(getDisplayedArkatStatus(component, 'arsak'))}
                          </div>
                          <div>
                            <span className="font-semibold">Risiko:</span> {translate(getDisplayedArkatStatus(component, 'risiko'))}
                          </div>
                          <div>
                            <span className="font-semibold">Konsekvens:</span> {translate(getDisplayedArkatStatus(component, 'konsekvens'))}
                          </div>
                          <div>
                            <span className="font-semibold">Anbefalt tiltak:</span> {translate(getDisplayedArkatStatus(component, 'anbefalt_tiltak'))}
                          </div>
                        </div>
                      </div>

                      <div className="space-y-3">
                        {(component.issues ?? []).map((issue, issueIndex) => {
                          const severityConfig = getSeverityConfig(issue.severity)
                          return (
                            <div key={issueIndex} className={`border-l-4 ${severityConfig.border} ${severityConfig.bg} rounded-lg p-4`}>
                              <div className="flex items-start space-x-3">
                                <span className="text-xl">{severityConfig.icon}</span>
                                <div className="flex-1">
                                  <h4 className="font-semibold text-gray-900">{formatFeedbackText(issue.summary)}</h4>
                                  <p className="text-sm text-gray-700 mt-1">{formatFeedbackText(issue.details)}</p>
                                  {issue.rule_refs.length > 0 && (
                                    <p className="text-xs text-gray-500 mt-2">
                                      Regler: {Array.from(new Set(issue.rule_refs)).map(ref => translate(ref)).join(', ')}
                                    </p>
                                  )}
                                  {issue.evidence && issue.evidence.length > 0 && (
                                    <div className="mt-3 bg-white/80 rounded-lg border border-gray-200 p-3 text-sm text-gray-700">
                                      <p className="font-semibold text-gray-900 mb-1">Dokumentasjon</p>
                                      <p>
                                        {issue.evidence[0].heading}
                                        {issue.evidence[0].point_id ? ` (Punkt ${issue.evidence[0].point_id}, ${translate(issue.evidence[0].tg)})` : ''}
                                      </p>
                                      <p>{translate('Page')} {issue.evidence[0].page} • {translate(issue.evidence[0].source)}</p>
                                      <p className="italic mt-1">"{issue.evidence[0].snippet}"</p>
                                      <p className="text-xs text-gray-500 mt-1">{translate(issue.evidence[0].match_explain)}</p>
                                    </div>
                                  )}
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                  </>
                  ) : null}
                </div>
              </div>


              <div className="bg-amber-50 rounded-2xl shadow-lg border border-amber-200 p-8 mb-6">
                <div className="flex items-start justify-between mb-6">
                  <div className="flex items-start space-x-4">
                    <div className="w-12 h-12 bg-orange-500 rounded-xl flex items-center justify-center shadow-md">
                      <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                      </svg>
                    </div>
                    <div>
                      <h2 className="text-3xl font-bold text-gray-900">Hva du må fikse for å øke scoren</h2>
                      <p className="text-gray-600 mt-1">Prioriterte tiltak basert på faktiske poengtrekk</p>
                    </div>
                  </div>
                  <div className="px-4 py-2 bg-white rounded-lg border border-orange-300 text-center">
                    <span className="text-2xl font-bold text-orange-600">{enrichedDedupedFixes.length}</span>
                    <span className="text-sm text-gray-600 block">punkter</span>
                  </div>
                </div>

                <div className="space-y-8">
                  {highPriorityDeductions.length > 0 && (
                    <div>
                      <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
                        <span className="w-2 h-2 bg-red-500 rounded-full mr-2"></span>
                        Høyprioriterte funn
                      </h3>
                      <div className="space-y-4">
                        {highPriorityDeductions.map((improvement, index) => (
                          <div key={index} className="bg-white rounded-xl border border-red-200 shadow-sm p-5">
                            <div className="flex items-start space-x-4">
                              <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
                                <svg className="w-5 h-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M5 19h14a2 2 0 002-2v-5a2 2 0 00-2-2h-2l-3-3H9L6 10H4a2 2 0 00-2 2v5a2 2 0 002 2z" />
                                </svg>
                              </div>
                              <div className="flex-1">
                                <div className="flex items-center justify-between mb-2">
                                  <h4 className="text-lg font-bold text-gray-900">{formatFeedbackText(improvement.reason)}</h4>
                                  <span className={`text-xs uppercase font-semibold px-2 py-1 rounded-full ${improvementBadgeClasses(improvement.severity)}`}>
                                    {impactLabel(impactLevel(improvement))}
                                  </span>
                                </div>
                                {improvement.point_id && (
                                  <p className="text-xs text-gray-500 mb-2">Punkt {improvement.point_id}</p>
                                )}
                                {(() => {
                                  const combined = formatFeedbackText(improvement.exampleFix || improvement.suggestion)
                                  const visible = buildVisibleProblemAndFix(
                                    combined,
                                    improvement.point_id,
                                    formatFeedbackText(improvement.exampleFix || improvement.suggestion)
                                  )
                                  return (visible.problemText || visible.fixText) ? (
                                    <div className="space-y-2">
                                      {visible.problemText && <p className="text-sm text-gray-700">{visible.problemText}</p>}
                                      {visible.fixText && (
                                        <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-900">
                                          <p className="font-semibold mb-1">Slik retter du:</p>
                                          <p className="italic">{visible.fixText}</p>
                                        </div>
                                      )}
                                    </div>
                                  ) : null
                                })()}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {mediumPriorityDeductions.length > 0 && (
                    <div>
                      <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
                        <span className="w-2 h-2 bg-yellow-500 rounded-full mr-2"></span>
                        Middels prioriterte forbedringer
                      </h3>
                      <div className="space-y-4">
                        {mediumPriorityDeductions.map((improvement, index) => (
                          <div key={index} className="bg-white rounded-xl border border-yellow-200 shadow-sm p-5">
                            <div className="flex items-start space-x-4">
                              <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center">
                                <svg className="w-5 h-5 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                              </div>
                              <div className="flex-1">
                                <div className="flex items-center justify-between mb-2">
                                  <h4 className="text-lg font-bold text-gray-900">{formatFeedbackText(improvement.reason)}</h4>
                                  <span className={`text-xs uppercase font-semibold px-2 py-1 rounded-full ${improvementBadgeClasses(improvement.severity)}`}>
                                    {impactLabel(impactLevel(improvement))}
                                  </span>
                                </div>
                                {improvement.point_id && (
                                  <p className="text-xs text-gray-500 mb-2">Punkt {improvement.point_id}</p>
                                )}
                                {(() => {
                                  const combined = formatFeedbackText(improvement.exampleFix || improvement.suggestion)
                                  const visible = buildVisibleProblemAndFix(
                                    combined,
                                    improvement.point_id,
                                    formatFeedbackText(improvement.exampleFix || improvement.suggestion)
                                  )
                                  return (visible.problemText || visible.fixText) ? (
                                    <div className="space-y-2">
                                      {visible.problemText && <p className="text-sm text-gray-700">{visible.problemText}</p>}
                                      {visible.fixText && (
                                        <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-900">
                                          <p className="font-semibold mb-1">Slik retter du:</p>
                                          <p className="italic">{visible.fixText}</p>
                                        </div>
                                      )}
                                    </div>
                                  ) : null
                                })()}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {lowPriorityDeductions.length > 0 && (
                    <div>
                      <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
                        <span className="w-2 h-2 bg-blue-500 rounded-full mr-2"></span>
                        Lavt prioriterte forbedringer
                      </h3>
                      <div className="space-y-4">
                        {lowPriorityDeductions.map((improvement, index) => (
                          <div key={index} className="bg-white rounded-xl border border-blue-200 shadow-sm p-5">
                            <div className="flex items-start space-x-4">
                              <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
                                <svg className="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                              </div>
                              <div className="flex-1">
                                <div className="flex items-center justify-between mb-2">
                                  <h4 className="text-lg font-bold text-gray-900">{formatFeedbackText(improvement.reason)}</h4>
                                  <span className={`text-xs uppercase font-semibold px-2 py-1 rounded-full ${improvementBadgeClasses(improvement.severity)}`}>
                                    {impactLabel(impactLevel(improvement))}
                                  </span>
                                </div>
                                {improvement.point_id && (
                                  <p className="text-xs text-gray-500 mb-2">Punkt {improvement.point_id}</p>
                                )}
                                {(() => {
                                  const combined = formatFeedbackText(improvement.exampleFix || improvement.suggestion)
                                  const visible = buildVisibleProblemAndFix(
                                    combined,
                                    improvement.point_id,
                                    formatFeedbackText(improvement.exampleFix || improvement.suggestion)
                                  )
                                  return (visible.problemText || visible.fixText) ? (
                                    <div className="space-y-2">
                                      {visible.problemText && <p className="text-sm text-gray-700">{visible.problemText}</p>}
                                      {visible.fixText && (
                                        <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-900">
                                          <p className="font-semibold mb-1">Slik retter du:</p>
                                          <p className="italic">{visible.fixText}</p>
                                        </div>
                                      )}
                                    </div>
                                  ) : null
                                })()}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {analysis?.disclaimers && analysis.disclaimers.length > 0 && (
                <div className="bg-gray-50 rounded-2xl border border-gray-200 p-6 mb-6 text-sm text-gray-600">
                  <h2 className="text-lg font-semibold text-gray-900 mb-2">Forbehold</h2>
                  <ul className="list-disc pl-5 space-y-1">
                    {analysis.disclaimers.map((disclaimer, index) => (
                      <li key={index}>{disclaimer}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}

          {/* Summary Section */}
          {!hasV14 && legacyAnalysis?.summary && (
            <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
              <div className="flex items-center space-x-3 mb-4">
                <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold text-gray-900">Sammendrag</h2>
              </div>
              <p className="text-gray-700 leading-relaxed text-lg">{legacyAnalysis.summary}</p>
            </div>
          )}

          {/* Improvements Needed Section - Actionable Checklist */}
          {!hasV14 && ((report.findings && report.findings.length > 0) || (legacyAnalysis?.improvement_suggestions)) && (
            <div className="bg-gradient-to-br from-amber-50 via-orange-50 to-red-50 rounded-2xl shadow-xl border-2 border-orange-200 p-8 mb-6">
              <div className="flex items-center space-x-3 mb-6">
                <div className="w-12 h-12 bg-gradient-to-br from-orange-500 to-red-500 rounded-xl flex items-center justify-center shadow-lg">
                  <svg className="w-7 h-7 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                  </svg>
                </div>
                <div className="flex-1">
                  <h2 className="text-3xl font-bold text-gray-900">Forbedringer som trengs</h2>
                  <p className="text-gray-600 mt-1">Tiltak for å forbedre rapportkvaliteten</p>
                </div>
                <div className="px-4 py-2 bg-white rounded-lg border-2 border-orange-300">
                  <span className="text-2xl font-bold text-orange-600">
                    {(report.findings?.length || 0) + (legacyAnalysis?.improvement_suggestions?.for_takstmann?.length || 0) + (legacyAnalysis?.improvement_suggestions?.for_report_text?.length || 0)}
                  </span>
                  <span className="text-sm text-gray-600 block">punkter</span>
                </div>
              </div>

              <div className="space-y-6">
                {/* Critical & High Priority Findings */}
                {report.findings && report.findings.filter(f => f.severity === 'critical' || f.severity === 'high').length > 0 && (
                  <div>
                    <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
                      <span className="w-2 h-2 bg-red-500 rounded-full mr-2"></span>
                      Høyprioriterte funn
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
                                    <p className="text-sm font-semibold text-green-800 mb-1">✅ Slik retter du:</p>
                                    <p className="text-sm text-green-900">{finding.suggestion}</p>
                                  </div>
                                )}
                                {finding.standard_reference && (
                                  <p className="text-xs text-gray-500 mt-2">
                                    <span className="font-medium">Referanse:</span> {finding.standard_reference}
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
                      Middels prioriterte forbedringer
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
                                    <p className="text-sm font-semibold text-green-800 mb-1">✅ Slik retter du:</p>
                                    <p className="text-sm text-green-900">{finding.suggestion}</p>
                                  </div>
                                )}
                                {finding.standard_reference && (
                                  <p className="text-xs text-gray-500 mt-2">
                                    <span className="font-medium">Referanse:</span> {finding.standard_reference}
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
                {legacyAnalysis?.improvement_suggestions && (
                  <>
                    {legacyAnalysis.improvement_suggestions.for_takstmann && legacyAnalysis.improvement_suggestions.for_takstmann.length > 0 && (
                      <div>
                        <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
                          <span className="w-2 h-2 bg-blue-500 rounded-full mr-2"></span>
                          Anbefalinger for takstmann
                        </h3>
                        <div className="space-y-3">
                          {legacyAnalysis.improvement_suggestions.for_takstmann.map((item: any, index: number) => {
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

                    {legacyAnalysis.improvement_suggestions.for_report_text && legacyAnalysis.improvement_suggestions.for_report_text.length > 0 && (
                      <div>
                        <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
                          <span className="w-2 h-2 bg-indigo-500 rounded-full mr-2"></span>
                          Tekstforbedringer som trengs
                        </h3>
                        <div className="space-y-3">
                          {legacyAnalysis.improvement_suggestions.for_report_text.map((item: any, index: number) => {
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

          {/* Court case section removed in v1.4 */}

          {/* Components Section */}
          {!hasV14 && report.components && report.components.length > 0 && (
            <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
              <div className="flex items-center space-x-3 mb-6">
                <div className="w-10 h-10 bg-gradient-to-br from-green-600 to-emerald-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold text-gray-900">Bygningsdeler</h2>
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
          {!hasV14 && report.findings && report.findings.length > 0 && (
            <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
              <div className="flex items-center space-x-3 mb-6">
                <div className="w-10 h-10 bg-gradient-to-br from-red-600 to-pink-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold text-gray-900">Funn</h2>
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
                          {translate(finding.severity)}
                        </span>
                      </div>
                      {finding.suggestion && (
                        <div className="mt-3 pt-3 border-t border-gray-200">
                          <p className="text-sm font-semibold text-gray-700 mb-1">💡 Forslag:</p>
                          <p className="text-sm text-gray-700">{finding.suggestion}</p>
                        </div>
                      )}
                      {finding.standard_reference && (
                        <div className="mt-2 flex items-center space-x-2 text-xs text-gray-600">
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                          </svg>
                          <span className="font-medium">Referanse: {finding.standard_reference}</span>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Recommendations */}
          {legacyAnalysis?.recommendations && legacyAnalysis.recommendations.length > 0 && (
            <div className="bg-gradient-to-br from-blue-50 to-indigo-50 rounded-2xl shadow-lg border border-blue-200 p-8 mb-6">
              <div className="flex items-center space-x-3 mb-6">
                <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-600 rounded-lg flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold text-gray-900">Anbefalinger</h2>
              </div>
              <ul className="space-y-3">
                {legacyAnalysis.recommendations.map((rec: string, index: number) => (
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
                <h2 className="text-xl font-bold text-gray-900">Verifisering og feilsøking</h2>
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
                  <h3 className="font-bold text-gray-900 mb-4">Analysemetadata</h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {[
                      { label: 'Tekstlengde', value: `${report.extracted_text?.length || 0} tegn` },
                      { label: 'Est. tokens', value: Math.floor((report.extracted_text?.length || 0) / 4).toLocaleString() },
                      { label: 'Bygningsdeler', value: report.components.length },
                      { label: 'Funn', value: report.findings.length },
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
                      <h3 className="font-bold text-gray-900">Uthentet PDF-tekst</h3>
                      <button
                        onClick={() => setShowFullText(!showFullText)}
                        className="text-sm text-blue-600 hover:text-blue-800 font-medium"
                      >
                        {showFullText ? 'Vis mindre' : 'Vis full tekst'}
                      </button>
                    </div>
                    <div className="bg-gray-900 rounded-lg p-4 max-h-96 overflow-y-auto">
                      <pre className="text-xs text-green-400 whitespace-pre-wrap font-mono">
                        {showFullText 
                          ? report.extracted_text 
                          : `${report.extracted_text.substring(0, 2000)}${report.extracted_text.length > 2000 ? '\n\n... (avkortet)' : ''}`
                        }
                      </pre>
                    </div>
                  </div>
                )}

                  {/* Full AI Analysis JSON */}
             {report.ai_analysis && (
                  <div>
                    <h3 className="font-bold text-gray-900 mb-3">Full AI-analyse (JSON)</h3>
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
