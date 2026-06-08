// 任务详情页：状态轮询 + 各模块产物展示 + 音频上传触发成片 + 下载 + 重跑。
import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { TaskResultsOut, TaskStatus } from '../api/types'

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '待处理', processing: '处理中', awaiting_confirm: '待确认', awaiting_audio: '待上传音频',
  completed: '已完成', blocked: '已拦截', failed: '失败', cancelled: '已取消',
}
const MODULE_NAME: Record<string, string> = {
  A: 'A 清洗', B: 'B 改写', D: 'D 图书识别', E: 'E 配图', P: 'P 提示词生成',
  F: 'F 配音分段', G: 'G 视频合成', H: 'H 合规审查', T: 'T 配音',
}
// 暂停步骤 → 中文，用于「待确认」提示。
const STEP_LABEL: Record<string, string> = {
  B: '智能改写', H: '合规审查', F: '分句分镜', P: '提示词生成', E: '批量生图',
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

  // 进行中状态每 3 秒轮询（待确认不轮询——等用户点确认）。
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

  async function onResume() {
    setBusy(true); setError(null)
    try {
      await api.resume(id)
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
  const canUpload = ['awaiting_audio', 'failed'].includes(task.status)
  const canRerun = ['blocked', 'failed', 'cancelled'].includes(task.status)
  const canCancel = !['completed', 'failed', 'cancelled'].includes(task.status)
  const hasDownload = task.status === 'completed'
  const awaitingConfirm = task.status === 'awaiting_confirm'

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center gap-3 mb-5">
        <Link to="/tasks" className="text-sm text-slate-500 hover:text-slate-300 hover:underline">← 列表</Link>
        <h1 className="text-2xl font-bold text-slate-100">{task.title || task.id}</h1>
        <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-300">
          {STATUS_LABEL[task.status]}
        </span>
      </div>

      {error && (
        <div className="mb-4 px-4 py-2.5 rounded-lg text-sm bg-red-50 text-red-700 border border-red-100">{error}</div>
      )}

      <div className="card mb-4 text-sm space-y-2">
        <div className="flex justify-between"><span className="text-slate-500">赛道</span><span className="font-medium">{task.track}</span></div>
        <div className="flex justify-between"><span className="text-slate-500">受众</span><span className="font-medium">{task.target_audience}</span></div>
        <div className="flex justify-between"><span className="text-slate-500">模块</span><span className="font-medium">{task.modules.join(' / ')}</span></div>
        <div className="flex justify-between"><span className="text-slate-500">累计成本</span><span className="font-medium">{task.total_cost.toFixed(4)} 元</span></div>
        {task.error_message && (
          <div className="flex justify-between text-red-600">
            <span>错误</span><span>{task.error_code}: {task.error_message}</span>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-3 mb-5">
        {canUpload && (
          <label className={`px-5 py-2.5 rounded-lg text-sm font-medium cursor-pointer transition-colors ${
            uploading ? 'bg-slate-200 text-slate-400' : 'bg-brand-600 text-white hover:bg-brand-700 shadow-sm shadow-brand-600/20'
          }`}>
            {uploading ? '上传中…' : '上传音频生成成片'}
            <input ref={fileRef} type="file" accept="audio/*" className="hidden"
              disabled={uploading} onChange={onUpload} />
          </label>
        )}
        {hasDownload && (
          <a href={api.downloadUrl(id)} className="btn-ghost">下载剪映草稿/成片</a>
        )}
        {canCancel && (
          <button onClick={onCancel} disabled={busy} className="btn-ghost">取消任务</button>
        )}
      </div>

      {hasDownload && (
        <div className="mb-5 px-4 py-2.5 rounded-lg text-sm bg-emerald-500/10 border border-emerald-500/30 text-emerald-300">
          ✅ 剪映草稿已生成。若已在配置页设置「剪映草稿目录」，打开剪映即可看到并编辑；否则点上方「下载剪映草稿/成片」获取。
        </div>
      )}

      {awaitingConfirm && (
        <div className="bg-brand-600/10 border border-brand-500/30 rounded-2xl p-5 mb-5">
          <div className="text-sm font-semibold text-brand-300 mb-1">
            已暂停 · 等待确认
            {task.paused_at && <span className="ml-2 text-slate-400 font-normal">当前停在：{STEP_LABEL[task.paused_at] || task.paused_at} 之后</span>}
          </div>
          <p className="text-xs text-slate-400 mb-3">检查下方该步骤产物，确认无误后继续后续步骤（已完成步骤不会重算、不重复扣费）。</p>
          <button onClick={onResume} disabled={busy}
            className="px-5 py-2.5 rounded-lg bg-brand-600 text-white text-sm font-medium
              shadow-sm shadow-brand-600/20 transition-colors hover:bg-brand-700 disabled:opacity-50">
            {busy ? '继续中…' : '确认并继续'}
          </button>
        </div>
      )}

      {canRerun && (
        <div className="bg-amber-50 border border-amber-200 rounded-2xl p-5 mb-5">
          <div className="text-sm font-semibold text-amber-800 mb-2">重新生成</div>
          <textarea rows={3} className="field"
            placeholder="可修改逐字稿后重跑；留空则沿用原文"
            value={rerunText} onChange={(e) => setRerunText(e.target.value)} />
          <button onClick={onRerun} disabled={busy}
            className="mt-3 px-5 py-2.5 rounded-lg bg-amber-600 text-white text-sm font-medium
              shadow-sm shadow-amber-600/20 transition-colors hover:bg-amber-700 disabled:opacity-50">
            {busy ? '提交中…' : '重新生成'}
          </button>
        </div>
      )}

      <h2 className="font-semibold text-slate-200 mb-3">模块产物</h2>
      <div className="space-y-3">
        {modules.length === 0 && <div className="text-sm text-slate-400">尚无产物，任务处理中…</div>}
        {modules.map((m) => (
          <div key={m.module} className="card !p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="font-medium text-sm text-slate-200">{MODULE_NAME[m.module] || m.module}</span>
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                m.status === 'success' ? 'bg-green-500/15 text-green-400'
                : m.status === 'failed' ? 'bg-red-500/15 text-red-400' : 'bg-slate-700 text-slate-400'
              }`}>{m.status}</span>
            </div>
            <pre className="text-xs bg-slate-950/60 rounded-lg p-3 overflow-auto max-h-72 whitespace-pre-wrap break-all text-slate-400">
              {JSON.stringify(m.output, null, 2)}
            </pre>
            {m.cost > 0 && <div className="text-xs text-slate-400 mt-1">成本 {m.cost.toFixed(4)} 元</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
