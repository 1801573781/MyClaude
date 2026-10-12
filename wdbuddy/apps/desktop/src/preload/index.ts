/**
 * preload（NFR-03 安全）
 * 仅暴露白名单 API；类型来自 shared-types（IpcRequestMap/IpcResponseMap 派生），禁 any。
 */
import { contextBridge, ipcRenderer } from 'electron';
import type {
  AppSettings,
  Artifact,
  Message,
  ModelId,
  NewTaskInput,
  PlanApprovalRequest,
  PlanStep,
  ProviderKeyEntry,
  PromptEnhanceResult,
  RuntimeEvent,
  Task,
  TaskControlRequest,
  TaskFilter,
} from '@wdbuddy/shared-types';

const api = {
  // ─────────── 任务 ───────────
  taskCreate: (input: NewTaskInput): Promise<{ taskId: string }> =>
    ipcRenderer.invoke('task:create', { input }),
  taskList: (filter: TaskFilter): Promise<{ tasks: Task[] }> =>
    ipcRenderer.invoke('task:list', { filter }),
  taskControl: (req: TaskControlRequest): Promise<{ ok: true }> =>
    ipcRenderer.invoke('task:control', req),
  taskApprovePlan: (req: PlanApprovalRequest): Promise<{ ok: true }> =>
    ipcRenderer.invoke('task:approvePlan', req),
  taskMessages: (taskId: string): Promise<{ messages: Message[] }> =>
    ipcRenderer.invoke('task:messages', { taskId }),
  taskSteps: (taskId: string): Promise<{ steps: PlanStep[] }> =>
    ipcRenderer.invoke('task:steps', { taskId }),
  taskArtifacts: (taskId: string): Promise<{ artifacts: Artifact[] }> =>
    ipcRenderer.invoke('task:artifacts', { taskId }),
  taskRename: (taskId: string, title: string): Promise<{ ok: true }> =>
    ipcRenderer.invoke('task:rename', { taskId, title }),
  taskDelete: (taskId: string): Promise<{ ok: true }> =>
    ipcRenderer.invoke('task:delete', { taskId }),
  taskAccept: (taskId: string): Promise<{ ok: true }> =>
    ipcRenderer.invoke('task:accept', { taskId }),
  chatExport: (taskId: string, format: 'md' | 'html'): Promise<{ ok: boolean; path: string }> =>
    ipcRenderer.invoke('chat:export', { taskId, format }),
  skillsList: (): Promise<{ skills: Array<import('@wdbuddy/shared-types').Skill & { instruction: string }> }> =>
    ipcRenderer.invoke('skills:list', {}),
  skillsCreate: (input: { name: string; description: string; icon: string; category: string; instruction: string; permissions: string[] }): Promise<{ ok: boolean; id?: string; error?: string }> =>
    ipcRenderer.invoke('skills:create', { input }),
  skillsRemove: (id: string): Promise<{ ok: true }> =>
    ipcRenderer.invoke('skills:remove', { id }),

  // ─────────── 提示词 / 工作空间 / 文件 ───────────
  promptEnhance: (text: string): Promise<PromptEnhanceResult> =>
    ipcRenderer.invoke('prompt:enhance', { text }),
  workspaceGrant: (): Promise<{ ok: boolean; uri: string | null }> =>
    ipcRenderer.invoke('workspace:grant', { uri: '' }),
  fsReveal: (path: string): Promise<{ ok: true }> =>
    ipcRenderer.invoke('fs:reveal', { path }),
  templatesPreview: (): Promise<import('@wdbuddy/shared-types').TemplatesPreviewResult> =>
    ipcRenderer.invoke('templates:preview', {}),

  // ─────────── 设置 ───────────
  settingsGet: (): Promise<AppSettings> => ipcRenderer.invoke('settings:get'),
  settingsSet: (settings: AppSettings): Promise<{ ok: true }> =>
    ipcRenderer.invoke('settings:set', { settings }),

  // ─────────── Provider 密钥 ───────────
  providerKeyList: (): Promise<{ keys: ProviderKeyEntry[] }> =>
    ipcRenderer.invoke('providerKey:list', {}),
  providerKeySet: (modelId: ModelId, apiKey: string): Promise<{ ok: true }> =>
    ipcRenderer.invoke('providerKey:set', { modelId, apiKey }),

  // ─────────── 权限应答 ───────────
  permissionRespond: (
    requestId: string,
    decision: 'allow-once' | 'allow-always-task' | 'deny'
  ): Promise<{ ok: true }> => ipcRenderer.invoke('permission:respond', { requestId, decision }),

  // ─────────── 事件订阅（M→R） ───────────
  onRuntimeEvent: (callback: (event: RuntimeEvent) => void): (() => void) => {
    const listener = (_e: unknown, event: RuntimeEvent): void => callback(event);
    ipcRenderer.on('runtime:event', listener);
    return () => ipcRenderer.removeListener('runtime:event', listener);
  },
  onUpdateStatus: (callback: (s: unknown) => void): (() => void) => {
    const listener = (_e: unknown, s: unknown): void => callback(s);
    ipcRenderer.on('update:status', listener);
    return () => ipcRenderer.removeListener('update:status', listener);
  },

  // ─────────── 平台信息 ───────────
  platform: process.platform,
};

export type WdBuddyApi = typeof api;

contextBridge.exposeInMainWorld('wdbuddy', api);
