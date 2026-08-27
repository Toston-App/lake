import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ApiError } from '../api/client'
import { fetchEconomics, fetchEconomicsUsers } from '../api/economics'
import type {
  EconomicsSummary,
  EconomicsUserRow,
  OutcomeFilter,
  SortKey,
  TimeRangeKey,
} from '../types'

const RANGES: { value: TimeRangeKey; label: string }[] = [
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: 'mtd', label: 'MTD' },
]

function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  const abs = Math.abs(n)
  const digits = abs !== 0 && abs < 0.01 ? 4 : 2
  return `$${n.toFixed(digits)}`
}

function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString()
}

function fmtLimit(n: number | null | undefined): string {
  return n == null ? '∞' : n.toLocaleString()
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.detail
  if (err instanceof Error) return err.message
  return 'Request failed'
}

function profitClass(n: number): string {
  if (n > 0) return 'profit-win'
  if (n < 0) return 'profit-loss'
  return 'profit-flat'
}

export function Dashboard() {
  const [range, setRange] = useState<TimeRangeKey>('30d')
  const [summary, setSummary] = useState<EconomicsSummary | null>(null)
  const [users, setUsers] = useState<EconomicsUserRow[]>([])
  const [usersCount, setUsersCount] = useState(0)
  const [tier, setTier] = useState('')
  const [outcome, setOutcome] = useState<OutcomeFilter>('all')
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortKey>('profit')
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const [skip, setSkip] = useState(0)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const limit = 50

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [s, u] = await Promise.all([
        fetchEconomics(range),
        fetchEconomicsUsers({
          range,
          tier: tier || undefined,
          outcome,
          search: search || undefined,
          sort,
          order,
          skip,
          limit,
        }),
      ])
      setSummary(s)
      setUsers(u.items)
      setUsersCount(u.count)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [range, tier, outcome, search, sort, order, skip])

  useEffect(() => {
    const task = window.setTimeout(() => {
      void load()
    }, 0)
    return () => window.clearTimeout(task)
  }, [load])

  const chartData = useMemo(
    () =>
      (summary?.by_tier ?? []).map((t) => ({
        label: t.label,
        profit: t.profit_usd,
        cost: t.ai_cost_usd,
        revenue: t.estimated_revenue_usd,
      })),
    [summary],
  )

  function submitSearch(e: FormEvent) {
    e.preventDefault()
    setSkip(0)
    setSearch(searchInput.trim())
  }

  function toggleSort(key: SortKey) {
    if (sort === key) {
      setOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'))
    } else {
      setSort(key)
      setOrder(key === 'profit' ? 'asc' : 'desc')
    }
    setSkip(0)
  }

  const net = summary?.profit_usd ?? 0
  const page = Math.floor(skip / limit) + 1
  const pages = Math.max(1, Math.ceil(usersCount / limit))

  return (
    <>
      <div className="panel-toolbar">
        <div className="range-toggle">
          {RANGES.map((r) => (
            <button
              key={r.value}
              type="button"
              className={range === r.value ? 'range-btn active' : 'range-btn'}
              onClick={() => {
                setRange(r.value)
                setSkip(0)
              }}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <main className="admin-main">
        <p className="disclaimer">
          Revenue is catalog list price ($4.99 / $14.99), pro-rated to the
          window ({summary?.window_days ?? '—'} days / 30). Not Polar invoices.
        </p>

        {error && <p className="error-banner">{error}</p>}
        {loading && <p className="admin-loading">Loading…</p>}

        <section className="admin-cards">
          <Card label="AI cost" value={fmtUsd(summary?.ai_cost_usd)} />
          <Card
            label="Est. revenue"
            value={fmtUsd(summary?.estimated_revenue_usd)}
            sub={`${fmtInt(summary?.paying_users)} paying`}
          />
          <Card
            label="Net"
            value={fmtUsd(summary?.profit_usd)}
            valueClass={profitClass(net)}
            sub={net >= 0 ? 'winning' : 'losing'}
          />
          <Card
            label="Losing users"
            value={fmtInt(summary?.losing_users)}
            valueClass="profit-loss"
          />
          <Card
            label="Winning users"
            value={fmtInt(summary?.winning_users)}
            valueClass="profit-win"
          />
          <Card label="Users in window" value={fmtInt(summary?.users)} />
        </section>

        <section className="admin-chart">
          <h4 className="chart-title">Profit by tier</h4>
          {chartData.length === 0 ? (
            <p className="empty-row">No usage in this window</p>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(value) => fmtUsd(Number(value))} />
                <Bar dataKey="revenue" name="Revenue" fill="#168fff" />
                <Bar dataKey="cost" name="AI cost" fill="#d97706" />
                <Bar dataKey="profit" name="Profit" fill="#16a34a" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </section>

        <section className="admin-turns">
          <div className="admin-turns-header">
            <h4 className="chart-title">Users</h4>
            <form className="admin-filters" onSubmit={submitSearch}>
              <select
                value={tier}
                onChange={(e) => {
                  setTier(e.target.value)
                  setSkip(0)
                }}
              >
                <option value="">All tiers</option>
                <option value="free">Free</option>
                <option value="tier1">Basic</option>
                <option value="tier2">Premium</option>
              </select>
              <select
                value={outcome}
                onChange={(e) => {
                  setOutcome(e.target.value as OutcomeFilter)
                  setSkip(0)
                }}
              >
                <option value="all">All outcomes</option>
                <option value="losing">Losing</option>
                <option value="winning">Winning</option>
              </select>
              <input
                type="search"
                placeholder="Search email or name"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
              />
              <button type="submit">Search</button>
            </form>
          </div>

          <table className="admin-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Tier</th>
                <th>Status</th>
                <SortHeader
                  label="Turns"
                  active={sort === 'turns'}
                  order={order}
                  onClick={() => toggleSort('turns')}
                />
                <SortHeader
                  label="Tokens"
                  active={sort === 'tokens'}
                  order={order}
                  onClick={() => toggleSort('tokens')}
                />
                <SortHeader
                  label="Cost"
                  active={sort === 'cost'}
                  order={order}
                  onClick={() => toggleSort('cost')}
                />
                <SortHeader
                  label="Revenue"
                  active={sort === 'revenue'}
                  order={order}
                  onClick={() => toggleSort('revenue')}
                />
                <SortHeader
                  label="Profit"
                  active={sort === 'profit'}
                  order={order}
                  onClick={() => toggleSort('profit')}
                />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <UserRows
                  key={u.user_id}
                  user={u}
                  expanded={expandedId === u.user_id}
                  onToggle={() =>
                    setExpandedId((id) => (id === u.user_id ? null : u.user_id))
                  }
                />
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan={8} className="empty-row">
                    No users match
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <div className="pager">
            <p className="admin-count">
              {fmtInt(usersCount)} users · page {page} of {pages}
            </p>
            <div className="pager-btns">
              <button
                type="button"
                className="logout-btn"
                disabled={skip === 0}
                onClick={() => setSkip((s) => Math.max(0, s - limit))}
              >
                Previous
              </button>
              <button
                type="button"
                className="logout-btn"
                disabled={skip + limit >= usersCount}
                onClick={() => setSkip((s) => s + limit)}
              >
                Next
              </button>
            </div>
          </div>
        </section>
      </main>
    </>
  )
}

function Card({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string
  value: string
  sub?: string
  valueClass?: string
}) {
  return (
    <div className="admin-card">
      <span className="admin-card-label">{label}</span>
      <span className={`admin-card-value ${valueClass ?? ''}`}>{value}</span>
      {sub && <span className="admin-card-sub">{sub}</span>}
    </div>
  )
}

function SortHeader({
  label,
  active,
  order,
  onClick,
}: {
  label: string
  active: boolean
  order: 'asc' | 'desc'
  onClick: () => void
}) {
  return (
    <th>
      <button type="button" className="sort-btn" onClick={onClick}>
        {label}
        {active ? (order === 'asc' ? ' ↑' : ' ↓') : ''}
      </button>
    </th>
  )
}

function UserRows({
  user,
  expanded,
  onToggle,
}: {
  user: EconomicsUserRow
  expanded: boolean
  onToggle: () => void
}) {
  return (
    <>
      <tr className="clickable-row" onClick={onToggle}>
        <td>
          <div className="user-cell">
            <strong>{user.email ?? `user ${user.user_id}`}</strong>
            {user.name && <span className="user-name">{user.name}</span>}
          </div>
        </td>
        <td>
          <span className="tier-badge" data-tier={user.tier}>
            {user.tier_label}
          </span>
        </td>
        <td>{user.status}</td>
        <td>{fmtInt(user.turns)}</td>
        <td>{fmtInt(user.tokens)}</td>
        <td>{fmtUsd(user.ai_cost_usd)}</td>
        <td>{fmtUsd(user.estimated_revenue_usd)}</td>
        <td className={profitClass(user.profit_usd)}>{fmtUsd(user.profit_usd)}</td>
      </tr>
      {expanded && (
        <tr className="expand-row">
          <td colSpan={8}>
            <div className="expand-grid">
              <span>Paid entitlement: {user.is_paid ? 'yes' : 'no'}</span>
              <span>Catalog price: {fmtUsd(user.monthly_price_usd)}/mo</span>
              <span>
                Message quota: {fmtInt(user.turns)} / {fmtLimit(user.messages_limit)}
              </span>
              <span>
                Token quota: {fmtInt(user.tokens)} / {fmtLimit(user.tokens_limit)}
              </span>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
