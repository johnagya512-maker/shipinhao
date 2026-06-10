// 任务状态枚举（对应 PRD 5.3 状态流转）
export type TaskStatus =
  | 'pending'
  | 'processing'
  | 'awaiting_confirm'
  | 'awaiting_audio'
  | 'completed'
  | 'blocked'
  | 'failed'
  | 'cancelled'

export interface TaskCreate {
  douyin_url?: string | null
  transcript?: string | null
  keyword?: string | null
  title?: string | null
  author?: string | null
  modules: string[]
  target_audience?: string
  track?: string
  monetization_mode?: string
  image_style?: string | null
  aspect_ratio?: string
  rewrite_strength?: string
  narrative_perspective?: string
  voice_speed?: number
  voice?: string
  reference_image?: string
  bgm?: string
  cost_limit?: number
  time_limit?: number
  enable_subtitles?: boolean
  enable_animations?: boolean
  processing_mode?: string
  pause_mode?: string
  pause_steps?: string[]
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
  duration?: number | null
}

// 分镜：口播原文 + 绘图提示词 + 是否主角出场
export interface Scene {
  id?: number
  cap: string
  desc_prompt: string
  has_character: boolean
}

// E 产物里的单张配图
export interface GeneratedImage {
  path: string
  sub_type: string
  suggested_duration?: number
  fallback?: boolean
  fail_reason?: string
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
  processing_mode?: string
  pause_mode?: string
  pause_steps?: string[] | null
  paused_at?: string | null
  aspect_ratio?: string | null
  reference_image?: string | null
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
  collect_provider: string
  collect_api_key_mask: string
  asr_provider: string
  asr_api_key_mask: string
  tts_provider: string
  tts_api_key_mask: string
  tts_voice: string
  tts_appid: string
  tts_favorites?: string[]
  daily_cost_cap: number
  concurrency: number
  max_concurrent_tasks: number
  jianying_draft_dir: string
  task_storage_dir: string
  bgm_dir: string
}

export interface ConfigUpdate {
  llm_provider?: string
  llm_model?: string
  llm_api_key?: string
  image_provider?: string
  image_api_key?: string
  collect_provider?: string
  collect_api_key?: string
  asr_provider?: string
  asr_api_key?: string
  tts_provider?: string
  tts_api_key?: string
  tts_voice?: string
  tts_appid?: string
  daily_cost_cap?: number
  concurrency?: number
  max_concurrent_tasks?: number
  jianying_draft_dir?: string
  task_storage_dir?: string
  bgm_dir?: string
}

export interface QueueStats {
  running: string[]
  queued: string[]
  running_count: number
  queued_count: number
  max_concurrent: number
}

export interface VoiceItem {
  id: string
  name: string
  tag: string
  category: string
}

export interface VoiceCategory {
  key: string
  name: string
  desc: string
}
