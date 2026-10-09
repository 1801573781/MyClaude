/**
 * task-list feature store（左栏，§4.3）
 * 任务分组（进行中/已完成/规则任务）+ 工作空间授权 + 快捷操作
 */
import { create } from 'zustand';
import type { Task, TaskTrigger } from '@wdbuddy/shared-types';
import { requireApi } from '../../shared/ipc';

export type TaskGroup = 'active' | 'completed' | 'rules';

interface TaskListStore {
  tasks: Task[];
  loading: boolean;
  workspaces: string[];
  selectedTaskId: string | null;

  refresh(): Promise<void>;
  select(id: string): void;
  control(taskId: string, action: 'start' | 'pause' | 'resume' | 'cancel' | 'approve' | 'reject'): Promise<void>;
  remove(taskId: string): Promise<void>;
  rename(taskId: string, title: string): Promise<void>;
  addWorkspace(): Promise<void>;
  loadWorkspaces(): Promise<void>;
}

function groupOf(t: Task): TaskGroup {
  if (t.status === 'completed') return 'completed';
  if (t.trigger.type !== 'manual') return 'rules';
  return 'active';
}

export const useTaskListStore = create<TaskListStore>((set, get) => ({
  tasks: [],
  loading: false,
  workspaces: [],
  selectedTaskId: null,

  refresh: async () => {
    set({ loading: true });
    try {
      const api = requireApi();
      const { tasks } = await api.taskList({ group: 'all' });
      set({ tasks });
    } finally {
      set({ loading: false });
    }
  },

  select: (id) => set({ selectedTaskId: id }),

  control: async (taskId, action) => {
    const api = requireApi();
    await api.taskControl({ taskId, action });
    await get().refresh();
  },

  remove: async (taskId) => {
    const api = requireApi();
    await api.taskDelete(taskId);
    await get().refresh();
  },

  rename: async (taskId, title) => {
    const api = requireApi();
    await api.taskRename(taskId, title);
    await get().refresh();
  },

  addWorkspace: async () => {
    const api = requireApi();
    await api.workspaceGrant();
    await get().loadWorkspaces();
  },

  loadWorkspaces: async () => {
    const api = requireApi();
    const s = await api.settingsGet();
    set({ workspaces: s.workspaces });
  },
}));

/** 分组视图（§4.3：进行中默认展开，其余折叠显示计数） */
export function groupTasks(tasks: Task[]): Record<TaskGroup, Task[]> {
  const out: Record<TaskGroup, Task[]> = { active: [], completed: [], rules: [] };
  for (const t of tasks) out[groupOf(t)].push(t);
  return out;
}

/** 任务状态徽标文案（§4.3） */
export function taskBadge(t: Task): { text: string; tone: 'neutral' | 'running' | 'success' | 'warning' | 'danger' | 'info' } {
  switch (t.status) {
    case 'running': return { text: '执行中', tone: 'running' };
    case 'awaiting-approval': return { text: '待我确认 ⚠', tone: 'warning' };
    case 'paused': return { text: '已暂停', tone: 'neutral' };
    case 'completed': return { text: '已完成', tone: 'success' };
    case 'failed': return { text: '失败', tone: 'danger' };
    case 'cancelled': return { text: '已取消', tone: 'neutral' };
    case 'interrupted': return { text: '可恢复', tone: 'info' };
    case 'draft': return { text: '草稿', tone: 'neutral' };
    case 'planning': return { text: '规划中', tone: 'info' };
    default: return { text: t.status, tone: 'neutral' };
  }
}

export type { TaskTrigger };
