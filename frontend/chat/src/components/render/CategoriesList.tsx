import { extractItems } from './extractItems'

type CategoriesListProps = {
  detail: unknown
}

type SubcategoryRow = {
  name?: string
}

type CategoryRow = {
  name?: string
  is_income?: boolean
  subcategories?: SubcategoryRow[]
}

export function CategoriesList({ detail }: CategoriesListProps) {
  const items = extractItems(detail) as CategoryRow[]
  if (items.length === 0) return <p className="action-empty">No categories found.</p>

  return (
    <div className="render-card list-card">
      <h4 className="chart-title">Categories</h4>
      <ul className="entity-list">
        {items.map((category, i) => (
          <li key={`${category.name ?? 'category'}-${i}`} className="entity-block">
            <div className="entity-row">
              <span className="entity-name">{category.name ?? 'Category'}</span>
              <span className="entity-meta">
                {category.is_income ? 'Income' : 'Expense'}
              </span>
            </div>
            {category.subcategories && category.subcategories.length > 0 ? (
              <ul className="entity-sublist">
                {category.subcategories.map((sub, j) => (
                  <li key={`${sub.name ?? 'sub'}-${j}`}>{sub.name}</li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}
