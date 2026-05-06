import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  LoaderCircle,
  MessageCircle,
  Quote,
  SearchCheck,
  Upload,
  X,
} from 'lucide-react'
import type { FormEvent, RefObject, UIEvent } from 'react'
import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import './App.css'

const API_URL = import.meta.env.VITE_API_URL ?? (import.meta.env.PROD ? '' : 'http://127.0.0.1:8000')
const MIN_AUDIT_LOADING_MS = 1000
const ASSISTANT_THINKING_MS = 850
const DEEP_CHECK_MIN_LOADING_MS = 1400
const HIGHLIGHT_STOPWORDS = new Set([
  'the',
  'a',
  'an',
  'and',
  'or',
  'of',
  'to',
  'in',
  'on',
  'for',
  'with',
  'that',
  'this',
  'as',
  'is',
  'are',
  'was',
  'were',
  'can',
  'they',
  'their',
  'from',
  'without',
])

const sampleText = `This classroom sample checks citation and quote handling in a short student paragraph.
Bloom et al. (2015) found that "home working led to a 13% performance increase" among call center workers.
Bloom et al. (2015) reported that the experiment used call center employees at Ctrip, a Chinese travel agency.
Piwowar et al. (2018) report that "at least 28% of the scholarly literature is OA".
The Community Life Survey 2024/25 reports that 9% of adults in England experience high loneliness, measured by the indirect loneliness score of 8 to 9.
Zephyr et al. (2099) claimed that 88% of prototype chatbots can detect sadness from keyboard colour.
A small pilot study found that companion apps reduce crisis referrals by 41%.`

const demoSources = [
  {
    name: 'bloom_2015_source.txt',
    text: `The experiment found that home working led to a 13% performance increase among call center workers.

The experiment used call center employees at Ctrip, a Chinese travel agency.

Publication details:
Title: Does Working from Home Work? Evidence from a Chinese Experiment
Authors: Nicholas Bloom, James Liang, John Roberts, Zhichun Jenny Ying
Year: 2015
DOI: 10.1093/qje/qju032`,
  },
  {
    name: 'piwowar_2018_source.txt',
    text: `Piwowar et al. report that at least 28% of the scholarly literature is OA.

Publication details:
Title: The state of OA: a large-scale analysis of the prevalence and impact of Open Access articles
Authors: Heather Piwowar, Jason Priem, Vincent Larivière, Juan Pablo Alperin, Lisa Matthias, Bree Norlander, Ashley Farley, Jevin West, Stefanie Haustein
Year: 2018
DOI: 10.7717/peerj.4375`,
  },
  {
    name: 'community_life_survey_2024_25_source.txt',
    text: `The Community Life Survey 2024/25 reports that 9% of adults in England experience high loneliness, measured by the indirect loneliness score of 8 to 9.

Publication details:
Title: Community Life Survey 2024/25
Authors: Department for Culture, Media and Sport
Year: 2025
URL: https://www.gov.uk/government/statistics/community-life-survey-202425-annual-publication`,
  },
]

type RuleScope = 'quoted' | 'cited' | 'data' | 'uncited'
type AuditMode = 'fast' | 'deep'
type DeepTaskStatus = 'queued' | 'active' | 'complete'

const deepCheckTasks = [
  'Parse draft',
  'Uploaded sources',
  'Crossref',
  'OpenAlex',
  'Semantic Scholar',
  'DataCite',
  'Europe PMC',
  'PubMed',
  'DOAJ',
  'OpenAIRE',
  'arXiv',
  'Web discovery',
  'Evidence match',
  'Quote check',
]

const initialDeepProgressTasks = deepCheckTasks.map((task, index) => ({
  task,
  status: index === 0 ? 'active' : 'queued' as DeepTaskStatus,
  message: index === 0 ? 'Starting' : 'Waiting',
}))

const progressTaskAliases: Array<[RegExp, string | null]> = [
  [/^parse\s*draft$/i, 'Parse draft'],
  [/^uploaded\s*sources?$/i, 'Uploaded sources'],
  [/^crossref$/i, 'Crossref'],
  [/^openalex$/i, 'OpenAlex'],
  [/^semantic\s*scholar$/i, 'Semantic Scholar'],
  [/^datacite$/i, 'DataCite'],
  [/^europe\s*pmc$/i, 'Europe PMC'],
  [/^pubmed$/i, 'PubMed'],
  [/^doaj$/i, 'DOAJ'],
  [/^openaire$/i, 'OpenAIRE'],
  [/^arxiv$/i, 'arXiv'],
  [/^web\s*discovery$/i, 'Web discovery'],
  [/^unpaywall$/i, 'Web discovery'],
  [/^full-?text\s*parsing$/i, 'Web discovery'],
  [/^evidence\s*match$/i, 'Evidence match'],
  [/^quote\s*check$/i, 'Quote check'],
  [/^provider\s*(lookup|search)$/i, null],
]

const ruleScopeOptions: Array<{ id: RuleScope; label: string; description: string }> = [
  { id: 'quoted', label: 'Direct quotes', description: 'Check quoted wording against source text.' },
  { id: 'cited', label: 'Cited sentences', description: 'Check sentences with citation, DOI, or URL markers.' },
  { id: 'data', label: 'Numbers and dates', description: 'Check percentages, years, and numeric evidence claims.' },
  { id: 'uncited', label: 'Uncited claims', description: 'Flag factual-looking claims with no citation marker.' },
]

type Verdict =
  | 'Likely Supported'
  | 'Needs Review'
  | 'Weak Evidence'
  | 'No Evidence Found'
  | 'Citation Missing'
  | 'No Check Needed'

type QuoteStatus = 'Quote Verified' | 'Quote Slightly Modified' | 'Quote Mismatch' | 'Quote Not Found'

interface ParsedSentence {
  id: string
  text: string
  citations: string[]
  urls: string[]
  dois: string[]
  quotes: string[]
  likely_factual_claim: boolean
}

interface EvidenceSnippet {
  source_id: string
  source_name: string
  page_number: number | null
  text: string
  relevance_score: number
  explanation: string | null
}

interface QuoteCheck {
  quote: string
  status: QuoteStatus
  score: number
  source_id: string | null
  source_name: string | null
  page_number: number | null
  context: string | null
}

interface SentenceAudit {
  sentence: ParsedSentence
  verdict: Verdict
  evidence: EvidenceSnippet[]
  quote_checks: QuoteCheck[]
  notes: string[]
}

interface SourceMetadata {
  title: string | null
  authors: string[]
  year: number | null
  doi: string | null
  url: string | null
  publisher: string | null
  container: string | null
}

interface Source {
  id: string
  kind: 'pdf' | 'web' | 'doi' | 'text'
  name: string
  metadata: SourceMetadata
}

interface ReliabilityScore {
  source_id: string
  level: 'High' | 'Medium' | 'Low' | 'Unknown'
  score: number
  freshness: 'Fresh' | 'Acceptable' | 'Possibly Outdated' | 'Unknown'
  reasons: string[]
}

interface AuditResponse {
  summary: {
    total_sentences: number
    likely_claims: number
    cited_sentences: number
    missing_citations: number
    quotes_found: number
    quotes_verified: number
    sources_processed: number
  }
  sentences: SentenceAudit[]
  sources: Source[]
  reliability: ReliabilityScore[]
}

interface DeepProgressTask {
  task: string
  status: DeepTaskStatus
  message?: string
}

type AuditStreamEvent =
  | { type: 'progress'; task: string; status: string; message?: string }
  | { type: 'result'; result: AuditResponse }

type DeepProgressUpdater = (updater: (current: DeepProgressTask[]) => DeepProgressTask[]) => void

function buildAuditForm({
  text,
  auditMode,
  selectedRuleScopes,
  sourceFiles,
  usesBuiltInDemoSource,
}: {
  text: string
  auditMode: AuditMode
  selectedRuleScopes: RuleScope[]
  sourceFiles: File[]
  usesBuiltInDemoSource: boolean
}) {
  const form = new FormData()
  form.append('text', text)
  form.append('check_mode', auditMode)
  form.append('online_lookup', usesBuiltInDemoSource ? 'false' : 'true')
  form.append('analysis_scope', JSON.stringify(selectedRuleScopes))
  if (usesBuiltInDemoSource) {
    demoSources.forEach((source) => {
      form.append('files', new File([source.text], source.name, { type: 'text/plain' }))
    })
  }
  sourceFiles.forEach((file) => {
    form.append('files', file)
  })
  return form
}

