type SourcesListProps = {
  detail: unknown
}

type SourceItem = {
  title?: string
  url?: string
  snippet?: string
  published_date?: string
}

function domainFromUrl(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function asSources(detail: unknown): SourceItem[] {
  if (!detail || typeof detail !== 'object') return []
  const d = detail as { results?: unknown[]; items?: unknown[] }
  const raw = d.results ?? d.items
  if (!Array.isArray(raw)) return []
  return raw.filter((item): item is SourceItem => !!item && typeof item === 'object')
}

export function SourcesList({ detail }: SourcesListProps) {
  const sources = asSources(detail)
  if (sources.length === 0) return null

  return (
    <div className="render-card sources-list">
      <h4 className="chart-title">Sources</h4>
      <ul className="sources-items">
        {sources.map((source, i) => {
          const title = source.title || source.url || `Source ${i + 1}`
          const url = source.url
          return (
            <li key={`${title}-${i}`} className="source-item">
              <div className="source-header">
                {url ? (
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="source-link"
                  >
                    {title}
                  </a>
                ) : (
                  <span className="source-title">{title}</span>
                )}
                {url ? (
                  <span className="source-domain">{domainFromUrl(url)}</span>
                ) : null}
              </div>
              {source.snippet ? (
                <p className="source-snippet">{source.snippet}</p>
              ) : null}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
