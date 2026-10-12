/**
 * 全局 AppStore（Zustand）
 * - 布局状态（侧栏折叠、右栏宽度）
 * - 全局模型选择 / Max 模式
 * - runtime:event 订阅桥接（chat/artifacts store 消费统一事件流）
 */
import { create } from 'zustand';
import type { CustomProvider, ModelId, RuntimeEvent } from '@wdbuddy/shared-types';
import { useEffect } from 'react';

export type SidePanelName = 'tasks' | 'skills' | 'preferences' | 'settings' | 'remote-link' | 'new-task' | 'templates' | null;

export type RuntimeState = 'idle' | 'running' | 'offline';

interface AppStore {
  // 布局
  sidebarCollapsed: boolean;
  artifactsHidden: boolean;
  artifactWidth: number;
  toggleSidebar(): void;
  toggleArtifacts(): void;
  setArtifactWidth(w: number): void;

  // 全局面板
  openPanel: SidePanelName;
  setOpenPanel(p: SidePanelName): void;

  // 模型 / Max
  currentModel: ModelId;
  setCurrentModel(m: ModelId): void;
  /** 用户自定义 Provider（settings 同步，供顶栏/向导下拉渲染） */
  customProviders: CustomProvider[];
  setCustomProviders(list: CustomProvider[]): void;
  /** 全局自定义 Provider 表单（顶栏/设置页共用入口） */
  customProviderModal: { initial: CustomProvider | null; switchOnSave: boolean } | null;
  setCustomProviderModal(m: { initial: CustomProvider | null; switchOnSave: boolean } | null): void;
  maxMode: boolean;
  setMaxMode(v: boolean): void;

  // 状态栏
  runtimeState: RuntimeState;
  tokenCount: number;
  credits: number;
  setRuntimeState(s: RuntimeState): void;
  addTokens(n: number): void;
  setCredits(n: number): void;

  // 当前选中任务
  activeTaskId: string | null;
  setActiveTaskId(id: string | null): void;

  /** 跨面板指令注入（技能中心"一点即用"→ Composer 输入框草稿；消费后置 null） */
  composerDraft: string | null;
  setComposerDraft(text: string | null): void;

  // 事件桥接（chat/artifacts 经此分发，feature 间不直接通信）
  dispatchRuntimeEvent(e: RuntimeEvent): void;
  eventListeners: Set<(e: RuntimeEvent) => void>;
  subscribeRuntimeEvent(fn: (e: RuntimeEvent) => void): () => void;
}

export const useAppStore = create<AppStore>((set, get) => ({
  sidebarCollapsed: false,
  artifactsHidden: false,
  artifactWidth: 420,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  toggleArtifacts: () => set((s) => ({ artifactsHidden: !s.artifactsHidden })),
  setArtifactWidth: (w) => set({ artifactWidth: w }),

  openPanel: null,
  setOpenPanel: (p) => set({ openPanel: p }),

  currentModel: 'auto',
  setCurrentModel: (m) => set({ currentModel: m }),
  customProviders: [],
  setCustomProviders: (list) => set({ customProviders: list }),
  customProviderModal: null,
  setCustomProviderModal: (m) => set({ customProviderModal: m }),
  maxMode: false,
  setMaxMode: (v) => set({ maxMode: v }),

  runtimeState: 'idle',
  tokenCount: 0,
  credits: 1000,
  setRuntimeState: (s) => set({ runtimeState: s }),
  addTokens: (n) => set((s) => ({ tokenCount: s.tokenCount + n })),
  setCredits: (n) => set({ credits: n }),

  activeTaskId: null,
  setActiveTaskId: (id) => set({ activeTaskId: id }),

  composerDraft: null,
  setComposerDraft: (text) => set({ composerDraft: text }),

  dispatchRuntimeEvent: (e) => {
    // 状态栏更新
    if (e.type === 'token.delta') {
      get().addTokens(e.text.length);
    }
    if (e.type === 'task.done' || e.type === 'task.error' || e.type === 'task.rejected' || e.type === 'task.cancelled') {
      set({ runtimeState: 'idle' });
    }
    // 广播给订阅者（chat/artifacts store）
    for (const fn of get().eventListeners) fn(e);
  },
  eventListeners: new Set(),
  subscribeRuntimeEvent: (fn) => {
    const set0 = get().eventListeners;
    set0.add(fn);
    return () => set0.delete(fn);
  },
}));

/** React hook：订阅统一事件流（App 根部挂载一次，§10.2） */
export function useRuntimeEventBridge(): void {
  useEffect(() => {
    const api = (window as { wdbuddy?: { onRuntimeEvent: (cb: (e: RuntimeEvent) => void) => () => void } }).wdbuddy;
    if (!api) return;
    const unsub = api.onRuntimeEvent((event) => {
      useAppStore.getState().dispatchRuntimeEvent(event);
    });
    return unsub;
  }, []);
}
