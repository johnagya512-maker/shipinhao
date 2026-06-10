// 任务列表页：状态筛选 + 分页 + 自动轮询进行中任务 + 并发状态条。
import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { TaskListItem, TaskStatus, QueueStats } from '../api/types'

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '排队中', processing: '处理中', awaiting_confirm: '待确认', awaiting_audio: '待上传音频',
  completed: '已完成', blocked: '已拦截', failed: '失败', cancelled: '已取消',
}
const STATUS_COLOR: Record<TaskStatus, string> = {
  pending: 'bg-slate-700 text-slate-300', processing: 'bg-blue-500/15 text-blue-400',
  awaiting_confirm: 'bg-purple-500/15 text-purple-400',
  awaiting_audio: 'bg-amber-500/15 text-amber-400', completed: 'bg-green-500/15 text-green-400',
  blocked: 'bg-orange-500/15 text-orange-400', failed: 'bg-red-500/15 text-red-400',
  cancelled: 'bg-slate-700 text-slate-500',
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
  const [queue, setQueue] = useState<QueueStats | null>(null)

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

  const loadQueue = useCallback(async () => {
    try { setQueue(await api.queueStats()) } catch { /* 忽略，非关键 */ }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { loadQueue() }, [loadQueue])

  // 有进行中/排队任务时每 4 秒轮询刷新状态与并发条。
  useEffect(() => {
    const active = items.some((t) => t.status === 'processing' || t.status === 'pending')
      || (queue && (queue.running_count > 0 || queue.queued_count > 0))
    if (!active) return
    const id = setInterval(() => { load(); loadQueue() }, 4000)
    return () => clearInterval(id)
  }, [items, queue, load, loadQueue])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-2xl font-bold text-slate-100">任务列表</h1>
        <Link to="/tasks/new" className="btn-primary">+ 新建任务</Link>
      </div>

      {queue && (
        <div className="flex items-center gap-4 mb-4 px-4 py-2.5 rounded-xl bg-slate-900/70 border border-slate-800 text-sm">
          <span className="flex items-center gap-1.5 text-blue-400">
            <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
            运行中 <b className="text-slate-100">{queue.running_count}</b>
          </span>
          <span className="flex items-center gap-1.5 text-amber-400">
            <span className="w-2 h-2 rounded-full bg-amber-400" />
            排队中 <b className="text-slate-100">{queue.queued_count}</b>
          </span>
          <span className="text-slate-500">并行上限 {queue.max_concurrent}</span>
          {queue.queued_count > 0 && (
            <span className="text-xs text-slate-500">· 超出上限的任务会自动排队，名额释放后依次执行</span>
          )}
          <Link to="/config" className="ml-auto text-xs text-brand-400 hover:underline">调整并行数</Link>
        </div>
      )}

      <div className="flex gap-2 mb-4">
        {FILTERS.map((f) => (
          <button key={f.key} onClick={() => { setStatus(f.key); setPage(1) }}
            className={`px-3.5 py-1.5 rounded-full text-sm font-medium transition-colors ${
              status === f.key
                ? 'bg-slate-800 text-white'
                : 'bg-slate-800/50 border border-slate-700 text-slate-400 hover:border-slate-600'
            }`}>{f.label}</button>
        ))}
      </div>

      {error && (
        <div className="mb-4 px-4 py-2.5 rounded-lg text-sm bg-red-50 text-red-700 border border-red-100">{error}</div>
      )}

      <div className="bg-slate-900/70 rounded-2xl border border-slate-800 shadow-lg shadow-black/20 overflow-hidden">
        {items.length === 0 ? (
          <div className="px-5 py-16 text-center text-slate-400 text-sm">
            <div className="text-3xl mb-2">📭</div>
            暂无任务，去<Link to="/tasks/new" className="text-brand-600 hover:underline mx-1">新建一个</Link>吧
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-800/50 text-slate-400 text-left">
              <tr>
                <th className="px-5 py-3 font-medium">摘要</th>
                <th className="px-5 py-3 font-medium w-28">状态</th>
                <th className="px-5 py-3 font-medium w-24">成本</th>
                <th className="px-5 py-3 font-medium w-40">创建时间</th>
              </tr>
            </thead>
            <tbody>
              {items.map((t) => (
                <tr key={t.id} className={`border-t border-slate-100 hover:bg-brand-50/40 transition-colors ${
                  t.status === 'processing' ? 'bg-blue-500/5' : ''}`}>
                  <td className="px-5 py-3.5">
                    <Link to={`/tasks/${t.id}`} className="text-brand-700 font-medium hover:underline">
                      {t.transcript_preview || t.id}
                    </Link>
                    <div className="text-xs text-slate-400 mt-0.5">{t.track} · {t.target_audience}</div>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLOR[t.status]} ${
                      t.status === 'processing' ? 'animate-pulse' : ''}`}>
                      {STATUS_LABEL[t.status]}
                    </span>
                    {t.error_code && <div className="text-xs text-red-400 mt-0.5">{t.error_code}</div>}
                  </td>
                  <td className="px-5 py-3.5 text-slate-600">{t.total_cost.toFixed(4)} 元</td>
                  <td className="px-5 py-3.5 text-slate-400 text-xs">
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
            className="px-3 py-1.5 rounded-lg border border-slate-700 text-slate-300 bg-slate-800/40 hover:bg-slate-800 disabled:opacity-40">上一页</button>
          <span className="text-slate-500">{page} / {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}
            className="px-3 py-1.5 rounded-lg border border-slate-700 text-slate-300 bg-slate-800/40 hover:bg-slate-800 disabled:opacity-40">下一页</button>
        </div>
      )}
    </div>
  )
}