async function runDeepAudit(buildForm: () => FormData, updateProgress: DeepProgressUpdater) {
  try {
    return await runAuditStream(buildForm(), updateProgress)
  } catch {
    const progressTimer = startFallbackDeepProgress(updateProgress)
    try {
      return await runAudit(buildForm())
    } finally {
      window.clearInterval(progressTimer)
    }
  }
}

async function runAudit(form: FormData) {
  const response = await fetch(`${API_URL}/audit`, {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    throw new Error(detail?.detail ?? `Audit failed with ${response.status}`)
  }
  return (await response.json()) as AuditResponse
}

async function runAuditStream(form: FormData, updateProgress: DeepProgressUpdater) {
  const response = await fetch(`${API_URL}/audit/stream`, {
    method: 'POST',
    body: form,
  })
  if (!response.ok || !response.body) {
    throw new Error(`Stream unavailable with ${response.status}`)
  }

  let result: AuditResponse | null = null
  await readAuditStream(response.body, (event) => {
    if (event.type === 'progress') {
      updateProgress((current) => applyDeepProgressEvent(current, event))
    } else if (event.type === 'result') {
      result = event.result
    }
  })

  if (!result) throw new Error('Stream ended without a result')
  return result
}

async function readAuditStream(stream: ReadableStream<Uint8Array>, onEvent: (event: AuditStreamEvent) => void) {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    const lines = buffer.split(/\r?\n/)
    buffer = lines.pop() ?? ''
    lines.forEach((line) => {
      const event = parseAuditStreamLine(line)
      if (event) onEvent(event)
    })
    if (done) break
  }

  const finalEvent = parseAuditStreamLine(buffer)
  if (finalEvent) onEvent(finalEvent)
}

function parseAuditStreamLine(line: string): AuditStreamEvent | null {
  const trimmed = line.trim()
  if (!trimmed || trimmed === '[DONE]') return null
  if (trimmed.startsWith(':') || trimmed.startsWith('event:')) return null
  const payload = trimmed.startsWith('data:') ? trimmed.slice(5).trim() : trimmed
  if (!payload || payload === '[DONE]') return null
  const parsed = JSON.parse(payload) as Partial<AuditStreamEvent>
  if (parsed.type === 'progress' && typeof parsed.task === 'string' && typeof parsed.status === 'string') {
    return {
      type: 'progress',
      task: parsed.task,
      status: parsed.status,
      message: typeof parsed.message === 'string' ? parsed.message : undefined,
    }
  }
  if (parsed.type === 'result' && parsed.result) {
    return { type: 'result', result: parsed.result as AuditResponse }
  }
  return null
}

function startFallbackDeepProgress(updateProgress: DeepProgressUpdater) {
  return window.setInterval(() => {
    updateProgress((current) => advanceSimulatedDeepProgress(current))
  }, 850)
}

function advanceSimulatedDeepProgress(tasks: DeepProgressTask[]) {
  const activeIndex = tasks.findIndex((task) => task.status === 'active')
  const nextIndex = Math.min((activeIndex === -1 ? 0 : activeIndex) + 1, Math.max(tasks.length - 2, 0))
  return tasks.map((task, index) => {
    if (index < nextIndex) return { ...task, status: 'complete' as DeepTaskStatus, message: 'Done' }
    if (index === nextIndex) return { ...task, status: 'active' as DeepTaskStatus, message: 'Checking' }
    return task.status === 'complete' ? task : { ...task, status: 'queued' as DeepTaskStatus, message: 'Waiting' }
  })
}

function applyDeepProgressEvent(tasks: DeepProgressTask[], event: Extract<AuditStreamEvent, { type: 'progress' }>) {
  const canonicalTask = canonicalProgressTask(event.task)
  if (!canonicalTask) return tasks
  const nextStatus = normalizeDeepTaskStatus(event.status)
  const taskIndex = tasks.findIndex((task) => task.task === canonicalTask)
  if (taskIndex === -1) return tasks

  const updatedTasks = tasks.map((task, index) => {
    if (index < taskIndex && task.status === 'queued') {
      return { ...task, status: 'complete' as DeepTaskStatus, message: 'Done' }
    }
    if (index === taskIndex) {
      if (task.status === 'complete' && nextStatus !== 'complete') return task
      return { ...task, status: nextStatus, message: progressStatusLabel(nextStatus) }
    }
    if (nextStatus === 'active' && task.status === 'active') {
      return { ...task, status: 'queued' as DeepTaskStatus, message: 'Waiting' }
    }
    return task
  })

  return keepOneActiveTask(updatedTasks)
}

function markAllDeepTasksComplete(tasks: DeepProgressTask[]) {
  return tasks.map((task) => ({ ...task, status: 'complete' as DeepTaskStatus, message: 'Done' }))
}

function keepOneActiveTask(tasks: DeepProgressTask[]) {
  if (tasks.some((task) => task.status === 'active')) return tasks
  const nextQueuedIndex = tasks.findIndex((task) => task.status === 'queued')
  if (nextQueuedIndex === -1) return tasks
  return tasks.map((task, index) =>
    index === nextQueuedIndex ? { ...task, status: 'active' as DeepTaskStatus, message: 'Checking' } : task,
  )
}

function canonicalProgressTask(task: string) {
  const cleaned = task.trim()
  for (const [pattern, canonical] of progressTaskAliases) {
    if (pattern.test(cleaned)) return canonical
  }
  return null
}

function normalizeDeepTaskStatus(status: string): DeepTaskStatus {
  const normalized = status.toLowerCase().replace(/[^a-z]+/g, '')
  if (['complete', 'completed', 'done', 'success', 'finished'].includes(normalized)) return 'complete'
  if (['queued', 'pending', 'waiting'].includes(normalized)) return 'queued'
  return 'active'
}

function progressStatusLabel(status: DeepTaskStatus) {
  if (status === 'complete') return 'Done'
  if (status === 'active') return 'Checking'
  return 'Waiting'
}

