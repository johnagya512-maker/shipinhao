// 任务详情页：状态轮询 + 各模块产物展示 + 音频上传触发成片 + 下载 + 重跑。
import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { TaskResultsOut, TaskStatus } from '../api/types'

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '待处理', processing: '处理中', awaiting_audio: '待上传音频',
  completed: '已完成', blocked: '已拦截', failed: '失败', cancelled: '已取消',
}
const MODULE_NAME: Record<string, string> = {
  A: 'A 清洗', B: 'B 改写', D: 'D 图书识别', E: 'E 配图',
  F: 'F 配音分段', G: 'G 视频合成', H: 'H 合规审查',
}

export default function TaskDetailPage() {
  const { id = '' } = useParams()
  const [data, setData] = useState<TaskResultsOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [rerunText, setRerunText] = useState('')
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    try {
      setData(await api.getTaskResults(id))
      setError(null)
    } catch (e) {
      setError((e as ApiError).message)
    }
  }, [id])

  useEffect(() => { load() }, [load])

  // 进行中状态每 3 秒轮询。
  useEffect(() => {
    const s = data?.task.status
    if (s !== 'processing' && s !== 'pending') return
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [data?.task.status, load])

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true); setError(null)
    try {
      await api.uploadAudio(id, file, 'jianying')
      await load()
    } catch (err) {
      setError((err as ApiError).message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function onRerun() {
    setBusy(true); setError(null)
    try {
      await api.rerun(id, rerunText.trim() || undefined)
      setRerunText('')
      await load()
    } catch (err) {
      setError((err as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  async function onCancel() {
    setBusy(true); setError(null)
    try {
      await api.cancel(id)
      await load()
    } catch (err) {
      setError((err as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  if (error && !data) {
    return <div className="px-4 py-2 rounded-lg text-sm bg-red-50 text-red-700">{error}</div>
  }
  if (!data) return <div className="text-slate-500">加载中…</div>

  const { task, modules } = data
  const canUpload = ['awaiting_audio', 'completed', 'failed'].includes(task.status)
  const canRerun = ['blocked', 'failed', 'cancelled'].includes(task.status)
  const canCancel = !['completed', 'failed', 'cancelled'].includes(task.status)
  const hasDownload = task.status === 'completed'

  return (
    <div className="max-w-3xl">
      <div className="flex items-center gap-3 mb-4">
        <Link to="/tasks" className="text-sm text-slate-500 hover:underline">← 列表</Link>
        <h1 className="text-xl font-semibold">{task.title || task.id}</h1>
        <span className="px-2 py-0.5 rounded text-xs bg-slate-100 text-slate-600">
          {STATUS_LABEL[task.status]}
        </span>
      </div>

      {error && (
        <div className="mb-4 px-4 py-2 rounded-lg text-sm bg-red-50 text-red-700">{error}</div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-5 mb-4 text-sm space-y-1">
        <div className="flex justify-between"><span className="text-slate-500">赛道</span><span>{task.track}</span></div>
        <div className="flex justify-between"><span className="text-slate-500">受众</span><span>{task.target_audience}</span></div>
        <div className="flex justify-between"><span className="text-slate-500">模块</span><span>{task.modules.join(' / ')}</span></div>
        <div className="flex justify-between"><span className="text-slate-500">累计成本</span><span>{task.total_cost.toFixed(4)} 元</span></div>
        {task.error_message && (
          <div className="flex justify-between text-red-600">
            <span>错误</span><span>{task.error_code}: {task.error_message}</span>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-3 mb-5">
        {canUpload && (
          <label className={`px-4 py-2 rounded-lg text-sm font-medium cursor-pointer ${
            uploading ? 'bg-slate-200 text-slate-400' : 'bg-brand-600 text-white'
          }`}>
            {uploading ? '上传中…' : '上传音频生成成片'}
            <input ref={fileRef} type="file" accept="audio/*" className="hidden"
              disabled={uploading} onChange={onUpload} />
          </label>
        )}
        {hasDownload && (
          <a href={api.downloadUrl(id)}
            className="px-4 py-2 rounded-lg border border-slate-300 text-sm font-medium">下载成片</a>
        )}
        {canCancel && (
          <button onClick={onCancel} disabled={busy}
            className="px-4 py-2 rounded-lg border border-slate-300 text-sm font-medium disabled:opacity-50">取消任务</button>
        )}
      </div>

      {canRerun && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 mb-5">
          <div className="text-sm font-medium text-amber-800 mb-2">重新生成</div>
          <textarea rows={3} className="w-full border rounded-lg px-3 py-2 text-sm"
            placeholder="可修改逐字稿后重跑；留空则沿用原文"
            value={rerunText} onChange={(e) => setRerunText(e.target.value)} />
          <button onClick={onRerun} disabled={busy}
            className="mt-2 px-4 py-2 rounded-lg bg-amber-600 text-white text-sm font-medium disabled:opacity-50">
            {busy ? '提交中…' : '重新生成'}
          </button>
        </div>
      )}

      <h2 className="font-medium text-slate-800 mb-2">模块产物</h2>
      <div className="space-y-3">
        {modules.length === 0 && <div className="text-sm text-slate-400">尚无产物，任务处理中…</div>}
        {modules.map((m) => (
          <div key={m.module} className="bg-white rounded-xl border border-slate-200 p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="font-medium text-sm">{MODULE_NAME[m.module] || m.module}</span>
              <span className={`text-xs px-2 py-0.5 rounded ${
                m.status === 'success' ? 'bg-green-100 text-green-700'
                : m.status === 'failed' ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-500'
              }`}>{m.status}</span>
            </div>
            <pre className="text-xs bg-slate-50 rounded-lg p-3 overflow-auto max-h-72 whitespace-pre-wrap break-all">
              {JSON.stringify(m.output, null, 2)}
            </pre>
            {m.cost > 0 && <div className="text-xs text-slate-400 mt-1">成本 {m.cost.toFixed(4)} 元</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
