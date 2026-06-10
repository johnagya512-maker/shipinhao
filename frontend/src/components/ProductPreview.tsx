// 产物预览：把文本类模块产物用人话卡片展示（替代裸 JSON）。
// 对齐竞品「编辑/重跑分开」：可编辑模块（A/B/F/D/CP）卡片右上有「编辑」按钮，
// 直接改字段、保存即生效，不调 AI、不扣费、不触发下游。要重算下游用左栏「从此步重跑」。
// 含竞品式「过程诊断」——展示画面脚本质检是否打回重写过。
import { useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ModuleResult } from '../api/types'

const MODULE_NAME: Record<string, string> = {
  A: '文案清洗', B: '智能改写', D: '图书识别', E: '批量配图', P: '提示词生成',
  F: '分句分段', G: '视频合成', H: '合规审查', T: '配音合成', SB: '画面脚本', CP: '人物反推',
}

// 可编辑模块 -> 编辑器类型
type EditKind = 'text' | 'segments' | 'book'
const EDITABLE: Record<string, EditKind> = {
  A: 'text', B: 'text', CP: 'text', F: 'segments', D: 'book',
}

interface Props {
  taskId: string
  modules: ModuleResult[]
  onChanged: () => void
}

// 从 F 产物里取分句文本数组
function segTexts(o: Record<string, unknown>): string[] {
  const segs = (o.segments ?? o.sentences) as unknown[] | undefined
  if (!Array.isArray(segs)) return []
  return segs.map((s) => typeof s === 'string' ? s
    : String((s as Record<string, unknown>)?.text ?? (s as Record<string, unknown>)?.cap ?? ''))
}

// 只读展示
function renderOutput(m: ModuleResult): React.ReactNode {
  const o = m.output as Record<string, unknown> | null
  if (!o) return <span className="text-slate-600 text-xs">无产物</span>

  const text = (o.rewritten ?? o.cleaned ?? o.script ?? o.text ?? o.cleaned_text) as string | undefined
  if (typeof text === 'string') {
    return <p className="text-sm text-slate-300 whitespace-pre-wrap leading-relaxed">{text}</p>
  }
  if (o.title || o.author) {
    return (
      <div className="text-sm text-slate-300 space-y-0.5">
        {o.title ? <div>书名：{String(o.title)}</div> : null}
        {o.author ? <div>作者：{String(o.author)}</div> : null}
        {o.category ? <div>分类：{String(o.category)}</div> : null}
      </div>
    )
  }
  const segs = (o.segments ?? o.sentences) as unknown[] | undefined
  if (Array.isArray(segs)) {
    return (
      <ol className="text-sm text-slate-300 space-y-1 list-decimal list-inside">
        {segs.slice(0, 20).map((seg, i) => {
          const t = typeof seg === 'string' ? seg
            : (seg as Record<string, unknown>)?.text ?? (seg as Record<string, unknown>)?.cap ?? JSON.stringify(seg)
          return <li key={i} className="text-slate-400">{String(t)}</li>
        })}
        {segs.length > 20 && <li className="text-slate-600 list-none">…共 {segs.length} 条</li>}
      </ol>
    )
  }
  if (typeof o.profile === 'string') {
    return <p className="text-sm text-slate-300 leading-relaxed">{o.profile}</p>
  }
  return (
    <pre className="text-xs bg-slate-950/60 rounded-lg p-2.5 overflow-auto max-h-48 whitespace-pre-wrap break-all text-slate-500">
      {JSON.stringify(o, null, 2)}
    </pre>
  )
}

// 画面脚本质检诊断
function Diagnostic({ modules }: { modules: ModuleResult[] }) {
  const sb = modules.find((m) => m.module === 'SB')
  const diag = sb?.output?.diagnostic as { attempts?: { reason: string }[]; fell_back?: boolean } | undefined
  if (!diag || !diag.attempts?.length) return null
  return (
    <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
      <div className="text-sm font-semibold text-amber-300 mb-2">过程诊断 · 画面脚本质检</div>
      {diag.attempts.map((a, i) => (
        <div key={i} className="text-xs text-slate-400 mb-1.5 leading-relaxed">
          <span className="text-amber-400">⚠ 检出问题并自动打回重写：</span>{a.reason}
        </div>
      ))}
      <div className="text-xs text-slate-500 mt-1">
        {diag.fell_back ? '重写后仍有瑕疵，已采用最佳结果。' : '✓ 重写后已修复，画面更有变化。'}
      </div>
    </div>
  )
}

