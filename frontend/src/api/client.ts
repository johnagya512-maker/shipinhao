// 后端 API 客户端。统一处理 baseURL、鉴权、错误码解析。
import type {
  TaskCreate, TaskOut, EstimateOut, ConfigOut, ConfigUpdate,
  TaskListOut, TaskResultsOut, Scene, GeneratedImage, QueueStats,
  VoiceItem, VoiceCategory, DraftTemplate, ViralStructure,
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
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      // 默认 120s 超时：LLM 类接口慢，但也不能无限转圈。调用方可传自己的 signal 覆盖。
      signal: init?.signal ?? AbortSignal.timeout(120_000),
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
        ...(init?.headers || {}),
      },
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'TimeoutError') {
      throw new ApiError('请求超时，可能是大模型响应太慢，请重试。', 0, 'E_TIMEOUT')
    }
    throw new ApiError('网络请求失败，请检查后端是否在运行。', 0, 'E_NETWORK')
  }
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
  // 二创预览：拆爆款结构 + 按骨架改写出成品文案（创建任务前预览）
  analyzeStructure: (body: {
    text: string; track?: string; target_audience?: string; title?: string
    monetization_mode?: string; rewrite_strength?: string
    narrative_perspective?: string; creation_mode?: string
  }) =>
    request<{ structure: ViralStructure; script: string }>('/tasks/analyze-structure', {
      method: 'POST', body: JSON.stringify(body), signal: AbortSignal.timeout(240_000),
    }),
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
  // 解析视频链接出逐字稿（不创建任务）：采集无水印视频 → ASR 转写。
  // 解析较慢（下载视频+转写），给足超时。失败抛带错误码的 ApiError 引导手填。
  parseTranscript: (douyinUrl: string) =>
    request<{ transcript: string; title: string; author: string; platform: string
      play_count: number; digg_count: number }>('/tasks/parse-transcript', {
      method: 'POST', body: JSON.stringify({ douyin_url: douyinUrl }),
      signal: AbortSignal.timeout(180_000),
    }),
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
  // 单张图重试：只重生成第 index 张，可带新提示词。一次高质量尝试，失败返回原因，
  // 不在后台自动改写重生——是否再试、是否改文案由用户决定。
  retryImage: (id: string, index: number, prompt?: string) =>
    request<{ index: number; image: GeneratedImage; failed: boolean; reason?: string | null
      rewritten?: boolean; new_prompt?: string | null }>(
      `/tasks/${id}/images/${index}/retry`, {
        // 单张生图可能较慢，给 3 分钟，别用默认 120s
        method: 'POST', body: JSON.stringify({ prompt: prompt ?? null }),
        signal: AbortSignal.timeout(180_000),
      }),
  // 多张图一起重新组图：传选中的图片下标，后端合并成一次组图请求生成（省请求、
  // 风格统一、人物一致）。后端按 ref 把图合并成最多两组、各一次请求出多张并发下载，
  // 不随张数线性变慢，固定给 5 分钟超时（默认 120s 不够组图出图+下载）。
  // genMode 可当场指定本次出图方式（grid 九宫格省成本 / per_image 逐张画质优先）；
  // 不传则跟随建任务时选的模式。
  batchRetryImages: (id: string, indices: number[], genMode?: string) =>
    request<{ count: number; cost?: number; results: { index: number; failed: boolean
      reason?: string | null; image: GeneratedImage }[] }>(
      `/tasks/${id}/images/batch-retry`, {
        method: 'POST', body: JSON.stringify({ indices, gen_mode: genMode ?? null }),
        signal: AbortSignal.timeout(300_000),
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
