import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  ApiError,
} from '../../api/client'
import {
  fetchCostTimeseries,
  fetchModelStats,
  fetchSummary,
  fetchToolStats,
  fetchTurnDetail,
  fetchTurns,
} from '../../api/observability'
import type {
  LLMCallDetail,
  ModelStat,
  ObservabilitySummary,
  TimeRangeKey,
  TimeseriesPoint,
  ToolCallDetail,
  ToolStat,
  TurnDetail,
  TurnRow,
} from '../../types/observability'

type Props = {
  onBack: () => void
}

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'ok', label: 'ok' },
  { value: 'error', label: 'error' },
  { value: 'max_rounds', label: 'max_rounds' },
  { value: 'pending_confirmation', label: 'pending_confirmation' },
  { value: 'pending_question', label: 'pending_question' },
]

function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return `$${n.toFixed(4)}`
}

function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString()
}

function fmtPct(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return `${(n * 100).toFixed(1)}%`
}

function fmtMs(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  if (n < 1000) return `${Math.round(n)} ms`
  return `${(n / 1000).toFixed(1)} s`
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString()
}

function fmtBucket(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.detail
  if (err instanceof Error) return err.message
  return 'Request failed'
}

function statusClass(status: string): string {
  if (status === 'ok') return 'status-badge status-ok'
  if (status === 'error' || status === 'max_rounds') return 'status-badge status-error'
  return 'status-badge status-pending'
}

