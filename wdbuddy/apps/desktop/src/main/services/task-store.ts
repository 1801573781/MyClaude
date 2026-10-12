/**
 * task-store — 任务持久化（§10.1）
 * SQLite 读写：tasks / messages / steps / artifacts / checkpoints / memories / settings
 * 全部行 ↔ shared-types 类型互转，禁 any。
 */
import type Database from 'better-sqlite3';
import type {
  Artifact,
  PreferenceItem,
  Message,
  MessageMeta,
  NewTaskInput,
  PlanStep,
  Task,
  TaskFilter,
  TaskStatus,
  TaskTrigger,
} from '@wdbuddy/shared-types';

interface TaskRow {
  id: string;
  title: string;
  mode: Task['mode'];
  status: string;
  workspace_uri: string | null;
  permission: Task['permission'];
  model_id: string;
  max_context: number;
  trigger_json: string;
  created_at: number;
  updated_at: number;
}

interface MessageRow {
  id: string;
  task_id: string;
  seq: number;
  role: Message['role'];
  content: string;
  meta_json: string | null;
  created_at: number;
}

interface StepRow {
  id: string;
  task_id: string;
  seq: number;
  title: string;
  status: PlanStep['status'];
  summary: string | null;
  started_at: number | null;
  ended_at: number | null;
}

interface ArtifactRow {
  id: string;
  task_id: string;
  kind: Artifact['kind'];
  path: string | null;
  mime_type: string;
  version: number;
  sha256: string;
  created_at: number;
}

export class TaskStore {
  constructor(private readonly db: Database.Database) {}

  // ─────────── tasks ───────────

  createTask(input: NewTaskInput, id: string, title: string): Task {
    const now = Date.now();
    const task: Task = {
      id,
      title,
      mode: input.mode,
      status: 'draft',
      workspaceUri: input.workspaceUri,
      permission: input.permission,
      modelId: input.modelId,
      maxContext: input.maxContext,
      trigger: input.trigger,
      createdAt: now,
      updatedAt: now,
    };
    this.db
      .prepare(
        `INSERT INTO tasks (id, title, mode, status, workspace_uri, permission, model_id, max_context, trigger_json, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .run(
        task.id,
        task.title,
        task.mode,
        task.status,
        task.workspaceUri,
        task.permission,
        task.modelId,
        task.maxContext ? 1 : 0,
        JSON.stringify(task.trigger),
        task.createdAt,
        task.updatedAt
      );
    return task;
  }

  getTask(taskId: string): Task | null {
    const row = this.db.prepare('SELECT * FROM tasks WHERE id = ?').get(taskId) as TaskRow | undefined;
    return row ? rowToTask(row) : null;
  }

  listTasks(filter: TaskFilter): Task[] {
    let sql = 'SELECT * FROM tasks';
    const params: unknown[] = [];
    switch (filter.group) {
      case 'active':
        sql += " WHERE status IN ('draft','planning','awaiting-approval','running','paused','interrupted')";
        break;
      case 'completed':
        sql += " WHERE status = 'completed'";
        break;
      case 'rules':
        sql += " WHERE trigger_json != '{\"type\":\"manual\"}'";
        break;
      case 'all':
      default:
        break;
    }
    sql += ' ORDER BY updated_at DESC';
    return (this.db.prepare(sql).all(...params) as TaskRow[]).map(rowToTask);
  }

  updateStatus(taskId: string, status: TaskStatus): void {
    this.db.prepare('UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?').run(status, Date.now(), taskId);
  }

  /** 更新任务模型（顶栏切换模型后随 start 同步，修复已有任务永远复用创建时模型） */
  updateTaskModel(taskId: string, modelId: Task['modelId']): void {
    this.db.prepare('UPDATE tasks SET model_id = ?, updated_at = ? WHERE id = ?').run(modelId, Date.now(), taskId);
  }

  /** 更新任务模式（发送时随 start 同步，修复模式在任务创建时固化、后续切换无效） */
  updateTaskMode(taskId: string, mode: Task['mode']): void {
    this.db.prepare('UPDATE tasks SET mode = ?, updated_at = ? WHERE id = ?').run(mode, Date.now(), taskId);
  }

  /** 回填任务工作空间（任务未指定时取设置页配置，修复"配置的工作空间从未被使用"） */
  updateTaskWorkspace(taskId: string, workspaceUri: string): void {
    this.db.prepare('UPDATE tasks SET workspace_uri = ?, updated_at = ? WHERE id = ?').run(workspaceUri, Date.now(), taskId);
  }

  renameTask(taskId: string, title: string): void {
    this.db.prepare('UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?').run(title, Date.now(), taskId);
  }

  deleteTask(taskId: string): void {
    const tx = this.db.transaction(() => {
      this.db.prepare('DELETE FROM messages WHERE task_id = ?').run(taskId);
      this.db.prepare('DELETE FROM steps WHERE task_id = ?').run(taskId);
      this.db.prepare('DELETE FROM artifacts WHERE task_id = ?').run(taskId);
      this.db.prepare('DELETE FROM checkpoints WHERE task_id = ?').run(taskId);
      this.db.prepare('DELETE FROM tasks WHERE id = ?').run(taskId);
    });
    tx();
  }

  // ─────────── messages ───────────

  appendMessage(taskId: string, role: Message['role'], content: string, meta?: MessageMeta): Message {
    const seqRow = this.db
      .prepare('SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM messages WHERE task_id = ?')
      .get(taskId) as { next: number };
    const msg: Message = {
      id: `msg-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      taskId,
      seq: seqRow['next'],
      role,
      content,
      meta,
      createdAt: Date.now(),
    };
    this.db
      .prepare('INSERT INTO messages (id, task_id, seq, role, content, meta_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)')
      .run(msg.id, msg.taskId, msg.seq, msg.role, msg.content, meta ? JSON.stringify(meta) : null, msg.createdAt);
    return msg;
  }

