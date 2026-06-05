// 后端 API 客户端。统一处理 baseURL、鉴权、错误码解析。
import type {
  TaskCreate, TaskOut, EstimateOut, ConfigOut, ConfigUpdate,
  TaskListOut, TaskResultsOut,
} from './types'

const BASE = '/api/v1'

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

  // ── 任务 ──
  listTasks: (params: { status?: string; page?: number; page_size?: number } = {}) => {
    const q = new URLSearchParams()
    if (params.status) q.set('status', params.status)
    if (params.page) q.set('page', String(params.page))
    if (params.page_size) q.set('page_size', String(params.page_size))
    const qs = q.toString()
    return request<TaskListOut>(`/tasks${qs ? `?${qs}` : ''}`)
  },
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

  downloadUrl: (id: string) => `${BASE}/tasks/${id}/download`,
}
