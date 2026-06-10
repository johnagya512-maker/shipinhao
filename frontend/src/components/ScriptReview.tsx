// 文案确认关卡：改写后任务停在 B，这里把文案全文醒目展示，
// 让用户确认/改字/让 AI 重写，确认后才继续配图配音（开始花钱）。
// 复用已有接口：resume（继续）、updateModuleOutput（改字不计费）、rerunStep（AI 重写）。
import { useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ModuleResult } from '../api/types'

interface Props {
  taskId: string
  modules: ModuleResult[]
  onChanged: () => void
}

// 取要确认的文案：优先 B 改写稿，无则退回 A 清洗稿。
function pickScript(modules: ModuleResult[]): { text: string; module: string } {
  const b = modules.find((m) => m.module === 'B' && m.status === 'success')
  const bo = b?.output as Record<string, unknown> | undefined
  if (bo && typeof bo.script === 'string' && bo.script.trim()) {
    return { text: bo.script, module: 'B' }
  }
  const a = modules.find((m) => m.module === 'A' && m.status === 'success')
  const ao = a?.output as Record<string, unknown> | undefined
  return { text: String(ao?.cleaned_text ?? ''), module: 'B' }
}

export default function ScriptReview({ taskId, modules, onChanged }: Props) {
  const { text, module } = pickScript(modules)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState<'' | 'resume' | 'save' | 'rewrite'>('')
  const [err, setErr] = useState('')

  async function onResume() {
    setBusy('resume'); setErr('')
    try { await api.resume(taskId); onChanged() }
    catch (e) { setErr((e as ApiError).message) }
    finally { setBusy('') }
  }

  async function onSave() {
    setBusy('save'); setErr('')
    try {
      await api.updateModuleOutput(taskId, module, { script: draft })
      setEditing(false); onChanged()
    } catch (e) { setErr((e as ApiError).message) }
    finally { setBusy('') }
  }

  async function onRewrite() {
    setBusy('rewrite'); setErr('')
    try { await api.rerunStep(taskId, 'B'); onChanged() }
    catch (e) { setErr((e as ApiError).message) }
    finally { setBusy('') }
  }

  const working = busy !== ''

  return (
    <div className="mb-4 rounded-xl border border-brand-500/40 bg-brand-600/5 p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-base">✋</span>
        <span className="text-sm font-semibold text-brand-300">文案已生成，请先确认</span>
      </div>

      {!editing ? (
        <div className="rounded-lg bg-slate-950/50 border border-slate-700/60 p-3 max-h-[42vh] overflow-auto">
          <p className="text-sm text-slate-200 whitespace-pre-wrap leading-relaxed">{text || '（无文案内容）'}</p>
        </div>
      ) : (
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={14}
          className="w-full text-sm bg-slate-950/60 border border-slate-700 rounded-lg p-3 text-slate-200 leading-relaxed outline-none focus:border-brand-500/60" />
      )}

      {err && <p className="mt-2 text-[12px] text-red-400">{err}</p>}

      {!editing ? (
        <>
          <p className="mt-2 text-[12px] text-amber-400/80">确认后将开始配图 + 配音 + 合成，会产生费用。建议先看清文案。</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button onClick={onResume} disabled={working}
              className="px-4 py-2 rounded-lg bg-brand-600 text-white text-sm font-medium hover:bg-brand-700 disabled:opacity-50">
              {busy === 'resume' ? '继续中…' : '✅ 文案没问题，继续生成'}
            </button>
            <button onClick={() => { setDraft(text); setEditing(true); setErr('') }} disabled={working}
              className="px-4 py-2 rounded-lg bg-slate-700/70 text-slate-200 text-sm hover:bg-slate-700 disabled:opacity-50">
              ✎ 改文案
            </button>
            <button onClick={onRewrite} disabled={working}
              title="让 AI 重新改写（会重新调用大模型）"
              className="px-4 py-2 rounded-lg bg-slate-700/70 text-slate-200 text-sm hover:bg-slate-700 disabled:opacity-50">
              {busy === 'rewrite' ? '重写中…' : '🔄 让 AI 重写'}
            </button>
          </div>
        </>
      ) : (
        <div className="mt-2 flex items-center gap-2">
          <p className="text-[11px] text-slate-500 flex-1">直接改字 · 保存即生效 · 不调用 AI、不计费。</p>
          <button onClick={onSave} disabled={working}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50">
            {busy === 'save' ? '保存中…' : '保存'}
          </button>
          <button onClick={() => setEditing(false)} disabled={working}
            className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200">取消</button>
        </div>
      )}
    </div>
  )
}
