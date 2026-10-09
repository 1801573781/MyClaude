/**
 * 主窗口（NFR-03 安全）
 * BrowserWindow：contextIsolation:true, nodeIntegration:false, sandbox:true
 * CSP 严格；开发模式加载 Vite dev server，生产加载本地文件。
 */
import { BrowserWindow, app, shell } from 'electron';
import * as path from 'node:path';

const DEV_SERVER_URL = process.env['WB_DEV_SERVER_URL'] ?? 'http://localhost:5173';
const MIN_WIDTH = 1280;
const MIN_HEIGHT = 800;

export function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    show: false,
    autoHideMenuBar: true,
    title: 'WDBuddy',
    backgroundColor: '#FFFFFF',
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,   // 隔离
      nodeIntegration: false,   // 禁 Node
      sandbox: true,            // 沙箱
      webSecurity: true,
    },
  });

  // 外部链接走系统浏览器
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });

  win.once('ready-to-show', () => {
    win.show();
  });

  const isDev = process.env['NODE_ENV'] === 'development' || process.env['WB_DEV'] === '1';
  if (isDev) {
    void win.loadURL(DEV_SERVER_URL);
    // DevTools 不再默认自动弹出：Chromium DevTools 自身加载时会打印若干已知无害的
    // 内部错误（"Unknown VE context: language-mismatch" 为中文 locale 下的可视化日志
    // bug；"Autofill.enable/setAddresses wasn't found" 为 DevTools 前端探测 Electron
    // 未实现的 CDP 域），既遮挡应用界面又造成"启动报错"误判。
    // 需要调试时：设 WB_DEVTOOLS=1 启动，或按 F12 / Ctrl+Shift+I 呼出。
    if (process.env['WB_DEVTOOLS'] === '1') {
      win.webContents.openDevTools({ mode: 'detach' });
    }
  } else {
    void win.loadFile(path.join(__dirname, '../renderer/index.html'));
  }

  // 开发模式：F12 / Ctrl+Shift+I 切换 DevTools（生产不注册，保持安全姿态）
  if (isDev) {
    win.webContents.on('before-input-event', (_e, input) => {
      if (input.type !== 'keyDown') return;
      const isF12 = input.key === 'F12';
      const isCtrlShiftI = input.control && input.shift && (input.key === 'I' || input.key === 'i');
      if (isF12 || isCtrlShiftI) {
        win.webContents.toggleDevTools();
      }
    });
  }

  return win;
}

/** 单实例锁（避免多开导致 SQLite 锁冲突） */
export function ensureSingleInstance(): boolean {
  const got = app.requestSingleInstanceLock();
  if (!got) {
    app.quit();
    return false;
  }
  app.on('second-instance', () => {
    const wins = BrowserWindow.getAllWindows();
    const main = wins.find((w) => !w.isDestroyed());
    if (main) {
      if (main.isMinimized()) main.restore();
      main.focus();
    }
  });
  return true;
}
