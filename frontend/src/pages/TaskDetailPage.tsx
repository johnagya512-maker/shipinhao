// 任务详情页：竞品式两栏布局。
// 左栏常驻：任务信息 + 指标(耗时/当前步骤/分镜数) + 操作按钮 + 步骤时间线。
// 右栏分页：产物预览 / 分镜画廊。每步可见、可编辑、可单步/单图重试（随停随跑）。
import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { TaskResultsOut, TaskStatus } from '../api/types'
import StepTimeline from '../components/StepTimeline'
import SceneGallery from '../components/SceneGallery'
import ProductPreview from '../components/ProductPreview'
import ScriptReview from '../components/ScriptReview'

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '待处理', processing: '处理中', awaiting_confirm: '待确认', awaiting_audio: '待上传音频',
  completed: '已完成', blocked: '已拦截', failed: '失败', cancelled: '已取消',
}
const STEP_LABEL: Record<string, string> = {
  B: '智能改写', H: '合规审查', F: '分句分镜', P: '提示词生成', E: '批量生图',
}

type Tab = 'preview' | 'gallery'

export default function TaskDetailPage() {
  const { id = '' } = useParams()
  const [data, setData] = useState<TaskResultsOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [rerunText, setRerunText] = useState('')
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<Tab>('preview')
  const fileRef = useRef<HTMLInputElement>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  // 发布物料「复制」反馈：记录刚复制的项 key，短暂显示「已复制」。
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const copyText = useCallback((key: string, text: string) => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopiedKey(key)
      setTimeout(() => setCopiedKey((k) => (k === key ? null : k)), 1500)
    }).catch(() => {})
  }, [])

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

  // 指标：总耗时（各步 duration 求和）、当前/最后步骤、分镜数。
  const metrics = useMemo(() => {
    if (!data) return { total: 0, current: '-', scenes: 0 }
    const total = data.modules.reduce((s, m) => s + (m.duration ?? 0), 0)
    const running = data.modules.find((m) => m.status === 'pending')
    const lastDone = [...data.modules].reverse().find((m) => m.status === 'success')
    const p = data.modules.find((m) => m.module === 'P')
    const sb = data.modules.find((m) => m.module === 'SB')
    const scenes = ((p?.output?.scenes ?? sb?.output?.scenes) as unknown[] | undefined)?.length ?? 0
    return {
      total: Math.round(total),
      current: running?.module || lastDone?.module || '-',
      scenes,
    }
  }, [data])

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

  async function onRecompose() {
    setBusy(true); setError(null)
    try {
      await api.recompose(id, 'jianying')
      await load()
    } catch (err) {
      setError((err as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  async function onSaveTitle() {
    const t = titleDraft.trim()
    if (!t) { setEditingTitle(false); return }
    setBusy(true); setError(null)
    try {
      await api.updateTitle(id, t)
      setEditingTitle(false)
      await load()
    } catch (err) {
      setError((err as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  async function onCancel() {
    setBusy(true); setError(null)
    try { await api.cancel(id); await load() }
    catch (err) { setError((err as ApiError).message) }
    finally { setBusy(false) }
  }

  async function onResume() {
    setBusy(true); setError(null)
    try { await api.resume(id); await load() }
    catch (err) { setError((err as ApiError).message) }
    finally { setBusy(false) }
  }

  // 重试 AI 配音：配音失败进入 awaiting_audio 后，从 T 步重跑（清 T/G 产物，上游走缓存）。
  async function onRetryTTS() {
    setBusy(true); setError(null)
    try { await api.rerunStep(id, 'T'); await load() }
    catch (err) { setError((err as ApiError).message) }
    finally { setBusy(false) }
  }

  if (error && !data) {
    return <div className="px-4 py-2 rounded-lg text-sm bg-red-50 text-red-700">{error}</div>
  }
  if (!data) return <div className="text-slate-500">加载中…</div>

  const { task, modules } = data
  const canUpload = ['awaiting_audio', 'failed'].includes(task.status)
  const canRerun = ['blocked', 'failed', 'cancelled'].includes(task.status)
  const canCancel = !['completed', 'failed', 'cancelled'].includes(task.status)
  // 已完成的任务可用最新的图重新合成（复用历史音频）
  const canRecompose = task.status === 'completed'
  const inJianying = modules.some(m => m.output && (m.output as Record<string, unknown>).in_jianying === true)
  const hasDownload = task.status === 'completed' && !inJianying
  const showCompletedTip = task.status === 'completed'
  const awaitingConfirm = task.status === 'awaiting_confirm'
  // 停在文案步（B）：用聚焦的文案确认窗口，而非通用确认卡片。
  const awaitingScript = awaitingConfirm && task.paused_at === 'B'

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex items-center gap-3 mb-5">
        <Link to="/tasks" className="text-sm text-slate-500 hover:text-slate-300 hover:underline">← 列表</Link>
        {editingTitle ? (
          <div className="flex items-center gap-2 flex-1">
            <input autoFocus value={titleDraft} maxLength={30}
              onChange={(e) => setTitleDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') onSaveTitle(); if (e.key === 'Escape') setEditingTitle(false) }}
              className="text-xl font-bold bg-slate-800 border border-brand-500/50 rounded-lg px-3 py-1 text-slate-100 flex-1 max-w-md outline-none" />
            <button onClick={onSaveTitle} disabled={busy}
              className="px-3 py-1.5 rounded-lg bg-brand-600 text-white text-sm hover:bg-brand-700 disabled:opacity-50">保存</button>
            <button onClick={() => setEditingTitle(false)}
              className="px-3 py-1.5 rounded-lg text-slate-400 text-sm hover:text-slate-200">取消</button>
          </div>
        ) : (
          <h1 className="text-2xl font-bold text-slate-100 group flex items-center gap-2">
            {task.title || task.id}
            <button onClick={() => { setTitleDraft(task.title || ''); setEditingTitle(true) }}
              title="修改标题（用作草稿名/下载文件名）"
              className="text-sm text-slate-500 hover:text-brand-400 opacity-0 group-hover:opacity-100 transition-opacity">✎</button>
          </h1>
        )}
        <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-300">
          {STATUS_LABEL[task.status]}
        </span>
      </div>

      {error && (
        <div className="mb-4 px-4 py-2.5 rounded-lg text-sm bg-red-50 text-red-700 border border-red-100">{error}</div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-5">
        {/* ── 左栏：信息 + 指标 + 操作 + 时间线 ── */}
        <aside className="space-y-4">
          {/* 指标卡 */}
          <div className="card !p-4 grid grid-cols-3 gap-2 text-center">
            <div>
              <div className="text-lg font-bold text-slate-100">{metrics.total}s</div>
              <div className="text-[11px] text-slate-500">总耗时</div>
            </div>
            <div>
              <div className="text-lg font-bold text-slate-100">{metrics.current}</div>
              <div className="text-[11px] text-slate-500">当前步骤</div>
            </div>
            <div>
              <div className="text-lg font-bold text-slate-100">{metrics.scenes}</div>
              <div className="text-[11px] text-slate-500">分镜数</div>
            </div>
          </div>

          {/* 任务信息 */}
          <div className="card !p-4 text-sm space-y-2">
            <div className="flex justify-between"><span className="text-slate-500">赛道</span><span className="font-medium">{task.track}</span></div>
            <div className="flex justify-between"><span className="text-slate-500">受众</span><span className="font-medium">{task.target_audience}</span></div>
            <div className="flex justify-between"><span className="text-slate-500">累计成本</span><span className="font-medium">{task.total_cost.toFixed(4)} 元</span></div>
            {task.error_message && (
              <div className="text-red-400 text-xs pt-1 border-t border-slate-700/60">{task.error_code}: {task.error_message}</div>
            )}
          </div>

          {/* 发布物料：短标题 / 长标题 / 热门标签，一键复制，直接拿去发布 */}
          {(task.long_title || task.short_title || task.title || (task.hashtags && task.hashtags.length > 0)) && (
            <div className="card !p-4 space-y-3">
              <div className="text-sm font-semibold text-slate-100">发布物料 <span className="text-[11px] text-slate-500 font-normal">· 复制即用</span></div>
              {task.long_title && (
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] text-slate-500">长标题</span>
                    <button onClick={() => copyText('long', task.long_title!)}
                      className="text-[11px] text-brand-300 hover:text-brand-200">
                      {copiedKey === 'long' ? '✓ 已复制' : '复制'}
                    </button>
                  </div>
                  <p className="text-sm text-slate-200 leading-relaxed">{task.long_title}</p>
                </div>
              )}
              {(task.short_title || task.title) && (
                <div className="pt-2 border-t border-slate-800/60">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] text-slate-500">短标题</span>
                    <button onClick={() => copyText('short', (task.short_title || task.title)!)}
                      className="text-[11px] text-brand-300 hover:text-brand-200">
                      {copiedKey === 'short' ? '✓ 已复制' : '复制'}
                    </button>
                  </div>
                  <p className="text-sm text-slate-200">{task.short_title || task.title}</p>
                </div>
              )}
              {task.hashtags && task.hashtags.length > 0 && (
                <div className="pt-2 border-t border-slate-800/60">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[11px] text-slate-500">热门标签</span>
                    <button onClick={() => copyText('tags', task.hashtags!.map((t) => `#${t}`).join(' '))}
                      className="text-[11px] text-brand-300 hover:text-brand-200">
                      {copiedKey === 'tags' ? '✓ 已复制' : '复制全部'}
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {task.hashtags.map((t, i) => (
                      <span key={i} className="text-[12px] px-2 py-0.5 rounded-full bg-brand-500/10 text-brand-300 border border-brand-500/20">#{t}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* 待确认：通用继续按钮（文案步 B 用右栏聚焦窗口，这里不重复显示） */}
          {awaitingConfirm && !awaitingScript && (
            <div className="card !p-4 border border-brand-500/30">
              <div className="text-sm font-semibold text-brand-300 mb-1">已暂停 · 等待确认</div>
              {task.paused_at && <p className="text-xs text-slate-400 mb-2">停在：{STEP_LABEL[task.paused_at] || task.paused_at} 之后</p>}
              <p className="text-xs text-slate-400 mb-3">检查右侧产物，确认后继续（已完成步骤不重算）。</p>
              <button onClick={onResume} disabled={busy}
                className="w-full px-4 py-2.5 rounded-lg bg-brand-600 text-white text-sm font-medium hover:bg-brand-700 disabled:opacity-50">
                {busy ? '继续中…' : '确认并继续'}
              </button>
            </div>
          )}

          {/* 操作按钮 */}
          <div className="flex flex-col gap-2">
            {canUpload && (
              <>
                <button onClick={onRetryTTS} disabled={busy || uploading}
                  className="px-4 py-2.5 rounded-lg text-sm font-medium text-center bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50 transition-colors">
                  {busy ? '重试中…' : '🔊 重试 AI 配音'}
                </button>
                <label className={`px-4 py-2.5 rounded-lg text-sm font-medium text-center cursor-pointer transition-colors ${
                  uploading ? 'bg-slate-700 text-slate-400' : 'bg-slate-700/70 text-slate-200 hover:bg-slate-700'}`}>
                  {uploading ? '上传中…' : '或上传自己的配音音频'}
                  <input ref={fileRef} type="file" accept="audio/*" className="hidden" disabled={uploading} onChange={onUpload} />
                </label>
              </>
            )}
            {canRecompose && (
              <>
                <button onClick={onRecompose} disabled={busy || uploading}
                  className="px-4 py-2.5 rounded-lg text-sm font-medium text-center bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50 transition-colors">
                  {busy ? '重新生成中…' : '🎬 用最新的图重新生成视频'}
                </button>
                <label className={`px-4 py-2.5 rounded-lg text-sm font-medium text-center cursor-pointer transition-colors ${
                  uploading ? 'bg-slate-700 text-slate-400' : 'bg-slate-700/70 text-slate-200 hover:bg-slate-700'}`}>
                  {uploading ? '上传中…' : '换配音音频重新生成'}
                  <input ref={fileRef} type="file" accept="audio/*" className="hidden" disabled={uploading} onChange={onUpload} />
                </label>
              </>
            )}
            {hasDownload && <a href={api.downloadUrl(id)} className="btn-ghost text-center">下载剪映草稿/成片</a>}
            {canCancel && <button onClick={onCancel} disabled={busy} className="btn-ghost">取消任务</button>}
          </div>

          {/* 步骤时间线 */}
          <div className="card !p-4">
            <StepTimeline taskId={id} modules={modules} onChanged={load} />
          </div>
        </aside>

        {/* ── 右栏：分页工作区 ── */}
        <main>
          {awaitingScript && (
            <ScriptReview taskId={id} modules={modules} onChanged={load} />
          )}
          {showCompletedTip && (
            <div className="mb-4 px-4 py-2.5 rounded-lg text-sm bg-emerald-500/10 border border-emerald-500/30 text-emerald-300">
              {inJianying ? '✅ 剪映草稿已生成并导入草稿箱，打开剪映即可看到并编辑。'
                : '✅ 剪映草稿已生成。未设置「剪映草稿目录」，点左侧下载，或在配置页设置目录后重跑可自动导入。'}
              <div className="text-xs text-emerald-400/80 mt-1">改图或重新生成配图后，点左侧「用最新的图重新生成视频」即可更新草稿。</div>
            </div>
          )}

          {canRerun && (
            <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
              <div className="text-sm font-semibold text-amber-300 mb-2">改文案重新生成</div>
              <textarea rows={2} className="field"
                placeholder="可修改逐字稿后整体重跑；留空则沿用原文"
                value={rerunText} onChange={(e) => setRerunText(e.target.value)} />
              <button onClick={onRerun} disabled={busy}
                className="mt-2 px-4 py-2 rounded-lg bg-amber-600 text-white text-sm font-medium hover:bg-amber-700 disabled:opacity-50">
                {busy ? '提交中…' : '重新生成'}
              </button>
            </div>
          )}

          {/* Tab 切换 */}
          <div className="flex gap-1 mb-4 border-b border-slate-700/60">
            {([['preview', '产物预览'], ['gallery', '分镜画廊']] as [Tab, string][]).map(([k, label]) => (
              <button key={k} onClick={() => setTab(k)}
                className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                  tab === k ? 'border-brand-500 text-brand-300' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
                {label}
              </button>
            ))}
          </div>

          {tab === 'preview' && <ProductPreview taskId={id} modules={modules} onChanged={load} />}
          {tab === 'gallery' && <SceneGallery taskId={id} modules={modules} onChanged={load} />}
        </main>
      </div>
    </div>
  )
}
