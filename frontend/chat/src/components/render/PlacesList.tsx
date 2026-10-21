import { extractItems } from './extractItems'

type PlacesListProps = {
  detail: unknown
}

type PlaceRow = {
  name?: string
  is_online?: boolean
}

export function PlacesList({ detail }: PlacesListProps) {
  const items = extractItems(detail) as PlaceRow[]
  if (items.length === 0) return <p className="action-empty">No places found.</p>

  return (
    <div className="render-card list-card">
      <h4 className="chart-title">Places</h4>
      <ul className="entity-list">
        {items.map((place, i) => (
          <li key={`${place.name ?? 'place'}-${i}`} className="entity-row">
            <span className="entity-name">{place.name ?? 'Place'}</span>
            <span className="entity-meta">
              {place.is_online ? 'Online' : 'Physical'}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
