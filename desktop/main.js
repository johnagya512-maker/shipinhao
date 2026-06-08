// Electron 主进程：拉起本地 Python 后端 → 健康检查 → 开窗口加载前端。
// 全本地运行，不需要任何远程服务器；AI 生成走用户自配的云 API（BYOK）。
const { app, BrowserWindow, shell, ipcMain, dialog, Menu } = require('electron')
const path = require('path')
const http = require('http')
const { spawn } = require('child_process')
const fs = require('fs')

const API_PORT = 8765                       // 避开常见 8000，减少端口冲突
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`
const isDev = !app.isPackaged

let backendProc = null
let mainWindow = null

// 用户数据目录：SQLite / storage / 加密密钥都写这里（AppData\<app>），
// 而非安装目录（安装目录通常只读、卸载即丢）。
function userDataDir() {
  const dir = path.join(app.getPath('userData'), 'data')
  fs.mkdirSync(dir, { recursive: true })
  return dir
}

// 加密主密钥：首次启动随机生成并存进 AppData，之后一直复用。
// 因为存在 AppData（更新/重装客户端默认不删），用户在界面填的 Key 用它加密落库后，
// 跨重启、跨客户端更新都能解开 —— 填一次永久保存。比代码里写死的密钥安全。
function masterKey() {
  const keyFile = path.join(app.getPath('userData'), 'master.key')
  try {
    const existing = fs.readFileSync(keyFile, 'utf-8').trim()
    if (existing) return existing
  } catch { /* 首次启动，下面生成 */ }
  const key = require('crypto').randomBytes(32).toString('hex')
  fs.writeFileSync(keyFile, key, { mode: 0o600 })
  return key
}

function backendCommand() {
  const dataDir = userDataDir()
  const env = {
    ...process.env,
    APP_HOST: '127.0.0.1',
    APP_PORT: String(API_PORT),
    APP_DATA_DIR: dataDir,                  // 后端据此把 db/storage 落到用户目录
    APP_ENCRYPTION_KEY: masterKey(),        // 固定的本机主密钥，保证 Key 跨更新可解
    PYTHONUTF8: '1',
  }
  if (isDev) {
    // 开发：直接用仓库里的 Python 运行 uvicorn。
    const backendCwd = path.join(__dirname, '..', 'backend')
    return {
      cmd: 'python',
      args: ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(API_PORT)],
      opts: { cwd: backendCwd, env },
    }
  }
  // 生产：PyInstaller 打出的单可执行（随安装包一起分发）。
  const exe = process.platform === 'win32' ? 'shipinhao-backend.exe' : 'shipinhao-backend'
  const exePath = path.join(process.resourcesPath, 'backend', exe)
  return { cmd: exePath, args: [], opts: { env } }
}

function startBackend() {
  const { cmd, args, opts } = backendCommand()
  backendProc = spawn(cmd, args, opts)
  backendProc.stdout?.on('data', (d) => console.log('[backend]', d.toString().trim()))
  backendProc.stderr?.on('data', (d) => console.log('[backend]', d.toString().trim()))
  backendProc.on('exit', (code) => console.log('[backend] exited', code))
}

// 轮询 /health，直到后端就绪（最多约 30s）。
function waitForBackend(timeoutMs = 30000) {
  const start = Date.now()
  return new Promise((resolve, reject) => {
    const tick = () => {
      http.get(`${API_ORIGIN}/health`, (res) => {
        res.resume()
        if (res.statusCode === 200) return resolve()
        retry()
      }).on('error', retry)
    }
    const retry = () => {
      if (Date.now() - start > timeoutMs) return reject(new Error('后端启动超时'))
      setTimeout(tick, 400)
    }
    tick()
  })
}

// 系统原生"选择文件夹"对话框：供前端配置剪映草稿目录，返回选中路径或 null。
ipcMain.handle('dialog:pickFolder', async () => {
  const r = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] })
  return r.canceled || !r.filePaths.length ? null : r.filePaths[0]
})

// 无边框窗口的控制：前端自绘顶栏的最小化/最大化/关闭按钮调这些。
ipcMain.handle('win:minimize', () => mainWindow?.minimize())
ipcMain.handle('win:maximize', () => {
  if (!mainWindow) return
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize()
})
ipcMain.handle('win:close', () => mainWindow?.close())

function createWindow() {
  Menu.setApplicationMenu(null)   // 去掉默认的英文菜单栏（File/Edit/View…），终端用户用不到
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    title: '视频号图书带货 AI 工作流',
    backgroundColor: '#0b1220',     // 与前端深色底一致，避免加载时露出白色
    frame: false,                   // 无边框：自绘深色顶栏，彻底统一颜色（见前端 TitleBar）
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  // 外链用系统浏览器打开，不在应用内导航。
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
  if (isDev) {
    mainWindow.loadURL('http://127.0.0.1:5173')
  } else {
    mainWindow.loadFile(path.join(__dirname, 'frontend', 'index.html'))
  }
}

app.whenReady().then(async () => {
  startBackend()
  try {
    await waitForBackend()
  } catch (e) {
    console.error(e)
  }
  createWindow()
})

app.on('window-all-closed', () => {
  if (backendProc) backendProc.kill()
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  if (backendProc) backendProc.kill()
})

module.exports = { API_ORIGIN }
