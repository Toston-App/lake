import { useEffect, useState } from 'react'
import type { ToolStep } from '../types/chat'
import { THINKING_PLACEHOLDERS } from '../utils/tools'

type ThinkingPanelProps = {
  steps?: ToolStep[]
  isActive: boolean
}

export function ThinkingPanel({ steps, isActive }: ThinkingPanelProps) {
  const [placeholderIndex, setPlaceholderIndex] = useState(0)

  useEffect(() => {
    if (!isActive || (steps && steps.length > 0)) return
    const id = window.setInterval(() => {
      setPlaceholderIndex((i) => (i + 1) % THINKING_PLACEHOLDERS.length)
    }, 2200)
    return () => window.clearInterval(id)
  }, [isActive, steps])

  if (!isActive) return null

  if (steps && steps.length > 0) {
    return (
      <div className="thinking-panel">
        <p className="thinking-title">Working…</p>
        <ul className="thinking-steps">
          {steps.map((step, i) => (
            <li
              key={`${step.tool_name}-${i}`}
              className={`thinking-step thinking-step-${step.status}`}
            >
              <span className="thinking-step-icon" aria-hidden />
              <span>{step.label ?? step.tool_name}</span>
            </li>
          ))}
        </ul>
      </div>
    )
  }

  return (
    <div className="thinking-panel thinking-panel-placeholder">
      <span className="thinking-spinner" aria-hidden />
      <p>{THINKING_PLACEHOLDERS[placeholderIndex]}</p>
    </div>
  )
}
