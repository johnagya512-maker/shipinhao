// 任务列表页：状态筛选 + 分页 + 自动轮询进行中任务。
import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { TaskListItem, TaskStatus } from '../api/types'

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '待处理', processing: '处理中', awaiting_audio: '待上传音频',
  completed: '已完成', blocked: '已拦截', failed: '失败', cancelled: '已取消',
}
const STATUS_COLOR: Record<TaskStatus, string> = {
  pending: 'bg-slate-100 text-slate-600', processing: 'bg-blue-100 text-blue-700',
  awaiting_audio: 'bg-amber-100 text-amber-700', completed: 'bg-green-100 text-green-700',
  blocked: 'bg-orange-100 text-orange-700', failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-slate-100 text-slate-400',
}
const FILTERS: Array<{ key: string; label: string }> = [
  { key: '', label: '全部' },
  { key: 'processing', label: '处理中' },
  { key: 'awaiting_audio', label: '待上传音频' },
  { key: 'completed', label: '已完成' },
  { key: 'blocked', label: '已拦截' },
  { key: 'failed', label: '失败' },
]
const PAGE_SIZE = 20

export default function TaskListPage() {
  const [items, setItems] = useState<TaskListItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await api.listTasks({ status: status || undefined, page, page_size: PAGE_SIZE })
      setItems(r.items)
      setTotal(r.total)
      setError(null)
    } catch (e) {
      setError((e as ApiError).message)
    }
  }, [status, page])

  useEffect(() => { load() }, [load])

  // 有进行中任务时每 4 秒轮询刷新状态。
  useEffect(() => {
    const active = items.some((t) => t.status === 'processing' || t.status === 'pending')
    if (!active) return
    const id = setInterval(load, 4000)
    return () => clearInterval(id)
  }, [items, load])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">任务列表</h1>
        <Link to="/tasks/new"
          className="px-4 py-2 rounded-lg bg-brand-600 text-white text-sm font-medium">
          + 新建任务
        </Link>
      </div>

      <div className="flex gap-2 mb-4">
        {FILTERS.map((f) => (
          <button key={f.key} onClick={() => { setStatus(f.key); setPage(1) }}
            className={`px-3 py-1.5 rounded-lg text-sm ${
              status === f.key ? 'bg-slate-800 text-white' : 'bg-white border border-slate-200 text-slate-600'
            }`}>{f.label}</button>
        ))}
      </div>

      {error && (
        <div className="mb-4 px-4 py-2 rounded-lg text-sm bg-red-50 text-red-700">{error}</div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        {items.length === 0 ? (
          <div className="px-5 py-12 text-center text-slate-400 text-sm">暂无任务</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-500 text-left">
              <tr>
                <th className="px-4 py-2.5 font-medium">摘要</th>
                <th className="px-4 py-2.5 font-medium w-28">状态</th>
                <th className="px-4 py-2.5 font-medium w-24">成本</th>
                <th className="px-4 py-2.5 font-medium w-40">创建时间</th>
              </tr>
            </thead>
            <tbody>
              {items.map((t) => (
                <tr key={t.id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link to={`/tasks/${t.id}`} className="text-brand-700 hover:underline">
                      {t.transcript_preview || t.id}
                    </Link>
                    <div className="text-xs text-slate-400 mt-0.5">{t.track} · {t.target_audience}</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded text-xs ${STATUS_COLOR[t.status]}`}>
                      {STATUS_LABEL[t.status]}
                    </span>
                    {t.error_code && <div className="text-xs text-red-400 mt-0.5">{t.error_code}</div>}
                  </td>
                  <td className="px-4 py-3 text-slate-600">{t.total_cost.toFixed(4)} 元</td>
                  <td className="px-4 py-3 text-slate-400 text-xs">
                    {new Date(t.created_at).toLocaleString('zh-CN')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 mt-4 text-sm">
          <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
            className="px-3 py-1.5 rounded-lg border border-slate-200 disabled:opacity-40">上一页</button>
          <span className="text-slate-500">{page} / {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}
            className="px-3 py-1.5 rounded-lg border border-slate-200 disabled:opacity-40">下一页</button>
        </div>
      )}
    </div>
  )
}
