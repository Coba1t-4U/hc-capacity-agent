// ============================================================================
//  配网承载力与主配微协同优化 Agent — Electron 主进程
//
//  移植自天枢一号的桌面壳设计（见 docs/移植自天枢一号.md），核心是三件事：
//
//   1. **托管后端**：Electron 负责起/停 Python 后端，而不是让用户手动开两个窗口。
//   2. **退出码 70 自愈**：后端看门狗判定引擎已无法在进程内恢复时，会以 70 退出；
//      本壳监听该码并**重新拉起一个干净的后端进程**。
//      —— 这正是"看门狗退出"有意义的前提：没有重启者时退出只会让任务静默消失。
//   3. **重启风暴熔断**：若短时间内反复重启仍起不来（Python 环境坏了、端口被
//      长期占用），继续重启只会让界面永远转圈；达到上限就停下并说清去哪看日志。
// ============================================================================
const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const http = require('http');

// ---- 早期诊断日志：在 main.js 加载各阶段就写，便于排查"启动即死" ----
const LOG_DIR = path.join(app.getPath('userData'), 'logs');
const EARLOG = path.join(LOG_DIR, 'hc-agent-main.log');
try { fs.mkdirSync(LOG_DIR, { recursive: true }); } catch (_) {}
const earlyLog = (msg) => {
  try {
    fs.appendFileSync(EARLOG,
      `[${new Date().toISOString()}] ${msg}\n`);
  } catch (_) {}
};
earlyLog(`main.js loaded; pid=${process.pid}; ` +
         `electron=${process.versions.electron}; node=${process.versions.node}`);

const BACKEND_HOST = '127.0.0.1';
const BACKEND_PORT = 8791;
const DEFAULT_API = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

// 看门狗约定退出码（与 hc_agent/errors.py: WATCHDOG_EXIT_CODE 一致）
const WATCHDOG_EXIT_CODE = 70;
// 重启风暴熔断
const RESTART_WINDOW_MS = 60000;
const RESTART_MAX = 4;
let restartAt = [];
// 重启前等旧进程释放端口，否则新后端绑定失败 → 又非 0 退出 → 风暴
const RESTART_DELAY_MS = 1200;

let backendProcess = null;
let backendOwned = false;
let shuttingDown = false;
let mainWindow = null;

function projectRoot() {
  // 开发模式：hc_agent_ui 的上一级即工程根；打包模式：resources/backend
  if (!app.isPackaged) return path.resolve(__dirname, '..');
  return path.join(process.resourcesPath, 'backend');
}

/** 定位 Python：优先工程内 .venv，其次 DSH 运行时，再到 PATH。 */
function pythonCommand() {
  const root = projectRoot();
  const cands = process.platform === 'win32'
    ? [path.join(root, '.venv', 'Scripts', 'python.exe')]
    : [path.join(root, '.venv', 'bin', 'python')];
  for (const c of cands) {
    if (fs.existsSync(c)) return c;
  }
  // DSH 自带运行时（本机环境）
  const dsh = process.platform === 'win32'
    ? path.join(process.env.USERPROFILE || '',
        '.dsh', 'dsh-runtimes', 'dsh-primary-runtime',
        'dependencies', 'python', 'python.exe')
    : '';
  if (dsh && fs.existsSync(dsh)) return dsh;
  return process.platform === 'win32' ? 'python' : 'python3';
}

function httpGet(url, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let body = '';
      res.on('data', (d) => { body += d; });
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('error', reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error('timeout')));
  });
}

async function waitForBackend(timeoutMs = 60000, onTick = null) {
  const started = Date.now();
  for (;;) {
    try {
      const r = await httpGet(`${DEFAULT_API}/api/health`, 2000);
      if (r.status === 200) return true;
    } catch (_) { /* 还没起来 */ }
    const e = Math.floor((Date.now() - started) / 1000);
    if (Date.now() - started > timeoutMs) {
      throw new Error(`后端启动超时（${timeoutMs / 1000}s）`);
    }
    if (onTick) onTick(e);
    await new Promise((r) => setTimeout(r, 400));
  }
}

function notify(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

/** 后端日志落盘，便于"死在哪儿"可查。 */
function backendLogStream() {
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
  } catch (_) {}
  return fs.createWriteStream(path.join(LOG_DIR, 'hc-agent-backend.log'),
                              { flags: 'a' });
}

