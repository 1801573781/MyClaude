/**
 * 自动更新服务（FR-23）
 * - 内置静默更新与手动"检查更新"
 * - electron-updater generic provider（分阶段灰度）
 */
import { autoUpdater } from 'electron-updater';
import { app } from 'electron';

export interface UpdateStatus {
  checking: boolean;
  downloading: boolean;
  available: boolean;
  version?: string;
  progress?: number;
  error?: string;
}

export class UpdaterService {
  private status: UpdateStatus = { checking: false, downloading: false, available: false };
  private readonly onStatusChange: (s: UpdateStatus) => void;

  constructor(onStatusChange: (s: UpdateStatus) => void) {
    this.onStatusChange = onStatusChange;
    this.wire();
  }

  private wire(): void {
    autoUpdater.autoDownload = true;      // 静默更新
    autoUpdater.autoInstallOnAppQuit = true;

    autoUpdater.on('checking-for-update', () => {
      this.status = { ...this.status, checking: true };
      this.onStatusChange(this.status);
    });
    autoUpdater.on('update-available', (info) => {
      this.status = { checking: false, downloading: true, available: true, version: info.version };
      this.onStatusChange(this.status);
    });
    autoUpdater.on('update-not-available', () => {
      this.status = { checking: false, downloading: false, available: false };
      this.onStatusChange(this.status);
    });
    autoUpdater.on('download-progress', (p) => {
      this.status = { ...this.status, progress: Math.round(p.percent) };
      this.onStatusChange(this.status);
    });
    autoUpdater.on('error', (err) => {
      this.status = { ...this.status, checking: false, downloading: false, error: err.message };
      this.onStatusChange(this.status);
    });
  }

  /** 手动检查更新（设置页"检查更新"按钮） */
  checkManually(): void {
    void autoUpdater.checkForUpdates();
  }

  /** 启动后台静默检查 */
  startSilentCheck(): void {
    // 启动后 5 分钟首查，之后每 6 小时
    setTimeout(() => {
      void autoUpdater.checkForUpdates();
    }, 5 * 60_000).unref?.();
    setInterval(() => {
      void autoUpdater.checkForUpdates();
    }, 6 * 60 * 60_000).unref?.();
  }

  get currentVersion(): string {
    return app.getVersion();
  }
}
