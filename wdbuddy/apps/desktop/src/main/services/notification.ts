/**
 * 系统通知服务（FR-22）
 * 任务完成弹系统通知；托盘常驻。
 */
import { Notification, shell } from 'electron';

export interface NotifyOptions {
  title: string;
  body: string;
  /** 点击通知打开的路径（如成果所在目录） */
  openPath?: string;
}

export class NotificationService {
  private enabled = true;

  setEnabled(v: boolean): void {
    this.enabled = v;
  }

  notify(opts: NotifyOptions): void {
    if (!this.enabled || !Notification.isSupported()) return;
    const n = new Notification({ title: opts.title, body: opts.body });
    if (opts.openPath) {
      n.on('click', () => {
        void shell.openPath(opts.openPath!);
      });
    }
    n.show();
  }

  /** 任务完成通知（含打开目录） */
  taskDone(taskTitle: string, artifactDir?: string): void {
    this.notify({
      title: 'WDBuddy 任务完成',
      body: `「${taskTitle}」已执行完毕，点击查看成果。`,
      openPath: artifactDir,
    });
  }

  /** 任务失败通知 */
  taskError(taskTitle: string, cause: string): void {
    this.notify({
      title: 'WDBuddy 任务失败',
      body: `「${taskTitle}」执行失败：${cause.slice(0, 120)}`,
    });
  }
}
