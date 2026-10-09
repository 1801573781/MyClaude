/**
 * 系统托盘（FR-22）
 * 常驻托盘，最小化到托盘；任务完成弹通知。
 */
import { Menu, Tray, app, nativeImage, BrowserWindow } from 'electron';
import * as path from 'node:path';

export function createTray(getMainWindow: () => BrowserWindow | null): Tray {
  // 1x1 蓝色像素占位图标（打包时替换为真实 icon）
  const icon = nativeImage.createFromBuffer(
    Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
      'base64'
    )
  );

  const tray = new Tray(icon);
  tray.setToolTip('WDBuddy — 桌面办公智能体');

  const contextMenu = Menu.buildFromTemplate([
    {
      label: '显示主窗口',
      click: () => {
        const win = getMainWindow();
        if (win) {
          win.show();
          win.focus();
        }
      },
    },
    { type: 'separator' },
    {
      label: '检查更新',
      click: () => {
        BrowserWindow.getAllWindows().forEach((w) =>
          w.webContents.send('menu:action', { action: 'check-update' })
        );
      },
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        app.quit();
      },
    },
  ]);

  tray.setContextMenu(contextMenu);
  tray.on('click', () => {
    const win = getMainWindow();
    if (win) {
      win.isVisible() ? win.focus() : win.show();
    }
  });

  return tray;
}
