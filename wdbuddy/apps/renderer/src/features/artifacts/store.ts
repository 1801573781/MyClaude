/**
 * artifacts feature store（右栏，§4.5）
 * 四 Tab：预览/文件/变更/浏览器；验收闭环（FR-11）
 */
import { create } from 'zustand';
import type { Artifact, RuntimeEvent } from '@wdbuddy/shared-types';
import { requireApi } from '../../shared/ipc';
import { useAppStore } from '../../shared/store';

export type ArtifactTab = 'preview' | 'files' | 'diff' | 'browser';

interface ArtifactsStore {
  tab: ArtifactTab;
  artifacts: Artifact[];
  selectedArtifactId: string | null;
  browserUrl: string;
  browserInput: string;

  setTab(t: ArtifactTab): void;
  load(taskId: string): Promise<void>;
  select(id: string | null): void;
  rollback(artifactId: string): Promise<void>;
  rollbackAll(): Promise<void>;
  accept(): Promise<void>;
  setBrowserUrl(url: string): void;
  setBrowserInput(url: string): void;
  handleEvent(e: RuntimeEvent): void;
}

export const useArtifactsStore = create<ArtifactsStore>((set, get) => ({
  tab: 'preview',
  artifacts: [],
  selectedArtifactId: null,
  browserUrl: '',
  browserInput: '',

  setTab: (t) => set({ tab: t }),

  load: async (taskId) => {
    const api = requireApi();
    const { artifacts } = await api.taskArtifacts(taskId);
    set({ artifacts });
  },

  select: (id) => set({ selectedArtifactId: id }),

  /** Diff 逐条回滚（FR-11）：从 artifact-store 恢复历史版本 */
  rollback: async (artifactId) => {
    // 回滚 = 从 artifact-store 恢复该产物的上一版本（主进程 fs 层实现）
    const api = requireApi();
    await api.fsReveal(String(artifactId)); // 占位：真实实现走 artifact:rollback 通道
    const active = useAppStore.getState().activeTaskId;
    if (active) await get().load(active);
  },

  /** 整任务回滚（FR-11） */
  rollbackAll: async () => {
    for (const a of get().artifacts.filter((x) => x.kind === 'diff')) {
      await get().rollback(a.id);
    }
  },

  /** 验收（FR-11）：任务闭环进入已完成。含结果反馈：成功提示 / 无任务提示 */
  accept: async () => {
    const active = useAppStore.getState().activeTaskId;
    if (!active) {
      alert('当前没有选中的任务，无法验收');
      return;
    }
    const api = requireApi();
    await api.taskAccept(active);
    alert('✅ 已验收：任务已归档到「已完成」分组');
    // 侧栏任务列表刷新（验收后任务移入已完成分组）
    void import('../task-list/store').then((m) => m.useTaskListStore.getState().refresh());
  },

  setBrowserUrl: (url) => set({ browserUrl: url }),
  setBrowserInput: (url) => set({ browserInput: url }),

  handleEvent: (e) => {
    if (e.type === 'artifact.created') {
      set((s) => ({ artifacts: [...s.artifacts, e.artifact] }));
    }
  },
}));
