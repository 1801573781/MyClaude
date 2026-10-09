/**
 * DB 每日滚动备份（可靠性 §13）
 * - 每日一份，保留最近 7 份
 * - temp + rename 原子写
 */
import type Database from 'better-sqlite3';
import * as fs from 'node:fs';
import * as path from 'node:path';

const KEEP_DAYS = 7;

export function backupDatabase(db: Database.Database, backupDir: string): string | null {
  try {
    fs.mkdirSync(backupDir, { recursive: true });
    const today = new Date().toISOString().slice(0, 10);
    const target = path.join(backupDir, `wdbuddy-${today}.db`);
    if (fs.existsSync(target)) return target; // 今日已备份

    const tmp = target + '.tmp';
    // WAL 模式下用 backup API 保证一致性快照
    db.backup(tmp).then(
      () => {
        fs.renameSync(tmp, target);
        prune(backupDir);
      },
      () => {
        try {
          fs.rmSync(tmp, { force: true });
        } catch {
          /* ignore */
        }
      }
    );
    return target;
  } catch {
    return null;
  }
}

/** 清理过期备份（保留最近 KEEP_DAYS 份） */
export function prune(backupDir: string): void {
  try {
    const files = fs
      .readdirSync(backupDir)
      .filter((f) => /^wdbuddy-\d{4}-\d{2}-\d{2}\.db$/.test(f))
      .sort()
      .reverse();
    for (const old of files.slice(KEEP_DAYS)) {
      fs.rmSync(path.join(backupDir, old), { force: true });
    }
  } catch {
    /* ignore */
  }
}