function App() {
  const [text, setText] = useState(sampleText)
  const [sourceFiles, setSourceFiles] = useState<File[]>([])
  const [analysisScopeOpen, setAnalysisScopeOpen] = useState(false)
  const [ruleScopes, setRuleScopes] = useState<Record<RuleScope, boolean>>({
    quoted: true,
    cited: true,
    data: true,
    uncited: true,
  })
  const [auditMode, setAuditMode] = useState<AuditMode>('deep')
  const [deepProgressTasks, setDeepProgressTasks] = useState<DeepProgressTask[]>(initialDeepProgressTasks)
  const [result, setResult] = useState<AuditResponse | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const selected = useMemo(() => {
    if (!result) return null
    const checkedSentences = visibleSentenceAudits(result)
    return checkedSentences.find((item) => item.sentence.id === selectedId) ?? prioritySentenceAudit(result)
  }, [result, selectedId])

  const isDemoSample = text.trim() === sampleText.trim()
  const usesBuiltInDemoSource = isDemoSample
  const selectedRuleScopes = ruleScopeOptions.filter((option) => ruleScopes[option.id]).map((option) => option.id)
  const hasSelectedRuleScope = selectedRuleScopes.length > 0

  async function submitAudit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const startedAt = Date.now()
    setLoading(true)
    setError(null)
    setResult(null)
    setSelectedId(null)
    setDeepProgressTasks(initialDeepProgressTasks)

    const buildForm = () => buildAuditForm({
      text,
      auditMode,
      selectedRuleScopes,
      sourceFiles,
      usesBuiltInDemoSource,
    })

    try {
      const payload =
        auditMode === 'deep'
          ? await runDeepAudit(buildForm, setDeepProgressTasks)
          : await runAudit(buildForm())
      await waitForMinimumDelay(startedAt, auditMode === 'deep' ? DEEP_CHECK_MIN_LOADING_MS : MIN_AUDIT_LOADING_MS)
      if (auditMode === 'deep') setDeepProgressTasks((current) => markAllDeepTasksComplete(current))
      setResult(payload)
      setSelectedId(firstPrioritySentenceId(payload))
    } catch (caught) {
      await waitForMinimumDelay(startedAt, auditMode === 'deep' ? DEEP_CHECK_MIN_LOADING_MS : MIN_AUDIT_LOADING_MS)
      setError(caught instanceof Error ? caught.message : 'Audit failed')
    } finally {
      setLoading(false)
    }
  }

  function loadSample() {
    setText(sampleText)
    setSourceFiles([])
    if (fileInputRef.current) fileInputRef.current.value = ''
    setResult(null)
    setSelectedId(null)
    setError(null)
  }

  function editText() {
    setResult(null)
    setSelectedId(null)
    setError(null)
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <SearchCheck size={22} aria-hidden="true" />
          </div>
          <div>
            <h1>Citation Check</h1>
            <p>Citation and evidence audit</p>
          </div>
        </div>
      </header>

      <section className="workspace">
        <form className="input-panel" onSubmit={submitAudit}>
          <div className="panel-heading">
            <h2>Draft</h2>
            <div className="heading-actions">
              {result ? (
                <button className="ghost-button" type="button" onClick={editText}>
                  Edit text
                </button>
              ) : null}
              <button className="ghost-button" type="button" onClick={loadSample}>
                Demo sample
              </button>
            </div>
          </div>

          <SourceDiscoveryNotice usesBuiltInDemoSource={usesBuiltInDemoSource} />
          <p className="custom-audit-note">Demo results are stable. Custom drafts depend on public source access.</p>
          <CheckModeControl
            mode={auditMode}
            disabled={loading}
            onChange={(mode) => {
              setAuditMode(mode)
              setError(null)
            }}
          />
          <AnalysisScopeControl
            open={analysisScopeOpen}
            ruleScopes={ruleScopes}
            onToggleOpen={setAnalysisScopeOpen}
            onChange={(scope, checked) => {
              setRuleScopes((current) => ({ ...current, [scope]: checked }))
              setResult(null)
              setSelectedId(null)
              setError(null)
            }}
          />
          {result ? <HighlightLegend /> : null}

          <label className="field">
            <span>Text to audit</span>
            <AuditEditor
              value={text}
              onChange={(nextText) => {
                setText(nextText)
                if (result) {
                  setResult(null)
                  setSelectedId(null)
                }
                setError(null)
              }}
              result={result}
              selectedId={selected?.sentence.id ?? null}
              onSelect={setSelectedId}
            />
          </label>

          <UploadedSourceControl
            fileInputRef={fileInputRef}
            files={sourceFiles}
            onChange={(files) => {
              setSourceFiles(files)
              if (result) {
                setResult(null)
                setSelectedId(null)
              }
              setError(null)
            }}
          />

          {result ? (
            <SourcePool
              reliability={result.reliability}
              sources={result.sources}
              usageBySource={sourceUsageByCheckedSentence(result)}
            />
          ) : null}

          {error ? (
            <div className="error-row" role="alert">
              <AlertTriangle size={18} aria-hidden="true" />
              {error}
            </div>
          ) : null}

          <button className="primary-button" disabled={loading || !text.trim() || !hasSelectedRuleScope} type="submit">
            {loading ? <LoaderCircle className="spin" size={18} aria-hidden="true" /> : <SearchCheck size={18} aria-hidden="true" />}
            {loading ? 'Checking...' : result ? 'Run again' : auditMode === 'deep' ? 'Start deep check' : 'Start quick check'}
          </button>
        </form>

        <section className="results-panel" aria-live="polite">
          {loading && auditMode === 'deep' ? (
            <DeepCheckProgress tasks={deepProgressTasks} />
          ) : result ? (
            <>
              <div className="results-heading">
                <h2>Audit report</h2>
              </div>
              <AuditSummaryPanel result={result} />
              <SummaryGrid result={result} />
              <div className="audit-layout">
                <SentenceList result={result} selectedId={selected?.sentence.id ?? null} onSelect={setSelectedId} />
                <DetailPane
                  audit={selected}
                  sources={result.sources}
                />
              </div>
            </>
          ) : (
            <EmptyState />
          )}
        </section>
      </section>
      {result ? <ReviewGuide result={result} selected={selected} /> : null}
    </main>
  )
}

function CheckModeControl({
  mode,
  disabled,
  onChange,
}: {
  mode: AuditMode
  disabled: boolean
  onChange: (mode: AuditMode) => void
}) {
  return (
    <div className="check-mode-control" aria-label="Check mode">
      <div className="check-mode-buttons" role="group" aria-label="Choose check depth">
        <button
          aria-pressed={mode === 'fast'}
          className={mode === 'fast' ? 'selected' : ''}
          disabled={disabled}
          onClick={() => onChange('fast')}
          type="button"
        >
          Quick check
        </button>
        <button
          aria-pressed={mode === 'deep'}
          className={mode === 'deep' ? 'selected' : ''}
          disabled={disabled}
          onClick={() => onChange('deep')}
          type="button"
        >
          Deep check
        </button>
      </div>
      <p>
        Quick check is faster but has limited accuracy and coverage. Deep check is recommended and usually takes
        20-45 seconds for a short draft.
      </p>
    </div>
  )
}

