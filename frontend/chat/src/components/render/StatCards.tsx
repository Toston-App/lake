type StatCardsProps = {
  detail: unknown
}

type StatItem = {
  label: string
  value: string | number
  sub?: string
}

function asStatItems(detail: unknown): StatItem[] {
  if (!detail || typeof detail !== 'object') return []
  const items = (detail as { items?: unknown[] }).items
  if (!Array.isArray(items)) return []
  return items.filter(
    (item): item is StatItem =>
      !!item &&
      typeof item === 'object' &&
      typeof (item as StatItem).label === 'string' &&
      (typeof (item as StatItem).value === 'string' ||
        typeof (item as StatItem).value === 'number'),
  )
}

export function StatCards({ detail }: StatCardsProps) {
  const items = asStatItems(detail)
  if (items.length === 0) return null

  return (
    <div className="render-card stat-cards">
      <div className="summary-grid">
        {items.map((item) => (
          <div key={item.label} className="summary-stat">
            <span className="summary-label">{item.label}</span>
            <span className="summary-value">{String(item.value)}</span>
            {item.sub ? <span className="stat-sub">{item.sub}</span> : null}
          </div>
        ))}
      </div>
    </div>
  )
}