// 单张产物卡片（含编辑态）
function Card({ taskId, m, onChanged }: { taskId: string; m: ModuleResult; onChanged: () => void }) {
  const kind = m.status === 'success' ? EDITABLE[m.module] : undefined
  const o = (m.output ?? {}) as Record<string, unknown>
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // 各编辑器的草稿态
  const [textVal, setTextVal] = useState('')
  const [segVals, setSegVals] = useState<string[]>([])
  const [book, setBook] = useState({ title: '', author: '', category: '' })

  function startEdit() {
    setErr(null)
    if (kind === 'text') {
      setTextVal(String(o.script ?? o.cleaned_text ?? o.profile ?? ''))
    } else if (kind === 'segments') {
      setSegVals(segTexts(o))
    } else if (kind === 'book') {
      setBook({ title: String(o.title ?? ''), author: String(o.author ?? ''), category: String(o.category ?? '') })
    }
    setEditing(true)
  }

  async function save() {
    setSaving(true); setErr(null)
    try {
      let fields: Record<string, unknown> = {}
      if (kind === 'text') {
        // A→cleaned_text, B→script, CP→profile
        const key = m.module === 'A' ? 'cleaned_text' : m.module === 'CP' ? 'profile' : 'script'
        fields = { [key]: textVal }
      } else if (kind === 'segments') {
        fields = { segments: segVals.map((t) => t.trim()).filter(Boolean) }
      } else if (kind === 'book') {
        fields = { title: book.title, author: book.author, category: book.category }
      }
      await api.updateModuleOutput(taskId, m.module, fields)
      setEditing(false)
      onChanged()
    } catch (e) {
      setErr((e as ApiError).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-xl border border-slate-700/60 bg-slate-900/40 p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-slate-200">{MODULE_NAME[m.module] || m.module}</span>
        <div className="flex items-center gap-2">
          {kind && !editing && (
            <button onClick={startEdit}
              title="直接改字 · 不重新生成 · 不计费"
              className="text-[11px] px-2 py-0.5 rounded-md bg-slate-700/70 text-slate-200 hover:bg-slate-700 transition-colors">
              ✎ 编辑
            </button>
          )}
          <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${
            m.status === 'success' ? 'bg-emerald-500/15 text-emerald-400'
            : m.status === 'failed' ? 'bg-red-500/15 text-red-400' : 'bg-slate-700 text-slate-400'
          }`}>{m.status}</span>
        </div>
      </div>

      {!editing && renderOutput(m)}

      {editing && (
        <div className="space-y-2">
          <div className="text-[11px] text-slate-500">直接改字 · 保存即生效 · 不调用 AI、不计费。要重算分镜/配图请用左侧「从此步重跑」。</div>
          {kind === 'text' && (
            <textarea value={textVal} onChange={(e) => setTextVal(e.target.value)} rows={10}
              className="w-full text-sm bg-slate-950/60 border border-slate-700 rounded-lg p-2.5 text-slate-200 leading-relaxed outline-none focus:border-brand-500/60" />
          )}
          {kind === 'book' && (
            <div className="space-y-2">
              {([['title', '书名'], ['author', '作者'], ['category', '分类']] as const).map(([k, label]) => (
                <label key={k} className="flex items-center gap-2 text-sm">
                  <span className="text-slate-400 w-10 shrink-0">{label}</span>
                  <input value={book[k]} onChange={(e) => setBook((b) => ({ ...b, [k]: e.target.value }))}
                    className="flex-1 bg-slate-950/60 border border-slate-700 rounded-lg px-2.5 py-1.5 text-slate-200 outline-none focus:border-brand-500/60" />
                </label>
              ))}
            </div>
          )}
          {kind === 'segments' && (
            <div className="space-y-1.5">
              {segVals.map((t, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <span className="text-[11px] text-slate-600 font-mono w-6 text-right shrink-0">{i + 1}</span>
                  <input value={t} onChange={(e) => setSegVals((arr) => arr.map((x, idx) => idx === i ? e.target.value : x))}
                    className="flex-1 text-sm bg-slate-950/60 border border-slate-700 rounded-lg px-2.5 py-1.5 text-slate-200 outline-none focus:border-brand-500/60" />
                  <button onClick={() => setSegVals((arr) => arr.filter((_, idx) => idx !== i))}
                    title="删除这句" className="text-slate-500 hover:text-red-400 px-1 shrink-0">✕</button>
                </div>
              ))}
              <button onClick={() => setSegVals((arr) => [...arr, ''])}
                className="text-[11px] text-brand-400 hover:text-brand-300">+ 加一句</button>
            </div>
          )}
          {err && <div className="text-[11px] text-red-400">{err}</div>}
          <div className="flex items-center gap-2 pt-1">
            <button onClick={save} disabled={saving}
              className="px-3 py-1.5 rounded-lg text-sm font-medium bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50">
              {saving ? '保存中…' : '保存'}
            </button>
            <button onClick={() => setEditing(false)} disabled={saving}
              className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200">取消</button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function ProductPreview({ taskId, modules, onChanged }: Props) {
  // 文本类产物（排除 P/E/G——P/E 在画廊看，G 是成片）
  const textModules = modules.filter((m) => !['P', 'E', 'G'].includes(m.module))
  return (
    <div className="space-y-3">
      <Diagnostic modules={modules} />
      {textModules.length === 0 && <div className="text-sm text-slate-400">尚无产物，任务处理中…</div>}
      {textModules.map((m) => (
        <Card key={m.module} taskId={taskId} m={m} onChanged={onChanged} />
      ))}
    </div>
  )
}
