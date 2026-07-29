import { useState, type FormEvent } from 'react'
import { browserChatLanguage, chatCopy } from '../utils/i18n'

type MessageInputProps = {
  onSend: (text: string) => void
  disabled: boolean
}

export function MessageInput({ onSend, disabled }: MessageInputProps) {
  const [text, setText] = useState('')
  const copy = chatCopy(browserChatLanguage())

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
  }

  return (
    <form className="message-input" onSubmit={handleSubmit}>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={copy.message}
        rows={2}
        disabled={disabled}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            handleSubmit(e)
          }
        }}
      />
      <button type="submit" disabled={disabled || !text.trim()}>
        {copy.send}
      </button>
    </form>
  )
}
