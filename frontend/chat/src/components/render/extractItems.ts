export function extractItems(detail: unknown): unknown[] {
  if (!detail) return []
  if (Array.isArray(detail)) return detail
  if (typeof detail === 'object' && Array.isArray((detail as { items?: unknown[] }).items)) {
    return (detail as { items: unknown[] }).items
  }
  return []
}
