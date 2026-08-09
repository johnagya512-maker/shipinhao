// 更换配音对话框：选择音色 + 调整语速后重新配音并出片（不重跑文案和配图）。
import { useState, useEffect } from 'react'
import { api, ApiError } from '../api/client'
import type { VoiceItem, VoiceCategory } from '../api/types'
import VoicePicker from './VoicePicker'
import { useVoicePreview } from '../hooks/useVoicePreview'

interface Props {
  taskId: string
  currentVoice?: string     // 当前任务的音色
  currentSpeed: number      // 当前任务的语速
  onClose: () => void
  onSuccess: () => void     // 成功后刷新任务详情
}

export default function VoiceReconfigDialog({ taskId, currentVoice, currentSpeed, onClose, onSuccess }: Props) {
  const [voice, setVoice] = useState(currentVoice || '')
  const [speed, setSpeed] = useState(currentSpeed)
  const [voices, setVoices] = useState<VoiceItem[]>([])
  const [voiceCats, setVoiceCats] = useState<VoiceCategory[]>([])
  const [favorites, setFavorites] = useState<string[]>([])
  const [showVoicePicker, setShowVoicePicker] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { previewingId, error: previewError, preview } = useVoicePreview()

  // 载入音色库 + 收藏列表
  useEffect(() => {
    api.getConfig().then((c) => setFavorites(c.tts_favorites ?? [])).catch(() => {})
    api.getVoices().then((r) => {
      setVoices(r.voices)
      setVoiceCats(r.categories)
    }).catch(() => {})
  }, [])

  // 收藏/取消收藏
  const toggleFavorite = async (voiceId: string) => {
    const isFav = favorites.includes(voiceId)
    try {
      const r = await api.toggleFavorite(voiceId, isFav ? 'remove' : 'add')
      setFavorites(r.favorites)
    } catch { /* 失败静默，不影响使用 */ }
  }

  const addCloneVoice = async (voiceId: string, name: string) => {
    const r = await api.addCloneVoice(voiceId, name)
    setVoices((prev) => {
      const ids = new Set(prev.map((v) => v.id))
      return [...prev, ...r.clone_voices.filter((v) => !ids.has(v.id))]
    })
  }

  const removeCloneVoice = async (voiceId: string) => {
    await api.removeCloneVoice(voiceId)
    setVoices((prev) => prev.filter((v) => v.id !== voiceId))
  }


  // 提交：调用后端接口更换配音并重新生成
  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await api.reconfigVoice(taskId, voice || undefined, speed)
      onSuccess()
      onClose()
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setSubmitting(false)
    }
  }

  const selectedVoice = voices.find((v) => v.id === voice)

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
        <div className="w-full max-w-md bg-slate-900 border border-slate-700 rounded-2xl flex flex-col overflow-hidden"
          onClick={(e) => e.stopPropagation()}>
          {/* 标题 */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
            <h2 className="text-lg font-semibold text-slate-100">更换配音重新生成</h2>
            <button onClick={onClose} className="text-slate-500 hover:text-slate-200">✕</button>
          </div>

          {/* 内容 */}
          <div className="px-5 py-4 space-y-5">
            {error && (
              <div className="px-4 py-2.5 rounded-lg text-sm bg-red-50/10 border border-red-500/30 text-red-300">
                {error}
              </div>
            )}

            {/* 音色选择 */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                配音音色
                <span className="ml-2 text-xs text-slate-500">（留空使用配置页默认音色）</span>
              </label>
              <div className="flex items-center gap-2">
                <div className="flex-1 px-3 py-2 rounded-lg bg-slate-800/60 border border-slate-700/60">
                  {selectedVoice ? (
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-slate-200">{selectedVoice.name}</span>
                      <span className="text-[10px] text-slate-500 px-1.5 py-0.5 rounded bg-slate-700/50">
                        {selectedVoice.tag}
                      </span>
                    </div>
                  ) : (
                    <span className="text-sm text-slate-500">使用默认音色</span>
                  )}
                </div>
                {voice && (
                  <button onClick={() => preview(voice, speed)} title="试听"
                    className="shrink-0 px-3 py-2 rounded-lg text-sm bg-slate-700/70 text-slate-200 hover:bg-slate-700">
                    {previewingId === voice ? '■' : '▶'}
                  </button>
                )}
                <button onClick={() => setShowVoicePicker(true)}
                  className="shrink-0 px-4 py-2 rounded-lg text-sm bg-brand-600 text-white hover:bg-brand-700">
                  选择
                </button>
              </div>
              {previewError && <p className="mt-1 text-xs text-red-400">{previewError}</p>}
            </div>

            {/* 语速调节 */}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                语速
                <span className="ml-2 text-sm text-slate-400">{speed.toFixed(1)}x</span>
              </label>
              <div className="flex items-center gap-3">
                <span className="text-xs text-slate-500 shrink-0">0.5x</span>
                <input type="range" min="0.5" max="2.0" step="0.1"
                  value={speed}
                  onChange={(e) => setSpeed(parseFloat(e.target.value))}
                  className="flex-1 h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer
                    [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
                    [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-brand-500
                    [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:rounded-full
                    [&::-moz-range-thumb]:bg-brand-500 [&::-moz-range-thumb]:border-0" />
                <span className="text-xs text-slate-500 shrink-0">2.0x</span>
              </div>
              <p className="mt-1.5 text-xs text-slate-500">
                1.0x = 正常速度 · 低于 1.0 更舒缓 · 高于 1.0 更快节奏
              </p>
            </div>

            {/* 说明 */}
            <div className="px-3 py-2.5 rounded-lg bg-blue-500/10 border border-blue-500/30">
              <p className="text-xs text-blue-300 leading-relaxed">
                只重新配音和生成草稿，不会重跑文案改写和配图生成，节省时间和成本。
              </p>
            </div>
          </div>

          {/* 底部按钮 */}
          <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-slate-800">
            <button onClick={onClose} disabled={submitting}
              className="px-4 py-2 rounded-lg text-sm text-slate-300 hover:text-slate-100 disabled:opacity-50">
              取消
            </button>
            <button onClick={handleSubmit} disabled={submitting}
              className="px-5 py-2 rounded-lg text-sm font-medium bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50">
              {submitting ? '生成中…' : '确认并重新生成'}
            </button>
          </div>
        </div>
      </div>

      {/* 音色选择器弹窗 */}
      {showVoicePicker && (
        <VoicePicker
          voices={voices}
          categories={voiceCats}
          value={voice}
          favorites={favorites}
          onSelect={setVoice}
          onToggleFav={toggleFavorite}
          onAddCloneVoice={addCloneVoice}
          onRemoveCloneVoice={removeCloneVoice}
          onClose={() => setShowVoicePicker(false)}
        />
      )}
    </>
  )
}
