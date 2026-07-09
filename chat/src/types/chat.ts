import type { UIMessage } from 'ai'

export type TokenResponse = {
  access_token: string
  token_type: string
}

export type ChatAction = {
  type: string
  detail?: Record<string, unknown>
}

export type ToolStep = {
  tool_name: string
  status: 'completed' | 'failed' | 'pending_confirmation'
  label?: string
}

export type PendingConfirmation = {
  action_id: string
  tool_name: string
  preview: Record<string, unknown>
  message: string
}

export type QuestionOption = {
  id: string
  label: string
}

export type PendingQuestion = {
  question: string
  options: QuestionOption[]
  allow_multiple: boolean
}

export type RenderDirective = {
  component: string
  detail?: Record<string, unknown>
}

export type ChatConfirmRequest = {
  action_id: string
  tool_args?: Record<string, unknown>
}

export type ChatMessageResponse = {
  conversation_id: string
  reply: string
  actions: ChatAction[]
  render?: RenderDirective[]
  tool_steps: ToolStep[]
  pending_confirmation?: PendingConfirmation
  pending_question?: PendingQuestion
  /** Vercel AI UI message the new frontend appends after a /confirm. */
  ui_message?: UIMessage
}

export type ConversationMessage = {
  role: string
  content: string
  created_at?: string
}

export type ConversationSummary = {
  id: string
  title: string
  updated_at: string
  last_message_preview?: string
}

export type ConversationDetail = {
  id: string
  title: string
  messages: ConversationMessage[]
  /** UI messages (id/role/parts) for seeding `useChat`; present for
   * conversations saved by the Pydantic AI flow, absent for legacy ones. */
  ui_messages?: UIMessage[]
  updated_at: string
}

export type Balance = {
  total: number
  income: number
  outcome: number
}

export type SummaryResponse = {
  country: string
  balance: Balance
  period_income: number
  period_expenses: number
  period_net: number
}

export type ChartSeries = {
  name: string
  data: number[]
  color?: string
}

export type LineBarChartData = {
  series: ChartSeries[]
  xAxis: string[]
  summary?: Record<string, unknown>
}

export type CategorySlice = {
  name: string
  value: number
  color?: string
}

export type CategoryChartData = {
  categories: CategorySlice[]
  drilldown?: { name: string; data: { name: string; value: number }[] }[]
}

export type ChartsResponse = {
  net: LineBarChartData | unknown[]
  income_vs_expense: LineBarChartData | unknown[]
  categories: CategoryChartData | unknown[] | null
}

/**
 * A Vercel AI `data-*` part emitted by the backend via `DataChunk`. The
 * `type` is `data-<component-id>` (e.g. `data-accounts-list`); `data` carries
 * the render payload. The MessageBubble parts dispatcher switches on `type`.
 */
export type ChatDataPart = {
  type: string
  data: unknown
}
