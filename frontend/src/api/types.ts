// 任务状态枚举（对应 PRD 5.3 状态流转）
export type TaskStatus =
  | 'pending'
  | 'processing'
  | 'awaiting_audio'
  | 'completed'
  | 'blocked'
  | 'failed'
  | 'cancelled'

export interface TaskCreate {
  transcript: string
  keyword?: string | null
  title?: string | null
  author?: string | null
  modules: string[]
  target_audience?: string
  track?: string
  monetization_mode?: string
  image_style?: string | null
  cost_limit?: number
  time_limit?: number
  enable_subtitles?: boolean
  enable_animations?: boolean
}

export interface TaskOut {
  id: string
  status: TaskStatus
  total_cost: number
  error_code?: string | null
  error_message?: string | null
}

export interface EstimateOut {
  estimated_cost: number
  daily_cap_reached: boolean
}

export interface TaskListItem {
  id: string
  status: TaskStatus
  total_cost: number
  track: string
  target_audience: string
  transcript_preview: string
  error_code?: string | null
  created_at: string
  updated_at: string
}

export interface TaskListOut {
  items: TaskListItem[]
  total: number
  page: number
  page_size: number
}

// 详情页：单个模块产物
export interface ModuleResult {
  module: string
  status: string
  output: Record<string, unknown> | null
  cost: number
  tokens_in?: number | null
  tokens_out?: number | null
  retry_count: number
}

export interface TaskDetail {
  id: string
  status: TaskStatus
  total_cost: number
  transcript: string
  title?: string | null
  author?: string | null
  keyword?: string | null
  track: string
  modules: string[]
  target_audience: string
  monetization_mode: string
  enable_subtitles: boolean
  enable_animations: boolean
  error_code?: string | null
  error_message?: string | null
  created_at: string
  updated_at: string
}

export interface TaskResultsOut {
  task: TaskDetail
  modules: ModuleResult[]
}

export interface ConfigOut {
  llm_provider: string
  llm_model: string
  llm_api_key_mask: string
  image_provider: string
  image_api_key_mask: string
  daily_cost_cap: number
  concurrency: number
}

export interface ConfigUpdate {
  llm_provider?: string
  llm_model?: string
  llm_api_key?: string
  image_provider?: string
  image_api_key?: string
  daily_cost_cap?: number
  concurrency?: number
}
