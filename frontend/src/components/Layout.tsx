// 应用外壳：顶部导航 + 内容区。
import { NavLink, Outlet } from 'react-router-dom'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
    isActive ? 'bg-brand-600 text-white' : 'text-slate-600 hover:bg-slate-100'
  }`

export default function Layout() {
  return (
    <div className="min-h-full flex flex-col">
      <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center gap-2">
          <span className="font-semibold text-slate-900 mr-4">视频号图书带货 · AI 工作流</span>
          <NavLink to="/tasks/new" className={navClass}>新建任务</NavLink>
          <NavLink to="/tasks" end className={navClass}>任务列表</NavLink>
          <NavLink to="/config" className={navClass}>配置</NavLink>
        </div>
      </header>
      <main className="flex-1 max-w-5xl w-full mx-auto px-6 py-6">
        <Outlet />
      </main>
    </div>
  )
}
