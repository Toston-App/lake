import { useState } from 'react'
import type { PendingQuestion } from '../types/chat'
import { chatCopy, normalizeChatLanguage } from '../utils/i18n'

type QuestionChipsProps = {
  pending: PendingQuestion
  onAnswer: (labels: string[]) => void
  disabled?: boolean
}

export function QuestionChips({
  pending,
  onAnswer,
  disabled = false,
}: QuestionChipsProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const copy = chatCopy(normalizeChatLanguage(pending.response_language))

  const toggle = (label: string) => {
    if (disabled) return
    setSelected((prev) => {
      const next = new Set(prev)
      if (pending.allow_multiple) {
        if (next.has(label)) next.delete(label)
        else next.add(label)
      } else {
        next.clear()
        next.add(label)
        onAnswer([label])
      }
      return next
    })
  }

  const submitMultiple = () => {
    if (selected.size === 0 || disabled) return
    onAnswer(Array.from(selected))
    setSelected(new Set())
  }

  return (
    <div className="question-chips-card">
      <p className="question-chips-prompt">{pending.question}</p>
      <div className="question-chips">
        {pending.options.map((option) => {
          const isSelected = selected.has(option.label)
          return (
            <button
              key={option.id}
              type="button"
              className={`question-chip${isSelected ? ' question-chip-selected' : ''}`}
              onClick={() => toggle(option.label)}
              disabled={disabled}
            >
              {option.label}
            </button>
          )
        })}
      </div>
      {pending.allow_multiple ? (
        <button
          type="button"
          className="question-chips-submit"
          onClick={submitMultiple}
          disabled={disabled || selected.size === 0}
        >
          {copy.continue}
        </button>
      ) : null}
    </div>
  )
}
