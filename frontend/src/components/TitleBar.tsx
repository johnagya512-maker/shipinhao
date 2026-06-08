// 无边框窗口的自绘顶栏：颜色与界面完全一致，可拖拽，右侧最小化/最大化/关闭。
// 仅桌面端（window.desktop 存在）渲染；浏览器端返回 null。
export default function TitleBar() {
  if (!window.desktop) return null
  const d = window.desktop
  return (
    <div
      className="h-8 shrink-0 bg-slate-950 border-b border-slate-800 flex items-center
        justify-between pl-3 select-none"
      style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
    >
      <span className="text-xs text-slate-500">视频号图书带货 · AI 工作流</span>
      <div className="flex" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        <button onClick={() => d.minimize()}
          className="w-11 h-8 grid place-items-center text-slate-400 hover:bg-slate-800 transition-colors">
          <svg width="10" height="10" viewBox="0 0 10 10"><rect y="4.5" width="10" height="1" fill="currentColor" /></svg>
        </button>
        <button onClick={() => d.maximize()}
          className="w-11 h-8 grid place-items-center text-slate-400 hover:bg-slate-800 transition-colors">
          <svg width="10" height="10" viewBox="0 0 10 10"><rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" /></svg>
        </button>
        <button onClick={() => d.close()}
          className="w-11 h-8 grid place-items-center text-slate-400 hover:bg-red-600 hover:text-white transition-colors">
          <svg width="10" height="10" viewBox="0 0 10 10"><path d="M1 1 L9 9 M9 1 L1 9" stroke="currentColor" strokeWidth="1.2" /></svg>
        </button>
      </div>
    </div>
  )
}