async function ensureBackend() {
  // 已有一个健康后端（可能是用户自己起的）→ 直接复用
  try {
    await waitForBackend(1500);
    backendOwned = false;
    return { reused: true };
  } catch (_) { /* 需要自己起 */ }

  notify('backend:progress', { message: '正在启动计算后端…', seconds: 0 });
  const root = projectRoot();
  const py = pythonCommand();
  earlyLog(`spawn backend: py=${py} cwd=${root}`);

  if (!fs.existsSync(root)) {
    throw new Error(`找不到工程目录：${root}`);
  }

  const log = backendLogStream();
  backendProcess = spawn(py, ['-m', 'hc_agent.web'],
                         {
                           cwd: root,
                           env: {
                             ...process.env,
                             // 关键：把"外层有重启者"告诉看门狗，
                             // 否则它按设计拒绝启用自愈退出
                             HC_WATCHDOG_SUPERVISOR: '1',
                             HC_WEB_HOST: BACKEND_HOST,
                             HC_WEB_PORT: String(BACKEND_PORT),
                             PYTHONUNBUFFERED: '1',
                             PYTHONIOENCODING: 'utf-8',
                           },
                           windowsHide: true,
                           stdio: ['ignore', 'pipe', 'pipe'],
                         });
  backendOwned = true;
  shuttingDown = false;
  backendProcess.stdout?.pipe(log);
  backendProcess.stderr?.pipe(log);

  backendProcess.on('error', (err) => {
    earlyLog(`spawn error: ${err.message}`);
    notify('backend:error', `后端启动失败：${err.message}`);
  });

  backendProcess.on('exit', (code, signal) => {
    earlyLog(`backend exited code=${code} signal=${signal}`);
    // 主动关闭不处理
    if (shuttingDown || code === 0) return;

    const byWatchdog = (code === WATCHDOG_EXIT_CODE);
    const now = Date.now();
    restartAt = restartAt.filter((t) => now - t < RESTART_WINDOW_MS);
    if (restartAt.length >= RESTART_MAX) {
      // 停下来说清楚 —— 继续重启只会让界面永远转圈
      earlyLog(`restart suppressed: ${restartAt.length} in window`);
      notify('backend:error',
        `后端反复启动失败（1 分钟内 ${restartAt.length} 次），已停止自动重启。\n` +
        `请查看日志：${path.join(LOG_DIR, 'hc-agent-backend.log')}`);
      backendOwned = false;
      backendProcess = null;
      return;
    }
    restartAt.push(now);
    // 措辞必须区分：看门狗自愈不等于程序出错
    notify('backend:error', byWatchdog
      ? '检测到计算引擎异常，正在自动恢复…'
      : `后端进程意外退出（退出码 ${code}${signal ? ' 信号 ' + signal : ''}），`
        + `正在自动重启…`);
    backendOwned = false;
    backendProcess = null;
    setTimeout(() => {
      ensureBackend()
        .then(() => notify('backend:ready', { restarted: true }))
        .catch((e) => notify('backend:error', String(e.message || e)));
    }, RESTART_DELAY_MS);
  });

  await waitForBackend(90000, (s) => {
    notify('backend:progress',
           { message: `正在启动计算后端… ${s}s`, seconds: s });
  });
  return { reused: false };
}

function stopBackend() {
  shuttingDown = true;
  if (backendProcess && backendOwned) {
    earlyLog('stopping backend');
    try { backendProcess.kill(); } catch (_) {}
  }
  backendProcess = null;
}

// ------------------------------------------------------------------ 窗口
function createWindow() {
  // 按屏幕比例给一个合适的初始尺寸。固定 1440x900 在 2560x1440 的桌面上
  // 明显偏小（实测过），所以按工作区比例取，并留出上下限。
  const { screen } = require('electron');
  const wa = screen.getPrimaryDisplay().workAreaSize;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, Math.round(v)));
  const w = clamp(wa.width * 0.78, 1180, 1920);
  const h = clamp(wa.height * 0.86, 720, 1200);

  mainWindow = new BrowserWindow({
    width: w,
    height: h,
    minWidth: 1180,
    minHeight: 720,
    center: true,
    // 先不显示，等尺寸校正后再显示，避免看到窗口跳动
    show: false,
    // 与侧边栏同色，避免启动瞬间闪白
    backgroundColor: '#001432',
    title: '配网承载力与主配微协同优化 Agent',
    // 无边框 + 自绘标题栏：界面自己画 56px 标题栏（与天枢一号一致）
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  // 界面由后端提供（单一来源，避免前后端两份 HTML 漂移）
  mainWindow.loadURL(`${DEFAULT_API}/`);
  mainWindow.once('ready-to-show', () => {
    mainWindow.setSize(w, h);
    mainWindow.center();
    mainWindow.show();
  });
  mainWindow.on('closed', () => { mainWindow = null; });
}

// 让窗口按钮真正能拖动/关闭；无边框窗口必须自己实现
ipcMain.handle('win:action', async (_e, action) => {
  if (!mainWindow || mainWindow.isDestroyed()) return { ok: false };
  if (action === 'min') mainWindow.minimize();
  else if (action === 'max') {
    if (mainWindow.isMaximized()) mainWindow.unmaximize();
    else mainWindow.maximize();
  } else if (action === 'close') mainWindow.close();
  return { ok: true };
});

// --------------------------------------------------------------- IPC
ipcMain.handle('api:get', async (_e, apiPath) => {
  try {
    const r = await httpGet(`${DEFAULT_API}${apiPath}`, 30000);
    return { ok: r.status === 200, status: r.status, body: r.body };
  } catch (e) {
    return { ok: false, error: String(e.message || e) };
  }
});

ipcMain.handle('app:info', async () => ({
  apiBase: DEFAULT_API,
  projectRoot: projectRoot(),
  python: pythonCommand(),
  logDir: LOG_DIR,
  backendOwned,
}));

ipcMain.handle('app:openLogs', async () => {
  shell.openPath(LOG_DIR);
  return true;
});

ipcMain.handle('app:openPath', async (_e, p) => {
  // 只允许打开工程目录内的路径，避免任意路径被前端指示打开
  const root = projectRoot();
  const resolved = path.resolve(String(p || ''));
  if (!resolved.startsWith(path.resolve(root))) {
    return { ok: false, error: '拒绝打开工程目录之外的路径' };
  }
  await shell.openPath(resolved);
  return { ok: true };
});

// ------------------------------------------------------------------ 生命周期
app.whenReady().then(async () => {
  createWindow();
  try {
    await ensureBackend();
    notify('backend:ready', { restarted: false });
  } catch (e) {
    earlyLog(`ensureBackend failed: ${e.message}`);
    notify('backend:error', String(e.message || e));
  }
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  stopBackend();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', stopBackend);
process.on('exit', stopBackend);
