import type { ToolStep } from '../types/chat'

type ToolStepsListProps = {
  steps: ToolStep[]
}

export function ToolStepsList({ steps }: ToolStepsListProps) {
  if (steps.length === 0) return null

  return (
    <details className="tool-steps-details">
      <summary>Steps taken ({steps.length})</summary>
      <ul className="tool-steps-list">
        {steps.map((step, i) => (
          <li
            key={`${step.tool_name}-${i}`}
            className={`tool-step tool-step-${step.status}`}
          >
            <span className="tool-step-badge">{step.status.replace('_', ' ')}</span>
            <span>{step.label ?? step.tool_name}</span>
          </li>
        ))}
      </ul>
    </details>
  )
}
