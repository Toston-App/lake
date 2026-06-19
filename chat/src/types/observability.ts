export type ObservabilitySummary = {
  turns: number
  errors: number
  error_rate: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  total_cost: number | null
  avg_cost_per_turn: number | null
  avg_duration_ms: number | null
  pending_confirmations: number
  pending_questions: number
  unique_users: number
  unique_conversations: number
}

export type TimeseriesPoint = {
  bucket: string | null
  turns: number
  tokens: number
  cost: number | null
}

export type ToolStat = {
  tool_name: string
  calls: number
  failed: number
  error_rate: number
  avg_duration_ms: number | null
}

export type ModelStat = {
  model: string | null
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost: number | null
  avg_duration_ms: number | null
}

export type TurnRow = {
  id: number
  conversation_id: string
  user_id: number
  user_email: string | null
  model: string | null
  status: string
  duration_ms: number | null
  rounds_count: number
  actions_count: number
  total_tokens: number | null
  total_cost_usd: number | null
  pending_confirmation: boolean
  pending_question: boolean
  error: string | null
  user_message: string | null
  started_at: string | null
}

export type LLMCallDetail = {
  id: number
  round_index: number
  model: string | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  cached_tokens: number | null
  cost_usd: number | null
  duration_ms: number | null
  finish_reason: string | null
  had_tool_calls: boolean
  created_at: string | null
}

export type ToolCallDetail = {
  id: number
  round_index: number
  tool_name: string
  arguments: Record<string, unknown> | null
  result: Record<string, unknown> | null
  status: string
  duration_ms: number | null
  error: string | null
  created_at: string | null
}

export type TurnDetail = TurnRow & {
  trace_id: string | null
  request_id: string | null
  provider: string
  assistant_reply: string | null
  total_prompt_tokens: number | null
  total_completion_tokens: number | null
  ended_at: string | null
  llm_calls: LLMCallDetail[]
  tool_calls: ToolCallDetail[]
}

export type TurnsListResponse = {
  items: TurnRow[]
  count: number
}

export type TimeRangeKey = '7d' | '30d' | 'all'

export type UserMe = {
  id: number | null
  email: string | null
  country: string | null
  is_superuser: boolean
}
