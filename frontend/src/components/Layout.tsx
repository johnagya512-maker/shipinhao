// 应用外壳：顶部自绘标题栏 + 左侧竖向边栏 + 右侧内容区（深色主题）。
import { NavLink, Outlet } from 'react-router-dom'
import TitleBar from './TitleBar'

const items = [
  { to: '/tasks/new', label: '新建任务', icon: '✏️', kbd: 'N' },
  { to: '/tasks', label: '任务列表', icon: '📋', end: true },
  { to: '/config', label: '系统配置', icon: '⚙️' },
]

const navClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
    isActive
      ? 'bg-brand-600/15 text-brand-300 ring-1 ring-brand-500/30'
      : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
  }`

export default function Layout() {
  return (
    <div className="h-screen flex flex-col">
      <TitleBar />
      <div className="flex-1 min-h-0 flex">
      <aside className="w-60 shrink-0 bg-slate-900/80 border-r border-slate-800 flex flex-col overflow-y-auto">
        <div className="flex items-center gap-2.5 px-5 h-16 border-b border-slate-800">
          <div className="w-8 h-8 rounded-lg bg-brand-600 text-white grid place-items-center font-bold text-sm shadow-sm shadow-brand-600/40">书</div>
          <div className="leading-tight">
            <div className="font-semibold text-slate-100 text-sm">图书带货 AI</div>
            <div className="text-[11px] text-slate-500">v1.0 工作流</div>
          </div>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1">
          {items.map((it) => (
            <NavLink key={it.to} to={it.to} end={it.end} className={navClass}>
              <span className="text-base">{it.icon}</span>
              <span className="flex-1">{it.label}</span>
              {it.kbd && (
                <kbd className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 border border-slate-700">{it.kbd}</kbd>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="px-5 py-4 border-t border-slate-800 text-[11px] text-slate-600">
          本地运行 · BYOK
        </div>
      </aside>
      <main className="flex-1 min-w-0 px-10 py-8 max-w-5xl overflow-y-auto">
        <Outlet />
      </main>
      </div>
    </div>
  )
}
