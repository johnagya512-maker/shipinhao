// 任务创建页：逐字稿输入 + 赛道/受众/模块/变现模式选择 + 成本预估 + 提交。
import { useState, useEffect, useMemo } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { TaskCreate, EstimateOut, ConfigOut, VoiceItem, VoiceCategory, DraftTemplate, ViralStructure } from '../api/types'
import VoicePicker from '../components/VoicePicker'
import { useVoicePreview } from '../hooks/useVoicePreview'

const TRACKS = [
  { key: 'character_story', name: '人物故事', desc: '历史人物 · 戏剧叙事 · 强悬念' },
  { key: 'health_book', name: '健康书单', desc: '健康科普 · 案例共鸣 · 温和带货' },
  { key: 'culture_science', name: '文化科普', desc: '华夏文化 · 传统民俗 · 知识讲解' },
  { key: 'kids_picturebook', name: '绘本故事', desc: '儿童睡前 · 温柔舒缓 · 正向价值' },
  { key: 'ecommerce', name: '电商带货', desc: '产品种草 · 痛点切入 · 强号召' },
  { key: 'soul_chicken', name: '心灵鸡汤', desc: '情感治愈 · 励志金句 · 强共鸣' },
  { key: 'folk_tale', name: '民间故事', desc: '虚构传说 · 因果寓言 · 悬念叙事' },
  { key: 'food_探店', name: '美食探店', desc: '城市烟火 · 美食诱惑 · 到店欲望' },
  { key: 'general', name: '通用故事', desc: '无特定赛道 · 通用兜底' },
]
const IMAGE_STYLES = [
  { key: '', name: '赛道默认', desc: '按赛道自动选画风' },
  { key: '古风电影', name: '古风电影', desc: '电影级布光 · 景深质感' },
  { key: '工笔古画', name: '工笔古画', desc: '细腻笔触 · 绢本设色' },
  { key: '水墨写意', name: '水墨写意', desc: '留白意境 · 墨色浓淡' },
  { key: '古风工笔画', name: '古风工笔画', desc: '工笔重彩 · 古典叙事' },
  { key: '新国风水彩', name: '新国风水彩', desc: '水彩晕染 · 国潮清新' },
  { key: '复古胶片', name: '复古胶片', desc: '颗粒质感 · 暖黄年代感' },
  { key: '温暖暖色', name: '温暖暖色', desc: '柔和亲切 · 简洁清晰' },
  { key: '写实彩色', name: '写实彩色', desc: '自然光影 · 真实质感' },
  { key: '黑白纪实', name: '黑白纪实', desc: '明暗对比 · 历史厚重' },
  { key: '皮克斯3D', name: '皮克斯3D', desc: '圆润造型 · 温暖渲染' },
  { key: '吉卜力动画', name: '吉卜力动画', desc: '手绘水彩 · 清新治愈' },
  { key: '极简插画', name: '极简插画', desc: '扁平简洁 · 现代设计' },
  { key: '温馨绘本', name: '温馨绘本', desc: '蜡笔童趣 · 明亮温暖' },
  { key: '明亮商业', name: '明亮商业', desc: '干净背景 · 产品突出' },
]
const ASPECTS = [
  { key: '9:16', name: '9:16', desc: '竖屏', box: 'w-3 h-5' },
  { key: '3:4', name: '3:4', desc: '竖版', box: 'w-4 h-5' },
  { key: '1:1', name: '1:1', desc: '方形', box: 'w-4 h-4' },
  { key: '16:9', name: '16:9', desc: '横屏', box: 'w-5 h-3' },
]
// 草稿模板：出图比例的上位概念。选模板自动带出对应比例（仍可手动覆盖）。
const TEMPLATES = [
  { key: 'vertical', name: '默认竖屏', desc: '视频号/抖音主流', ratio: '9:16' },
  { key: 'vertical_43', name: '竖版 3:4', desc: '朋友圈/图文', ratio: '3:4' },
  { key: 'square', name: '方形 1:1', desc: '封面/多平台', ratio: '1:1' },
  { key: 'landscape', name: '横屏 16:9', desc: '横版/B站', ratio: '16:9' },
]
const REWRITE_STRENGTHS = [
  { key: 'light', name: '轻度', desc: '贴近原文' },
  { key: 'medium', name: '适中', desc: '平衡改写' },
  { key: 'strong', name: '强力', desc: '大胆重构' },
]
const PERSPECTIVES = [
  { key: 'auto', name: '自动', desc: '按赛道' },
  { key: 'first', name: '第一人称', desc: '“我”代入' },
  { key: 'third', name: '第三人称', desc: '客观叙述' },
]
const MONETIZATIONS = [
  { key: 'revenue_share', name: '创作分成', desc: '结尾引导互动' },
  { key: 'book_sales', name: '图书带货', desc: '结尾带出书籍' },
]
// 平台 key → 中文名（解析结果展示用）
const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音', tiktok: 'TikTok', kuaishou: '快手', xiaohongshu: '小红书',
  bilibili: 'B站', weibo: '微博', wechat: '视频号',
}
const PLATFORM_NAME = (k: string) => PLATFORM_LABELS[k] || k || '视频'
// 音色库从后端拉取（GET /config/voices）。voice 为空=用配置页默认音色。
// 实际可用性取决于火山账号授权，可点「试听」验证。
// 可选模块（A 清洗 / B 改写 / G 合成为必选，后端强制）。
const OPTIONAL_MODULES = [
  { key: 'D', name: 'D 图书识别' },
  { key: 'E', name: 'E 智能配图' },
  { key: 'F', name: 'F 配音分段' },
  { key: 'H', name: 'H 合规审查' },
]
// 处理模式：决定前段文案怎么来（后续生图/配音/成片三模式一致）。
const PROCESSING_MODES = [
  { key: 'full_auto', name: '全自动', desc: '清洗+AI改写+智能分句（推荐）' },
  { key: 'semi_auto', name: '半自动', desc: '用原文，仅AI智能分句，不改写' },
  { key: 'direct', name: '直接出片', desc: '不改写，按标点机械切分（仍合规审查）' },
]
// 暂停确认：在关键步骤后停下等人工确认。
const PAUSE_MODES = [
  { key: 'none', name: '不暂停', desc: '一条龙跑完' },
  { key: 'key_nodes', name: '关键节点', desc: '改写稿+生图后停（推荐）' },
  { key: 'every_step', name: '每步确认', desc: '每步后都停' },
  { key: 'custom', name: '自定义', desc: '自选暂停步骤' },
]
// 可作为暂停点的步骤（面向用户命名 → 后端 step）。被处理模式跳过的会灰显。
const PAUSE_STEPS: { key: string; name: string; skipModes: string[]; needsModule?: string }[] = [
  { key: 'B', name: '智能改写', skipModes: ['semi_auto', 'direct'] },
  { key: 'H', name: '合规审查', skipModes: [] },
  { key: 'F', name: '分句分镜', skipModes: [] },
  { key: 'P', name: '提示词生成', skipModes: [], needsModule: 'E' },
  { key: 'E', name: '批量生图', skipModes: [], needsModule: 'E' },
]

