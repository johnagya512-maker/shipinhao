// 配置页：LLM / 图像供应商与 Key、每日成本上限、并发。Key 写入后只回显掩码。
import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ConfigOut, ConfigUpdate } from '../api/types'

const LLM_PROVIDERS = ['deepseek', 'openai', 'qwen', 'doubao']
const IMAGE_PROVIDERS = ['doubao', 'openai']

export default function ConfigPage() {
  const [cfg, setCfg] = useState<ConfigOut | null>(null)
  const [form, setForm] = useState<ConfigUpdate>({})
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    api.getConfig().then(setCfg).catch((e: ApiError) =>
      setMsg({ type: 'err', text: e.message }))
  }, [])

  const set = (patch: Partial<ConfigUpdate>) => setForm((f) => ({ ...f, ...patch }))

  async function save() {
    setSaving(true)
    setMsg(null)
    try {
      const updated = await api.updateConfig(form)
      setCfg(updated)
      setForm((f) => ({ ...f, llm_api_key: '', image_api_key: '' }))
      setMsg({ type: 'ok', text: '已保存' })
    } catch (e) {
      setMsg({ type: 'err', text: (e as ApiError).message })
    } finally {
      setSaving(false)
    }
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

  if (!cfg) return <div className="text-slate-500">加载中…</div>

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-semibold mb-4">系统配置</h1>

      {msg && (
        <div className={`mb-4 px-4 py-2 rounded-lg text-sm ${
          msg.type === 'ok' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
        }`}>{msg.text}</div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
        <div className="font-medium text-slate-800">文案模型（LLM）</div>
        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="text-sm text-slate-600">供应商</span>
            <select className="mt-1 w-full border rounded-lg px-3 py-2"
              defaultValue={cfg.llm_provider}
              onChange={(e) => set({ llm_provider: e.target.value })}>
              {LLM_PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="text-sm text-slate-600">模型</span>
            <input className="mt-1 w-full border rounded-lg px-3 py-2"
              defaultValue={cfg.llm_model}
              onChange={(e) => set({ llm_model: e.target.value })} />
          </label>
        </div>
        <label className="block">
          <span className="text-sm text-slate-600">API Key</span>
          <input type="password" className="mt-1 w-full border rounded-lg px-3 py-2"
            placeholder={cfg.llm_api_key_mask || '未配置'}
            value={form.llm_api_key ?? ''}
            onChange={(e) => set({ llm_api_key: e.target.value })} />
          {cfg.llm_api_key_mask && (
            <span className="text-xs text-slate-400">当前：{cfg.llm_api_key_mask}（留空不修改）</span>
          )}
        </label>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4 mt-4">
        <div className="font-medium text-slate-800">配图模型（图像）</div>
        <label className="block">
          <span className="text-sm text-slate-600">供应商</span>
          <select className="mt-1 w-full border rounded-lg px-3 py-2"
            defaultValue={cfg.image_provider}
            onChange={(e) => set({ image_provider: e.target.value })}>
            {IMAGE_PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-sm text-slate-600">API Key</span>
          <input type="password" className="mt-1 w-full border rounded-lg px-3 py-2"
            placeholder={cfg.image_api_key_mask || '未配置'}
            value={form.image_api_key ?? ''}
            onChange={(e) => set({ image_api_key: e.target.value })} />
        </label>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 grid grid-cols-2 gap-4 mt-4">
        <label className="block">
          <span className="text-sm text-slate-600">每日成本上限（元）</span>
          <input type="number" min={0} step={1} className="mt-1 w-full border rounded-lg px-3 py-2"
            defaultValue={cfg.daily_cost_cap}
            onChange={(e) => set({ daily_cost_cap: Number(e.target.value) })} />
        </label>
        <label className="block">
          <span className="text-sm text-slate-600">并发数（1-10）</span>
          <input type="number" min={1} max={10} className="mt-1 w-full border rounded-lg px-3 py-2"
            defaultValue={cfg.concurrency}
            onChange={(e) => set({ concurrency: Number(e.target.value) })} />
        </label>
      </div>

      <div className="flex gap-3 mt-5">
        <button onClick={save} disabled={saving}
          className="px-5 py-2 rounded-lg bg-brand-600 text-white text-sm font-medium disabled:opacity-50">
          {saving ? '保存中…' : '保存配置'}
        </button>
        <button onClick={test} disabled={testing}
          className="px-5 py-2 rounded-lg border border-slate-300 text-sm font-medium disabled:opacity-50">
          {testing ? '测试中…' : '测试 LLM 连通'}
        </button>
      </div>
    </div>
  )
}
