export type ChatMessageRequest = {
  message: string
  conversation_id?: string
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

export type ChatMessageResponse = {
  conversation_id: string
  reply: string
  actions: ChatAction[]
  tool_steps: ToolStep[]
  pending_confirmation?: PendingConfirmation
}

export type ChatConfirmRequest = {
  action_id: string
  tool_args?: Record<string, unknown>
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
  updated_at: string
}

export type TokenResponse = {
  access_token: string
  token_type: string
}

export type UiMessage = {
  role: 'user' | 'assistant'
  content: string
  created_at?: string
  actions?: ChatAction[]
  toolSteps?: ToolStep[]
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