  listMessages(taskId: string): Message[] {
    const rows = this.db
      .prepare('SELECT * FROM messages WHERE task_id = ? ORDER BY seq')
      .all(taskId) as MessageRow[];
    return rows.map((r) => ({
      id: r.id,
      taskId: r.task_id,
      seq: r.seq,
      role: r.role,
      content: r.content,
      meta: r.meta_json ? (JSON.parse(r.meta_json) as MessageMeta) : undefined,
      createdAt: r.created_at,
    }));
  }

  /**
   * 流式 token 落库：最后一条是 assistant 则拼接，否则新建一条。
   * 与渲染层 chat/store 的拼接逻辑一致（修复：此前每个 token.delta 独立插一条，
   * 数据库消息碎片化，导出 md/html 时"一字一条"、回看任务时无法阅读）。
   */
  appendAssistantDelta(taskId: string, text: string): void {
    const row = this.db
      .prepare('SELECT id, role FROM messages WHERE task_id = ? ORDER BY seq DESC LIMIT 1')
      .get(taskId) as { id: string; role: string } | undefined;
    if (row && row.role === 'assistant') {
      this.db
        .prepare('UPDATE messages SET content = content || ? WHERE id = ?')
        .run(text, row.id);
    } else {
      this.appendMessage(taskId, 'assistant', text);
    }
  }

  // ─────────── steps ───────────

  upsertSteps(taskId: string, steps: PlanStep[]): void {
    const tx = this.db.transaction(() => {
      this.db.prepare('DELETE FROM steps WHERE task_id = ?').run(taskId);
      const ins = this.db.prepare(
        'INSERT INTO steps (id, task_id, seq, title, status, summary, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
      );
      for (const s of steps) {
        ins.run(s.id, taskId, s.seq, s.title, s.status, s.summary ?? null, s.startedAt ?? null, s.endedAt ?? null);
      }
    });
    tx();
  }

  updateStep(taskId: string, step: PlanStep): void {
    this.db
      .prepare('UPDATE steps SET status = ?, summary = ?, started_at = ?, ended_at = ? WHERE id = ? AND task_id = ?')
      .run(step.status, step.summary ?? null, step.startedAt ?? null, step.endedAt ?? null, step.id, taskId);
  }

  /** 步骤 upsert：行不存在则插入（craft 模式无 plan.ready，首个 step.update
   *  到达时表为空，UPDATE 是 no-op → 步骤时间线重进任务即消失、导出缺
   *  "执行过程"——467 事故根因之一） */
  upsertStep(taskId: string, step: PlanStep): void {
    const existing = this.db
      .prepare('SELECT id FROM steps WHERE task_id = ? AND id = ?')
      .get(taskId, step.id) as { id: string } | undefined;
    if (existing) {
      this.updateStep(taskId, step);
      return;
    }
    this.db
      .prepare(
        'INSERT INTO steps (id, task_id, seq, title, status, summary, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
      )
      .run(
        step.id,
        taskId,
        step.seq,
        step.title,
        step.status,
        step.summary ?? null,
        step.startedAt ?? null,
        step.endedAt ?? null
      );
  }

  listSteps(taskId: string): PlanStep[] {
    const rows = this.db
      .prepare('SELECT * FROM steps WHERE task_id = ? ORDER BY seq')
      .all(taskId) as StepRow[];
    return rows.map((r) => ({
      id: r.id,
      seq: r.seq,
      title: r.title,
      status: r.status,
      summary: r.summary ?? undefined,
      artifactIds: [], // 关联产物按 artifacts 表查询
      startedAt: r.started_at ?? undefined,
      endedAt: r.ended_at ?? undefined,
    }));
  }

