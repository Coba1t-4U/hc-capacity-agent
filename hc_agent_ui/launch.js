// 单入口启动器：清理宿主注入的 NODE_OPTIONS / ELECTRON_RUN_AS_NODE 再启动 Electron。
//
// 为什么必须有这一层：IDE、终端、命令行工具常给子进程注入 Node 专用参数，
// 而 Electron 不接受这些参数；ELECTRON_RUN_AS_NODE 更会让 Electron **退化成
// 纯 Node**（表现为"bad option: --disable-gpu"之类莫名其妙的报错）。
// 本机实测：ELECTRON_RUN_AS_NODE=1 时 Electron 完全无法创建窗口。
const { spawn } = require('child_process');
const path = require('path');

const env = { ...process.env };
delete env.NODE_OPTIONS;
delete env.ELECTRON_RUN_AS_NODE;

let electron;
try {
  electron = require('electron');
} catch (e) {
  console.error('未找到 electron 依赖，请先在 hc_agent_ui 目录执行 npm install');
  process.exit(1);
}

const appDir = __dirname;
const args = ['--disable-gpu', '--disable-gpu-compositing',
              '--disable-gpu-sandbox', appDir];
const child = spawn(electron, args, { cwd: appDir, stdio: 'inherit', env });
child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code === null ? 0 : code);
});
child.on('error', (err) => {
  console.error('Electron 启动失败:', err.message);
  process.exit(1);
});
