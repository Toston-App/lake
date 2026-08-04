import { useEffect, useState } from 'react'
import { browserChatLanguage, chatCopy } from '../utils/i18n'
import { formatToolName } from '../utils/tools'

type ThinkingPanelProps = {
  isActive: boolean
  toolName?: string | null
}

export function ThinkingPanel({ isActive, toolName }: ThinkingPanelProps) {
  const [placeholderIndex, setPlaceholderIndex] = useState(0)
  const language = browserChatLanguage()
  const placeholders = chatCopy(language).thinking

  useEffect(() => {
    if (!isActive) return
    const id = window.setInterval(() => {
      setPlaceholderIndex((i) => (i + 1) % placeholders.length)
    }, 2200)
    return () => window.clearInterval(id)
  }, [isActive, placeholders.length])

  if (!isActive) return null

  const statusText = toolName
    ? `${formatToolName(toolName, language)}…`
    : placeholders[placeholderIndex]

  return (
    <div
      className="thinking-panel thinking-panel-placeholder"
      aria-live="polite"
    >
      <span className="thinking-spinner" aria-hidden />
      <p>{statusText}</p>
    </div>
  )
}