  // ─────────── artifacts ───────────

  insertArtifact(art: Artifact): void {
    // upsert 语义：同任务同路径的产物只保留一条（刷新为最新版本）。
    // 修复"完成提示与文件栏显示两个相同文件"：artifact.created 与 task.done
    // 两条链路都会登记同一产物，且 Agent 重复生成同路径文件时也不应重复显示
    const existing = this.db
      .prepare('SELECT id FROM artifacts WHERE task_id = ? AND path = ?')
      .get(art.taskId, art.path ?? null) as { id: string } | undefined;
    if (existing) {
      this.db
        .prepare('UPDATE artifacts SET kind = ?, mime_type = ?, sha256 = ?, created_at = ? WHERE id = ?')
        .run(art.kind, art.mimeType, art.sha256, art.createdAt, existing.id);
      return;
    }
    this.db
      .prepare(
        'INSERT INTO artifacts (id, task_id, kind, path, mime_type, version, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
      )
      .run(art.id, art.taskId, art.kind, art.path ?? null, art.mimeType, art.version, art.sha256, art.createdAt);
  }

  listArtifacts(taskId: string): Artifact[] {
    const rows = this.db
      .prepare('SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at')
      .all(taskId) as ArtifactRow[];
    return rows.map((r) => ({
      id: r.id,
      taskId: r.task_id,
      kind: r.kind,
      path: r.path ?? undefined,
      mimeType: r.mime_type,
      version: r.version,
      sha256: r.sha256,
      createdAt: r.created_at,
    }));
  }

  // ─────────── checkpoints ───────────

  saveCheckpoint(taskId: string, stepId: string | null, snapshotJson: string): void {
    this.db
      .prepare('INSERT INTO checkpoints (task_id, step_id, snapshot_json, created_at) VALUES (?, ?, ?, ?)')
      .run(taskId, stepId, snapshotJson, Date.now());
  }

  loadLatestCheckpoint(taskId: string): unknown {
    const row = this.db
      .prepare('SELECT snapshot_json FROM checkpoints WHERE task_id = ? ORDER BY id DESC LIMIT 1')
      .get(taskId) as { snapshot_json: string } | undefined;
    return row ? (JSON.parse(row['snapshot_json']) as unknown) : null;
  }

  clearCheckpoints(taskId: string): void {
    this.db.prepare('DELETE FROM checkpoints WHERE task_id = ?').run(taskId);
  }

  /** 启动恢复：扫描 running/paused 任务（interrupted 提示，§10.3） */
  findInterruptedTasks(): Task[] {
    const rows = this.db
      .prepare("SELECT * FROM tasks WHERE status IN ('running','paused')")
      .all() as TaskRow[];
    return rows.map(rowToTask);
  }

  // ─────────── preferences（用户偏好，FR-18） ───────────

  upsertPreference(key: string, value: string, origin: PreferenceItem['origin']): void {
    this.db
      .prepare(
        `INSERT INTO preferences (id, key, value, origin, updated_at) VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`
      )
      .run(`pref-${key}`, key, value, origin, Date.now());
  }

  listPreferences(): PreferenceItem[] {
    const rows = this.db.prepare('SELECT * FROM preferences ORDER BY updated_at DESC').all() as Array<{
      id: string;
      key: string;
      value: string;
      origin: PreferenceItem['origin'];
      updated_at: number;
    }>;
    return rows.map((r) => ({ id: r.id, key: r.key, value: r.value, origin: r.origin, updatedAt: r.updated_at }));
  }

  deletePreference(id: string): void {
    this.db.prepare('DELETE FROM preferences WHERE id = ?').run(id);
  }

  // ─────────── settings ───────────

  getSetting<T>(key: string): T | null {
    const row = this.db.prepare('SELECT value_json FROM settings WHERE key = ?').get(key) as
      | { value_json: string }
      | undefined;
    return row ? (JSON.parse(row['value_json']) as T) : null;
  }

  setSetting(key: string, value: unknown): void {
    this.db
      .prepare(
        `INSERT INTO settings (key, value_json) VALUES (?, ?)
         ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json`
      )
      .run(key, JSON.stringify(value));
  }
}

function rowToTask(r: TaskRow): Task {
  return {
    id: r.id,
    title: r.title,
    mode: r.mode,
    status: r.status as TaskStatus,
    workspaceUri: r.workspace_uri,
    permission: r.permission,
    modelId: r.model_id as Task['modelId'],
    maxContext: r.max_context === 1,
    trigger: JSON.parse(r.trigger_json) as TaskTrigger,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}
