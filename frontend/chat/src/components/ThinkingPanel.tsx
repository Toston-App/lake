import { useEffect, useState } from 'react'
import { browserChatLanguage, chatCopy } from '../utils/i18n'

type ThinkingPanelProps = {
  isActive: boolean
}

export function ThinkingPanel({ isActive }: ThinkingPanelProps) {
  const [placeholderIndex, setPlaceholderIndex] = useState(0)
  const placeholders = chatCopy(browserChatLanguage()).thinking

  useEffect(() => {
    if (!isActive) return
    const id = window.setInterval(() => {
      setPlaceholderIndex((i) => (i + 1) % placeholders.length)
    }, 2200)
    return () => window.clearInterval(id)
  }, [isActive, placeholders.length])

  if (!isActive) return null

  return (
    <div className="thinking-panel thinking-panel-placeholder">
      <span className="thinking-spinner" aria-hidden />
      <p>{placeholders[placeholderIndex]}</p>
    </div>
  )
}
