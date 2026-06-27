// 分镜画廊：对齐竞品的卡片网格。每张卡 = 一个分镜，展示缩略图 + 可编辑的
// 口播原文(cap)/绘图提示词(desc_prompt) + 「参考」角标(has_character) + 重试按钮。
// 改完点「保存全部修改」写回后端；单张可「改提示词重试」只重生成那一张。
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { Scene, GeneratedImage, ModuleResult, ImagePreset } from '../api/types'

interface Props {
  taskId: string
  modules: ModuleResult[]
  onChanged: () => void   // 保存/重试后通知父组件刷新
}

// 分镜列表优先取 P 产物（已与实际配图数一一对应）；P 没有 scenes 字段（老任务）才回退 SB。
// 不能直接拿 SB.scenes——SB 分镜数可能多于实际配图数，会导致多出的卡片显示"未生成"。
function extractScenes(modules: ModuleResult[]): Scene[] {
  const p = modules.find((m) => m.module === 'P')
  const sb = modules.find((m) => m.module === 'SB')
  const pScenes = p?.output?.scenes as Scene[] | undefined
  const raw = (pScenes !== undefined ? pScenes
    : (sb?.output?.scenes as Scene[] | undefined)) ?? []
  return raw.map((s, i) => ({
    id: s.id ?? i + 1,
    cap: s.cap ?? '',
    desc_prompt: s.desc_prompt ?? '',
    has_character: s.has_character ?? true,
  }))
}

function extractImages(modules: ModuleResult[]): GeneratedImage[] {
  const e = modules.find((m) => m.module === 'E')
  return (e?.output?.images as GeneratedImage[] | undefined) ?? []
}

