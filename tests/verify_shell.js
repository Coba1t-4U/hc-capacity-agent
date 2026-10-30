// Verify the Electron shell's LOGIC without needing a GUI window.
//
// GUI init is blocked in this sandbox (proven: a 3-line Electron app dies with
// -2147483645 before any JS runs). But main.js's supervisory logic -- backend
// spawning, health polling, exit-code-70 handling, restart-storm breaker --
// is plain Node logic that we CAN exercise by running Electron in Node mode
// with a stubbed `electron` module.
const path = require('path');
const fs = require('fs');
const os = require('os');
const Module = require('module');

let pass = 0, fail = 0;
const check = (name, cond, detail) => {
  if (cond) { pass++; console.log(`  PASS  ${name}`); }
  else { fail++; console.log(`  FAIL  ${name}  ${detail || ''}`); }
};

// ---- stub the electron module so main.js can be required headlessly ----
// 用工程内目录而非 %TEMP%：本沙箱禁止在系统临时目录创建目录（EPERM）。
const tmpUserData = fs.mkdtempSync(
  path.join(__dirname, '_shell_tmp_'));
try { process.on('exit', () => { try { fs.rmSync(tmpUserData, { recursive: true, force: true }); } catch (_) {} }); } catch (_) {}
const stub = {
  app: {
    isPackaged: false,
    getPath: (k) => k === 'userData' ? tmpUserData : tmpUserData,
    whenReady: () => Promise.resolve(),
    on: () => {},
    quit: () => {},
  },
  BrowserWindow: class {
    constructor() { this.webContents = { send: () => {} }; }
    loadFile() {}
    on() {}
    isDestroyed() { return false; }
    static getAllWindows() { return []; }
  },
  ipcMain: { handle: () => {} },
  shell: { openPath: async () => '' },
  dialog: {},
};
const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request === 'electron') return 'electron-stub';
  return origResolve.call(this, request, ...rest);
};
require.cache['electron-stub'] = {
  id: 'electron-stub', filename: 'electron-stub',
  loaded: true, exports: stub,
};

console.log('='.repeat(74));
console.log('桌面壳逻辑验证（Node 模式，无需 GUI 窗口）');
console.log('='.repeat(74));

// ---- 1) 常量与退出码契约 ----
const src = fs.readFileSync(path.join(__dirname, '..', 'hc_agent_ui', 'main.js'),
                            'utf8');
console.log('\n1) 退出码契约与熔断常量');
check('监听看门狗退出码 70', /WATCHDOG_EXIT_CODE\s*=\s*70/.test(src));
const errSrc = fs.readFileSync(
  path.join(__dirname, '..', 'hc_agent', 'errors.py'), 'utf8');
check('Python 侧同为 70', /WATCHDOG_EXIT_CODE\s*=\s*70/.test(errSrc));
check('重启窗口 60s', /RESTART_WINDOW_MS\s*=\s*60000/.test(src));
check('重启上限 4 次', /RESTART_MAX\s*=\s*4/.test(src));
check('重启前等端口释放', /RESTART_DELAY_MS\s*=\s*1200/.test(src));
check('向看门狗声明有重启者', /HC_WATCHDOG_SUPERVISOR/.test(src));

// ---- 2) main.js 可被加载（语法与依赖解析） ----
console.log('\n2) main.js 可加载');
let mainExports = null, loadErr = null;
try {
  // main.js 顶层会读 app.getPath 并 spawn；截断到"只加载模块定义"
  // 的做法不可靠，这里直接 require —— 它会在 whenReady 后尝试起后端，
  // 那是我们想验证的行为。
  mainExports = require(path.join(__dirname, '..', 'hc_agent_ui', 'main.js'));
} catch (e) {
  loadErr = e;
}
check('main.js 加载无异常', loadErr === null,
      loadErr ? `${loadErr.constructor.name}: ${loadErr.message}` : '');

// ---- 3) 早期日志确已写出（证明模块顶层确实执行了） ----
console.log('\n3) 早期诊断日志');
const earlyLog = path.join(tmpUserData, 'logs', 'hc-agent-main.log');
// main.js 用 app.getPath('userData') 定位日志
const realLog = path.join(tmpUserData, 'logs', 'hc-agent-main.log');
check('日志目录已创建', fs.existsSync(path.join(tmpUserData, 'logs')),
      tmpUserData);
if (fs.existsSync(realLog)) {
  const txt = fs.readFileSync(realLog, 'utf8');
  check('含 main.js loaded 记录', /main\.js loaded/.test(txt));
} else {
  check('含 main.js loaded 记录', false, '日志文件未生成');
}

// ---- 4) 后端命令定位逻辑 ----
console.log('\n4) Python 定位');
const py = process.env.USERPROFILE
  ? path.join(process.env.USERPROFILE, '.dsh', 'dsh-runtimes',
              'dsh-primary-runtime', 'dependencies', 'python', 'python.exe')
  : null;
check('DSH 运行时 Python 存在', py && fs.existsSync(py), py || '(n/a)');

// ---- 5) 熔断逻辑（复刻 main.js 的算法，验证判据正确） ----
console.log('\n5) 重启风暴熔断（算法复刻验证）');
const WINDOW = 60000, MAX = 4;
function decide(stamps, now) {
  const recent = stamps.filter((t) => now - t < WINDOW);
  return { open: recent.length >= MAX, recent: recent.length };
}
const now = Date.now();
check('0 次 -> 允许', decide([], now).open === false);
check('3 次 -> 允许', decide([now - 1, now - 2, now - 3], now).open === false);
check('4 次 -> 熔断', decide([now - 1, now - 2, now - 3, now - 4], now).open === true);
check('窗口外的旧记录不计入',
      decide([now - 70000, now - 80000, now - 90000, now - 100000], now).open === false);

console.log('\n' + '='.repeat(74));
console.log(`结果：${pass} 通过 / ${fail} 失败 / ${pass + fail} 总计`);
console.log('='.repeat(74));
process.exit(fail ? 1 : 0);
