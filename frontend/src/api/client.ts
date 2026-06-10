// 后端 API 客户端。统一处理 baseURL、鉴权、错误码解析。
import type {
  TaskCreate, TaskOut, EstimateOut, ConfigOut, ConfigUpdate,
  TaskListOut, TaskResultsOut, Scene, GeneratedImage, QueueStats,
  VoiceItem, VoiceCategory, DraftTemplate,
} from './types'

// API 根地址：
// - 浏览器开发：用相对路径 '/api/v1'，由 Vite 代理转发到后端。
// - Electron 桌面端：主进程通过 preload 注入 window.__API_ORIGIN__
//   （形如 http://127.0.0.1:8000），此处拼成绝对地址，因为 file:// 下相对路径无效。
declare global {
  interface Window {
    __API_ORIGIN__?: string
    // Electron 桌面端注入：调系统原生选文件夹对话框 + 无边框窗口控制。浏览器端为 undefined。
    desktop?: {
      pickFolder: () => Promise<string | null>
      minimize: () => void
      maximize: () => void
      close: () => void
    }
  }
}
const ORIGIN = (typeof window !== 'undefined' && window.__API_ORIGIN__) || ''
const BASE = `${ORIGIN}/api/v1`

// 本机模式后端豁免鉴权；若设置了 access_token，从 localStorage 读取注入。
function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('access_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export class ApiError extends Error {
  status: number
  code?: string
  constructor(message: string, status: number, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

// 后端错误 detail 形如 "E2001: 未配置..."，拆出错误码便于 UI 区分处理。
function parseDetail(detail: unknown): { code?: string; message: string } {
  if (typeof detail === 'string') {
    const m = detail.match(/^(E\d{4}):\s*(.*)$/)
    return m ? { code: m[1], message: m[2] } : { message: detail }
  }
  return { message: '请求失败' }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init?.headers || {}),
    },
  })
  if (!res.ok) {
    let detail: unknown = res.statusText
    try { detail = (await res.json()).detail } catch { /* 非 JSON 响应 */ }
    const { code, message } = parseDetail(detail)
    throw new ApiError(message, res.status, code)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  // ── 配置 ──
  getConfig: () => request<ConfigOut>('/config'),
  updateConfig: (body: ConfigUpdate) =>
    request<ConfigOut>('/config', { method: 'PUT', body: JSON.stringify(body) }),
  testApi: () => request<{ ok: boolean; reply: string }>('/config/test-api', { method: 'POST' }),
  bgmList: () => request<{ dir: string; files: string[] }>('/config/bgm-list'),
  // 上传主角参考图，返回暂存路径，创建任务时填入 reference_image。
  uploadReference: async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${BASE}/tasks/upload-reference`, {
      method: 'POST', headers: { ...authHeaders() }, body: fd,
    })
    if (!res.ok) {
      let detail: unknown = res.statusText
      try { detail = (await res.json()).detail } catch { /* 非 JSON */ }
      const { message, code } = parseDetail(detail)
      throw new ApiError(message, res.status, code)
    }
    return res.json() as Promise<{ reference_image: string }>
  },
  testTts: () =>
    request<{ ok: boolean; provider: string; audio_bytes: number }>('/config/test-tts', { method: 'POST' }),
  // 试听：返回 mp3 Blob，前端用 Audio 播放。可传音色/语速覆盖配置。
  previewTts: async (body: { voice?: string; speed?: number } = {}) => {
    const res = await fetch(`${BASE}/config/preview-tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      let detail: unknown = res.statusText
      try { detail = (await res.json()).detail } catch { /* 非 JSON */ }
      const { message, code } = parseDetail(detail)
      throw new ApiError(message, res.status, code)
    }
    return res.blob()
  },
  // 音色库：分类 + 候选音色清单（每个带 available 探活状态）。probing=true 表示后台还在探活，稍后可重取。
  getVoices: () =>
    request<{ categories: VoiceCategory[]; voices: VoiceItem[]; probing?: boolean }>('/config/voices'),
  // 草稿动画模板清单
  getDraftTemplates: () =>
    request<{ templates: DraftTemplate[] }>('/config/draft-templates'),
  // 收藏/取消收藏音色
  toggleFavorite: (voiceId: string, action: 'add' | 'remove') =>
    request<{ favorites: string[] }>('/config/favorites', {
      method: 'PUT', body: JSON.stringify({ voice_id: voiceId, action }),
    }),

  // ── 任务 ──
  listTasks: (params: { status?: string; page?: number; page_size?: number } = {}) => {
    const q = new URLSearchParams()
    if (params.status) q.set('status', params.status)
    if (params.page) q.set('page', String(params.page))
    if (params.page_size) q.set('page_size', String(params.page_size))
    const qs = q.toString()
    return request<TaskListOut>(`/tasks${qs ? `?${qs}` : ''}`)
  },
  // 调度器并发状态：运行中/排队中数量与上限
  queueStats: () => request<QueueStats>('/tasks-queue/stats'),
  createTask: (body: TaskCreate) =>
    request<TaskOut>('/tasks', { method: 'POST', body: JSON.stringify(body) }),
  estimate: (body: TaskCreate) =>
    request<EstimateOut>('/tasks/estimate', { method: 'POST', body: JSON.stringify(body) }),
  getTask: (id: string) => request<TaskOut>(`/tasks/${id}`),
  getTaskResults: (id: string) => request<TaskResultsOut>(`/tasks/${id}/results`),
  rerun: (id: string, transcript?: string) =>
    request<TaskOut>(`/tasks/${id}/rerun`, {
      method: 'POST', body: JSON.stringify({ transcript: transcript ?? null }),
    }),
  cancel: (id: string) => request<TaskOut>(`/tasks/${id}/cancel`, { method: 'POST' }),
  resume: (id: string) => request<TaskOut>(`/tasks/${id}/resume`, { method: 'POST' }),

  // ── 逐句编辑 / 单图重试 / 单步重跑（对齐竞品「随停随跑、每步可改」）──
  // 保存编辑后的分镜（cap/desc_prompt/has_character），写回 SB+P 产物，不触发生图。
  saveScenes: (id: string, scenes: Scene[]) =>
    request<TaskOut>(`/tasks/${id}/scenes`, {
      method: 'PATCH', body: JSON.stringify({ scenes }),
    }),
  // 单张图重试：只重生成第 index 张，可带新提示词。失败返回原因；
  // 若被审核拦截会自动改写提示词重试，rewritten=true 时 new_prompt 是改写后的词。
  retryImage: (id: string, index: number, prompt?: string) =>
    request<{ index: number; image: GeneratedImage; failed: boolean; reason?: string | null
      rewritten?: boolean; new_prompt?: string | null }>(
      `/tasks/${id}/images/${index}/retry`, {
        method: 'POST', body: JSON.stringify({ prompt: prompt ?? null }),
      }),
  // 单步重跑：清掉该步及下游产物，从该步重算（上游走缓存）。
  rerunStep: (id: string, module: string) =>
    request<TaskOut>(`/tasks/${id}/step/${module}/rerun`, { method: 'POST' }),
  // 任务配图预览地址（按文件名取该任务 images 目录里的图）。
  imageUrl: (id: string, path: string) => {
    const name = path.split(/[\\/]/).pop() || ''
    return `${BASE}/tasks/${id}/image?name=${encodeURIComponent(name)}`
  },

  // 上传音频用 FormData，不能带 JSON Content-Type，单独处理。
  uploadAudio: async (id: string, file: File, outputMode: 'jianying' | 'mp4' = 'jianying') => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${BASE}/tasks/${id}/audio?output_mode=${outputMode}`, {
      method: 'POST', headers: authHeaders(), body: fd,
    })
    if (!res.ok) {
      let detail: unknown = res.statusText
      try { detail = (await res.json()).detail } catch { /* 非 JSON */ }
      const { code, message } = parseDetail(detail)
      throw new ApiError(message, res.status, code)
    }
    return res.json() as Promise<TaskOut>
  },
  // 用最新的图重新合成视频，复用上次上传的音频，无需重传
  recompose: (id: string, outputMode: 'jianying' | 'mp4' = 'jianying') =>
    request<TaskOut>(`/tasks/${id}/recompose?output_mode=${outputMode}`, { method: 'POST' }),
  // 手动修改任务标题（用作草稿名/下载文件名）
  updateTitle: (id: string, title: string) =>
    request<TaskOut>(`/tasks/${id}/title`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  // 直接编辑某步文本产物（不调 AI、不扣费、不触发下游）
  updateModuleOutput: (id: string, module: string, fields: Record<string, unknown>) =>
    request<TaskOut>(`/tasks/${id}/modules/${module}/output`, {
      method: 'PATCH', body: JSON.stringify({ fields }),
    }),

  downloadUrl: (id: string) => `${BASE}/tasks/${id}/download`,
}
