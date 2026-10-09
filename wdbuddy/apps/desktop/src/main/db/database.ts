/**
 * SQLite 连接管理（better-sqlite3，WAL 模式，ADR-0002）
 * - schema 初始化与增量迁移
 * - 每日滚动备份
 * - 原子写（temp + rename）用于 settings 等小文件
 */
import Database from 'better-sqlite3';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { backupDatabase } from './backup.js';

export interface DbOptions {
  dbPath: string;
}

export class AppDatabase {
  readonly db: Database.Database;

  constructor(opts: DbOptions) {
    fs.mkdirSync(path.dirname(opts.dbPath), { recursive: true });
    this.db = new Database(opts.dbPath);
    this.db.pragma('journal_mode = WAL');
    this.migrate();
  }

  /** 执行 schema.sql（幂等：IF NOT EXISTS）+ 存量库迁移 */
  private migrate(): void {
    const schemaPath = this.locateSchema();
    const sql = fs.readFileSync(schemaPath, 'utf-8');
    this.db.exec(sql);
    // 迁移：memories 表更名为 preferences（新库由 schema 直接建表，旧库平滑更名）
    const hasOld = this.db
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
      .get();
    const hasNew = this.db
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='preferences'")
      .get();
    if (hasOld && !hasNew) {
      this.db.exec('ALTER TABLE memories RENAME TO preferences');
    }
  }

  private locateSchema(): string {
    const candidates = [
      path.join(__dirname, 'schema.sql'),
      path.join(process.cwd(), 'src/main/db/schema.sql'),
      path.join(process.cwd(), 'dist/main/db/schema.sql'),
    ];
    for (const c of candidates) {
      if (fs.existsSync(c)) return c;
    }
    throw new Error('未找到 schema.sql');
  }

  /** 每日滚动备份（调用方挂到定时器） */
  dailyBackup(backupDir: string): void {
    backupDatabase(this.db, backupDir);
  }

  close(): void {
    this.db.close();
  }
}

/** 便捷：打开应用数据库（默认 userData 路径） */
export function openDatabase(dbPath: string): AppDatabase {
  return new AppDatabase({ dbPath });
}
