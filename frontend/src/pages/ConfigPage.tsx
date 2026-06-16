// 配置页：LLM / 图像供应商与 Key、每日成本上限、并发。Key 写入后只回显掩码。
import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ConfigOut, ConfigUpdate } from '../api/types'

const LLM_PROVIDERS = ['deepseek', 'openai', 'qwen', 'doubao']
const IMAGE_PROVIDERS = ['doubao', 'openai']
const COLLECT_PROVIDERS = ['tikhub']
const ASR_PROVIDERS = ['volcano', 'siliconflow']
const TTS_PROVIDERS = ['edge_local', 'volcano', 'siliconflow', 'yuntts_edge']

export default function ConfigPage() {
  const [cfg, setCfg] = useState<ConfigOut | null>(null)
  const [form, setForm] = useState<ConfigUpdate>({})
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [testing, setTesting] = useState(false)
  const [testingTts, setTestingTts] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  // 字段级自动保存状态：key → 该字段当前是 保存中/已保存/出错。
  const [fieldStatus, setFieldStatus] = useState<Record<string, 'saving' | 'saved' | 'error'>>({})
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({})

  useEffect(() => {
    api.getConfig().then(setCfg).catch((e: ApiError) =>
      setMsg({ type: 'err', text: e.message }))
  }, [])

  const set = (patch: Partial<ConfigUpdate>) => setForm((f) => ({ ...f, ...patch }))

  // 自动保存单个字段：失焦(文本/密码)或改动(下拉/数字)即调用。成功后局部更新 cfg、
  // 字段旁实时显示「已保存」；Key 类字段保存后清空输入框（回显掩码）。
  async function saveField<K extends keyof ConfigUpdate>(key: K, value: ConfigUpdate[K]) {
    // Key 类字段留空表示「不修改」，不触发保存。
    if (typeof key === 'string' && key.endsWith('_api_key') && !String(value ?? '').trim()) return
    setFieldStatus((s) => ({ ...s, [key]: 'saving' }))
    setFieldErr((e) => ({ ...e, [key]: '' }))
    try {
      const updated = await api.updateConfig({ [key]: value } as ConfigUpdate)
      setCfg(updated)
      if (typeof key === 'string' && key.endsWith('_api_key')) {
        setForm((f) => ({ ...f, [key]: '' }))
      }
      setFieldStatus((s) => ({ ...s, [key]: 'saved' }))
      // 「已保存」提示 2.5 秒后淡出，避免长期占着；用 key 比对防止被后续保存覆盖误清。
      setTimeout(() => {
        setFieldStatus((s) => (s[key as string] === 'saved' ? { ...s, [key]: undefined as never } : s))
      }, 2500)
    } catch (e) {
      setFieldStatus((s) => ({ ...s, [key]: 'error' }))
      setFieldErr((er) => ({ ...er, [key]: (e as ApiError).message }))
    }
  }

  // 字段旁的实时状态小标（保存中 / ✓ 已保存 / ✗ 错误）。
  function Status({ k }: { k: keyof ConfigUpdate }) {
    const s = fieldStatus[k as string]
    if (s === 'saving') return <span className="text-[11px] text-slate-500 ml-2">保存中…</span>
    if (s === 'saved') return <span className="text-[11px] text-emerald-400 ml-2">✓ 已保存</span>
    if (s === 'error') return <span className="text-[11px] text-red-400 ml-2">✗ {fieldErr[k as string] || '保存失败'}</span>
    return null
  }


  async function test() {
    setTesting(true)
    setMsg(null)
    try {
      const r = await api.testApi()
      setMsg({ type: 'ok', text: `连通正常，模型回复：${r.reply}` })
    } catch (e) {
      setMsg({ type: 'err', text: (e as ApiError).message })
    } finally {
      setTesting(false)
    }
  }

  async function testTts() {
    setTestingTts(true)
    setMsg(null)
    try {
      const r = await api.testTts()
      setMsg({ type: 'ok', text: `TTS 连通正常（${r.provider}），合成 ${r.audio_bytes} 字节音频` })
    } catch (e) {
      setMsg({ type: 'err', text: (e as ApiError).message })
    } finally {
      setTestingTts(false)
    }
  }

  async function previewTts() {
    setPreviewing(true)
    setMsg(null)
    try {
      // 用表单里未保存的音色优先试听；语速固定 1.0（语速在新建任务页按任务设）。
      const blob = await api.previewTts({ voice: form.tts_voice ?? cfg?.tts_voice ?? undefined })
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => URL.revokeObjectURL(url)
      await audio.play()
      setMsg({ type: 'ok', text: '正在播放试听…' })
    } catch (e) {
      setMsg({ type: 'err', text: (e as ApiError).message })
    } finally {
      setPreviewing(false)
    }
  }

  if (!cfg) return <div className="text-slate-500">加载中…</div>

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <h1 className="text-2xl font-bold text-slate-100">系统配置</h1>

      {msg && (
        <div className={`px-4 py-2.5 rounded-lg text-sm border ${
          msg.type === 'ok' ? 'bg-green-50 text-green-700 border-green-100' : 'bg-red-50 text-red-700 border-red-100'
        }`}>{msg.text}</div>
      )}

      <div className="card space-y-4">
        <div className="font-semibold text-slate-100">文案模型（LLM）<Status k="llm_provider" /><Status k="llm_model" /><Status k="llm_api_key" /></div>
        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="text-sm text-slate-400">供应商</span>
            <select className="field"
              defaultValue={cfg.llm_provider}
              onChange={(e) => { set({ llm_provider: e.target.value }); saveField('llm_provider', e.target.value) }}>
              {LLM_PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="text-sm text-slate-600">模型</span>
            <input className="field"
              defaultValue={cfg.llm_model}
              onChange={(e) => set({ llm_model: e.target.value })}
              onBlur={(e) => saveField('llm_model', e.target.value)} />
          </label>
        </div>
        <label className="block">
          <span className="text-sm text-slate-400">API Key</span>
          <input type="password" className="field"
            placeholder={cfg.llm_api_key_mask || '未配置'}
            value={form.llm_api_key ?? ''}
            onChange={(e) => set({ llm_api_key: e.target.value })}
            onBlur={(e) => saveField('llm_api_key', e.target.value)} />
          {cfg.llm_api_key_mask && (
            <span className="text-xs text-slate-400">当前：{cfg.llm_api_key_mask}（留空不修改）</span>
          )}
        </label>
      </div>

      <div className="card space-y-4">
        <div className="font-semibold text-slate-100">配图模型（图像）<Status k="image_provider" /><Status k="image_api_key" /></div>
        <label className="block">
          <span className="text-sm text-slate-400">供应商</span>
          <select className="field"
            defaultValue={cfg.image_provider}
            onChange={(e) => { set({ image_provider: e.target.value }); saveField('image_provider', e.target.value) }}>
            {IMAGE_PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-sm text-slate-400">API Key</span>
          <input type="password" className="field"
            placeholder={cfg.image_api_key_mask || '未配置'}
            value={form.image_api_key ?? ''}
            onChange={(e) => set({ image_api_key: e.target.value })}
            onBlur={(e) => saveField('image_api_key', e.target.value)} />
        </label>
        <label className="block">
          <span className="text-sm text-slate-400">模型名</span>
          <input type="text" className="field"
            placeholder={cfg.image_model || 'doubao-seedream-4-5-251128'}
            value={form.image_model ?? ''}
            onChange={(e) => set({ image_model: e.target.value })}
            onBlur={(e) => saveField('image_model', e.target.value)} />
          <span className="block text-[11px] text-slate-500 mt-1">
            填中转站支持的模型 ID，如 doubao-seedream-4-5-251128（默认）。留空用默认。
          </span>
        </label>
        <label className="block">
          <span className="text-sm text-slate-400">接口地址（中转站，可选）</span>
          <input type="text" className="field"
            placeholder={cfg.image_base_url || '留空走豆包官方；填中转站如 https://api.apicore.ai/v1/images/generations'}
            value={form.image_base_url ?? ''}
            onChange={(e) => set({ image_base_url: e.target.value })}
            onBlur={(e) => saveField('image_base_url', e.target.value)} />
          <span className="block text-[11px] text-slate-500 mt-1">
            填 OpenAI 兼容的中转站地址可降单价（逐张全分辨率、风格与参考图正常）。留空用官方。
          </span>
        </label>
        <label className="block">
          <span className="text-sm text-slate-400">配图单价（元/张，可选）</span>
          <input type="number" step="0.01" min="0" className="field"
            placeholder={cfg.image_unit_price != null ? String(cfg.image_unit_price) : '留空用内置缺省价（豆包0.25）；按中转站实价填，如 0.12'}
            value={form.image_unit_price ?? ''}
            onChange={(e) => set({ image_unit_price: e.target.value === '' ? null : Number(e.target.value) })}
            onBlur={(e) => saveField('image_unit_price', e.target.value === '' ? null : Number(e.target.value))} />
          <span className="block text-[11px] text-slate-500 mt-1">
            成本核算与上限校验按此单价算（每次请求一张）。中转站实价比内置缺省价低很多，
            填对才不会虚高误判超限。留空或填0用缺省价。九宫格按 ceil(张数/9) 折算请求数。
          </span>
        </label>
      </div>

      <div className="card space-y-4">
        <div className="font-semibold text-slate-100">视频采集（贴链接自动取素材，可选）<Status k="collect_provider" /><Status k="collect_api_key" /><Status k="proxy_url" /></div>
        <div className="text-[11px] text-slate-500 -mt-2">支持抖音/快手/小红书/B站/微博/视频号/TikTok，按链接自动识别平台（TikHub）。</div>
        <label className="block">
          <span className="text-sm text-slate-400">供应商</span>
          <select className="field"
            defaultValue={cfg.collect_provider}
            onChange={(e) => { set({ collect_provider: e.target.value }); saveField('collect_provider', e.target.value) }}>
            {COLLECT_PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-sm text-slate-400">API Key</span>
          <input type="password" className="field"
            placeholder={cfg.collect_api_key_mask || '未配置'}
            value={form.collect_api_key ?? ''}
            onChange={(e) => set({ collect_api_key: e.target.value })}
            onBlur={(e) => saveField('collect_api_key', e.target.value)} />
        </label>
        <label className="block">
          <span className="text-sm text-slate-400">出站代理（可选）</span>
          <input type="text" className="field"
            placeholder={cfg.proxy_url || '如 http://127.0.0.1:7890，留空则直连'}
            value={form.proxy_url ?? ''}
            onChange={(e) => set({ proxy_url: e.target.value })}
            onBlur={(e) => saveField('proxy_url', e.target.value)} />
          <span className="block text-[11px] text-slate-600 mt-1">
            TikHub 等境外采集接口直连不通（如报「远程主机强迫关闭连接」）时填此项，让采集请求走代理。
          </span>
        </label>
      </div>

      <div className="card space-y-4">
        <div className="font-semibold text-slate-100">语音转写 ASR（视频自动转逐字稿，可选）<Status k="asr_provider" /><Status k="asr_api_key" /></div>
        <label className="block">
          <span className="text-sm text-slate-400">供应商</span>
          <select className="field"
            defaultValue={cfg.asr_provider}
            onChange={(e) => { set({ asr_provider: e.target.value }); saveField('asr_provider', e.target.value) }}>
            {ASR_PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-sm text-slate-400">API Key</span>
          <input type="password" className="field"
            placeholder={cfg.asr_api_key_mask || '未配置'}
            value={form.asr_api_key ?? ''}
            onChange={(e) => set({ asr_api_key: e.target.value })}
            onBlur={(e) => saveField('asr_api_key', e.target.value)} />
          <span className="block text-[11px] text-slate-600 mt-1">
            {(form.asr_provider ?? cfg.asr_provider) === 'volcano'
              ? '火山：填「豆包录音文件识别大模型2.0」的 API Key（控制台 x-api-key，无需 appid）。需先开通该模型。'
              : '硅基流动：填平台 API Key（SenseVoice 转写）。'}
          </span>
        </label>
      </div>

      <div className="card space-y-4">
        <div className="font-semibold text-slate-100">配音 TTS（自动配音成片，可选）<Status k="tts_provider" /><Status k="tts_voice" /><Status k="tts_api_key" /><Status k="tts_appid" /></div>
        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="text-sm text-slate-400">供应商</span>
            <select className="field"
              defaultValue={cfg.tts_provider}
              onChange={(e) => { set({ tts_provider: e.target.value }); saveField('tts_provider', e.target.value) }}>
              {TTS_PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="text-sm text-slate-400">音色 voice</span>
            <div className="flex gap-2">
              <input className="field flex-1"
                placeholder="留空用默认音色"
                defaultValue={cfg.tts_voice}
                onChange={(e) => set({ tts_voice: e.target.value })}
                onBlur={(e) => saveField('tts_voice', e.target.value)} />
              <button type="button" onClick={previewTts} disabled={previewing}
                className="mt-1 px-3 rounded-lg border border-slate-700 text-slate-200 text-sm whitespace-nowrap bg-slate-800/40 hover:bg-slate-800 disabled:opacity-50">
                {previewing ? '试听中…' : '▶ 试听'}
              </button>
            </div>
          </label>
        </div>
        <label className="block">
          <span className="text-sm text-slate-600">API Key（火山填 access_token）</span>
          <input type="password" className="field"
            placeholder={cfg.tts_api_key_mask || '未配置'}
            value={form.tts_api_key ?? ''}
            onChange={(e) => set({ tts_api_key: e.target.value })}
            onBlur={(e) => saveField('tts_api_key', e.target.value)} />
        </label>
        <label className="block">
          <span className="text-sm text-slate-600">App ID（仅火山 volcano 需要）</span>
          <input className="mt-1 w-full border rounded-lg px-3 py-2"
            placeholder={cfg.tts_appid || '火山控制台的 appid'}
            defaultValue={cfg.tts_appid}
            onChange={(e) => set({ tts_appid: e.target.value })}
            onBlur={(e) => saveField('tts_appid', e.target.value)} />
        </label>
      </div>

      <div className="card grid grid-cols-2 gap-4 mt-4">
        <label className="block">
          <span className="text-sm text-slate-600">每日成本上限（元）<Status k="daily_cost_cap" /></span>
          <input type="number" min={0} step={1} className="mt-1 w-full border rounded-lg px-3 py-2"
            defaultValue={cfg.daily_cost_cap}
            onChange={(e) => { set({ daily_cost_cap: Number(e.target.value) }); saveField('daily_cost_cap', Number(e.target.value)) }} />
        </label>
        <label className="block">
          <span className="text-sm text-slate-600">最大并行任务数（1-10）<Status k="max_concurrent_tasks" /></span>
          <input type="number" min={1} max={10} className="mt-1 w-full border rounded-lg px-3 py-2"
            defaultValue={cfg.max_concurrent_tasks}
            onChange={(e) => { set({ max_concurrent_tasks: Number(e.target.value) }); saveField('max_concurrent_tasks', Number(e.target.value)) }} />
          <span className="text-xs text-slate-500 mt-1 block">同时执行几个任务，超出的自动排队。改后立即生效。</span>
        </label>
        <label className="block">
          <span className="text-sm text-slate-600">单任务配图并发（1-10）<Status k="concurrency" /></span>
          <input type="number" min={1} max={10} className="mt-1 w-full border rounded-lg px-3 py-2"
            defaultValue={cfg.concurrency}
            onChange={(e) => { set({ concurrency: Number(e.target.value) }); saveField('concurrency', Number(e.target.value)) }} />
          <span className="text-xs text-slate-500 mt-1 block">一个任务内同时生成几张图。</span>
        </label>
        <div className="col-span-2 text-xs text-amber-500/90 bg-amber-500/5 rounded-lg px-3 py-2">
          ⚠ 同时发出的图片请求 ≈ 并行任务数 × 配图并发。两者都设大容易触发绘图平台限流（429），
          导致更多失败。建议两项乘积控制在 10 以内（如 3 × 3）。
        </div>
      </div>

      <div className="card space-y-2 mt-4">
        <div className="font-semibold text-slate-100">剪映草稿目录（成片自动写入，可直接打开编辑）<Status k="jianying_draft_dir" /></div>
        <label className="block">
          <div className="flex gap-2">
            <input className="field flex-1"
              placeholder={cfg.jianying_draft_dir || 'D:\\下载\\JianyingPro Drafts'}
              value={form.jianying_draft_dir ?? cfg.jianying_draft_dir}
              onChange={(e) => set({ jianying_draft_dir: e.target.value })}
              onBlur={(e) => saveField('jianying_draft_dir', e.target.value)} />
            {window.desktop && (
              <button type="button"
                className="mt-1 px-4 rounded-lg border border-slate-700 text-slate-200 text-sm whitespace-nowrap bg-slate-800/40 hover:bg-slate-800"
                onClick={async () => {
                  const dir = await window.desktop!.pickFolder()
                  if (dir) { set({ jianying_draft_dir: dir }); saveField('jianying_draft_dir', dir) }
                }}>
                浏览…
              </button>
            )}
          </div>
          <span className="text-xs text-slate-500">
            填本地剪映「草稿存放位置」（剪映专业版 → 设置可查）。填了成片直接写入，剪映重启即可见、可编辑；留空则仅生成草稿文件供下载。
          </span>
        </label>
      </div>

      <div className="card space-y-2 mt-4">
        <div className="font-semibold text-slate-100">任务存储目录（图片/音频等中间产物落盘位置）<Status k="task_storage_dir" /></div>
        <label className="block">
          <div className="flex gap-2">
            <input className="field flex-1"
              placeholder={cfg.task_storage_dir || '留空用默认（应用数据目录）'}
              value={form.task_storage_dir ?? cfg.task_storage_dir}
              onChange={(e) => set({ task_storage_dir: e.target.value })}
              onBlur={(e) => saveField('task_storage_dir', e.target.value)} />
            {window.desktop && (
              <button type="button"
                className="mt-1 px-4 rounded-lg border border-slate-700 text-slate-200 text-sm whitespace-nowrap bg-slate-800/40 hover:bg-slate-800"
                onClick={async () => {
                  const dir = await window.desktop!.pickFolder()
                  if (dir) { set({ task_storage_dir: dir }); saveField('task_storage_dir', dir) }
                }}>
                浏览…
              </button>
            )}
          </div>
          <span className="text-xs text-slate-500">
            任务产物较占空间（每个任务几十 MB）。默认存在应用数据目录；C 盘紧张可改到大盘。改动只影响之后的新任务。
          </span>
        </label>
      </div>

      <div className="card space-y-2 mt-4">
        <div className="font-semibold text-slate-100">背景音乐目录（可选，mp4 合成时混入 BGM）<Status k="bgm_dir" /></div>
        <label className="block">
          <div className="flex gap-2">
            <input className="field flex-1"
              placeholder={cfg.bgm_dir || '留空则不启用 BGM'}
              value={form.bgm_dir ?? cfg.bgm_dir}
              onChange={(e) => set({ bgm_dir: e.target.value })}
              onBlur={(e) => saveField('bgm_dir', e.target.value)} />
            {window.desktop && (
              <button type="button"
                className="mt-1 px-4 rounded-lg border border-slate-700 text-slate-200 text-sm whitespace-nowrap bg-slate-800/40 hover:bg-slate-800"
                onClick={async () => {
                  const dir = await window.desktop!.pickFolder()
                  if (dir) { set({ bgm_dir: dir }); saveField('bgm_dir', dir) }
                }}>
                浏览…
              </button>
            )}
          </div>
          <span className="text-xs text-slate-500">
            把 mp3/wav 音乐文件放进这个目录，新建任务时就能在「高级选项」里选作背景音乐（自动降到 15% 音量衬底，仅 mp4 输出生效）。
          </span>
        </label>
      </div>

      <div className="flex gap-3 mt-5">
        <button onClick={test} disabled={testing}
          className="px-5 py-2 rounded-lg border border-slate-300 text-sm font-medium disabled:opacity-50">
          {testing ? '测试中…' : '测试 LLM 连通'}
        </button>
        <button onClick={testTts} disabled={testingTts}
          className="px-5 py-2 rounded-lg border border-slate-300 text-sm font-medium disabled:opacity-50">
          {testingTts ? '测试中…' : '测试 TTS 连通'}
        </button>
        <span className="self-center text-[12px] text-slate-500">改动即时自动保存，无需手动保存。</span>
      </div>
    </div>
  )
}
