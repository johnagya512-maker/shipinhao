// 音色试听 hook：调后端 preview-tts 合成短句并播放。
// 同一时刻只播一个；记录正在试听的 voiceId 与错误。火山未授权(E6210)翻成人话。
import { useRef, useState } from 'react'
import { api, ApiError } from '../api/client'

export function useVoicePreview() {
  const [previewingId, setPreviewingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  function stop() {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    setPreviewingId(null)
  }

  async function preview(voiceId: string, speed: number = 1.0) {
    setError(null)
    // 再次点同一个 = 停止
    if (previewingId === voiceId) { stop(); return }
    stop()
    setPreviewingId(voiceId)
    try {
      const blob = await api.previewTts({ voice: voiceId, speed })
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audioRef.current = audio
      audio.onended = () => { URL.revokeObjectURL(url); setPreviewingId(null); audioRef.current = null }
      await audio.play()
    } catch (e) {
      const msg = (e as ApiError).message || ''
      setError(/E6210|未授权|不存在/.test(msg)
        ? '该音色未授权，换一个试试或检查火山账号授权'
        : (msg || '试听失败'))
      setPreviewingId(null)
    }
  }

  return { previewingId, error, preview, stop }
}
