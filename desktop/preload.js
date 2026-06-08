// Preload：暴露给前端的安全接口。contextIsolation 下用 contextBridge。
const { contextBridge, ipcRenderer } = require('electron')

const API_PORT = 8765
contextBridge.exposeInMainWorld('__API_ORIGIN__', `http://127.0.0.1:${API_PORT}`)

// 桌面端专属能力：调用系统原生“选择文件夹”对话框，返回真实路径。
// 网页版没有此能力（浏览器安全限制），前端据 window.desktop 是否存在来决定显示“浏览”按钮。
contextBridge.exposeInMainWorld('desktop', {
  pickFolder: () => ipcRenderer.invoke('dialog:pickFolder'),
  // 无边框窗口的控制（前端自绘顶栏按钮调用）。
  minimize: () => ipcRenderer.invoke('win:minimize'),
  maximize: () => ipcRenderer.invoke('win:maximize'),
  close: () => ipcRenderer.invoke('win:close'),
})