// 上传前压缩参考图：长边限制到 1536px、JPEG 质量 0.85，避免原图过大被绘图 API 拒。
// 参考图只用于让模型认人物特征，不需要原始分辨率。返回压缩后的 File。
async function compressImage(file: File, maxEdge = 1536, quality = 0.85): Promise<File> {
  // 已经很小（<1.5MB）的图直接用原图，省一次编解码。
  if (file.size <= 1.5 * 1024 * 1024) return file
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = reject
    r.readAsDataURL(file)
  })
  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const i = new Image()
    i.onload = () => resolve(i)
    i.onerror = reject
    i.src = dataUrl
  })
  const scale = Math.min(1, maxEdge / Math.max(img.width, img.height))
  const w = Math.round(img.width * scale)
  const h = Math.round(img.height * scale)
  const canvas = document.createElement('canvas')
  canvas.width = w; canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) return file
  ctx.drawImage(img, 0, 0, w, h)
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality))
  if (!blob) return file
  return new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' })
}

type ParseMeta = { title: string; author: string; platform: string }

export default function TaskCreatePage() {
  const nav = useNavigate()
  // 草稿持久化：切到别的页面（如配置页）再回来，用户填的内容不应被清空。
  // 表单实时存 localStorage，组件重建时还原；提交成功后清掉。
  const DRAFT_KEY = 'task_create_draft'
  const DEFAULT_FORM: TaskCreate = {
    douyin_url: '', transcript: '', keyword: '', title: '', author: '',
    modules: ['A', 'B', 'E', 'F', 'G', 'H'],
    target_audience: '50+女性', track: 'character_story',
    monetization_mode: 'revenue_share', image_style: '', aspect_ratio: '9:16',
    cost_limit: 5.0, time_limit: 900, enable_subtitles: true, enable_animations: true,
    draft_template: 'guofeng',
    creation_mode: 'same_topic',
    processing_mode: 'full_auto', pause_mode: 'key_nodes', pause_steps: [],
  }
  function loadDraft(): { form: TaskCreate; previewScript: string; inputMode: 'transcript' | 'douyin'; parseMeta: ParseMeta | null } {
    try {
      const raw = localStorage.getItem(DRAFT_KEY)
      if (raw) {
        const d = JSON.parse(raw)
        return {
          form: { ...DEFAULT_FORM, ...(d.form || {}) },
          previewScript: d.previewScript || '',
          inputMode: d.inputMode === 'douyin' ? 'douyin' : 'transcript',
          parseMeta: d.parseMeta || null,
        }
      }
    } catch { /* 忽略损坏的草稿 */ }
    return { form: DEFAULT_FORM, previewScript: '', inputMode: 'transcript', parseMeta: null }
  }
  const initial = loadDraft()
  const [form, setForm] = useState<TaskCreate>(initial.form)
  const [est, setEst] = useState<EstimateOut | null>(null)
  const [estimating, setEstimating] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [cfg, setCfg] = useState<ConfigOut | null>(null)
  const [inputMode, setInputMode] = useState<'transcript' | 'douyin'>(initial.inputMode)
  const [bgmFiles, setBgmFiles] = useState<string[]>([])
  const [refUploading, setRefUploading] = useState(false)
  const [refName, setRefName] = useState<string | null>(null)
  const [refPreview, setRefPreview] = useState<string | null>(null)
  // 音色库 + 收藏 + 选择器弹窗
  const [voices, setVoices] = useState<VoiceItem[]>([])
  const [voiceCats, setVoiceCats] = useState<VoiceCategory[]>([])
  const [favorites, setFavorites] = useState<string[]>([])
  const [showVoicePicker, setShowVoicePicker] = useState(false)
  const [draftTemplates, setDraftTemplates] = useState<DraftTemplate[]>([])
  // 爆款结构拆解 + 二创成品预览
  const [structure, setStructure] = useState<ViralStructure | null>(null)
  const [previewScript, setPreviewScript] = useState(initial.previewScript)
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeErr, setAnalyzeErr] = useState('')
  // 链接解析出逐字稿（采集+ASR）
  const [parsing, setParsing] = useState(false)
  const [parseErr, setParseErr] = useState('')
  const [parseMeta, setParseMeta] = useState<ParseMeta | null>(initial.parseMeta)
  const { previewingId, error: previewError, preview: previewVoice } = useVoicePreview()

  // 载入配置，用于"凭证未配置"提示。
  useEffect(() => { api.getConfig().then((c) => { setCfg(c); setFavorites(c.tts_favorites ?? []) }).catch(() => {}) }, [])
  useEffect(() => {
    let tries = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = () => {
      api.getVoices().then((r) => {
        setVoices(r.voices)
        setVoiceCats(r.categories)
        // 后台还在探活且未超过重试上限 → 稍后重取，拿到可用性标记
        if (r.probing && tries < 8) { tries += 1; timer = setTimeout(load, 2500) }
      }).catch(() => {})
    }
    load()
    return () => { if (timer) clearTimeout(timer) }
  }, [])
  // 载入 BGM 列表（配置了 bgm 目录时才有内容）。
  useEffect(() => { api.bgmList().then((r) => setBgmFiles(r.files)).catch(() => {}) }, [])
  // 载入草稿动画模板清单。
  useEffect(() => { api.getDraftTemplates().then((r) => setDraftTemplates(r.templates)).catch(() => {}) }, [])
  // 实时把表单+预览文案+输入模式+解析元信息存草稿，切页面回来不丢（含解析出文案后停留的 tab 与“已解析”提示）。
  useEffect(() => {
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify({ form, previewScript, inputMode, parseMeta })) } catch { /* 配额满则忽略 */ }
  }, [form, previewScript, inputMode, parseMeta])

  // 检测关键凭证是否缺失（LLM 必需；配图视模块而定）。
  const missingKeys: string[] = []
  if (cfg) {
    if (!cfg.llm_api_key_mask) missingKeys.push('文案模型')
    if (form.modules.includes('E') && !cfg.image_api_key_mask) missingKeys.push('配图模型')
  }

  const set = (patch: Partial<TaskCreate>) => setForm((f) => ({ ...f, ...patch }))
  // 「不改文案」：用原文不做 AI 改写（semi_auto/direct 都属此类）。选中时隐藏改写强度等仅改写相关的设置。
  const noRewrite = (form.processing_mode ?? 'full_auto') !== 'full_auto'

  // 收藏/取消收藏音色（写后端 + 本地同步）
  async function toggleFav(voiceId: string) {
    const has = favorites.includes(voiceId)
    setFavorites((f) => has ? f.filter((v) => v !== voiceId) : [...f, voiceId])  // 乐观更新
    try {
      const r = await api.toggleFavorite(voiceId, has ? 'remove' : 'add')
      setFavorites(r.favorites)
    } catch { /* 失败回滚到后端真实值 */ api.getConfig().then((c) => setFavorites(c.tts_favorites ?? [])).catch(() => {}) }
  }

  const voiceById = useMemo(() => new Map(voices.map((v) => [v.id, v])), [voices])
  // 创建页 chips：收藏的音色 + 当前选中的（即使没收藏也显示），去重
  const chipVoices = useMemo(() => {
    const ids = [...favorites]
    if (form.voice && !ids.includes(form.voice)) ids.push(form.voice)
    return ids.map((id) => voiceById.get(id)).filter(Boolean) as VoiceItem[]
  }, [favorites, form.voice, voiceById])

  // 配图相关设置（素材来源/画面风格/参考图/模板/比例）仅在勾选 E 配图时生效；
  // 否则灰显并禁用交互，避免空跑误导。
  const imageOn = form.modules.includes('E')
  const dimImg = imageOn ? '' : 'opacity-40 pointer-events-none select-none'

  function toggleModule(key: string) {
    setForm((f) => {
      const has = f.modules.includes(key)
      const modules = has ? f.modules.filter((m) => m !== key) : [...f.modules, key]
      return { ...f, modules }
    })
  }

  function hasInput() {
    return Boolean((form.douyin_url ?? '').trim() || (form.transcript ?? '').trim())
  }

  // 解析视频链接 → 出逐字稿。成功后把文案填进 transcript 并切到「粘贴文案」tab，
  // 用户即可看到/微调文案、点二创预览，再开始生成。
  async function doParseTranscript() {
    const url = (form.douyin_url ?? '').trim()
    if (!url) { setParseErr('请先粘贴视频分享链接或口令'); return }
    setParsing(true); setParseErr(''); setParseMeta(null)
    try {
      const r = await api.parseTranscript(url)
      set({ transcript: r.transcript })
      setParseMeta({ title: r.title, author: r.author, platform: r.platform })
      // r.title 是视频原标题/描述，可能是一长段文案；书名框只取前 30 字，
      // 避免把整段 desc 灌进去（后续会被后端当短标题用而串台）。
      if (r.title && !(form.title ?? '').trim()) set({ title: r.title.slice(0, 30) })
      if (r.author && !(form.author ?? '').trim()) set({ author: r.author })
      // 不切 tab：文案直接显示在下方结果框，用户在链接 tab 即可看到/微调，再点二创预览或开始生成。
      setEst(null)
    } catch (e) {
      setParseErr((e as ApiError).message)
    } finally {
      setParsing(false)
    }
  }

  async function doAnalyzeStructure() {
    const text = (form.transcript ?? '').trim()
    if (text.length < 20) { setAnalyzeErr('请先在上方粘贴文案（至少 20 字）'); return }
    setAnalyzing(true); setAnalyzeErr(''); setStructure(null); setPreviewScript('')
    try {
      const r = await api.analyzeStructure({
        text,
        track: form.track,
        target_audience: form.target_audience,
        title: form.title ?? undefined,
        monetization_mode: form.monetization_mode,
        rewrite_strength: form.rewrite_strength,
        narrative_perspective: form.narrative_perspective,
        creation_mode: form.creation_mode,
      })
      setStructure(r.structure)
      setPreviewScript(r.script)
    } catch (e) {
      setAnalyzeErr((e as ApiError).message)
    } finally {
      setAnalyzing(false)
    }
  }

  async function doEstimate() {
    if (!hasInput()) { setError('请填写视频链接或逐字稿'); return }
    setEstimating(true); setError(null)
    try {
      setEst(await api.estimate(form))
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setEstimating(false)
    }
  }

  function clearDraft() {
    try { localStorage.removeItem(DRAFT_KEY) } catch { /* 忽略 */ }
    setForm(DEFAULT_FORM)
    setPreviewScript(''); setStructure(null); setEst(null); setError(null)
    setRefName(null); setRefPreview(null)
    setParseErr(''); setParseMeta(null)
  }

  async function submit() {
    if (!hasInput()) { setError('请填写视频链接或逐字稿'); return }
    setSubmitting(true); setError(null)
    try {
      const task = await api.createTask(form)
      try { localStorage.removeItem(DRAFT_KEY) } catch { /* 忽略 */ }
      nav(`/tasks/${task.id}`)
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold text-slate-100 mb-1">新建任务</h1>
      <p className="text-sm text-slate-500 mb-5">贴逐字稿或视频链接，自动生成可在剪映里编辑的成片草稿。</p>

      {error && (
        <div className="mb-4 px-4 py-2.5 rounded-lg text-sm bg-red-50 text-red-700 border border-red-100">{error}</div>
      )}

      <div className="card space-y-4">
        {/* 两种创作方式：左=我有素材（可用），右=AI创作（敬请期待，后端待建） */}
        <div className="grid grid-cols-2 gap-3">
          <div className="px-4 py-3 rounded-xl border bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30">
            <div className="text-sm font-medium text-brand-300">📝 我有素材</div>
            <div className="text-xs text-slate-500 mt-0.5">粘贴文案或贴视频链接，改写成片</div>
          </div>
          <div className="relative px-4 py-3 rounded-xl border border-dashed border-slate-700 bg-slate-800/20 cursor-not-allowed select-none">
            <span className="absolute top-2 right-2 text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-400">敬请期待</span>
            <div className="text-sm font-medium text-slate-500">✨ AI 创作</div>
            <div className="text-xs text-slate-600 mt-0.5">给个关键词，AI 自动搜资料写稿</div>
          </div>
        </div>

        {/* 「我有素材」内部：粘贴文案 / 贴链接 两个 tab */}
        <div className="flex gap-4 border-b border-slate-800 -mb-1">
          {([['transcript', '粘贴文案'], ['douyin', '贴视频链接']] as const).map(([m, label]) => (
            <button key={m} type="button"
              onClick={() => { setInputMode(m); set(m === 'douyin' ? { transcript: '' } : { douyin_url: '' }); setEst(null) }}
              className={`pb-2 text-sm border-b-2 -mb-px transition-colors ${
                inputMode === m ? 'border-brand-500 text-brand-300' : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}>{label}</button>
          ))}
        </div>

        {inputMode === 'douyin' ? (
          <div className="space-y-2">
            <label className="block">
              <span className="text-sm text-slate-400">视频链接（需配采集/ASR Key）</span>
              <input className="field"
                placeholder="粘贴抖音/快手/小红书/B站/微博/视频号/TikTok 的分享口令或链接…"
                value={form.douyin_url ?? ''}
                onChange={(e) => { set({ douyin_url: e.target.value }); setEst(null); setParseErr(''); setParseMeta(null) }} />
              <span className="block text-[11px] text-slate-600 mt-1">支持：抖音 · 快手 · 小红书 · B站 · 微博 · 视频号 · TikTok（自动识别平台）</span>
            </label>
            <div className="flex items-center gap-3">
              <button type="button" onClick={doParseTranscript} disabled={parsing || !(form.douyin_url ?? '').trim()}
                className="text-[13px] px-3 py-1.5 rounded-lg border border-brand-500/50 text-brand-300 hover:bg-brand-600/10 disabled:opacity-50">
                {parsing ? '解析中…下载视频+转写，约30-90秒' : '🔍 解析出文案'}
              </button>
              {parseMeta && (
                <span className="text-[11px] text-emerald-400 truncate">
                  ✓ 已解析（{PLATFORM_NAME(parseMeta.platform)}{parseMeta.author ? ` · ${parseMeta.author}` : ''}），文案已填入下方
                </span>
              )}
            </div>
            {parsing && (
              <p className="text-[11px] text-slate-500">正在采集无水印视频并语音转写，请稍候，不要离开本页。</p>
            )}
            {parseErr && <p className="text-[11px] text-red-400">{parseErr}</p>}
            <p className="text-[11px] text-slate-600">
              提示：分享文案里要包含真实链接（如 v.douyin.com/xxx）才能解析。解析完文案会填到「粘贴文案」里，可微调后再二创。
            </p>
            {/* 解析结果框：当场展示转写出的逐字稿，可直接编辑（与下方文案/二创预览共用 form.transcript）。 */}
            {(form.transcript ?? '').trim() && (
              <div className="mt-1 rounded-xl bg-slate-900/60 border border-emerald-500/30 p-3">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[12px] font-medium text-emerald-300">📄 解析出的文案</span>
                  <span className="text-[10px] text-slate-600">可直接编辑微调</span>
                </div>
                <textarea rows={10} value={form.transcript ?? ''}
                  onChange={(e) => { set({ transcript: e.target.value }); setEst(null) }}
                  className="w-full text-sm bg-slate-950/50 border border-slate-700 rounded-lg p-2.5 text-slate-200 leading-relaxed outline-none resize-y font-mono focus:border-brand-500" />
                <p className="mt-1 text-[10px] text-slate-600">{(form.transcript ?? '').length} 字 · 满意后可点上方「二创方式」预览，或直接「开始生成」。</p>
              </div>
            )}
          </div>
        ) : (
          <label className="block">
            <span className="text-sm text-slate-400">逐字稿</span>
            <textarea rows={8} className="field font-mono"
              placeholder="粘贴口播逐字稿原文…"
              value={form.transcript ?? ''}
              onChange={(e) => { set({ transcript: e.target.value }); setEst(null) }} />
          </label>
        )}

        {/* 二创方式：拆解爆款结构 → 复刻骨架重写。核心卖点。「不改文案」走 semi_auto，原文一字不改。 */}
        <div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-400">二创方式 <span className="text-[11px] text-slate-600">· 拆解爆款结构再仿写</span></span>
            {!noRewrite && (inputMode === 'transcript' || (form.transcript ?? '').trim()) ? (
              <button type="button" onClick={doAnalyzeStructure} disabled={analyzing}
                className="text-[12px] px-2.5 py-1 rounded-lg border border-brand-500/40 text-brand-300 hover:bg-brand-600/10 disabled:opacity-50">
                {analyzing ? '生成中…约30-60秒' : '✨ 预览二创文案'}
              </button>
            ) : null}
          </div>
          {analyzing && (
            <p className="mt-1 text-[11px] text-slate-500">正在拆解结构并改写，大模型生成需要点时间，请稍候，不要离开本页。</p>
          )}
          <div className="mt-2 grid grid-cols-3 gap-2">
            {([
              ['same_topic', '拆解结构二创', '先拆原文爆款骨架 → 按骨架重写，学它为什么爆'],
              ['none', '直接改写', '不拆结构，按常规套路改写'],
              ['keep', '不改文案', '用我写的原文，一字不改，按标点分句'],
            ] as const).map(([key, name, desc]) => {
              const on = key === 'keep' ? noRewrite : (!noRewrite && (form.creation_mode || 'same_topic') === key)
              return (
                <button key={key} type="button" onClick={() => {
                  if (key === 'keep') {
                    set({ processing_mode: 'direct' })
                    setPreviewScript(''); setStructure(null)   // 不改文案：清掉残留的二创预览
                  } else {
                    set({ creation_mode: key, processing_mode: 'full_auto' })
                  }
                }}
                  className={`text-left px-2.5 py-1.5 rounded-lg border transition-colors ${
                    on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30'
                       : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'}`}>
                  <span className={`text-[13px] font-medium ${on ? 'text-brand-300' : 'text-slate-200'}`}>{name}</span>
                  <span className="block text-[10px] text-slate-500 leading-tight mt-0.5">{desc}</span>
                </button>
              )
            })}
          </div>

          {/* 改写强度 / 叙事视角：直接影响二创效果，放在预览旁边，调完即可预览看效果。
              仅全自动模式有效（半自动/直接出片不改写）。 */}
          {(form.processing_mode ?? 'full_auto') === 'full_auto' && (
            <div className="mt-2 grid grid-cols-2 gap-3">
              <div>
                <span className="text-[12px] text-slate-500">改写强度 <span className="text-slate-600">· 嫌改得太像原文就调「强力」</span></span>
                <div className="mt-1 flex gap-1.5">
                  {REWRITE_STRENGTHS.map((s) => {
                    const on = form.rewrite_strength === s.key
                    return (
                      <button key={s.key} type="button" onClick={() => set({ rewrite_strength: s.key })}
                        className={`flex-1 px-2 py-1 rounded-lg border text-center transition-colors ${
                          on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30' : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'
                        }`}>
                        <div className={`text-[12px] ${on ? 'text-brand-300' : 'text-slate-200'}`}>{s.name}</div>
                        <div className="text-[9px] text-slate-500">{s.desc}</div>
                      </button>
                    )
                  })}
                </div>
              </div>
              <div>
                <span className="text-[12px] text-slate-500">叙事视角</span>
                <div className="mt-1 flex gap-1.5">
                  {PERSPECTIVES.map((p) => {
                    const on = form.narrative_perspective === p.key
                    return (
                      <button key={p.key} type="button" onClick={() => set({ narrative_perspective: p.key })}
                        className={`flex-1 px-2 py-1 rounded-lg border text-center transition-colors ${
                          on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30' : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'
                        }`}>
                        <div className={`text-[12px] ${on ? 'text-brand-300' : 'text-slate-200'}`}>{p.name}</div>
                        <div className="text-[9px] text-slate-500">{p.desc}</div>
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>
          )}
          {analyzeErr && <p className="mt-1 text-[11px] text-red-400">{analyzeErr}</p>}

          {/* 预览成品：二创后的完整文案（主），结构骨架折叠为次要信息。不改文案模式不显示。 */}
          {previewScript && !noRewrite && (
            <div className="mt-2 rounded-xl bg-slate-900/60 border border-brand-500/30 p-3">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[12px] font-medium text-brand-300">✨ 二创文案预览</span>
                <span className="text-[10px] text-slate-600">仅预览，正式生成以任务为准</span>
              </div>
              <textarea rows={10} readOnly value={previewScript}
                className="w-full text-sm bg-slate-950/50 border border-slate-700 rounded-lg p-2.5 text-slate-200 leading-relaxed outline-none resize-y" />
              <p className="mt-1 text-[10px] text-slate-600">{previewScript.length} 字 · 不满意可换「改写强度/视角」或重新预览，满意了点下方「开始生成」。</p>
              {structure && (structure.why_viral || (structure.structure?.length ?? 0) > 0) && (
                <details className="mt-2">
                  <summary className="text-[11px] text-slate-500 cursor-pointer hover:text-slate-300">查看拆解的爆款结构 ▾</summary>
                  <div className="mt-1.5 space-y-1 text-[11px]">
                    {structure.why_viral && <p className="text-brand-300/80">💡 {structure.why_viral}</p>}
                    {structure.hook && <p className="text-slate-400"><span className="text-slate-600">钩子·{structure.hook.type}：</span>{structure.hook.text}</p>}
                    {(structure.structure?.length ?? 0) > 0 && (
                      <p className="text-slate-400">
                        <span className="text-slate-600">结构：</span>
                        {structure.structure!.map((p, i) => (
                          <span key={i} className="inline-block mr-1.5">{p.part}<span className="text-slate-600">({p.emotion}/{p.pace})</span>{i < structure.structure!.length - 1 ? ' →' : ''}</span>
                        ))}
                      </p>
                    )}
                    {structure.ending && <p className="text-slate-400"><span className="text-slate-600">结尾·{structure.ending.type}：</span>{structure.ending.text}</p>}
                    {structure.rhythm && <p className="text-slate-500"><span className="text-slate-600">节奏：</span>{structure.rhythm}{structure.duration_hint ? ` · ${structure.duration_hint}` : ''}</p>}
                  </div>
                </details>
              )}
            </div>
          )}
        </div>

        <div className="grid grid-cols-3 gap-4">
          <label className="block">
            <span className="text-sm text-slate-400">书名</span>
            <input className="mt-1 w-full border rounded-lg px-3 py-2"
              value={form.title ?? ''} onChange={(e) => set({ title: e.target.value })} />
          </label>
          <label className="block">
            <span className="text-sm text-slate-400">作者</span>
            <input className="mt-1 w-full border rounded-lg px-3 py-2"
              value={form.author ?? ''} onChange={(e) => set({ author: e.target.value })} />
          </label>
          <label className="block">
            <span className="text-sm text-slate-400">关键词</span>
            <input className="mt-1 w-full border rounded-lg px-3 py-2"
              value={form.keyword ?? ''} onChange={(e) => set({ keyword: e.target.value })} />
          </label>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="text-sm text-slate-400">受众</span>
            <input className="field"
              value={form.target_audience} onChange={(e) => set({ target_audience: e.target.value })} />
          </label>
        </div>

        <div>
          <span className="text-sm text-slate-400">内容赛道</span>
          <div className="mt-2 grid grid-cols-3 gap-2">
            {TRACKS.map((t) => {
              const on = form.track === t.key
              return (
                <button key={t.key} type="button" onClick={() => set({ track: t.key })}
                  className={`text-left px-2.5 py-1.5 rounded-lg border transition-colors ${
                    on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30'
                       : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'
                  }`}>
                  <span className={`text-sm font-medium ${on ? 'text-brand-300' : 'text-slate-200'}`}>{t.name}</span>
                  <span className="block text-[10px] text-slate-500 truncate" title={t.desc}>{t.desc}</span>
                </button>
              )
            })}
          </div>
        </div>

        <div>
          <span className="text-sm text-slate-400">素材来源 {!imageOn && <span className="text-[11px] text-amber-400/70">· 需勾选「E 智能配图」</span>}</span>
          <div className={`mt-2 grid grid-cols-2 gap-2 ${dimImg}`}>
            <div className="px-3 py-2 rounded-lg border bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30">
              <span className="text-[13px] font-medium text-brand-300">🎨 AI 绘图</span>
              <span className="block text-[10px] text-slate-500">按文案分镜自动生成配图</span>
            </div>
            <div className="relative px-3 py-2 rounded-lg border border-dashed border-slate-700 bg-slate-800/20 cursor-not-allowed select-none">
              <span className="absolute top-1.5 right-1.5 text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-400">敬请期待</span>
              <span className="text-[13px] font-medium text-slate-500">🌐 网络素材</span>
              <span className="block text-[10px] text-slate-600">检索网络图片/视频素材</span>
            </div>
          </div>
        </div>

        <div className={dimImg}>
          <span className="text-sm text-slate-400">画面风格</span>
          <div className="mt-2 grid grid-cols-4 gap-2">
            {IMAGE_STYLES.map((s) => {
              const on = (form.image_style ?? '') === s.key
              return (
                <button key={s.key} type="button" onClick={() => set({ image_style: s.key })}
                  className={`text-left px-2.5 py-1.5 rounded-lg border transition-colors ${
                    on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30'
                       : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'
                  }`}>
                  <span className={`text-[13px] font-medium ${on ? 'text-brand-300' : 'text-slate-200'}`}>{s.name}</span>
                  <span className="block text-[10px] text-slate-500 truncate" title={s.desc}>{s.desc}</span>
                </button>
              )
            })}
          </div>
        </div>

        <div>
          <span className="text-sm text-slate-400">主角参考图（可选）</span>
          <div className={`mt-2 flex items-center gap-3 ${dimImg}`}>
            <label className="px-3 py-1.5 rounded-lg border border-slate-700 text-slate-200 text-sm cursor-pointer bg-slate-800/40 hover:bg-slate-800">
              {refUploading ? '上传中…' : '选择图片'}
              <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
                disabled={refUploading}
                onChange={async (e) => {
                  const f = e.target.files?.[0]
                  if (!f) return
                  setRefUploading(true); setError(null)
                  try {
                    const compressed = await compressImage(f)
                    const r = await api.uploadReference(compressed)
                    set({ reference_image: r.reference_image }); setRefName(f.name); setRefPreview(URL.createObjectURL(f))
                  } catch (err) { setError((err as ApiError).message) }
                  finally { setRefUploading(false) }
                }} />
            </label>
            {refPreview && (
              <img src={refPreview} alt="参考图" className="w-12 h-12 rounded-lg object-cover border border-slate-700" />
            )}
            {refName && (
              <span className="text-xs text-emerald-400 truncate max-w-[160px]" title={refName}>✓ {refName}</span>
            )}
            {refName && (
              <button type="button" className="text-xs text-slate-500 hover:text-slate-300"
                onClick={() => { set({ reference_image: undefined }); setRefName(null); setRefPreview(null) }}>清除</button>
            )}
          </div>
          <p className="mt-1 text-[10px] text-slate-600">上传主角图片后，分镜配图会以这张为参考保持人物一致（取决于绘图模型支持，不支持则自动退回纯文生图）。</p>
        </div>

        <div className={dimImg}>
          <span className="text-sm text-slate-400">草稿模板</span>
          <div className="mt-2 grid grid-cols-4 gap-2">
            {TEMPLATES.map((t) => {
              const on = form.aspect_ratio === t.ratio
              return (
                <button key={t.key} type="button" onClick={() => set({ aspect_ratio: t.ratio })}
                  className={`text-left px-2.5 py-1.5 rounded-lg border transition-colors ${
                    on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30'
                       : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'
                  }`}>
                  <span className={`text-[13px] font-medium ${on ? 'text-brand-300' : 'text-slate-200'}`}>{t.name}</span>
                  <span className="block text-[10px] text-slate-500 truncate" title={t.desc}>{t.desc}</span>
                </button>
              )
            })}
          </div>
        </div>

        <div className={dimImg}>
          <span className="text-sm text-slate-400">出图比例 <span className="text-[11px] text-slate-600">· 已跟随草稿模板，可手动覆盖</span></span>
          <div className="mt-2 flex gap-2">
            {ASPECTS.map((a) => {
              const on = form.aspect_ratio === a.key
              return (
                <button key={a.key} type="button" onClick={() => set({ aspect_ratio: a.key })}
                  className={`flex items-center gap-2 px-3 py-2 rounded-xl border transition-colors ${
                    on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30'
                       : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'
                  }`}>
                  <span className={`${a.box} rounded-sm border ${on ? 'border-brand-400' : 'border-slate-500'}`} />
                  <span className={`text-sm ${on ? 'text-brand-300' : 'text-slate-200'}`}>{a.name}</span>
                  <span className="text-[11px] text-slate-500">{a.desc}</span>
                </button>
              )
            })}
          </div>
        </div>

        <div>
          <span className="text-sm text-slate-400">配音员</span>
          {/* 供应商 tab：豆包=火山引擎(可用)，MiniMax=未接入(占位) */}
          <div className="mt-2 flex gap-4 border-b border-slate-800">
            <span className="pb-2 text-sm border-b-2 border-brand-500 text-brand-300 -mb-px">豆包</span>
            <span className="pb-2 text-sm text-slate-600 cursor-not-allowed">MiniMax · 敬请期待</span>
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2">
            {/* 默认音色 */}
            <button type="button" onClick={() => set({ voice: '' })}
              className={`text-left px-2.5 py-1.5 rounded-lg border transition-colors ${
                !form.voice ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30'
                            : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'}`}>
              <span className={`text-[13px] font-medium ${!form.voice ? 'text-brand-300' : 'text-slate-200'}`}>默认音色</span>
              <span className="block text-[10px] text-slate-500 truncate">用配置页设定</span>
            </button>
            {/* 收藏 + 当前选中的音色，每个可点主体选用、点 🔊 试听 */}
            {chipVoices.map((v) => {
              const on = form.voice === v.id
              const playing = previewingId === v.id
              const unavail = v.available === false   // 探活确认当前账号未授权
              return (
                <div key={v.id}
                  className={`relative text-left px-2.5 py-1.5 rounded-lg border transition-colors cursor-pointer ${
                    on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30'
                       : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'} ${
                    unavail ? 'opacity-45' : ''}`}
                  title={unavail ? '当前火山账号未授权此音色' : undefined}
                  onClick={() => set({ voice: v.id })}>
                  <div className="flex items-center justify-between gap-1">
                    <span className={`text-[13px] font-medium truncate ${on ? 'text-brand-300' : 'text-slate-200'}`}>{v.name}</span>
                    {unavail
                      ? <span className="shrink-0 text-[10px] text-amber-500/80" title="未授权">未授权</span>
                      : <button type="button" title="试听"
                          onClick={(e) => { e.stopPropagation(); previewVoice(v.id) }}
                          className="shrink-0 text-slate-400 hover:text-brand-300 text-xs">{playing ? '■' : '🔊'}</button>}
                  </div>
                  <span className="block text-[10px] text-slate-500 truncate" title={v.tag}>{v.tag}</span>
                </div>
              )
            })}
            {/* 更多音色 → 弹窗 */}
            <button type="button" onClick={() => setShowVoicePicker(true)}
              className="text-center px-2.5 py-1.5 rounded-lg border border-dashed border-slate-600 text-slate-400 hover:text-slate-200 hover:border-slate-500 text-[13px]">
              更多音色…
            </button>
          </div>
          <p className="mt-1 text-[10px] text-slate-600">点 🔊 试听 · ★ 收藏常用音色 · 可用性取决于火山账号授权。</p>
          {previewError && (
            <p className="mt-1 text-[11px] text-red-400">试听失败：{previewError}</p>
          )}
        </div>

        <div>
          <span className="text-sm text-slate-400">动画模板 <span className="text-[11px] text-slate-600">· 镜头入场+转场风格，每镜头自动变化</span></span>
          <div className="mt-2 grid grid-cols-4 gap-2">
            {draftTemplates.map((t) => {
              const on = t.key === 'none'
                ? form.enable_animations === false
                : (form.enable_animations !== false && (form.draft_template || 'guofeng') === t.key)
              return (
                <button key={t.key} type="button"
                  onClick={() => set(t.key === 'none'
                    ? { enable_animations: false, draft_template: 'none' }
                    : { enable_animations: true, draft_template: t.key })}
                  className={`text-left px-2.5 py-1.5 rounded-lg border transition-colors ${
                    on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30'
                       : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'}`}>
                  <span className={`text-[13px] font-medium ${on ? 'text-brand-300' : 'text-slate-200'}`}>{t.name}</span>
                  <span className="block text-[10px] text-slate-500 truncate" title={t.desc}>{t.desc}</span>
                </button>
              )
            })}
          </div>
        </div>

        <div className="border-t border-slate-800 pt-4">
          <button type="button" onClick={() => setShowAdvanced((v) => !v)}
            className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200 transition-colors">
            <span className={`transition-transform ${showAdvanced ? 'rotate-90' : ''}`}>▶</span>
            高级选项（模块 / 成本 / 字幕）
          </button>

          {showAdvanced && (
            <div className="mt-4 space-y-4">
              <div>
                <span className="text-sm text-slate-400">可选模块（A 清洗 / B 改写 / G 合成为必选）</span>
                <div className="mt-2 flex flex-wrap gap-2">
                  {OPTIONAL_MODULES.map((m) => {
                    const on = form.modules.includes(m.key)
                    return (
                      <button key={m.key} type="button" onClick={() => toggleModule(m.key)}
                        className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${
                          on ? 'bg-brand-600/15 border-brand-500 text-brand-300'
                             : 'border-slate-700 text-slate-500 hover:border-slate-600'
                        }`}>{m.name}</button>
                    )
                  })}
                </div>
              </div>

              <div>
                <span className="text-sm text-slate-400">处理模式 <span className="text-[11px] text-slate-600">· 决定文案怎么来，后续生图/配音/成片一致</span></span>
                <div className="mt-2 grid grid-cols-3 gap-2">
                  {PROCESSING_MODES.map((m) => {
                    const on = (form.processing_mode ?? 'full_auto') === m.key
                    return (
                      <button key={m.key} type="button"
                        onClick={() => set({ processing_mode: m.key })}
                        className={`text-left px-2.5 py-2 rounded-lg border transition-colors ${
                          on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30' : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'
                        }`}>
                        <div className={`text-sm font-medium ${on ? 'text-brand-300' : 'text-slate-200'}`}>{m.name}</div>
                        <div className="text-[10px] text-slate-500 mt-0.5">{m.desc}</div>
                      </button>
                    )
                  })}
                </div>
                {form.processing_mode !== 'full_auto' && (
                  <p className="mt-1.5 text-[11px] text-slate-500">此模式不做 AI 改写，「改写强度 / 叙事视角 / 带货模式」不可调，已隐藏。</p>
                )}
              </div>

              <div>
                <span className="text-sm text-slate-400">暂停确认 <span className="text-[11px] text-slate-600">· 在关键步骤后停下等你确认</span></span>
                <div className="mt-2 grid grid-cols-4 gap-2">
                  {PAUSE_MODES.map((m) => {
                    const on = (form.pause_mode ?? 'none') === m.key
                    return (
                      <button key={m.key} type="button"
                        onClick={() => set({ pause_mode: m.key })}
                        className={`text-left px-2.5 py-2 rounded-lg border transition-colors ${
                          on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30' : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'
                        }`}>
                        <div className={`text-[13px] font-medium ${on ? 'text-brand-300' : 'text-slate-200'}`}>{m.name}</div>
                        <div className="text-[10px] text-slate-500 mt-0.5">{m.desc}</div>
                      </button>
                    )
                  })}
                </div>
                {form.pause_mode === 'custom' && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {PAUSE_STEPS.map((s) => {
                      const skipped = (s.skipModes ?? []).includes(form.processing_mode ?? 'full_auto')
                        || (s.needsModule != null && !form.modules.includes(s.needsModule))
                      const on = (form.pause_steps ?? []).includes(s.key)
                      return (
                        <button key={s.key} type="button" disabled={skipped}
                          onClick={() => {
                            const cur = form.pause_steps ?? []
                            set({ pause_steps: cur.includes(s.key) ? cur.filter((k) => k !== s.key) : [...cur, s.key] })
                          }}
                          className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${
                            skipped ? 'border-slate-800 text-slate-700 cursor-not-allowed line-through'
                              : on ? 'bg-brand-600/15 border-brand-500 text-brand-300'
                                   : 'border-slate-700 text-slate-400 hover:border-slate-600'
                          }`} title={skipped ? '当前处理模式/模块下此步骤被跳过' : ''}>{s.name}</button>
                      )
                    })}
                  </div>
                )}
              </div>

              {/* 改写强度 / 叙事视角已移到上方「二创方式」区，调完即可预览，所见即所得 */}

              <label className="block">
                <span className="text-sm text-slate-400">配音语速：{(form.voice_speed ?? 1).toFixed(2)}×</span>
                <input type="range" min={0.5} max={2} step={0.05} className="mt-2 w-full accent-brand-500"
                  value={form.voice_speed ?? 1}
                  onChange={(e) => set({ voice_speed: Number(e.target.value) })} />
                <div className="flex justify-between text-[10px] text-slate-600"><span>0.5× 慢</span><span>1× 正常</span><span>2× 快</span></div>
              </label>

              {/* 带货模式：仅全自动模式有效（改写时才决定带货段落，整块隐藏，对齐竞品） */}
              {(form.processing_mode ?? 'full_auto') === 'full_auto' && (
              <div>
                <span className="text-sm text-slate-400">带货模式</span>
                <div className="mt-2 flex gap-2">
                  {MONETIZATIONS.map((m) => {
                    const on = form.monetization_mode === m.key
                    return (
                      <button key={m.key} type="button" onClick={() => set({ monetization_mode: m.key })}
                        className={`flex-1 px-2 py-1.5 rounded-lg border text-center transition-colors ${
                          on ? 'bg-brand-600/15 border-brand-500 ring-1 ring-brand-500/30' : 'bg-slate-800/40 border-slate-700 hover:border-slate-600'
                        }`}>
                        <div className={`text-sm ${on ? 'text-brand-300' : 'text-slate-200'}`}>{m.name}</div>
                        <div className="text-[10px] text-slate-500">{m.desc}</div>
                      </button>
                    )
                  })}
                </div>
              </div>
              )}

              <div>
                <span className="text-sm text-slate-400">背景音乐 BGM（仅 mp4 合成时混入，剪映草稿可在剪映里加）</span>
                {bgmFiles.length === 0 ? (
                  <p className="mt-1 text-xs text-slate-600">
                    未发现可用音乐。在<Link to="/config" className="text-brand-400 hover:underline">系统配置</Link>里设置「背景音乐目录」，把 mp3 放进去即可。
                  </p>
                ) : (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button type="button" onClick={() => set({ bgm: '' })}
                      className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${
                        !form.bgm ? 'bg-brand-600/15 border-brand-500 text-brand-300' : 'border-slate-700 text-slate-500 hover:border-slate-600'
                      }`}>无</button>
                    {bgmFiles.map((f) => {
                      const on = form.bgm === f
                      return (
                        <button key={f} type="button" onClick={() => set({ bgm: f })}
                          className={`px-3 py-1.5 rounded-lg text-sm border transition-colors max-w-[180px] truncate ${
                            on ? 'bg-brand-600/15 border-brand-500 text-brand-300' : 'border-slate-700 text-slate-400 hover:border-slate-600'
                          }`} title={f}>🎵 {f}</button>
                      )
                    })}
                  </div>
                )}
              </div>

              <div className="grid grid-cols-3 gap-4">
                <label className="block">
                  <span className="text-sm text-slate-400">成本上限（元）</span>
                  <input type="number" min={0} step={0.1} className="field"
                    value={form.cost_limit}
                    onChange={(e) => { set({ cost_limit: Number(e.target.value) }); setEst(null) }} />
                </label>
                <label className="block">
                  <span className="text-sm text-slate-400">超时上限（分钟）</span>
                  <input type="number" min={1} max={60} step={1} className="field"
                    value={Math.round((form.time_limit ?? 900) / 60)}
                    onChange={(e) => {
                      const m = Math.min(60, Math.max(1, Number(e.target.value) || 1))
                      set({ time_limit: m * 60 })
                    }} />
                </label>
                <div className="flex items-end gap-4 pb-1">
                  <label className="flex items-center gap-2 text-sm text-slate-300">
                    <input type="checkbox" checked={form.enable_subtitles}
                      onChange={(e) => set({ enable_subtitles: e.target.checked })} /> 字幕
                  </label>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {missingKeys.length > 0 && (
        <div className="mt-4 px-4 py-3 rounded-xl text-sm bg-amber-500/10 border border-amber-500/30 text-amber-300 flex items-center justify-between gap-3">
          <span>还有 {missingKeys.length} 项凭证未配置（{missingKeys.join('、')}），生成会失败。</span>
          <Link to="/config" className="shrink-0 px-3 py-1.5 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 font-medium">前往配置 →</Link>
        </div>
      )}

      {est && (
        <div className={`mt-4 px-4 py-3 rounded-xl text-sm border ${
          est.daily_cap_reached ? 'bg-red-500/10 text-red-300 border-red-500/30' : 'bg-brand-600/10 text-brand-300 border-brand-500/30'
        }`}>
          预估成本：<b>{est.estimated_cost.toFixed(4)} 元</b>
          {est.daily_cap_reached && '（已达每日成本上限，无法提交）'}
        </div>
      )}

      {/* 固定底部操作栏 */}
      <div className="sticky bottom-0 -mx-10 mt-6 px-10 py-3 bg-slate-950/90 backdrop-blur border-t border-slate-800 flex items-center justify-between">
        <span className="text-xs text-slate-500">
          预计耗时约 {Math.round((form.time_limit ?? 900) / 60)} 分钟 · 成本上限 {form.cost_limit} 元
        </span>
        <div className="flex gap-3">
          <button onClick={clearDraft} disabled={submitting} className="btn-ghost" title="清空本页所有填写内容">
            清空重填
          </button>
          <button onClick={doEstimate} disabled={estimating} className="btn-ghost">
            {estimating ? '预估中…' : '成本预估'}
          </button>
          <button onClick={submit} disabled={submitting || est?.daily_cap_reached} className="btn-primary">
            {submitting ? '提交中…' : '开始生成'}
          </button>
        </div>
      </div>

      {showVoicePicker && (
        <VoicePicker
          voices={voices} categories={voiceCats}
          value={form.voice ?? ''} favorites={favorites}
          onSelect={(id) => set({ voice: id })}
          onToggleFav={toggleFav}
          onClose={() => setShowVoicePicker(false)}
        />
      )}
    </div>
  )
}
