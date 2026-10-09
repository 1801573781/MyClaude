/**
 * 步骤级检查点（ADR-0002：事件溯源，断点恢复零丢失）
 * Runtime 侧通过注入的 CheckpointStore 接口持久化快照；
 * 桌面端注入 better-sqlite3 实现（apps/desktop task-store）。
 */
import type { PlanStep, Task } from '@wdbuddy/shared-types';

export interface CheckpointSnapshot {
  taskId: string;
  stepId: string | null;
  steps: PlanStep[];
  history: Array<{ role: 'system' | 'user' | 'assistant'; content: string }>;
  replanCount: number;
  artifactIds: string[];
  savedAt: number;
}

export interface CheckpointStore {
  /** 保存检查点（幂等：同 taskId+stepId 覆盖） */
  save(task: Task, stepId: string | null, snapshot: Omit<CheckpointSnapshot, 'taskId' | 'stepId' | 'savedAt'>): void;
  /** 读取某任务最新检查点 */
  loadLatest(taskId: string): CheckpointSnapshot | null;
  /** 删除任务全部检查点（任务完成/删除时清理） */
  clear(taskId: string): void;
}

/** 内存实现（测试替身 / 无 DB 场景） */
export class InMemoryCheckpointStore implements CheckpointStore {
  private readonly map = new Map<string, CheckpointSnapshot>();

  save(task: Task, stepId: string | null, snapshot: Omit<CheckpointSnapshot, 'taskId' | 'stepId' | 'savedAt'>): void {
    this.map.set(task.id, { ...snapshot, taskId: task.id, stepId, savedAt: Date.now() });
  }

  loadLatest(taskId: string): CheckpointSnapshot | null {
    return this.map.get(taskId) ?? null;
  }

  clear(taskId: string): void {
    this.map.delete(taskId);
  }
}

/** 校验快照完整性（恢复前防止损坏数据进入循环） */
export function validateSnapshot(s: unknown): s is CheckpointSnapshot {
  if (!s || typeof s !== 'object') return false;
  const o = s as Partial<CheckpointSnapshot>;
  return (
    typeof o['taskId'] === 'string' &&
    Array.isArray(o['steps']) &&
    Array.isArray(o['history']) &&
    typeof o['replanCount'] === 'number'
  );
}
