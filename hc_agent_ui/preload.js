// 预加载脚本：通过 contextBridge 暴露受控 API。
// 渲染层拿不到 Node，只能调用这里白名单内的能力。
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('hc', {
  // 只读 API 调用（后端是本工程自己的服务，路径由渲染层给）
  apiGet: (path) => ipcRenderer.invoke('api:get', path),
  appInfo: () => ipcRenderer.invoke('app:info'),
  openLogs: () => ipcRenderer.invoke('app:openLogs'),
  openPath: (p) => ipcRenderer.invoke('app:openPath', p),
  // 无边框窗口的窗口控制（最小化 / 最大化 / 关闭）
  win: (action) => ipcRenderer.invoke('win:action', action),

  onBackendReady: (cb) => ipcRenderer.on('backend:ready', (_e, v) => cb(v)),
  onBackendProgress: (cb) =>
    ipcRenderer.on('backend:progress', (_e, v) => cb(v)),
  onBackendError: (cb) => ipcRenderer.on('backend:error', (_e, v) => cb(v)),
});