export function ObservabilityDashboard({ onBack }: Props) {
  const [range, setRange] = useState<TimeRangeKey>('7d')
  const [summary, setSummary] = useState<ObservabilitySummary | null>(null)
  const [timeseries, setTimeseries] = useState<TimeseriesPoint[]>([])
  const [tools, setTools] = useState<ToolStat[]>([])
  const [models, setModels] = useState<ModelStat[]>([])
  const [turns, setTurns] = useState<TurnRow[]>([])
  const [turnsCount, setTurnsCount] = useState(0)

  const [statusFilter, setStatusFilter] = useState('')
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')

  const [selectedTurn, setSelectedTurn] = useState<TurnDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadOverview = useCallback(async (r: TimeRangeKey) => {
    setLoading(true)
    setError(null)
    try {
      const [s, ts, tl, ml] = await Promise.all([
        fetchSummary(r),
        fetchCostTimeseries(r, 'day'),
        fetchToolStats(r),
        fetchModelStats(r),
      ])
      setSummary(s)
      setTimeseries(ts)
      setTools(tl)
      setModels(ml)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadTurns = useCallback(
    async (r: TimeRangeKey, st: string, srch: string) => {
      try {
        const res = await fetchTurns({
          range: r,
          status: st || undefined,
          search: srch || undefined,
          limit: 50,
        })
        setTurns(res.items)
        setTurnsCount(res.count)
      } catch (err) {
        setError(errorMessage(err))
      }
    },
    [],
  )

  useEffect(() => {
    const task = window.setTimeout(() => {
      void loadOverview(range)
      void loadTurns(range, statusFilter, search)
    }, 0)
    return () => window.clearTimeout(task)
  }, [range, statusFilter, search, loadOverview, loadTurns])

  const chartData = useMemo(
    () =>
      timeseries.map((t) => ({
        label: fmtBucket(t.bucket),
        turns: t.turns,
        tokens: t.tokens,
        cost: t.cost ?? 0,
      })),
    [timeseries],
  )

  async function openTurn(turn: TurnRow) {
    setDetailLoading(true)
    setSelectedTurn(null)
    try {
      const detail = await fetchTurnDetail(turn.id)
      setSelectedTurn(detail)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setDetailLoading(false)
    }
  }

  function submitSearch(e: React.FormEvent) {
    e.preventDefault()
    setSearch(searchInput.trim())
  }

  return (
    <div className="app admin-view">
      <header className="chat-header">
        <h1>Observability</h1>
        <div className="admin-header-actions">
          <div className="range-toggle">
            {(['7d', '30d', 'all'] as TimeRangeKey[]).map((r) => (
              <button
                key={r}
                type="button"
                className={range === r ? 'range-btn active' : 'range-btn'}
                onClick={() => setRange(r)}
              >
                {r === 'all' ? 'All time' : r}
              </button>
            ))}
          </div>
          <button type="button" className="logout-btn" onClick={onBack}>
            Back to chat
          </button>
        </div>
      </header>

      <main className="admin-main">
        {error && <p className="error-banner">{error}</p>}
        {loading && <p className="admin-loading">Loading…</p>}

        <section className="admin-cards">
          <Card label="Turns" value={fmtInt(summary?.turns)} />
          <Card
            label="Error rate"
            value={fmtPct(summary?.error_rate)}
            sub={`${fmtInt(summary?.errors)} errors`}
          />
          <Card
            label="Total cost"
            value={fmtUsd(summary?.total_cost)}
            sub={`avg ${fmtUsd(summary?.avg_cost_per_turn)}/turn`}
          />
          <Card
            label="Total tokens"
            value={fmtInt(summary?.total_tokens)}
            sub={`${fmtInt(summary?.prompt_tokens)} in / ${fmtInt(
              summary?.completion_tokens,
            )} out`}
          />
          <Card
            label="Avg duration"
            value={fmtMs(summary?.avg_duration_ms)}
          />
          <Card
            label="Active users"
            value={fmtInt(summary?.unique_users)}
            sub={`${fmtInt(summary?.unique_conversations)} conversations`}
          />
        </section>

        <section className="admin-chart">
          <h4 className="chart-title">Cost & token usage over time</h4>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} />
              <YAxis yAxisId="cost" tick={{ fontSize: 11 }} />
              <YAxis
                yAxisId="tokens"
                orientation="right"
                tick={{ fontSize: 11 }}
              />
              <Tooltip />
              <Line
                yAxisId="cost"
                type="monotone"
                dataKey="cost"
                name="Cost (USD)"
                stroke="#168fff"
                strokeWidth={2}
                dot={false}
              />
              <Line
                yAxisId="tokens"
                type="monotone"
                dataKey="tokens"
                name="Tokens"
                stroke="#16a34a"
                strokeWidth={2}
                dot={false}
              />
              <Line
                yAxisId="tokens"
                type="monotone"
                dataKey="turns"
                name="Turns"
                stroke="#d97706"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </section>

        <section className="admin-tables">
          <div className="admin-table-card">
            <h4 className="chart-title">Tools</h4>
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Tool</th>
                  <th>Calls</th>
                  <th>Failed</th>
                  <th>Error rate</th>
                  <th>Avg ms</th>
                </tr>
              </thead>
              <tbody>
                {tools.map((t) => (
                  <tr key={t.tool_name}>
                    <td>{t.tool_name}</td>
                    <td>{t.calls}</td>
                    <td>{t.failed}</td>
                    <td>{fmtPct(t.error_rate)}</td>
                    <td>{fmtMs(t.avg_duration_ms)}</td>
                  </tr>
                ))}
                {tools.length === 0 && (
                  <tr>
                    <td colSpan={5} className="empty-row">
                      No tool calls
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="admin-table-card">
            <h4 className="chart-title">Models</h4>
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Calls</th>
                  <th>Tokens</th>
                  <th>Cost</th>
                  <th>Avg ms</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m.model ?? 'unknown'}>
                    <td>{m.model ?? '—'}</td>
                    <td>{m.calls}</td>
                    <td>{fmtInt(m.total_tokens)}</td>
                    <td>{fmtUsd(m.cost)}</td>
                    <td>{fmtMs(m.avg_duration_ms)}</td>
                  </tr>
                ))}
                {models.length === 0 && (
                  <tr>
                    <td colSpan={5} className="empty-row">
                      No model data
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="admin-turns">
          <div className="admin-turns-header">
            <h4 className="chart-title">Recent turns</h4>
            <form className="admin-filters" onSubmit={submitSearch}>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                {STATUS_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <input
                type="search"
                placeholder="Search user message…"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
              />
              <button type="submit">Search</button>
            </form>
          </div>
          <table className="admin-table">
            <thead>
              <tr>
                <th>Started</th>
                <th>User</th>
                <th>Model</th>
                <th>Status</th>
                <th>Message</th>
                <th>Tokens</th>
                <th>Cost</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody>
              {turns.map((t) => (
                <tr key={t.id} className="clickable-row" onClick={() => void openTurn(t)}>
                  <td>{fmtTime(t.started_at)}</td>
                  <td>{t.user_email ?? t.user_id}</td>
                  <td>{t.model ?? '—'}</td>
                  <td>
                    <span className={statusClass(t.status)}>{t.status}</span>
                  </td>
                  <td className="msg-cell">
                    {t.user_message ?? '—'}
                  </td>
                  <td>{fmtInt(t.total_tokens)}</td>
                  <td>{fmtUsd(t.total_cost_usd)}</td>
                  <td>{fmtMs(t.duration_ms)}</td>
                </tr>
              ))}
              {turns.length === 0 && (
                <tr>
                  <td colSpan={8} className="empty-row">
                    No turns found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <p className="admin-count">{turnsCount} shown</p>
        </section>
      </main>

      {selectedTurn && (
        <TurnDrawer detail={selectedTurn} onClose={() => setSelectedTurn(null)} />
      )}
      {detailLoading && (
        <div className="drawer-overlay">
          <div className="drawer">
            <p className="admin-loading">Loading turn…</p>
          </div>
        </div>
      )}
    </div>
  )
}

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="admin-card">
      <span className="admin-card-label">{label}</span>
      <span className="admin-card-value">{value}</span>
      {sub && <span className="admin-card-sub">{sub}</span>}
    </div>
  )
}

function TurnDrawer({
  detail,
  onClose,
}: {
  detail: TurnDetail
  onClose: () => void
}) {
  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <h3>Turn #{detail.id}</h3>
          <button type="button" className="drawer-close" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="drawer-body">
          <div className="drawer-meta">
            <span>
              <strong>Status:</strong>{' '}
              <span className={statusClass(detail.status)}>{detail.status}</span>
            </span>
            <span>
              <strong>User:</strong> {detail.user_email ?? detail.user_id}
            </span>
            <span>
              <strong>Model:</strong> {detail.model ?? '—'}
            </span>
            <span>
              <strong>Conversation:</strong> {detail.conversation_id}
            </span>
            <span>
              <strong>Started:</strong> {fmtTime(detail.started_at)}
            </span>
            <span>
              <strong>Duration:</strong> {fmtMs(detail.duration_ms)}
            </span>
            <span>
              <strong>Rounds:</strong> {detail.rounds_count}
            </span>
            <span>
              <strong>Total cost:</strong> {fmtUsd(detail.total_cost_usd)}
            </span>
            <span>
              <strong>Tokens:</strong>{' '}
              {fmtInt(detail.total_prompt_tokens)} in /{' '}
              {fmtInt(detail.total_completion_tokens)} out
            </span>
            {detail.trace_id && (
              <span>
                <strong>Trace:</strong> {detail.trace_id}
              </span>
            )}
          </div>

          {detail.error && (
            <p className="error-banner">{detail.error}</p>
          )}

          <div className="drawer-section">
            <h5>User message</h5>
            <pre className="drawer-pre">{detail.user_message ?? '—'}</pre>
          </div>

          <div className="drawer-section">
            <h5>Assistant reply</h5>
            <pre className="drawer-pre">{detail.assistant_reply ?? '—'}</pre>
          </div>

          <div className="drawer-section">
            <h5>LLM calls ({detail.llm_calls.length})</h5>
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Round</th>
                  <th>Model</th>
                  <th>Tokens (in/out)</th>
                  <th>Cached</th>
                  <th>Cost</th>
                  <th>Ms</th>
                  <th>Finish</th>
                  <th>Tools</th>
                </tr>
              </thead>
              <tbody>
                {detail.llm_calls.map((c) => (
                  <LLMCallRow key={c.id} c={c} />
                ))}
              </tbody>
            </table>
          </div>

          <div className="drawer-section">
            <h5>Tool calls ({detail.tool_calls.length})</h5>
            <div className="tool-call-list">
              {detail.tool_calls.map((c) => (
                <ToolCallRow key={c.id} c={c} />
              ))}
              {detail.tool_calls.length === 0 && (
                <p className="empty-row">No tool calls</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function LLMCallRow({ c }: { c: LLMCallDetail }) {
  return (
    <tr>
      <td>{c.round_index}</td>
      <td>{c.model ?? '—'}</td>
      <td>
        {fmtInt(c.prompt_tokens)} / {fmtInt(c.completion_tokens)}
      </td>
      <td>{fmtInt(c.cached_tokens)}</td>
      <td>{fmtUsd(c.cost_usd)}</td>
      <td>{fmtMs(c.duration_ms)}</td>
      <td>{c.finish_reason ?? '—'}</td>
      <td>{c.had_tool_calls ? 'yes' : 'no'}</td>
    </tr>
  )
}

function ToolCallRow({ c }: { c: ToolCallDetail }) {
  return (
    <details className="tool-call">
      <summary>
        <span className={statusClass(c.status)}>{c.status}</span>
        <span className="tool-call-name">{c.tool_name}</span>
        <span className="tool-call-meta">
          round {c.round_index} · {fmtMs(c.duration_ms)}
        </span>
      </summary>
      {c.error && <p className="error-banner">{c.error}</p>}
      <div className="tool-call-cols">
        <div>
          <h6>Arguments</h6>
          <pre className="drawer-pre">{prettyJson(c.arguments)}</pre>
        </div>
        <div>
          <h6>Result</h6>
          <pre className="drawer-pre">{prettyJson(c.result)}</pre>
        </div>
      </div>
    </details>
  )
}

function prettyJson(v: unknown): string {
  if (v === null || v === undefined) return '—'
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}