function DeepCheckProgress({ tasks }: { tasks: DeepProgressTask[] }) {
  const completedCount = tasks.filter((task) => task.status === 'complete').length
  const percent = Math.round((completedCount / Math.max(tasks.length, 1)) * 100)
  const activeTask = tasks.find((task) => task.status === 'active') ?? tasks.find((task) => task.status !== 'complete') ?? tasks.at(-1)

  return (
    <div className="deep-progress-panel">
      <div className="deep-progress-header">
        <div>
          <span>Deep check running</span>
          <h2>{activeTask?.task ?? 'Checking evidence'}</h2>
          <p>{progressStatusLabel(activeTask?.status ?? 'active')}</p>
        </div>
        <strong>{percent}%</strong>
      </div>
      <div className="deep-progress-bar" aria-hidden="true">
        <span style={{ width: `${Math.max(8, percent)}%` }} />
      </div>
      <ul className="deep-task-list" aria-label="Deep check tasks">
        {tasks.map(({ task, status }) => {
          return (
            <li className={status} key={task}>
              <span className="deep-task-icon">
                {status === 'complete' ? (
                  <CheckCircle2 size={16} aria-hidden="true" />
                ) : status === 'active' ? (
                  <LoaderCircle className="spin" size={16} aria-hidden="true" />
                ) : (
                  <span aria-hidden="true" />
                )}
              </span>
              <span>{task}</span>
              <small>{progressStatusLabel(status)}</small>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function SourceDiscoveryNotice({ usesBuiltInDemoSource }: { usesBuiltInDemoSource: boolean }) {
  const title = usesBuiltInDemoSource ? 'Demo source attached' : 'Automatic source discovery'
  const message = usesBuiltInDemoSource
    ? 'The demo sample includes three built-in source excerpts, so the citation and quote checks are stable for this prototype.'
    : 'For custom text, the app will try public metadata and accessible web text automatically.'

  return (
    <div className="source-notice is-info">
      <strong>{title}</strong>
      <span>{message}</span>
    </div>
  )
}

function HighlightLegend() {
  return (
    <div className="highlight-legend" aria-label="Highlight legend">
      <span className="legend-item legend-supported">
        <span aria-hidden="true" />
        Matched source text
      </span>
      <span className="legend-item legend-risk">
        <span aria-hidden="true" />
        Missing or invalid citation
      </span>
    </div>
  )
}

function AnalysisScopeControl({
  open,
  ruleScopes,
  onToggleOpen,
  onChange,
}: {
  open: boolean
  ruleScopes: Record<RuleScope, boolean>
  onToggleOpen: (open: boolean) => void
  onChange: (scope: RuleScope, checked: boolean) => void
}) {
  return (
    <details
      className="analysis-scope"
      open={open}
      onToggle={(event) => onToggleOpen(event.currentTarget.open)}
    >
      <summary>Analysis scope</summary>
      <div className="analysis-scope-options">
        {ruleScopeOptions.map((option) => (
          <label className="scope-checkbox" key={option.id}>
            <input
              checked={ruleScopes[option.id]}
              onChange={(event) => onChange(option.id, event.target.checked)}
              type="checkbox"
            />
            <span>
              <strong>{option.label}</strong>
              <small>{option.description}</small>
            </span>
          </label>
        ))}
      </div>
    </details>
  )
}

function UploadedSourceControl({
  fileInputRef,
  files,
  onChange,
}: {
  fileInputRef: RefObject<HTMLInputElement | null>
  files: File[]
  onChange: (files: File[]) => void
}) {
  return (
    <section className="upload-source">
      <div className="upload-source-header">
        <div>
          <strong>Upload source</strong>
          <span>PDF or TXT files used to check citations and quotes.</span>
        </div>
        {files.length ? (
          <button
            className="ghost-button"
            type="button"
            onClick={() => {
              onChange([])
              if (fileInputRef.current) fileInputRef.current.value = ''
            }}
          >
            Clear
          </button>
        ) : null}
      </div>
      <button
        className="upload-source-dropzone"
        onClick={() => fileInputRef.current?.click()}
        type="button"
      >
        <Upload size={18} aria-hidden="true" />
        <span>{files.length ? 'Add or replace source files' : 'Choose source files'}</span>
      </button>
      <input
        accept=".pdf,.txt,text/plain,application/pdf"
        className="upload-source-input"
        multiple
        onChange={(event) => onChange(Array.from(event.target.files ?? []))}
        ref={fileInputRef}
        type="file"
      />
      {files.length ? (
        <ul className="upload-source-list">
          {files.map((file) => (
            <li key={`${file.name}-${file.size}`}>
              <span>{file.name}</span>
              <small>{formatFileSize(file.size)}</small>
            </li>
          ))}
        </ul>
      ) : (
        <p className="upload-source-note">Optional for custom drafts. The demo sample already has built-in sources.</p>
      )}
    </section>
  )
}

function SourcePool({
  reliability,
  sources,
  usageBySource,
}: {
  reliability: ReliabilityScore[]
  sources: Source[]
  usageBySource: Map<string, number>
}) {
  return (
    <section className="source-pool">
      <div className="source-pool-header">
        <h3>Source pool</h3>
        <span>{sources.length} source{sources.length === 1 ? '' : 's'} used for this draft</span>
      </div>
      {sources.length ? (
        <div className="source-pool-list">
          {sources.map((source) => (
            <SourceCard
              key={source.id}
              source={source}
              score={reliability.find((item) => item.source_id === source.id)}
              usedByCheckedSentences={usageBySource.get(source.id) ?? 0}
            />
          ))}
        </div>
      ) : (
        <p className="muted">No source attached.</p>
      )}
    </section>
  )
}

function AuditSummaryPanel({ result }: { result: AuditResponse }) {
  const checked = visibleSentenceAudits(result)
  const confirmed = checked.filter((item) => item.verdict === 'Likely Supported').length
  const needsReview = checked.filter((item) => item.verdict === 'Needs Review' || item.verdict === 'Weak Evidence').length
  const clearIssues = checked.filter(
    (item) => item.verdict === 'Citation Missing' || item.verdict === 'No Evidence Found',
  ).length
  const quoteIssues = checked
    .flatMap((item) => item.quote_checks)
    .filter((check) => check.status === 'Quote Mismatch' || check.status === 'Quote Not Found').length
  const findings = auditSummaryFindings(result, {
    confirmed,
    needsReview,
    clearIssues,
    quoteIssues,
  })

  return (
    <section className="audit-summary-panel">
      <div className="audit-summary-header">
        <div>
          <h2>{summaryPanelTitle()}</h2>
          <p>{auditSummaryHeadline(checked.length, confirmed, needsReview, clearIssues, quoteIssues)}</p>
        </div>
        <span>{summaryPanelBadge()}</span>
      </div>
      <p className="audit-summary-narrative">{auditNarrativeSummary(result, { confirmed, needsReview, clearIssues, quoteIssues })}</p>
      <p className="prototype-note">This prototype checks accessible source text, not absolute truth.</p>
      <div className="audit-summary-metrics">
        <SummaryStatus label="Confirmed" value={confirmed} tone="good" />
        <SummaryStatus label="Review" value={needsReview} tone="warn" />
        <SummaryStatus label="Issues" value={clearIssues} tone="bad" />
        <SummaryStatus label="Quotes" value={`${result.summary.quotes_verified}/${result.summary.quotes_found}`} tone={quoteIssues ? 'bad' : 'good'} />
      </div>
      {findings.length ? (
        <ul className="audit-summary-findings">
          {findings.map((finding) => (
            <li className={`summary-finding ${finding.tone}`} key={finding.text}>
              {finding.text}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

function SummaryStatus({ label, value, tone }: { label: string; value: string | number; tone: 'good' | 'warn' | 'bad' }) {
  return (
    <div className={`summary-status ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function SummaryGrid({ result }: { result: AuditResponse }) {
  const stats = [
    ['Sentences', result.summary.total_sentences],
    ['Claims', result.summary.likely_claims],
    ['Cited', result.summary.cited_sentences],
    ['Missing', result.summary.missing_citations],
    ['Quotes', `${result.summary.quotes_verified}/${result.summary.quotes_found}`],
    ['Sources', result.summary.sources_processed],
  ]

  return (
    <div className="summary-grid">
      {stats.map(([label, value]) => (
        <div className="summary-tile" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  )
}

type AssistantTopic = 'overview' | 'selected' | 'fix' | 'sources' | 'limits'

function ReviewGuide({ result, selected }: { result: AuditResponse; selected: SentenceAudit | null }) {
  const [open, setOpen] = useState(false)
  const [topic, setTopic] = useState<AssistantTopic>('overview')
  const [assistantText, setAssistantText] = useState(() => assistantAnswer('overview', result, selected))
  const [thinking, setThinking] = useState(false)
  const thinkingTimerRef = useRef<number | null>(null)

  useEffect(() => {
    setAssistantText(assistantAnswer(topic, result, selected))
  }, [result, selected, topic])

  useEffect(() => {
    return () => {
      if (thinkingTimerRef.current) window.clearTimeout(thinkingTimerRef.current)
    }
  }, [])

  function chooseTopic(nextTopic: AssistantTopic) {
    setTopic(nextTopic)
    setThinking(true)
    if (thinkingTimerRef.current) window.clearTimeout(thinkingTimerRef.current)
    thinkingTimerRef.current = window.setTimeout(() => {
      setAssistantText(assistantAnswer(nextTopic, result, selected))
      setThinking(false)
    }, ASSISTANT_THINKING_MS)
  }

  if (!open) {
    return (
      <button className="assistant-launcher" type="button" onClick={() => setOpen(true)}>
        <MessageCircle size={18} aria-hidden="true" />
        Review guide
      </button>
    )
  }

  return (
    <aside className="assistant-panel" aria-label="Review guide">
      <div className="assistant-header">
        <div>
          <strong>Review guide</strong>
          <span>Result explainer</span>
        </div>
        <button type="button" aria-label="Close review guide" onClick={() => setOpen(false)}>
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      <div className={`assistant-message ${thinking ? 'is-thinking' : ''}`}>
        <span>{thinking ? 'Thinking' : 'Guide'}</span>
        {thinking ? (
          <div className="assistant-thinking" aria-label="Guide is preparing">
            <i />
            <i />
            <i />
          </div>
        ) : (
          <p>{assistantText}</p>
        )}
      </div>
      <div className="assistant-prompts" aria-label="Guide prompts">
        <button className={topic === 'overview' ? 'selected' : ''} type="button" onClick={() => chooseTopic('overview')}>
          Explain result
        </button>
        <button className={topic === 'selected' ? 'selected' : ''} type="button" onClick={() => chooseTopic('selected')}>
          Current sentence
        </button>
        <button className={topic === 'fix' ? 'selected' : ''} type="button" onClick={() => chooseTopic('fix')}>
          What to fix
        </button>
        <button className={topic === 'sources' ? 'selected' : ''} type="button" onClick={() => chooseTopic('sources')}>
          Source pool
        </button>
        <button className={topic === 'limits' ? 'selected' : ''} type="button" onClick={() => chooseTopic('limits')}>
          Limits
        </button>
      </div>
    </aside>
  )
}

function AuditEditor({
  value,
  onChange,
  result,
  selectedId,
  onSelect,
}: {
  value: string
  onChange: (value: string) => void
  result: AuditResponse | null
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const backdropRef = useRef<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    const viewportLimit = typeof window === 'undefined' ? 560 : Math.max(320, Math.floor(window.innerHeight * 0.62))
    const maxHeight = Math.min(560, viewportLimit)
    const nextHeight = Math.max(320, Math.min(textarea.scrollHeight, maxHeight))
    textarea.style.height = `${nextHeight}px`
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden'
    if (backdropRef.current) {
      backdropRef.current.style.height = `${nextHeight}px`
      backdropRef.current.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden'
    }
  }, [value, result])

  function syncScroll(event: UIEvent<HTMLTextAreaElement>) {
    if (!backdropRef.current) return
    backdropRef.current.scrollTop = event.currentTarget.scrollTop
    backdropRef.current.scrollLeft = event.currentTarget.scrollLeft
  }

  if (result) {
    return (
      <div aria-label="Highlighted audit text" className="audit-editor-preview" tabIndex={0}>
        <InlineDraftHighlights rawText={value} result={result} selectedId={selectedId} onSelect={onSelect} />
      </div>
    )
  }

  return (
    <div className="audit-editor-shell">
      <div aria-hidden="true" className="audit-editor-backdrop" ref={backdropRef}>
        <div className="audit-editor-highlights">
          <InlineDraftHighlights rawText={value} result={result} selectedId={selectedId} />
          {value.endsWith('\n') ? <br /> : null}
        </div>
      </div>
      <textarea
        aria-label="Text to audit"
        className="audit-editor-input"
        onChange={(event) => onChange(event.target.value)}
        onScroll={syncScroll}
        placeholder="Paste text to audit"
        ref={textareaRef}
        spellCheck
        value={value}
      />
    </div>
  )
}

function InlineDraftHighlights({
  rawText,
  result,
  selectedId,
  onSelect,
}: {
  rawText: string
  result: AuditResponse | null
  selectedId: string | null
  onSelect?: (id: string) => void
}) {
  const segments = result ? splitDraftIntoHighlightSegments(rawText, result.sentences) : [{ text: rawText }]

  return (
    <>
      {segments.map((segment, segmentIndex) => {
        if (!segment.audit) {
          return <Fragment key={`plain-${segmentIndex}`}>{segment.text}</Fragment>
        }
        const audit = segment.audit
        const leading = segment.text.match(/^\s*/)?.[0] ?? ''
        const trailing = segment.text.match(/\s*$/)?.[0] ?? ''
        const core = segment.text.slice(leading.length, segment.text.length - trailing.length)
        if (!core) return <Fragment key={`blank-${segmentIndex}`}>{segment.text}</Fragment>
        return (
          <Fragment key={`${audit.sentence.id}-${segmentIndex}`}>
            {leading}
            <span
              className={`editor-highlight-sentence ${draftHighlightClass(audit.verdict)} ${
                selectedId === audit.sentence.id ? 'selected' : ''
              }`}
              onClick={onSelect ? () => onSelect(audit.sentence.id) : undefined}
              onKeyDown={
                onSelect
                  ? (event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        onSelect(audit.sentence.id)
                      }
                    }
                  : undefined
              }
              role={onSelect ? 'button' : undefined}
              tabIndex={onSelect ? 0 : undefined}
            >
              {core}
            </span>
            {trailing}
          </Fragment>
        )
      })}
    </>
  )
}

function SentenceList({
  result,
  selectedId,
  onSelect,
}: {
  result: AuditResponse
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const visibleSentences = visibleSentenceAudits(result)
  return (
    <div className="sentence-list">
      {visibleSentences.length ? visibleSentences.map((item) => (
        <button
          className={`sentence-row ${verdictClass(item.verdict)} ${selectedId === item.sentence.id ? 'selected' : ''}`}
          key={item.sentence.id}
          type="button"
          onClick={() => onSelect(item.sentence.id)}
        >
          <span className="verdict-dot" />
          <span>{item.sentence.text}</span>
          <strong>{displayVerdict(item)}</strong>
        </button>
      )) : <p className="muted checked-empty">No checked sentences need attention.</p>}
    </div>
  )
}

function DetailPane({
  audit,
  sources,
}: {
  audit: SentenceAudit | null
  sources: Source[]
}) {
  if (!audit) return null

  return (
    <aside className="detail-pane">
      <div className={`verdict-banner ${verdictClass(audit.verdict)}`}>
        <CheckCircle2 size={18} aria-hidden="true" />
        <strong>{displayVerdict(audit)}</strong>
      </div>

      <section className="detail-section">
        <h3>Sentence</h3>
        <p>{audit.sentence.text}</p>
        <MetaLine label="Citations" values={audit.sentence.citations} />
        <MetaLine label="DOIs" values={audit.sentence.dois} />
        {isNeedsReviewVerdict(audit.verdict) ? <p className="review-reason">{needsReviewReason(audit)}</p> : null}
      </section>

      {audit.quote_checks.length ? (
        <section className="detail-section">
          <h3>
            <Quote size={16} aria-hidden="true" />
            Quotes
          </h3>
          {audit.quote_checks.map((check) => (
            <div className={`quote-card ${quoteStatusClass(check.status)}`} key={`${check.quote}-${check.source_id}`}>
              <div>
                <strong>{check.status}</strong>
                <span>{check.score}% match</span>
              </div>
              <p>"{check.quote}"</p>
              <MatchChips terms={quoteHighlightTerms(check.quote)} label="Matched quote words" />
              {check.context ? (
                <>
                  <div className="source-passage-label">Source passage</div>
                  <blockquote className="source-passage">
                    <HighlightedText text={check.context} terms={quoteHighlightTerms(check.quote)} />
                  </blockquote>
                </>
              ) : null}
              <small>{sourceLabel(check.source_name, check.page_number)}</small>
              <SourceReference source={sourceById(sources, check.source_id)} />
            </div>
          ))}
        </section>
      ) : null}

      <section className="detail-section">
        <h3>Evidence</h3>
        {audit.verdict === 'Citation Missing' ? (
          <>
            <p className="muted">{citationProblemExplanation(audit)}</p>
            <div className="fix-card">
              <strong>Fix</strong>
              <span>{citationFixSuggestion(audit)}</span>
            </div>
          </>
        ) : audit.evidence.length ? (
          <EvidencePanel evidence={audit.evidence} sources={sources} />
        ) : (
          <p className="muted">No evidence snippet retrieved from provided source text.</p>
        )}
      </section>

      {audit.notes.length ? (
        <section className="detail-section">
          <h3>Notes</h3>
          <ul className="notes-list">
            {audit.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </aside>
  )
}

function SourceCard({
  source,
  score,
  usedByCheckedSentences,
}: {
  source: Source
  score: ReliabilityScore | undefined
  usedByCheckedSentences: number
}) {
  const href = sourceHref(source)
  const title = source.metadata.title ?? source.name
  return (
    <article className={`source-card ${score ? reliabilityClass(score.level) : 'source-unknown'}`}>
      <div>
        {href ? (
          <a className="source-title-link" href={href} target="_blank" rel="noreferrer" title={title}>
            {shortSourceName(title)}
            <ExternalLink size={13} aria-hidden="true" />
          </a>
        ) : (
          <strong title={title}>{shortSourceName(title)}</strong>
        )}
        <span className={`source-confidence ${score ? reliabilityClass(score.level) : 'source-unknown'}`}>
          {score ? reliabilityLabel(score) : 'Unknown source confidence'}
        </span>
      </div>
      <p>{source.metadata.authors.join(', ') || source.metadata.publisher || source.metadata.container || source.kind}</p>
      <small className="source-usage">{sourceUsageLabel(usedByCheckedSentences)}</small>
      <small>{score ? reliabilityExplanation(score) : 'Not enough source metadata was available to score this source.'}</small>
    </article>
  )
}

function MetaLine({ label, values }: { label: string; values: string[] }) {
  if (!values.length) return null
  return (
    <p className="meta-line">
      <strong>{label}:</strong> {values.join(', ')}
    </p>
  )
}

function EvidencePanel({ evidence, sources }: { evidence: EvidenceSnippet[]; sources: Source[] }) {
  const [best, ...otherMatches] = evidence
  return (
    <div className="evidence-stack">
      <EvidenceCard snippet={best} label="Best match" variant="best" sources={sources} />
      {otherMatches.length ? (
        <details className="other-matches">
          <summary>Other possible matches ({otherMatches.length})</summary>
          <p>These are lower-confidence text matches. Use them as leads, not as final evidence.</p>
          {otherMatches.map((snippet) => (
            <EvidenceCard
              key={`${snippet.source_id}-${snippet.text.slice(0, 24)}`}
              snippet={snippet}
              label={relevanceLabel(snippet.relevance_score)}
              variant="secondary"
              sources={sources}
            />
          ))}
        </details>
      ) : null}
    </div>
  )
}

function EvidenceCard({
  snippet,
  label,
  variant,
  sources,
}: {
  snippet: EvidenceSnippet
  label: string
  variant: 'best' | 'secondary'
  sources: Source[]
}) {
  const terms = evidenceTerms(snippet.explanation)
  const source = sourceById(sources, snippet.source_id)
  return (
    <article className={`evidence-card ${variant === 'best' ? 'is-best-match' : 'is-possible-match'}`}>
      <div>
        <strong title={snippet.source_name}>{label}</strong>
        <span className="evidence-relevance-label">{evidenceMatchLabel(snippet.relevance_score)}</span>
      </div>
      <p>
        <HighlightedText text={snippet.text} terms={terms} />
      </p>
      <MatchChips terms={terms} label={variant === 'best' ? 'Best match words' : 'Matched words'} />
      <small>{plainEvidenceExplanation(snippet.explanation)}</small>
      <small title={snippet.source_name}>{sourceLabel(snippet.source_name, snippet.page_number)}</small>
      <SourceReference source={source} />
    </article>
  )
}

function SourceReference({ source }: { source: Source | null }) {
  if (!source) return null
  const href = sourceHref(source)
  const doi = source.metadata.doi
  if (!href && !doi) return null

  return (
    <small className="source-reference">
      {doi ? 'Source DOI: ' : 'Source link: '}
      {href ? (
        <a href={href} target="_blank" rel="noreferrer">
          {doi ?? href}
          <ExternalLink size={12} aria-hidden="true" />
        </a>
      ) : (
        doi
      )}
    </small>
  )
}

function HighlightedText({ text, terms }: { text: string; terms: string[] }) {
  const normalizedTerms = [...new Set(terms.map((term) => term.trim()).filter(Boolean))]
    .filter((term) => term.length > 1)
    .sort((left, right) => right.length - left.length)

  if (!normalizedTerms.length) return <>{text}</>

  const matcher = new RegExp(`(${normalizedTerms.map(escapeRegExp).join('|')})`, 'gi')
  return (
    <>
      {text.split(matcher).map((part, index) => {
        const isMatch = normalizedTerms.some((term) => term.toLowerCase() === part.toLowerCase())
        return isMatch ? (
          <mark className="match-highlight" key={`${part}-${index}`}>
            {part}
          </mark>
        ) : (
          <Fragment key={`${part}-${index}`}>{part}</Fragment>
        )
      })}
    </>
  )
}

function MatchChips({ terms, label }: { terms: string[]; label: string }) {
  const uniqueTerms = [...new Set(terms.map((term) => term.trim()).filter(Boolean))].slice(0, 8)
  if (!uniqueTerms.length) return null

  return (
    <div className="match-chip-row" aria-label={label}>
      <span>{label}</span>
      {uniqueTerms.map((term) => (
        <mark className="match-chip" key={term}>
          {term}
        </mark>
      ))}
    </div>
  )
}

function EmptyState() {
  return (
    <div className="empty-state">
      <SearchCheck size={42} aria-hidden="true" />
      <h2>Ready to audit</h2>
      <p>Run the demo sample to see source-backed claims, verified quotes, and citation problems.</p>
    </div>
  )
}

function sourceLabel(name: string | null, pageNumber: number | null) {
  if (!name && !pageNumber) return 'No source location'
  const displayName = shortSourceName(name ?? 'Source')
  if (!pageNumber) return displayName
  return `${displayName}, page ${pageNumber}`
}

function sourceHref(source: Source) {
  if (source.metadata.url) return source.metadata.url
  if (source.metadata.doi) return `https://doi.org/${source.metadata.doi}`
  return undefined
}

function sourceById(sources: Source[], sourceId: string | null) {
  if (!sourceId) return null
  return sources.find((source) => source.id === sourceId) ?? null
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function waitForMinimumDelay(startedAt: number, minimumMs: number) {
  const elapsed = Date.now() - startedAt
  return elapsed < minimumMs ? delay(minimumMs - elapsed) : Promise.resolve()
}

function reliabilityClass(level: ReliabilityScore['level']) {
  if (level === 'High') return 'source-high'
  if (level === 'Medium') return 'source-medium'
  if (level === 'Low') return 'source-low'
  return 'source-unknown'
}

function reliabilityLabel(score: ReliabilityScore) {
  return `${score.level} source confidence · ${score.score}/100`
}

function reliabilityExplanation(score: ReliabilityScore) {
  const reason = score.reasons[0] ?? 'The score is based on source text and metadata availability.'
  const freshness = score.freshness !== 'Unknown' ? ` Freshness: ${score.freshness}.` : ''
  return `${reason}${freshness}`
}

function verdictClass(verdict: Verdict) {
  if (verdict === 'Likely Supported') return 'is-supported'
  if (verdict === 'Needs Review') return 'is-review'
  if (verdict === 'Weak Evidence') return 'is-weak'
  if (verdict === 'Citation Missing') return 'is-missing'
  if (verdict === 'No Check Needed') return 'is-skipped'
  return 'is-none'
}

function draftHighlightClass(verdict: Verdict) {
  if (verdict === 'No Check Needed') return 'draft-skip'
  if (verdict === 'Likely Supported') return 'draft-supported'
  if (verdict === 'Needs Review' || verdict === 'Weak Evidence') return 'draft-review'
  if (verdict === 'Citation Missing' || verdict === 'No Evidence Found') return 'draft-risk'
  return 'draft-muted'
}

function isNeedsReviewVerdict(verdict: Verdict) {
  return verdict === 'Needs Review' || verdict === 'Weak Evidence'
}

function visibleSentenceAudits(result: AuditResponse) {
  return result.sentences.filter((item) => item.verdict !== 'No Check Needed')
}

function prioritySentenceAudit(result: AuditResponse) {
  const visible = visibleSentenceAudits(result)
  return visible.find(isRiskAudit) ?? visible[0] ?? null
}

function firstPrioritySentenceId(result: AuditResponse) {
  return prioritySentenceAudit(result)?.sentence.id ?? null
}

function isRiskAudit(audit: SentenceAudit) {
  return audit.verdict === 'Citation Missing' || audit.verdict === 'No Evidence Found'
}

function hasCitationMarker(audit: SentenceAudit) {
  return Boolean(audit.sentence.citations.length || audit.sentence.dois.length || audit.sentence.urls.length)
}

function hasFutureCitationIssue(audit: SentenceAudit) {
  return audit.notes.some((note) => note.toLowerCase().includes('future'))
}

function displayVerdict(audit: SentenceAudit) {
  if (audit.verdict === 'Citation Missing') {
    if (hasFutureCitationIssue(audit) || hasCitationMarker(audit)) return 'Invalid Citation'
    return 'Citation Missing'
  }
  return audit.verdict
}

function citationProblemExplanation(audit: SentenceAudit) {
  if (hasFutureCitationIssue(audit)) {
    return 'This citation uses a future year, so it cannot be treated as a valid source marker.'
  }
  if (!hasCitationMarker(audit)) {
    return 'This claim needs a citation before source evidence can be checked.'
  }
  return 'This citation needs to be fixed before source evidence can be checked.'
}

function citationFixSuggestion(audit: SentenceAudit) {
  if (hasFutureCitationIssue(audit)) {
    return 'Replace this citation with a real source, or remove the claim from the draft.'
  }
  if (!hasCitationMarker(audit)) {
    return 'Add a citation or source lead before checking evidence for this claim.'
  }
  return 'Correct the citation marker, then run the audit again.'
}

function needsReviewReason(audit: SentenceAudit) {
  const notes = audit.notes.join(' ').toLowerCase()
  const quote = audit.quote_checks.find((check) => check.status !== 'Quote Verified')
  const hasSourceLead = Boolean(audit.sentence.citations.length || audit.sentence.dois.length || audit.sentence.urls.length)
  const hasEvidenceText = audit.evidence.some((snippet) => snippet.text.trim())
  const bestScore = audit.evidence[0]?.relevance_score ?? 0

  if (quote) {
    if (quote.status === 'Quote Not Found' || quote.status === 'Quote Mismatch') {
      return 'Quote not verified: the quoted wording was not found in the accessible source text.'
    }
    return 'Quote not verified: the wording is close, but it still needs a manual check.'
  }

  if (notes.includes('metadata') || notes.includes('abstract') || notes.includes('title') || notes.includes('bibliographic')) {
    return 'Metadata only: the source record was found, but there was not enough article text to confirm the claim.'
  }

  if (
    notes.includes('full text') ||
    notes.includes('full-text') ||
    notes.includes('paywall') ||
    notes.includes('unavailable') ||
    notes.includes('not accessible')
  ) {
    return 'Full text unavailable: the source may be real, but the claim could not be checked against accessible text.'
  }

  if (
    notes.includes('search') ||
    notes.includes('online') ||
    notes.includes('lookup') ||
    notes.includes('web') ||
    notes.includes('result')
  ) {
    return 'Search result needs manual review: a possible source lead was found, but it was not enough to confirm the claim.'
  }

  if (!hasEvidenceText || bestScore < 0.18) {
    return hasSourceLead
      ? 'No matching source text: the citation or source lead was found, but the accessible text did not support this sentence.'
      : 'No matching source text: this claim still needs a source that can be checked.'
  }

  return 'Search result needs manual review: the available text is related, but not strong enough to treat the claim as confirmed.'
}

function summaryPanelTitle() {
  return 'Audit summary'
}

function summaryPanelBadge() {
  return 'Rule-based summary'
}

function auditSummaryHeadline(
  checkedCount: number,
  confirmed: number,
  needsReview: number,
  clearIssues: number,
  quoteIssues: number,
) {
  if (!checkedCount) return 'No selected sentence types need checking in this draft.'
  const issueParts: string[] = []
  if (clearIssues) issueParts.push(`${clearIssues} citation issue${clearIssues === 1 ? '' : 's'}`)
  if (quoteIssues) issueParts.push(`${quoteIssues} quote issue${quoteIssues === 1 ? '' : 's'}`)
  if (issueParts.length) {
    return `${checkedCount} checked items: ${confirmed} confirmed, ${needsReview} need review, ${issueParts.join(', ')}.`
  }
  if (needsReview) return `${checkedCount} checked items: ${confirmed} confirmed and ${needsReview} need a second look.`
  return `${checkedCount} checked items were confirmed by the available evidence.`
}

function auditNarrativeSummary(
  result: AuditResponse,
  counts: { confirmed: number; needsReview: number; clearIssues: number; quoteIssues: number },
) {
  const checked = visibleSentenceAudits(result)
  const invalidCount = checked.filter((item) => item.verdict === 'Citation Missing' && hasCitationMarker(item)).length
  const missingCount = checked.filter((item) => item.verdict === 'Citation Missing' && !hasCitationMarker(item)).length
  const quoteText =
    result.summary.quotes_found > 0
      ? `${result.summary.quotes_verified}/${result.summary.quotes_found} direct quotes matched source text.`
      : 'No direct quotes were checked.'
  const sourceText =
    result.summary.sources_processed === 1
      ? '1 source excerpt'
      : `${result.summary.sources_processed} source excerpts`

  if (!checked.length) {
    return 'No checked sentence needs source verification in this draft.'
  }

  const issueParts: string[] = []
  if (invalidCount) issueParts.push(`${invalidCount} invalid citation${invalidCount === 1 ? '' : 's'}`)
  if (missingCount) issueParts.push(`${missingCount} uncited claim${missingCount === 1 ? '' : 's'}`)
  if (counts.needsReview) issueParts.push(`${counts.needsReview} item${counts.needsReview === 1 ? '' : 's'} needing manual review`)
  if (counts.quoteIssues) issueParts.push(`${counts.quoteIssues} quote issue${counts.quoteIssues === 1 ? '' : 's'}`)

  const issueSentence = issueParts.length ? ` Remaining issues: ${issueParts.join(', ')}.` : ' No citation problems remain.'
  if (counts.confirmed) {
    return `${counts.confirmed} checked claim${counts.confirmed === 1 ? '' : 's'} matched accessible text from ${sourceText}. ${quoteText}${issueSentence}`
  }

  return `The audit finished, but accessible source text was too limited to confirm the checked claims. ${quoteText}${issueSentence}`
}

function auditSummaryFindings(
  result: AuditResponse,
  counts: {
    confirmed: number
    needsReview: number
    clearIssues: number
    quoteIssues: number
  },
) {
  const findings: Array<{ text: string; tone: 'good' | 'warn' | 'bad' }> = []
  const checked = visibleSentenceAudits(result)
  const invalidCount = checked.filter((item) => item.verdict === 'Citation Missing' && hasCitationMarker(item)).length
  const missingCount = checked.filter((item) => item.verdict === 'Citation Missing' && !hasCitationMarker(item)).length
  if (counts.confirmed) {
    findings.push({ text: `${counts.confirmed} checked item${counts.confirmed === 1 ? '' : 's'} matched accessible source evidence.`, tone: 'good' })
  }
  if (counts.needsReview) {
    findings.push({ text: `${counts.needsReview} item${counts.needsReview === 1 ? '' : 's'} could not be fully confirmed and should be reviewed manually.`, tone: 'warn' })
  }
  if (invalidCount) {
    findings.push({
      text:
        invalidCount === 1
          ? '1 citation marker is clearly invalid and should be fixed.'
          : `${invalidCount} citation markers are clearly invalid and should be fixed.`,
      tone: 'bad',
    })
  }
  if (missingCount) {
    findings.push({
      text: missingCount === 1 ? '1 claim has no usable citation marker.' : `${missingCount} claims have no usable citation marker.`,
      tone: 'bad',
    })
  }
  if (counts.quoteIssues) {
    findings.push({ text: `${counts.quoteIssues} direct quote${counts.quoteIssues === 1 ? '' : 's'} were not found in the attached or retrieved source text.`, tone: 'bad' })
  }
  return findings.slice(0, 4)
}

function assistantAnswer(topic: AssistantTopic, result: AuditResponse, selected: SentenceAudit | null) {
  const checked = visibleSentenceAudits(result)
  const confirmed = checked.filter((item) => item.verdict === 'Likely Supported').length
  const review = checked.filter((item) => item.verdict === 'Needs Review' || item.verdict === 'Weak Evidence').length
  const invalid = checked.filter((item) => item.verdict === 'Citation Missing' && hasCitationMarker(item)).length
  const missing = checked.filter((item) => item.verdict === 'Citation Missing' && !hasCitationMarker(item)).length

  if (topic === 'selected') return selectedSentenceExplanation(selected)
  if (topic === 'fix') return fixPriorityExplanation(invalid, missing, review)
  if (topic === 'sources') return sourcePoolExplanation(result)
  if (topic === 'limits') {
    return 'This helper explains the audit result. It does not prove real-world truth. Green means accessible source text matched the claim or quote. Red means the citation marker is missing or clearly invalid. Anything unavailable behind a paywall or outside the attached source pool still needs human review.'
  }

  const issueParts: string[] = []
  if (invalid) issueParts.push(`${invalid} invalid citation${invalid === 1 ? '' : 's'}`)
  if (missing) issueParts.push(`${missing} uncited claim${missing === 1 ? '' : 's'}`)
  if (review) issueParts.push(`${review} item${review === 1 ? '' : 's'} needing review`)
  const issueText = issueParts.length ? `The remaining problems are ${issueParts.join(', ')}.` : 'No citation problems were found in the checked claims.'
  return `${confirmed} checked claim${confirmed === 1 ? '' : 's'} matched accessible source text. ${result.summary.quotes_verified}/${result.summary.quotes_found} direct quote${result.summary.quotes_found === 1 ? '' : 's'} were verified. ${issueText}`
}

function selectedSentenceExplanation(selected: SentenceAudit | null) {
  if (!selected) return 'Select a sentence in the report to see a sentence-level explanation here.'

  const source = selected.evidence[0]?.source_name || selected.quote_checks[0]?.source_name
  const sourceText = source ? ` The closest source is ${shortSourceName(source)}.` : ''
  const quote = selected.quote_checks[0]

  if (selected.verdict === 'Likely Supported') {
    if (quote?.status === 'Quote Verified') {
      return `This sentence is marked green because the quoted wording was found in accessible source text and the surrounding evidence matched the claim.${sourceText}`
    }
    return `This sentence is marked green because accessible source text matched the main claim.${sourceText}`
  }

  if (selected.verdict === 'Citation Missing') {
    return citationProblemExplanation(selected)
  }

  if (isNeedsReviewVerdict(selected.verdict)) {
    return `${needsReviewReason(selected)}${sourceText}`
  }

  if (selected.verdict === 'No Evidence Found') {
    return 'The sentence has a citation-like marker, but no useful matching source evidence was retrieved. Treat it as unconfirmed.'
  }

  return 'This sentence was treated as framing or background for the draft and was not checked as a source-backed claim.'
}

function fixPriorityExplanation(invalid: number, missing: number, review: number) {
  if (invalid) {
    return 'Fix the invalid citation first. In this sample, the future-year citation should be replaced with a real source or the claim should be removed.'
  }
  if (missing) {
    return 'Add citations to the uncited claims next. The tool cannot verify a claim against source text until there is a usable citation or source lead.'
  }
  if (review) {
    return 'Review the yellow items next. They may be real, but this prototype could not confirm them from accessible text.'
  }
  return 'No immediate citation fixes remain in the checked sample. The green items still mean matched accessible evidence, not absolute truth.'
}

function sourcePoolExplanation(result: AuditResponse) {
  if (!result.sources.length) return 'No source text was attached or retrieved for this audit.'
  const names = result.sources
    .slice(0, 3)
    .map((source) => shortSourceName(source.metadata.title ?? source.name))
    .join('; ')
  return `The source pool is shared by the whole pasted draft, not by one selected sentence. This audit used ${result.sources.length} source${result.sources.length === 1 ? '' : 's'}: ${names}. Sentence-level Evidence shows which source matched the selected claim.`
}

function sourceUsageByCheckedSentence(result: AuditResponse) {
  const sourceIdByName = new Map(result.sources.map((source) => [source.name, source.id]))
  const counts = new Map<string, number>()

  visibleSentenceAudits(result).forEach((audit) => {
    if (audit.verdict !== 'Likely Supported') return

    const sourceIds = new Set<string>()
    const bestEvidence = audit.evidence[0]

    if (bestEvidence) sourceIds.add(bestEvidence.source_id)

    audit.quote_checks.forEach((check) => {
      if (check.source_id) {
        sourceIds.add(check.source_id)
        return
      }
      if (check.source_name) {
        const matchedId = sourceIdByName.get(check.source_name)
        if (matchedId) sourceIds.add(matchedId)
      }
    })

    sourceIds.forEach((sourceId) => {
      counts.set(sourceId, (counts.get(sourceId) ?? 0) + 1)
    })
  })

  return counts
}

function sourceUsageLabel(count: number) {
  if (!count) return 'Not matched by a checked sentence'
  return `Matched by ${count} checked sentence${count === 1 ? '' : 's'}`
}

function splitDraftIntoHighlightSegments(rawText: string, audits: SentenceAudit[]) {
  const segments: Array<{ text: string; audit?: SentenceAudit }> = []
  const normalizedDraft = normalizeWithRawMap(rawText)
  let rawCursor = 0
  let normalizedCursor = 0

  audits.forEach((audit) => {
    const normalizedSentence = normalizeInlineText(audit.sentence.text)
    if (!normalizedSentence) return

    const normalizedIndex = normalizedDraft.text.toLowerCase().indexOf(normalizedSentence.toLowerCase(), normalizedCursor)
    if (normalizedIndex < 0) return

    let rawStart = normalizedDraft.rawIndexes[normalizedIndex]
    let rawEnd = normalizedDraft.rawIndexes[normalizedIndex + normalizedSentence.length - 1] + 1
    while (rawStart < rawEnd && /\s/.test(rawText[rawStart])) rawStart += 1
    while (rawEnd > rawStart && /\s/.test(rawText[rawEnd - 1])) rawEnd -= 1

    if (rawStart > rawCursor) {
      segments.push({ text: rawText.slice(rawCursor, rawStart) })
    }
    segments.push({ text: rawText.slice(rawStart, rawEnd), audit })
    rawCursor = rawEnd
    normalizedCursor = normalizedIndex + normalizedSentence.length
  })

  if (rawCursor < rawText.length) {
    segments.push({ text: rawText.slice(rawCursor) })
  }

  return segments
}

function normalizeWithRawMap(value: string) {
  let text = ''
  const rawIndexes: number[] = []

  for (let index = 0; index < value.length; index += 1) {
    const char = value[index]
    if (/\s/.test(char)) {
      if (text && text[text.length - 1] !== ' ') {
        text += ' '
        rawIndexes.push(index)
      }
      continue
    }
    text += char
    rawIndexes.push(index)
  }

  if (text.endsWith(' ')) {
    text = text.slice(0, -1)
    rawIndexes.pop()
  }

  return { text, rawIndexes }
}

function normalizeInlineText(value: string) {
  return value.replace(/\s+/g, ' ').trim()
}

function quoteStatusClass(status: QuoteStatus) {
  if (status === 'Quote Verified') return 'is-quote-verified'
  if (status === 'Quote Slightly Modified') return 'is-quote-modified'
  if (status === 'Quote Mismatch') return 'is-quote-mismatch'
  return 'is-quote-missing'
}

function relevanceLabel(score: number) {
  return evidenceMatchLabel(score)
}

function evidenceMatchLabel(score: number) {
  if (score >= 0.34) return 'Strong text match'
  if (score >= 0.18) return 'Possible text match'
  if (score >= 0.08) return 'Weak text match'
  return 'Very weak text match'
}

function evidenceTerms(explanation: string | null) {
  if (!explanation) return []
  const match = explanation.match(/Matched terms:\s*(.+?)\.$/)
  if (!match) return []
  return match[1]
    .split(',')
    .map((term) => term.trim())
    .filter(Boolean)
}

function quoteHighlightTerms(quote: string) {
  return quote
    .toLowerCase()
    .match(/[a-z0-9%]+|[\u4e00-\u9fff]/g)
    ?.filter((term) => (term.length > 2 || /\d/.test(term)) && !HIGHLIGHT_STOPWORDS.has(term)) ?? []
}

function plainEvidenceExplanation(explanation: string | null) {
  if (!explanation) return 'Matched against source text.'
  const match = explanation.match(/Matched terms:\s*(.+?)\.$/)
  if (!match) return 'Matched against source text.'
  return `Matched words: ${match[1]}.`
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function shortSourceName(name: string) {
  const clean = name.split('/').pop()?.replace(/[_]+/g, ' ').replace(/\s+/g, ' ').trim() || name
  if (clean.length <= 56) return clean
  const extension = clean.match(/\.[A-Za-z0-9]{2,5}$/)?.[0] ?? ''
  const base = extension ? clean.slice(0, -extension.length) : clean
  return `${base.slice(0, 48).trim()}...${extension}`
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default App
