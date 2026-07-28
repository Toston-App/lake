import { useEffect, useState } from 'react'
import { THINKING_PLACEHOLDERS } from '../utils/tools'

type ThinkingPanelProps = {
  isActive: boolean
}

export function ThinkingPanel({ isActive }: ThinkingPanelProps) {
  const [placeholderIndex, setPlaceholderIndex] = useState(0)

  useEffect(() => {
    if (!isActive) return
    const id = window.setInterval(() => {
      setPlaceholderIndex((i) => (i + 1) % THINKING_PLACEHOLDERS.length)
    }, 2200)
    return () => window.clearInterval(id)
  }, [isActive])

  if (!isActive) return null

  return (
    <div className="thinking-panel thinking-panel-placeholder">
      <span className="thinking-spinner" aria-hidden />
      <p>{THINKING_PLACEHOLDERS[placeholderIndex]}</p>
    </div>
  )
}
