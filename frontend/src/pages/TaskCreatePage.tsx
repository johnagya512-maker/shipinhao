// 任务创建页：逐字稿输入 + 赛道/受众/模块/变现模式选择 + 成本预估 + 提交。
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { TaskCreate, EstimateOut } from '../api/types'

const TRACKS = [
  { key: 'character_story', name: '人物故事' },
  { key: 'health_book', name: '健康书单' },
]
const IMAGE_STYLES = ['', '古风电影', '工笔古画', '水墨写意', '复古胶片', '温暖暖色']
// 可选模块（A 清洗 / B 改写 / G 合成为必选，后端强制）。
const OPTIONAL_MODULES = [
  { key: 'D', name: 'D 图书识别' },
  { key: 'E', name: 'E 智能配图' },
  { key: 'F', name: 'F 配音分段' },
  { key: 'H', name: 'H 合规审查' },
]

export default function TaskCreatePage() {
  const nav = useNavigate()
  const [form, setForm] = useState<TaskCreate>({
    transcript: '', keyword: '', title: '', author: '',
    modules: ['A', 'B', 'E', 'F', 'G', 'H'],
    target_audience: '50+女性', track: 'character_story',
    monetization_mode: 'revenue_share', image_style: '',
    cost_limit: 1.0, time_limit: 900, enable_subtitles: true, enable_animations: true,
  })
  const [est, setEst] = useState<EstimateOut | null>(null)
  const [estimating, setEstimating] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (patch: Partial<TaskCreate>) => setForm((f) => ({ ...f, ...patch }))

  function toggleModule(key: string) {
    setForm((f) => {
      const has = f.modules.includes(key)
      const modules = has ? f.modules.filter((m) => m !== key) : [...f.modules, key]
      return { ...f, modules }
    })
  }

  async function doEstimate() {
    if (!form.transcript.trim()) { setError('请先填写逐字稿'); return }
    setEstimating(true); setError(null)
    try {
      setEst(await api.estimate(form))
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setEstimating(false)
    }
  }

  async function submit() {
    if (!form.transcript.trim()) { setError('请先填写逐字稿'); return }
    setSubmitting(true); setError(null)
    try {
      const task = await api.createTask(form)
      nav(`/tasks/${task.id}`)
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-semibold mb-4">新建任务</h1>

      {error && (
        <div className="mb-4 px-4 py-2 rounded-lg text-sm bg-red-50 text-red-700">{error}</div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
        <label className="block">
          <span className="text-sm text-slate-600">逐字稿 <span className="text-red-500">*</span></span>
          <textarea rows={8} className="mt-1 w-full border rounded-lg px-3 py-2 font-mono text-sm"
            placeholder="粘贴口播逐字稿原文…"
            value={form.transcript}
            onChange={(e) => { set({ transcript: e.target.value }); setEst(null) }} />
        </label>

        <div className="grid grid-cols-3 gap-4">
          <label className="block">
            <span className="text-sm text-slate-600">书名</span>
            <input className="mt-1 w-full border rounded-lg px-3 py-2"
              value={form.title ?? ''} onChange={(e) => set({ title: e.target.value })} />
          </label>
          <label className="block">
            <span className="text-sm text-slate-600">作者</span>
            <input className="mt-1 w-full border rounded-lg px-3 py-2"
              value={form.author ?? ''} onChange={(e) => set({ author: e.target.value })} />
          </label>
          <label className="block">
            <span className="text-sm text-slate-600">关键词</span>
            <input className="mt-1 w-full border rounded-lg px-3 py-2"
              value={form.keyword ?? ''} onChange={(e) => set({ keyword: e.target.value })} />
          </label>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <label className="block">
            <span className="text-sm text-slate-600">赛道</span>
            <select className="mt-1 w-full border rounded-lg px-3 py-2"
              value={form.track} onChange={(e) => set({ track: e.target.value })}>
              {TRACKS.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="text-sm text-slate-600">受众</span>
            <input className="mt-1 w-full border rounded-lg px-3 py-2"
              value={form.target_audience} onChange={(e) => set({ target_audience: e.target.value })} />
          </label>
          <label className="block">
            <span className="text-sm text-slate-600">变现模式</span>
            <select className="mt-1 w-full border rounded-lg px-3 py-2"
              value={form.monetization_mode} onChange={(e) => set({ monetization_mode: e.target.value })}>
              <option value="revenue_share">创作分成</option>
              <option value="book_sales">图书销售</option>
            </select>
          </label>
        </div>

        <label className="block">
          <span className="text-sm text-slate-600">画风（留空则用赛道默认）</span>
          <select className="mt-1 w-full border rounded-lg px-3 py-2"
            value={form.image_style ?? ''} onChange={(e) => set({ image_style: e.target.value })}>
            {IMAGE_STYLES.map((s) => <option key={s} value={s}>{s || '赛道默认'}</option>)}
          </select>
        </label>

        <div>
          <span className="text-sm text-slate-600">可选模块（A 清洗 / B 改写 / G 合成为必选）</span>
          <div className="mt-2 flex flex-wrap gap-2">
            {OPTIONAL_MODULES.map((m) => {
              const on = form.modules.includes(m.key)
              return (
                <button key={m.key} type="button" onClick={() => toggleModule(m.key)}
                  className={`px-3 py-1.5 rounded-lg text-sm border ${
                    on ? 'bg-brand-50 border-brand-500 text-brand-700' : 'border-slate-300 text-slate-500'
                  }`}>{m.name}</button>
              )
            })}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="text-sm text-slate-600">成本上限（元）</span>
            <input type="number" min={0} step={0.1} className="mt-1 w-full border rounded-lg px-3 py-2"
              value={form.cost_limit}
              onChange={(e) => { set({ cost_limit: Number(e.target.value) }); setEst(null) }} />
          </label>
          <div className="flex items-end gap-4 pb-1">
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" checked={form.enable_subtitles}
                onChange={(e) => set({ enable_subtitles: e.target.checked })} /> 字幕
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" checked={form.enable_animations}
                onChange={(e) => set({ enable_animations: e.target.checked })} /> 动效
            </label>
          </div>
        </div>
      </div>

      {est && (
        <div className={`mt-4 px-4 py-3 rounded-lg text-sm ${
          est.daily_cap_reached ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'
        }`}>
          预估成本：<b>{est.estimated_cost.toFixed(4)} 元</b>
          {est.daily_cap_reached && '（已达每日成本上限，无法提交）'}
        </div>
      )}

      <div className="flex gap-3 mt-5">
        <button onClick={doEstimate} disabled={estimating}
          className="px-5 py-2 rounded-lg border border-slate-300 text-sm font-medium disabled:opacity-50">
          {estimating ? '预估中…' : '成本预估'}
        </button>
        <button onClick={submit} disabled={submitting || est?.daily_cap_reached}
          className="px-5 py-2 rounded-lg bg-brand-600 text-white text-sm font-medium disabled:opacity-50">
          {submitting ? '提交中…' : '创建任务'}
        </button>
      </div>
    </div>
  )
}
