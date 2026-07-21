type DataTableProps = {
  detail: unknown
}

type TableData = {
  title?: string
  columns: string[]
  rows: (string | number)[][]
}

function asTableData(detail: unknown): TableData | null {
  if (!detail || typeof detail !== 'object') return null
  const d = detail as TableData
  if (!Array.isArray(d.columns) || !Array.isArray(d.rows)) return null
  return d
}

export function DataTable({ detail }: DataTableProps) {
  const table = asTableData(detail)
  if (!table) return null

  return (
    <div className="render-card data-table-card">
      {table.title ? <h4 className="chart-title">{table.title}</h4> : null}
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              {table.columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{String(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
