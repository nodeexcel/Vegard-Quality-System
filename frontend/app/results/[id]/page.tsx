'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import axios from 'axios'
import { useAuth } from '../../contexts/AuthContext'
import outputOverlay from '../../../../files/scoring_policy.validert_output_overlay.v1.1.json'
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
  point_id: string
  title: string
  tg: string
  status: 'ok' | 'improve' | 'deduction' | 'blocking'
  summary: string
  deduction_total?: number
  finding_ids?: string[]
  parent_id?: string | null
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
  recommended_fix_text?: string
  gate_effect?: { blocks_96_gate?: boolean; caps_total_score_to?: number }
}
interface AllFindingV16 {
  finding_id: string
  category: string
  severity: string
  title: string
  message: string
  deduction_band?: string
  recommended_fix_text?: string
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

  const analysis = report.ai_analysis as (AnalysisV14 & AnalysisV16) | null
  const feedbackV11 = report.scoring_result?.feedback_v11 || null
  const hasFeedbackV11 = Boolean(feedbackV11 && feedbackV11.points_overview)
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
  const scoreTotal = hasV14
    ? (hasV16 && typeof (analysis as AnalysisV16).trygghetsscore === 'number'
        ? (analysis as AnalysisV16).trygghetsscore
        : analysis?.score_total ?? null)
    : feedbackV11?.score?.total ?? report.overall_score
  const improvementPriorityOrder: Record<string, number> = {
    critical: 0,
    high: 1,
    medium: 2,
    low: 3
  }
  const ensureCategoryF = (items: ScoreByCategory[]) => {
    if (items.some((item) => item.category_id === 'F')) {
      return items
    }
    return [
      ...items,
      {
        category_id: 'F',
        category_name: 'Lovlighetsmangler',
        deduction: 0,
        max_deduction: 15
      }
    ]
  }
  const categoryBreakdown = (analysis as AnalysisV16)?.category_breakdown
  const scoreByCategoryRaw = hasV14 && analysis ? ensureCategoryF(analysis.score_by_category || []) : []
  const allCategoryDeductionsZero = scoreByCategoryRaw.every((c) => c.deduction === 0)
  const trygghetsscoreNum = hasV16 && typeof (analysis as AnalysisV16)?.trygghetsscore === 'number' ? (analysis as AnalysisV16).trygghetsscore! : null
  const totalDeductionFromScore = trygghetsscoreNum != null ? Math.max(0, 100 - trygghetsscoreNum) : 0
  const bandToWeight: Record<string, number> = {
    'Høyt trekk': 3,
    'Høy påvirkning': 3,
    'Middels trekk': 2,
    'Middels påvirkning': 2,
    'Lavt trekk': 1,
    'Lav påvirkning': 1,
    'Ikke scoretrekk': 0,
    'Lavere trekk': 0
  }
  const scoreByCategory =
    hasV16 && allCategoryDeductionsZero && totalDeductionFromScore > 0
      ? categoryBreakdown?.length
        ? (() => {
            const totalWeight = categoryBreakdown.reduce((sum, cb) => sum + (bandToWeight[cb.deduction_band] ?? 0), 0)
            const withWeight = categoryBreakdown.map((cb) => {
              const existing = scoreByCategoryRaw.find((s) => s.category_id === cb.category)
              const maxDed = existing?.max_deduction ?? 20
              const w = bandToWeight[cb.deduction_band] ?? 0
              return { cb, existing, maxDed, weight: w }
            })
            const rawDeductions = withWeight.map(({ weight, maxDed }) => {
              if (totalWeight <= 0) return 0
              const raw = Math.round((totalDeductionFromScore * weight) / totalWeight)
              return Math.min(maxDed, Math.max(0, raw))
            })
            let sumRaw = rawDeductions.reduce((a, b) => a + b, 0)
            let adjusted = [...rawDeductions]
            if (sumRaw > totalDeductionFromScore) {
              let excess = sumRaw - totalDeductionFromScore
              adjusted = rawDeductions.map((d, i) => {
                const reduce = Math.min(d, excess)
                excess -= reduce
                return d - reduce
              })
            } else if (sumRaw < totalDeductionFromScore && totalWeight > 0) {
              const toAdd = totalDeductionFromScore - sumRaw
              const idx = withWeight.reduce((best, x, i) => (x.weight > 0 && (best === -1 || withWeight[best].weight < x.weight) ? i : best), -1)
              if (idx >= 0) {
                const maxRoom = withWeight[idx].maxDed - adjusted[idx]
                adjusted[idx] += Math.min(toAdd, maxRoom)
              }
            }
            return ensureCategoryF(
              categoryBreakdown.map((cb, i) => {
                const existing = scoreByCategoryRaw.find((s) => s.category_id === cb.category)
                return {
                  category_id: cb.category as ScoreByCategory['category_id'],
                  category_name: existing?.category_name ?? `Kategori ${cb.category}`,
                  deduction: adjusted[i] ?? 0,
                  max_deduction: existing?.max_deduction ?? 20,
                  deduction_band: cb.deduction_band,
                  summary: cb.summary
                }
              }) as ScoreByCategory[]
            )
          })()
        : (() => {
            // Fallback when we have trygghetsscore but no category_breakdown: distribute by max_deduction
            const totalMax = scoreByCategoryRaw.reduce((s, c) => s + (c.max_deduction || 0), 0)
            if (totalMax <= 0) return scoreByCategoryRaw
            let remaining = totalDeductionFromScore
            const deductions = scoreByCategoryRaw.map((c) => {
              const maxD = c.max_deduction || 0
              const raw = Math.round((totalDeductionFromScore * maxD) / totalMax)
              const d = Math.min(maxD, Math.max(0, raw))
              remaining -= d
              return { ...c, deduction: d }
            })
            if (remaining > 0) {
              const idx = deductions.findIndex((d) => d.deduction < (d.max_deduction || 0))
              if (idx >= 0) {
                deductions[idx] = {
                  ...deductions[idx],
                  deduction: Math.min((deductions[idx].max_deduction ?? 0), deductions[idx].deduction + remaining)
                }
              }
            }
            return deductions as ScoreByCategory[]
          })()
      : scoreByCategoryRaw
  type ScoreByCategoryDisplay = ScoreByCategory & { deduction_band?: string; summary?: string }
  const scoreByCategoryDisplay = scoreByCategory as ScoreByCategoryDisplay[]
  const topScoreDriversSource =
    hasV16 && analysis && (!analysis.top_score_drivers?.length) && (analysis as AnalysisV16).top_issues?.length
      ? (analysis as AnalysisV16).top_issues!.map((t) => ({
          title: t.title,
          severity: (t.severity || 'medium') as TopScoreDriver['severity'],
          reason: t.message,
          deduction_points: t.gate_effect?.caps_total_score_to ? 100 - t.gate_effect.caps_total_score_to : 0,
          rule_refs: [t.category],
          evidence: [] as Evidence[]
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
        return 'bg-green-100 text-green-800'
      case 'improve':
        return 'bg-yellow-100 text-yellow-800'
      case 'deduction':
        return 'bg-red-100 text-red-800'
      case 'blocking':
        return 'bg-red-200 text-red-900'
      default:
        return 'bg-gray-100 text-gray-800'
    }
  }
  /** Replace vague AI phrasing with precise, legally clear wording in user-facing text only. */
  const formatFeedbackText = (text: string | null | undefined): string => {
    if (text == null || typeof text !== 'string') return ''
    return text
      .replace(/Konsekvens uklar for kjøper/g, 'Konsekvens kan presiseres i forhold til kjøpers bruk, økonomi eller risiko.')
      .replace(/Konsekvens ikke eksplisitt oversatt til kjøpers situasjon\.?/g, 'Dette innebærer at kjøper må påregne utbedring før normal bruk kan anses trygg, og at det foreligger risiko for videre konstruksjonsskade dersom tiltak utsettes.')
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

  const renderTrekkLabel = (value: number | null | undefined) => {
    if (value === null || value === undefined) return null
    const policy = (outputOverlay as any)?.field_policies?.trekk
    const prefix = policy?.prefix || 'Vurdering'
    const format = policy?.render?.format || '{prefix}: {label}'
    const directLabel = policy?.external_labels?.[String(value)]
    if (directLabel) {
      return format.replace('{prefix}', prefix).replace('{label}', directLabel)
    }
    const bucket =
      value <= 1
        ? '1'
        : value <= 3
          ? '2'
          : '3'
    const bucketLabel = policy?.external_labels?.[bucket]
    if (bucketLabel) {
      return format.replace('{prefix}', prefix).replace('{label}', bucketLabel)
    }
    return `${prefix}: ${value}`
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
          severity: 'high',
          suggestion: 'Utbedre forholdet som utløser scoretaket for å heve totalscoren.'
        })
      })
    }
    return summaries
  }

  const buildDeductionSummariesFromFeedback = (items: FeedbackFinding[]): DeductionSummary[] => {
    return items
      .filter((finding) => typeof finding.deduction === 'number' && finding.deduction > 0)
      .map((finding) => ({
        rule_id: finding.rule_id,
        points: finding.deduction || 0,
        reason: finding.message || finding.rule_id,
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

  const allFindingsV16 = (analysis as AnalysisV16)?.all_findings
  const howToImproveV16 = (analysis as AnalysisV16)?.how_to_improve
  /** Try to extract point reference from message (e.g. "Punkt 4.2", "i punkt 5.1", "punkt 4.2") */
  const parsePointIdFromMessage = (message: string): string | undefined => {
    if (!message || typeof message !== 'string') return undefined
    const m = message.match(/(?:Punkt|punkt)\s+(\d+(?:\.\d+)*)/i) || message.match(/(?:punkt|ved)\s+(\d+(?:\.\d+)*)/i)
    return m ? m[1] : undefined
  }
  const buildDeductionSummariesFromV16 = (findings: AllFindingV16[]): DeductionSummary[] =>
    findings.map((f) => ({
      rule_id: f.finding_id,
      points: f.deduction_band === 'Høyt trekk' ? 5 : f.deduction_band === 'Middels trekk' ? 3 : f.deduction_band === 'Lavt trekk' ? 1 : 0,
      reason: f.title,
      severity: (f.severity === 'critical' ? 'critical' : f.severity === 'major' ? 'high' : f.severity === 'minor' ? 'low' : 'medium') as DeductionSummary['severity'],
      suggestion: f.recommended_fix_text,
      exampleFix: f.recommended_fix_text,
      point_id: parsePointIdFromMessage(f.message) ?? parsePointIdFromMessage(f.title) ?? (f as { location?: string; point_id?: string }).point_id ?? (f as { location?: string }).location,
      evidence_snippets: f.evidence_snippets?.length ? f.evidence_snippets : undefined
    }))
  const improvementsForFixes = hasV16 && howToImproveV16?.length && !analysis?.improvements?.length
    ? howToImproveV16.map((h) => ({
        title: h.title,
        priority: 'medium' as const,
        what_to_change: h.recommended_fix_text,
        suggested_text: h.recommended_fix_text
      }))
    : analysis?.improvements
  const allDeductions: DeductionSummary[] =
    feedbackV11?.findings?.length
      ? buildDeductionSummariesFromFeedback(feedbackV11.findings)
      : hasV16 && allFindingsV16?.length
        ? buildDeductionSummariesFromV16(allFindingsV16)
        : hasV14 && analysis
          ? buildDeductionSummariesFromAnalysis(analysis)
          : []

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

  const highPriorityDeductions = enrichedDedupedFixes.filter(
    (item) => item.severity === 'critical' || item.severity === 'high'
  )
  const mediumPriorityDeductions = enrichedDedupedFixes.filter((item) => item.severity === 'medium')
  const lowPriorityDeductions = enrichedDedupedFixes.filter((item) => item.severity === 'low')


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

          {hasV16 && (analysis as AnalysisV16).gate?.blocked_96 && (
            <div className="bg-amber-50 border border-amber-200 rounded-2xl p-6 mb-6">
              <p className="text-amber-900 font-medium">
                {(analysis as AnalysisV16).gate?.message ?? 'Rapporten kan ikke oppnå over 95 % før gate-avvik er rettet.'}
              </p>
            </div>
          )}

          {hasFeedbackV11 && feedbackV11 && (
            <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
              <div className="flex items-start justify-between mb-6">
                <div>
                  <h2 className="text-2xl font-bold text-gray-900">Punkt-for-punkt oversikt</h2>
                  <p className="text-sm text-gray-500 mt-1">Alle punkter som finnes i rapporten</p>
                </div>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                {feedbackV11.points_overview.map((point) => (
                  <div
                    key={point.point_id}
                    className={`border border-gray-200 rounded-xl p-5 bg-white shadow-sm ${point.parent_id ? 'ml-4 md:ml-6 border-l-4 border-l-blue-200' : ''}`}
                  >
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <div>
                        <p className="text-xs font-semibold uppercase text-gray-500">Punkt {point.point_id}</p>
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
                          {translate(point.status)}
                        </span>
                        <p className="text-xs text-gray-500 mt-2">{translate(point.tg)}</p>
                      </div>
                    </div>
                    <p className="text-sm text-gray-700">{point.summary}</p>
                    {typeof point.deduction_total === 'number' && point.deduction_total > 0 && (
                      <p className="text-xs text-red-600 mt-2">{renderTrekkLabel(point.deduction_total)}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {hasV14 && analysis && (
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
                  {allFindingsV16?.length && !analysis.findings?.length ? 'Alle funn' : 'Funn per bygningsdel'}
                </h2>
                <div className="space-y-6">
                  {allFindingsV16?.length && !analysis.findings?.length ? (
                    allFindingsV16.map((f, index) => {
                      const severityConfig = getSeverityConfig(f.severity)
                      return (
                        <div key={index} className={`border-l-4 ${severityConfig.border} ${severityConfig.bg} rounded-xl p-5`}>
                          <div className="flex items-start justify-between gap-4">
                            <div>
                              <p className="text-xs font-semibold uppercase text-gray-500">Kategori {f.category}</p>
                              <h3 className="text-lg font-bold text-gray-900 mt-1">{formatFeedbackText(f.title)}</h3>
                              <p className="text-sm text-gray-700 mt-2">{formatFeedbackText(f.message)}</p>
                              {f.recommended_fix_text && (
                                <div className="mt-3 bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-900">
                                  <p className="font-semibold mb-1">Anbefalt tiltak:</p>
                                  <p>{formatFeedbackText(f.recommended_fix_text)}</p>
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
                                {f.evidence_snippets.slice(0, 3).map((snip, i) => (
                                  <li key={i} className="italic">&quot;{snip}&quot;</li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>
                      )
                    })
                  ) : (
                  analysis.findings?.map((component, index) => (
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
                            <span className="font-semibold">Årsak:</span> {translate(component.arkat?.arsak?.status ?? 'unknown')}
                          </div>
                          <div>
                            <span className="font-semibold">Risiko:</span> {translate(component.arkat?.risiko?.status ?? 'unknown')}
                          </div>
                          <div>
                            <span className="font-semibold">Konsekvens:</span> {translate(component.arkat?.konsekvens?.status ?? 'unknown')}
                          </div>
                          <div>
                            <span className="font-semibold">Anbefalt tiltak:</span> {translate(component.arkat?.anbefalt_tiltak?.status ?? 'unknown')}
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
                  )))}
                </div>
              </div>

              <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8 mb-6">
                <div className="flex items-start justify-between mb-6">
                  <div>
                    <h2 className="text-2xl font-bold text-gray-900">Alle trekk</h2>
                    <p className="text-sm text-gray-500 mt-1">Fullstendig oversikt over poengtrekk som påvirker score</p>
                    {hasV16 && scoreTotal != null && (
                      <p className="text-xs text-amber-700 mt-1">
                        Total score ({scoreTotal}%) er satt av analysen og kategoritak; summen av enkelttrekk kan derfor avvike.
                      </p>
                    )}
                  </div>
                  <div className="px-4 py-2 bg-gray-50 rounded-lg border border-gray-200 text-center">
                    <span className="text-2xl font-bold text-gray-900">{deductionsForAlleTrekk.length}</span>
                    <span className="text-sm text-gray-600 block">trekk</span>
                  </div>
                </div>
                {deductionsForAlleTrekk.length === 0 ? (
                  <p className="text-sm text-gray-600">Ingen poengtrekk registrert.</p>
                ) : (
                  <div className="space-y-4">
                    {deductionsForAlleTrekk.map((item, index) => {
                      const severityConfig = getSeverityConfig(item.severity)
                      const reasonText = formatFeedbackText(item.reason)
                      const rawSuggestion = formatFeedbackText(item.suggestion)
                      const rawExampleFix = formatFeedbackText(item.exampleFix)
                      const combined = rawSuggestion || rawExampleFix
                      const { problemPart, fixPart } = splitProblemAndFix(combined)
                      // Top: only the problem part when we have a separate fix part (no duplication)
                      const suggestionText = fixPart
                        ? (item.point_id && problemPart && improvementTextRefersToDifferentPoint(problemPart, item.point_id) ? '' : problemPart)
                        : ''
                      const showFixInBox = fixPart || (!combined.includes('Slik retter du') && rawExampleFix)
                      return (
                        <div key={index} className={`border-l-4 ${severityConfig.border} ${severityConfig.bg} rounded-xl p-5`}>
                          <div className="flex items-start justify-between gap-4">
                            <div className="min-w-0 flex-1">
                              <h3 className="text-lg font-bold text-gray-900">{reasonText}</h3>
                              <p className="text-sm text-gray-600">
                                {item.point_id ? `Punkt ${item.point_id}` : 'Generelt'} • {translate(item.rule_id)}
                              </p>
                              {item.evidence_snippets && item.evidence_snippets.length > 0 && (
                                <div className="mt-2 p-2 bg-gray-50 border border-gray-100 rounded-lg text-sm text-gray-700">
                                  <p className="font-medium text-gray-600 mb-1">Hvor i rapporten:</p>
                                  {item.evidence_snippets.slice(0, 2).map((snip, i) => (
                                    <p key={i} className="italic text-gray-600 border-l-2 border-gray-200 pl-2 mt-1">
                                      &quot;{snip}&quot;
                                    </p>
                                  ))}
                                </div>
                              )}
                              {(suggestionText || showFixInBox) && (
                                <div className="mt-2 space-y-2">
                                  {suggestionText && (
                                    <p className="text-sm text-gray-700">{suggestionText}</p>
                                  )}
                                  {showFixInBox && (
                                    <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-900">
                                      <p className="font-semibold mb-1">Slik retter du:</p>
                                      <p className="italic">{fixPart || rawExampleFix}</p>
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                            <span className={`px-3 py-1 rounded-full text-sm font-bold shrink-0 ${severityConfig.badge}`}>
                              -{item.points}
                            </span>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
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
                                    {translate(improvement.severity)}
                                  </span>
                                </div>
                                {improvement.point_id && (
                                  <p className="text-xs text-gray-500 mb-2">Punkt {improvement.point_id}</p>
                                )}
                                {(() => {
                                  const combined = formatFeedbackText(improvement.suggestion || improvement.exampleFix)
                                  const { problemPart, fixPart } = splitProblemAndFix(combined)
                                  const safeProblem = improvement.point_id && problemPart && improvementTextRefersToDifferentPoint(problemPart, improvement.point_id) ? '' : problemPart
                                  const fixToShow = fixPart || (!combined.includes('Slik retter du') ? formatFeedbackText(improvement.exampleFix || improvement.suggestion) : '')
                                  return (safeProblem || fixToShow) ? (
                                    <div className="space-y-2">
                                      {safeProblem && <p className="text-sm text-gray-700">{safeProblem}</p>}
                                      {fixToShow && (
                                        <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-900">
                                          <p className="font-semibold mb-1">Slik retter du:</p>
                                          <p className="italic">{fixToShow}</p>
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
                                    {translate(improvement.severity)}
                                  </span>
                                </div>
                                {improvement.point_id && (
                                  <p className="text-xs text-gray-500 mb-2">Punkt {improvement.point_id}</p>
                                )}
                                {(() => {
                                  const combined = formatFeedbackText(improvement.suggestion || improvement.exampleFix)
                                  const { problemPart, fixPart } = splitProblemAndFix(combined)
                                  const safeProblem = improvement.point_id && problemPart && improvementTextRefersToDifferentPoint(problemPart, improvement.point_id) ? '' : problemPart
                                  const fixToShow = fixPart || (!combined.includes('Slik retter du') ? formatFeedbackText(improvement.exampleFix || improvement.suggestion) : '')
                                  return (safeProblem || fixToShow) ? (
                                    <div className="space-y-2">
                                      {safeProblem && <p className="text-sm text-gray-700">{safeProblem}</p>}
                                      {fixToShow && (
                                        <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-900">
                                          <p className="font-semibold mb-1">Slik retter du:</p>
                                          <p className="italic">{fixToShow}</p>
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
                                    {translate(improvement.severity)}
                                  </span>
                                </div>
                                {improvement.point_id && (
                                  <p className="text-xs text-gray-500 mb-2">Punkt {improvement.point_id}</p>
                                )}
                                {(() => {
                                  const combined = formatFeedbackText(improvement.suggestion || improvement.exampleFix)
                                  const { problemPart, fixPart } = splitProblemAndFix(combined)
                                  const safeProblem = improvement.point_id && problemPart && improvementTextRefersToDifferentPoint(problemPart, improvement.point_id) ? '' : problemPart
                                  const fixToShow = fixPart || (!combined.includes('Slik retter du') ? formatFeedbackText(improvement.exampleFix || improvement.suggestion) : '')
                                  return (safeProblem || fixToShow) ? (
                                    <div className="space-y-2">
                                      {safeProblem && <p className="text-sm text-gray-700">{safeProblem}</p>}
                                      {fixToShow && (
                                        <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-900">
                                          <p className="font-semibold mb-1">Slik retter du:</p>
                                          <p className="italic">{fixToShow}</p>
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

              {analysis.disclaimers && analysis.disclaimers.length > 0 && (
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
