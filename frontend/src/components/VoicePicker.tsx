// 音色选择器弹窗（对齐竞品 Storybound「更多音色」）。
// 顶部搜索 + 左侧分类（我的收藏 + 各分类）+ 右侧音色行（名字/标签/voice_id/⭐收藏/▶试听/选用）。
import { useMemo, useState } from 'react'
import type { VoiceItem, VoiceCategory } from '../api/types'
import { useVoicePreview } from '../hooks/useVoicePreview'

interface Props {
  voices: VoiceItem[]
  categories: VoiceCategory[]
  value: string                       // 当前选中的 voice_id
  favorites: string[]
  onSelect: (voiceId: string) => void
  onToggleFav: (voiceId: string) => void
  onClose: () => void
  onAddCloneVoice?: (voiceId: string, name: string) => Promise<void>
  onRemoveCloneVoice?: (voiceId: string) => Promise<void>
}

const FAV = '__fav__'

export default function VoicePicker({ voices, categories, value, favorites, onSelect, onToggleFav, onClose, onAddCloneVoice, onRemoveCloneVoice }: Props) {
  const [cat, setCat] = useState<string>(favorites.length ? FAV : (categories[0]?.key ?? ''))
  const [q, setQ] = useState('')
  const { previewingId, error, preview } = useVoicePreview()
  const [addId, setAddId] = useState('')
  const [addName, setAddName] = useState('')
  const [adding, setAdding] = useState(false)
  const [addErr, setAddErr] = useState('')

  const favSet = useMemo(() => new Set(favorites), [favorites])

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase()
    return voices.filter((v) => {
      if (cat === FAV ? !favSet.has(v.id) : v.category !== cat) return false
      if (kw && !(`${v.name}${v.tag}${v.id}`.toLowerCase().includes(kw))) return false
      return true
    })
  }, [voices, cat, q, favSet])

  const countOf = (key: string) =>
    key === FAV ? favorites.length : voices.filter((v) => v.category === key).length

  const handleAdd = async () => {
    if (!addId.trim() || !addName.trim()) { setAddErr('请填写音色 ID 和名称'); return }
    setAdding(true); setAddErr('')
    try {
      await onAddCloneVoice?.(addId.trim(), addName.trim())
      setAddId(''); setAddName('')
    } catch (e: unknown) {
      setAddErr((e as Error).message || '添加失败')
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-2xl h-[560px] bg-slate-900 border border-slate-700 rounded-2xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}>
        {/* 顶部：标题 + 搜索 + 关闭 */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-800">
          <span className="text-sm font-medium text-slate-200 shrink-0">选择音色</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜名字 / 标签 / ID"
            className="flex-1 bg-slate-950/60 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200 outline-none focus:border-brand-500/60" />
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200 px-1 shrink-0">✕</button>
        </div>

        <div className="flex flex-1 min-h-0">
          {/* 左侧分类 */}
          <div className="w-32 shrink-0 border-r border-slate-800 overflow-y-auto py-2">
            <CatBtn label={`我的收藏 ${favorites.length}`} active={cat === FAV} onClick={() => setCat(FAV)} />
            {categories.map((c) => (
              <CatBtn key={c.key} label={`${c.name} ${countOf(c.key)}`} active={cat === c.key} onClick={() => setCat(c.key)} />
            ))}
          </div>

          {/* 右侧音色列表 */}
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {error && <div className="text-[11px] text-red-400 px-2 py-1">{error}</div>}

            {/* 声音复刻分类：顶部添加表单 */}
            {cat === 'clone' && onAddCloneVoice && (
              <div className="mb-2 p-2.5 rounded-lg border border-slate-700/60 bg-slate-800/40 space-y-2">
                <div className="text-[11px] text-slate-400 font-medium">添加复刻音色</div>
                <div className="flex gap-2">
                  <input value={addId} onChange={(e) => setAddId(e.target.value)}
                    placeholder="音色 ID（如 S_xXK5czr62）"
                    className="flex-1 min-w-0 bg-slate-950/60 border border-slate-700 rounded-md px-2 py-1 text-xs text-slate-200 outline-none focus:border-brand-500/60 font-mono" />
                  <input value={addName} onChange={(e) => setAddName(e.target.value)}
                    placeholder="显示名称"
                    className="w-24 bg-slate-950/60 border border-slate-700 rounded-md px-2 py-1 text-xs text-slate-200 outline-none focus:border-brand-500/60" />
                  <button onClick={handleAdd} disabled={adding}
                    className="shrink-0 px-3 py-1 rounded-md text-xs font-medium bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50">
                    {adding ? '…' : '添加'}
                  </button>
                </div>
                {addErr && <div className="text-[11px] text-red-400">{addErr}</div>}
              </div>
            )}

            {filtered.length === 0 && (
              <div className="text-sm text-slate-500 text-center mt-8">
                {cat === FAV ? '还没有收藏音色，点 ☆ 收藏常用的' : '没有匹配的音色'}
              </div>
            )}
            {filtered.map((v) => {
              const on = v.id === value
              const fav = favSet.has(v.id)
              const playing = previewingId === v.id
              const unavail = v.available === false
              const isClone = v.category === 'clone'
              return (
                <div key={v.id} className={`flex items-center gap-2 px-2.5 py-2 rounded-lg border ${
                  on ? 'bg-brand-600/15 border-brand-500' : 'bg-slate-800/40 border-slate-700/60'} ${
                  unavail ? 'opacity-50' : ''}`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-[13px] font-medium ${on ? 'text-brand-300' : 'text-slate-200'}`}>{v.name}</span>
                      <span className="text-[10px] text-slate-500 px-1.5 py-0.5 rounded bg-slate-700/50">{v.tag}</span>
                      {unavail && <span className="text-[10px] text-amber-500/80" title="当前火山账号未授权">未授权</span>}
                    </div>
                    <div className="text-[10px] text-slate-600 font-mono truncate" title={v.id}>{v.id}</div>
                  </div>
                  <button onClick={() => onToggleFav(v.id)} title={fav ? '取消收藏' : '收藏'}
                    className={`shrink-0 px-1 ${fav ? 'text-amber-400' : 'text-slate-600 hover:text-amber-400'}`}>
                    {fav ? '★' : '☆'}
                  </button>
                  <button onClick={() => preview(v.id)} title={unavail ? '未授权，仍可试听验证' : '试听'}
                    className="shrink-0 px-2 py-1 rounded-md text-xs bg-slate-700/70 text-slate-200 hover:bg-slate-700">
                    {playing ? '■' : '▶'}
                  </button>
                  <button onClick={() => { onSelect(v.id); onClose() }}
                    className={`shrink-0 px-2.5 py-1 rounded-md text-xs font-medium ${
                      on ? 'bg-emerald-600/30 text-emerald-300' : 'bg-brand-600 text-white hover:bg-brand-700'}`}>
                    {on ? '✓ 已选' : '选用'}
                  </button>
                  {isClone && onRemoveCloneVoice && (
                    <button onClick={() => onRemoveCloneVoice(v.id)} title="删除此复刻音色"
                      className="shrink-0 px-1 text-slate-600 hover:text-red-400 text-xs">
                      🗑
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        <div className="px-4 py-2 border-t border-slate-800 text-[10px] text-slate-600">
          试听走火山 TTS · 短句成本极低 · 可用性取决于火山账号授权，未授权的换一个即可
        </div>
      </div>
    </div>
  )
}

function CatBtn({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className={`w-full text-left px-3 py-2 text-[13px] transition-colors ${
        active ? 'bg-brand-600/15 text-brand-300 border-r-2 border-brand-500' : 'text-slate-400 hover:text-slate-200'}`}>
      {label}
    </button>
  )
}