export default function SceneGallery({ taskId, modules, onChanged }: Props) {
  const serverScenes = useMemo(() => extractScenes(modules), [modules])
  const images = useMemo(() => extractImages(modules), [modules])
  const [scenes, setScenes] = useState<Scene[]>(serverScenes)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  // 支持多张图并行重试：retrying 记录正在跑的分镜下标集合；queued 是等待名额的下标。
  const MAX_PARALLEL = 3
  const [retrying, setRetrying] = useState<Set<number>>(new Set())
  const [queued, setQueued] = useState<Set<number>>(new Set())
  const [err, setErr] = useState<string | null>(null)
  // 缓存击穿：重试后图片 URL 不变，加版本号强制刷新 <img>
  const [bust, setBust] = useState<Record<number, number>>({})
  // 单图重试后仍失败的原因（按分镜下标），覆盖在卡片上
  const [failReason, setFailReason] = useState<Record<number, string>>({})
  // 多选「一起重新组图」：selected 记录勾选的分镜下标；batchRunning 标记组图请求进行中
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [batchRunning, setBatchRunning] = useState(false)
  // 防双击锁：React 状态更新异步，用同步 ref 防止快速双击时两次都通过 batchRunning 检查
  const batchLockRef = useRef(false)
  // 本次重组的出图方式：''=跟随建任务时选的模式；'grid'=九宫格省成本；'per_image'=逐张画质优先。
  // 让用户在画廊当场切换，不必回建任务页改。
  const [batchMode, setBatchMode] = useState<'' | 'grid' | 'per_image'>('')
  // 本次重生的出图模型：''=跟随当前生效配置；否则按所选预设名覆盖（默认 GPT 失败后可换豆包重生）。
  // 同时作用于单张重生和批量「一起重新组图」，只影响这一次、不改全局配置。
  const [presets, setPresets] = useState<ImagePreset[]>([])
  const [curModel, setCurModel] = useState<string>('')
  const [modelName, setModelName] = useState<string>('')
  // 当前选中的预设对象（null=跟随配置，重生时不传覆盖）。用 ref 让并发/排队闭包拿到最新值。
  const selPreset = useMemo(
    () => presets.find((p) => p.name === modelName) ?? null, [presets, modelName])
  const presetRef = useRef<ImagePreset | null>(null)
  useEffect(() => { presetRef.current = selPreset }, [selPreset])

  // 拉一次配置，取「配图预设」列表 + 当前生效模型，供顶部「出图模型」下拉用。
  useEffect(() => {
    api.getConfig()
      .then((c) => { setPresets(c.image_presets ?? []); setCurModel(c.image_model ?? '') })
      .catch(() => { /* 取不到就只剩默认项，行为=现状 */ })
  }, [])
  // 点击缩略图放大查看：lightbox 存当前放大的图片 URL（null = 关闭）
  const [lightbox, setLightbox] = useState<string | null>(null)
  // 并发重试时读取最新 scenes（避免闭包拿到旧值）
  const scenesRef = useRef(scenes)
  useEffect(() => { scenesRef.current = scenes }, [scenes])

  // 后端分镜变化(且本地未编辑)时同步下来
  useEffect(() => { if (!dirty) setScenes(serverScenes) }, [serverScenes, dirty])

  // 放大查看时按 Esc 关闭灯箱
  useEffect(() => {
    if (!lightbox) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setLightbox(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [lightbox])

  // 把绘图供应商的英文/原始报错翻成人话
  function humanReason(raw?: string | null): string {
    if (!raw) return '生成失败，可再试一次或调整提示词'
    const s = String(raw)
    if (/sensitive|审核|拒绝|reject/i.test(s))
      return '提示词被内容审核拦截，请改写画面描述（避开敏感/暴力/政治字眼）后重试'
    if (/限流|429|rate/i.test(s)) return '触发限流，稍等片刻再重试'
    if (/超时|timeout/i.test(s)) return '请求超时，可直接再试一次'
    if (/key|401|无效/i.test(s)) return '绘图 API Key 无效，请到配置页检查'
    return s.slice(0, 80)
  }

  function editScene(i: number, patch: Partial<Scene>) {
    setScenes((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)))
    setDirty(true)
  }

  async function saveAll() {
    setSaving(true); setErr(null)
    try {
      await api.saveScenes(taskId, scenes)
      setDirty(false)
      onChanged()
    } catch (e) {
      setErr((e as ApiError).message)
    } finally {
      setSaving(false)
    }
  }

  // 图片与分镜严格一一对应：image[i] = scene[i]（不再有独立封面占下标0）。
  // 实际执行一张重试（不含排队控制）。每张把自己的提示词直接传给后端，后端在 per-task
  // 锁内回写单张，互不覆盖，所以并行安全。
  async function runRetry(i: number) {
    const imgIndex = i
    try {
      // 传当前画廊里的提示词。若该图被审核拦截、当前词仍过不了，后端会自动升级到
      // 安全画面再生成（点一次即出图，不原样重发必败词）。
      const prompt = scenesRef.current[i]?.desc_prompt
      const ov = presetRef.current
      const r = await api.retryImage(taskId, imgIndex, prompt,
        ov ? { model: ov.model, base_url: ov.base_url, unit_price: ov.unit_price } : undefined)
      setBust((b) => ({ ...b, [imgIndex]: (b[imgIndex] ?? 0) + 1 }))
      // 后端自动改写并成功时，把改写后的安全提示词回填到画廊输入框，让用户看到改成了什么
      if (r.rewritten && r.new_prompt) {
        setScenes((prev) => prev.map((s, idx) => (idx === i ? { ...s, desc_prompt: r.new_prompt as string } : s)))
      }
      setFailReason((m) => {
        const next = { ...m }
        if (r.failed) next[i] = humanReason(r.reason)
        else if (r.rewritten) next[i] = '✓ 原画面被审核拦截，已自动改为安全画面并生成成功'
        else delete next[i]
        return next
      })
      onChanged()
    } catch (e) {
      setFailReason((m) => ({ ...m, [i]: humanReason((e as ApiError).message) }))
    }
  }

  // 点击重试：支持多张并行（上限 MAX_PARALLEL），超出的进队列，名额释放后自动顶上。
  // 加入队列是原子操作，防止双击时重复加入（如果已在 retrying/queued 则直接返回）。
  async function retryOne(i: number) {
    if (retrying.has(i) || queued.has(i)) return
    setErr(null)
    // 本地有未保存编辑时，先整体存一次，保证后端用的是最新提示词
    if (dirty) {
      try { await api.saveScenes(taskId, scenes); setDirty(false) }
      catch (e) { setErr((e as ApiError).message); return }
    }
    if (retrying.size >= MAX_PARALLEL) {
      setQueued((q) => new Set(q).add(i))
      return
    }
    // 先检查再设置：防止快速双击时第二次调用在第一次 setRetrying 完成前通过检查。
    // 虽然 React 状态更新是批处理的，但 has() 检查在同步执行，存在极小窗口期。
    // 解决：先同步标记到 retrying，再开始异步执行。
    setRetrying((r) => new Set(r).add(i))
    try {
      await runRetry(i)
    } finally {
      setRetrying((r) => { const n = new Set(r); n.delete(i); return n })
    }
  }

  // 勾选/取消勾选某张（用于多选「一起重新组图」）
  function toggleSelect(i: number) {
    setSelected((s) => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n })
  }

  // 一键勾选所有失败的占位图，方便「把失败的一起重新组图」
  function selectAllFailed() {
    const f = new Set<number>()
    scenes.forEach((_, i) => { if (images[i]?.fallback) f.add(i) })
    setSelected(f)
  }

  // 把选中的几张合并成一次组图请求重新生成（省请求、风格统一、人物一致）。
  async function runBatchRetry() {
    // 防重复：用同步 ref 锁防止快速双击，比异步的 batchRunning 状态更可靠
    if (selected.size === 0 || batchLockRef.current) return
    batchLockRef.current = true
    setErr(null)
    // 有未保存编辑先存，保证后端用最新提示词
    if (dirty) {
      try { await api.saveScenes(taskId, scenes); setDirty(false) }
      catch (e) {
        setErr((e as ApiError).message)
        batchLockRef.current = false  // 保存失败时释放锁
        return
      }
    }
    setBatchRunning(true)
    const sel = [...selected].sort((a, b) => a - b)
    // 分镜与图一一对应：分镜下标 i → 图片下标 i
    const imgIndices = sel.map((i) => i)
    try {
      const ov = presetRef.current
      const r = await api.batchRetryImages(taskId, imgIndices, batchMode || undefined,
        ov ? { model: ov.model, base_url: ov.base_url, unit_price: ov.unit_price } : undefined)
      setBust((b) => {
        const n = { ...b }
        imgIndices.forEach((idx) => { n[idx] = (n[idx] ?? 0) + 1 })
        return n
      })
      // 回填每张的失败原因（按分镜下标）
      setFailReason((m) => {
        const next = { ...m }
        r.results.forEach((res) => {
          const i = res.index
          if (res.failed) next[i] = humanReason(res.reason)
          else delete next[i]
        })
        return next
      })
      setSelected(new Set())
      onChanged()
    } catch (e) {
      setErr((e as ApiError).message)
    } finally {
      setBatchRunning(false)
      batchLockRef.current = false  // 无论成功失败都释放锁
    }
  }

  // 名额释放时，从队列里取下一张顶上
  useEffect(() => {
    if (retrying.size >= MAX_PARALLEL || queued.size === 0) return
    const next = Math.min(...queued)
    setQueued((q) => { const n = new Set(q); n.delete(next); return n })
    setRetrying((r) => new Set(r).add(next))
    runRetry(next).finally(() => {
      setRetrying((r) => { const n = new Set(r); n.delete(next); return n })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retrying, queued])

  if (scenes.length === 0) {
    return <div className="text-sm text-slate-400">尚无分镜，画面脚本(SB)生成后显示。</div>
  }

  return (
    <div>
      <div className="space-y-3 mb-3">
        <div className="text-sm text-slate-400">
          共 {scenes.length} 个分镜 · 编辑后保存，或单张换图；勾选多张可「一起重新组图」（同批生成，风格统一、人物一致）
          {(retrying.size > 0 || queued.size > 0) && (
            <span className="ml-2 text-brand-400">
              · 重新生成中 {retrying.size}
              {queued.size > 0 && `，排队 ${queued.size}`}（最多 {MAX_PARALLEL} 张同时）
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* 出图模型：单张重生与批量重组共用。默认跟随当前配置（即默认 GPT）；
              失败/不满意的图可当场切到豆包等预设重生，只影响这一次、不动全局配置。
              标签不内嵌长模型名（会撑宽挤坏布局），完整模型名放 title。 */}
          <select value={modelName}
            onChange={(e) => setModelName(e.target.value)}
            title={`本次重生用哪个出图模型：默认跟随当前配置${curModel ? `（${curModel}）` : ''}；可临时换成已存的预设（如默认 GPT 失败后换豆包），只影响这次重生、不改全局配置`}
            className="max-w-[160px] px-2 py-1.5 rounded-lg text-sm bg-slate-700 text-slate-200 border-none
              focus:ring-1 focus:ring-brand-500">
            <option value="">出图模型：跟随配置</option>
            {presets.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
          <select value={batchMode}
            onChange={(e) => setBatchMode(e.target.value as '' | 'grid' | 'per_image')}
            title="本次重组的出图方式：九宫格一次请求出多张省钱；逐张每张单独出、画质优先（黑白等强约束更稳）"
            className="px-2 py-1.5 rounded-lg text-sm bg-slate-700 text-slate-200 border-none
              focus:ring-1 focus:ring-brand-500">
            <option value="">出图方式：跟随任务</option>
            <option value="grid">九宫格（省成本）</option>
            <option value="per_image">逐张（画质优先）</option>
          </select>
          <button onClick={selectAllFailed}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-slate-700 text-slate-200
              hover:bg-slate-600 transition-colors">
            选中所有失败
          </button>
          <button onClick={runBatchRetry} disabled={selected.size === 0 || batchRunning}
            className="px-4 py-1.5 rounded-lg text-sm font-medium bg-brand-600 text-white
              hover:bg-brand-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            title="把选中的图合并成一次组图请求重新生成（同批生成→风格统一、人物镜头带参考图→主角一致；按张计费，组图不省钱）">
            {batchRunning ? '组图生成中…'
              : selected.size > 0 ? `一起重新组图（${selected.size}）` : '一起重新组图'}
          </button>
          <button onClick={saveAll} disabled={!dirty || saving}
            className="px-4 py-1.5 rounded-lg text-sm font-medium bg-brand-600 text-white
              hover:bg-brand-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
            {saving ? '保存中…' : dirty ? '保存全部修改' : '已保存'}
          </button>
        </div>
      </div>

      {err && <div className="mb-3 px-3 py-2 rounded-lg text-sm bg-red-500/10 text-red-400">{err}</div>}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4">
        {scenes.map((s, i) => {
          const imgIndex = i
          const img = images[imgIndex]
          const v = bust[imgIndex] ?? 0
          const src = img ? `${api.imageUrl(taskId, img.path)}&v=${v}` : null
          const failed = !!img?.fallback
          const reason = failReason[i] || (failed ? humanReason(img?.fail_reason) : null)
          const isRetrying = retrying.has(i)
          const isQueued = queued.has(i)
          const isSelected = selected.has(i)
          return (
            <div key={s.id ?? i} className={`rounded-xl border overflow-hidden flex flex-col ${
              isSelected ? 'border-brand-500 ring-2 ring-brand-500/50 bg-brand-500/5'
              : failed ? 'border-red-500/60 bg-red-500/5'
              : 'border-slate-700/60 bg-slate-900/40'}`}>
              {/* 缩略图区 */}
              <div className="relative aspect-[9/16] bg-slate-950/60 flex items-center justify-center">
                {src && !failed
                  ? <img src={src} alt={`分镜${i + 1}`}
                      onClick={() => setLightbox(src)}
                      className="w-full h-full object-cover cursor-zoom-in"
                      title="点击放大查看" />
                  : <span className={`text-xs ${failed ? 'text-red-400' : 'text-slate-600'}`}>
                      {failed ? '生成失败' : '未生成'}
                    </span>}
                <label className="absolute top-2 left-2 flex items-center justify-center w-6 h-6
                  rounded bg-black/60 cursor-pointer hover:bg-black/80"
                  title="勾选后可「一起重新组图」">
                  <input type="checkbox" checked={isSelected}
                    onChange={() => toggleSelect(i)} className="accent-brand-600 w-4 h-4" />
                </label>
                <span className="absolute top-2 left-10 text-[11px] px-1.5 py-0.5 rounded bg-black/60 text-slate-200 font-mono">
                  #{String(i + 1).padStart(2, '0')}
                </span>
                {s.has_character && (
                  <span className="absolute top-2 right-2 text-[11px] px-1.5 py-0.5 rounded bg-brand-600/80 text-white">
                    参考
                  </span>
                )}
                {failed && (
                  <span className="absolute bottom-2 left-2 text-[11px] px-1.5 py-0.5 rounded bg-red-600 text-white">
                    占位图 · 待重试
                  </span>
                )}
              </div>

              {/* 编辑区 */}
              <div className="p-3 flex flex-col gap-2 flex-1">
                <label className="text-[11px] text-slate-500">口播文案</label>
                <textarea rows={2} value={s.cap}
                  onChange={(e) => editScene(i, { cap: e.target.value })}
                  className="w-full text-xs rounded-md bg-slate-950/60 border border-slate-700/60 px-2 py-1.5
                    text-slate-300 resize-none focus:outline-none focus:border-brand-500" />

                <label className="text-[11px] text-slate-500">绘图提示词</label>
                <textarea rows={4} value={s.desc_prompt}
                  onChange={(e) => editScene(i, { desc_prompt: e.target.value })}
                  className="w-full text-xs rounded-md bg-slate-950/60 border border-slate-700/60 px-2 py-1.5
                    text-slate-300 resize-none focus:outline-none focus:border-brand-500" />

                {reason && (
                  <div className={`text-[11px] rounded px-2 py-1.5 leading-snug ${
                    reason.startsWith('✓') ? 'text-emerald-400 bg-emerald-500/10'
                                           : 'text-red-400 bg-red-500/10'}`}>
                    {reason.startsWith('✓') ? reason : `⚠ ${reason}`}
                  </div>
                )}

                <div className="flex items-center justify-between mt-1">
                  <label className="flex items-center gap-1.5 text-[11px] text-slate-400 cursor-pointer">
                    <input type="checkbox" checked={s.has_character}
                      onChange={(e) => editScene(i, { has_character: e.target.checked })}
                      className="accent-brand-600" />
                    主角出场
                  </label>
                  <button onClick={() => retryOne(i)} disabled={isRetrying || isQueued}
                    className={`px-3 py-1 rounded-md text-xs font-medium disabled:opacity-40 transition-colors ${
                      failed ? 'bg-red-600 text-white hover:bg-red-700'
                             : dirty ? 'bg-brand-600 text-white hover:bg-brand-700'
                                     : 'bg-slate-700 text-slate-200 hover:bg-slate-600'}`}
                    title={failed ? '重新生成这张图'
                                  : dirty ? '用改后的提示词重新生成'
                                          : '同提示词换一张（构图会不同）'}>
                    {isRetrying ? '生成中…'
                      : isQueued ? '排队中…'
                      : failed ? '重新生成'
                      : dirty ? '改提示词重生成' : '不满意 · 换一张'}
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* 点击缩略图放大：全屏遮罩，点任意处或按钮关闭 */}
      {lightbox && (
        <div onClick={() => setLightbox(null)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4 cursor-zoom-out">
          <img src={lightbox} alt="放大查看"
            className="max-w-full max-h-full object-contain rounded-lg shadow-2xl"
            onClick={(e) => e.stopPropagation()} />
          <button onClick={() => setLightbox(null)}
            className="absolute top-4 right-4 w-10 h-10 rounded-full bg-white/10 hover:bg-white/20
              text-white text-xl flex items-center justify-center transition-colors"
            title="关闭（或点击空白处 / 按 Esc）">
            ✕
          </button>
        </div>
      )}
    </div>
  )
}
