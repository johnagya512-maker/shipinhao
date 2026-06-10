// 步骤时间线：竖向展示流水线各步状态/耗时，挂了的步骤可单步重跑。
// 对齐竞品左栏——一眼看清"现在到第几步、每步花了多久、哪步失败"。
import { useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ModuleResult } from '../api/types'

// 流水线步骤顺序与中文名（按执行先后）。
const STEP_FLOW: { key: string; name: string }[] = [
  { key: 'A', name: '文案清洗' },
  { key: 'H', name: '合规审查' },
  { key: 'B', name: '智能改写' },
  { key: 'F', name: '分句分段' },
  { key: 'D', name: '图书识别' },
  { key: 'CP', name: '人物反推' },
  { key: 'SB', name: '画面脚本' },
  { key: 'P', name: '提示词生成' },
  { key: 'E', name: '批量配图' },
  { key: 'T', name: '配音合成' },
  { key: 'G', name: '视频合成' },
]
// 可单步重跑的步骤（对应后端 _DOWNSTREAM）
const RERUNNABLE = new Set(['A', 'B', 'D', 'CP', 'SB', 'P', 'E'])

interface Props {
  taskId: string
  modules: ModuleResult[]
  onChanged: () => void
}

export default function StepTimeline({ taskId, modules, onChanged }: Props) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const byKey = new Map(modules.map((m) => [m.module, m]))
  // 只展示该任务实际涉及的步骤（产物存在的，或核心必经步骤）
  const flow = STEP_FLOW.filter((s) => byKey.has(s.key) || ['A', 'B', 'P', 'E', 'G'].includes(s.key))

  async function rerunStep(key: string) {
    if (!confirm(`从「${STEP_FLOW.find((s) => s.key === key)?.name}」重新执行？该步及其后续会重算。`)) return
    setBusy(key); setErr(null)
    try {
      await api.rerunStep(taskId, key)
      onChanged()
    } catch (e) {
      setErr((e as ApiError).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div>
      <div className="text-xs font-semibold text-slate-400 mb-2">流水线步骤</div>
      {err && <div className="mb-2 px-2 py-1.5 rounded text-xs bg-red-500/10 text-red-400">{err}</div>}
      <ol className="space-y-1">
        {flow.map((s) => {
          const m = byKey.get(s.key)
          const status = m?.status
          const dot = status === 'success' ? 'bg-emerald-500'
            : status === 'failed' ? 'bg-red-500'
            : status === 'pending' ? 'bg-amber-500 animate-pulse'
            : 'bg-slate-600'
          return (
            <li key={s.key} className="flex items-center gap-2.5 group">
              <span className={`w-2 h-2 rounded-full shrink-0 ${dot}`} />
              <span className={`text-sm flex-1 ${status ? 'text-slate-200' : 'text-slate-500'}`}>
                {s.name}
              </span>
              {m?.duration != null && (
                <span className="text-[11px] text-slate-500 font-mono">{m.duration}s</span>
              )}
              {RERUNNABLE.has(s.key) && status && (
                <button onClick={() => rerunStep(s.key)} disabled={busy !== null}
                  className="text-[11px] px-1.5 py-0.5 rounded text-slate-400 opacity-0 group-hover:opacity-100
                    hover:bg-slate-700 hover:text-slate-200 transition disabled:opacity-40"
                  title="从该步重跑">
                  {busy === s.key ? '…' : '重跑'}
                </button>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
